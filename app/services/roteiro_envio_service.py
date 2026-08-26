"""
O MOTOR (F3): processa uma execução em fatias com orçamento de tempo.

Cada fatia re-lê o estado do banco — é assim que pausar/cancelar funciona no
meio de um lote de 30 minutos. As decisões de segurança, na ordem em que
aparecem no loop:

  claim atômico   dois workers nunca seguram a mesma linha (SKIP LOCKED);
  presa=falhou    worker morto entre claim e envio NUNCA gera reenvio;
  janela          fecha no meio → execução volta a `agendada` na próxima
                  abertura (a linha claimada volta a `pendente`);
  tetos           plano → global → instância; instância no teto sai do pool;
                  nenhum pool → `pausada`, retomável;
  rodadas         N mensagens + pausa longa (padrão humano), jitter entre
                  mensagens da mesma rodada;
  disjuntor       erro fatal ou 5 falhas seguidas desconecta a instância e
                  os grupos dela tentam outro número;
  grupo_invalido  pula a linha e desativa o grupo — nunca aborta o lote.
"""
import asyncio
import logging
import random
import time as time_mod
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.plans import normalize_plan, plan_limit, is_unlimited
from app.models.roteiro import (
    CONTEUDO_ACAO, CONTEUDO_MIDIA, CONTEUDO_OFERTA, EXEC_AGENDADA, EXEC_CANCELADA,
    EXEC_CONCLUIDA, EXEC_ENVIANDO, EXEC_PAUSADA, MSG_ENVIADA, MSG_FALHOU,
    MSG_PENDENTE, MSG_PULADA, RoteiroMensagem,
)
from app.models.whatsapp_grupos import INSTANCIA_CONECTADA, INSTANCIA_DESCONECTADA
from app.repositories.roteiro_repository import RoteiroRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository
from app.repositories.whatsapp_instancia_repository import WhatsappInstanciaRepository
from app.services.janela_envio_service import (
    BRT, carregar_config, janela_aberta, proxima_abertura,
)
from app.services.roteiro_service import RoteiroService
from app.services.template_mensagem_service import montar_texto, sortear_variacao
from app.services.waha_client import ErroWhatsapp
from app.services.whatsapp_instancia_service import cliente_da_sessao

logger = logging.getLogger(__name__)


def _janela_do_dia_brt(agora_utc: datetime):
    """(inicio, fim) do dia civil BRT em UTC — a ÚNICA forma de contar 'hoje'."""
    agora_brt = agora_utc.astimezone(BRT)
    inicio = datetime.combine(agora_brt.date(), time.min, tzinfo=BRT)
    return inicio.astimezone(timezone.utc), (inicio + timedelta(days=1)).astimezone(timezone.utc)


class ResultadoDaFatia:
    def __init__(self):
        self.enviadas = 0
        self.falhas = 0
        self.puladas = 0
        self.reagendar = False
        self.motivo_parada: Optional[str] = None

    def to_dict(self) -> Dict:
        return {"enviadas": self.enviadas, "falhas": self.falhas,
                "puladas": self.puladas, "reagendar": self.reagendar,
                "motivo_parada": self.motivo_parada}


class RoteiroEnvioService:
    def __init__(self, db: Session,
                 dormir: Callable[[float], None] = time_mod.sleep,
                 cliente_factory: Callable = cliente_da_sessao,
                 short_link_factory: Optional[Callable] = None):
        self.db = db
        self.repo = RoteiroRepository(db)
        self.repo_instancias = WhatsappInstanciaRepository(db)
        self.repo_grupos = WhatsappGrupoRepository(db)
        self.dormir = dormir                 # injetável: teste não espera 20s
        self.cliente_factory = cliente_factory
        self.short_link_factory = short_link_factory
        self._clientes: Dict[str, object] = {}

    # --- preparação ---------------------------------------------------------

    def _cliente(self, nome_instancia: str):
        if nome_instancia not in self._clientes:
            self._clientes[nome_instancia] = self.cliente_factory(nome_instancia)
        return self._clientes[nome_instancia]

    def _resolver_short_links(self, execucao) -> None:
        """Short links congelados ANTES do loop (spec: erro em UM grupo vira
        `pulado`, nunca aborta o lote). Chamada async via asyncio.run — o
        worker é sync (precedente instagram_tasks)."""
        pendentes = self.repo.pendentes_sem_short_link(execucao.id)
        ofertas = [m for m in pendentes if self._passo_de(m).tipo_conteudo == CONTEUDO_OFERTA]
        if not ofertas:
            return
        from app.repositories.shopee_integration_repository import (
            ShopeeIntegrationRepository,
        )
        from app.services.shopee_integration_service import ShopeeIntegrationService

        # O construtor recebe o REPOSITORY, não a Session. Passar a Session dava
        # AttributeError, o `except` marcava a linha como `pulado` e TODO passo
        # de oferta era silenciosamente descartado em produção — os testes não
        # pegavam porque injetam `short_link_factory`.
        svc = ShopeeIntegrationService(ShopeeIntegrationRepository(self.db))
        for m in ofertas:
            grupo = self._grupo_de(m)
            passo = self._passo_de(m)
            try:
                gerar = self.short_link_factory or (
                    lambda uid, url, sid: asyncio.run(svc.generate_short_link(uid, url, sid))
                )
                m.short_link = gerar(execucao.user_id, passo.oferta_url, grupo.sub_id)
                self.db.add(m)
            except Exception as e:
                logger.warning("Short link falhou p/ grupo %s: %s", grupo.id, str(e)[:120])
                self.repo.marcar(m, MSG_PULADA, erro="short_link")
        self.db.commit()

    def _passo_de(self, mensagem: RoteiroMensagem):
        if not hasattr(self, "_passos_cache"):
            self._passos_cache = {}
        if mensagem.passo_id not in self._passos_cache:
            from app.models.roteiro import RoteiroPasso
            self._passos_cache[mensagem.passo_id] = self.db.query(RoteiroPasso).get(mensagem.passo_id)
        return self._passos_cache[mensagem.passo_id]

    def _grupo_de(self, mensagem: RoteiroMensagem):
        if not hasattr(self, "_grupos_cache"):
            self._grupos_cache = {}
        if mensagem.grupo_id not in self._grupos_cache:
            from app.models.whatsapp_grupos import WhatsappGrupo
            self._grupos_cache[mensagem.grupo_id] = self.db.query(WhatsappGrupo).get(mensagem.grupo_id)
        return self._grupos_cache[mensagem.grupo_id]

    def _texto_final(self, execucao, mensagem: RoteiroMensagem) -> str:
        passo = self._passo_de(mensagem)
        prefixo = sufixo = None
        from app.models.roteiro import Roteiro
        roteiro = self.db.query(Roteiro).get(passo.roteiro_id)
        if roteiro and roteiro.campanha_id:
            from app.models.campanha_grupos import Campanha
            campanha = self.db.query(Campanha).get(roteiro.campanha_id)
            if campanha:
                prefixo, sufixo = campanha.prefixo, campanha.sufixo

        corpo = passo.texto or ""
        if passo.template_id:
            from app.models.roteiro import TemplateVariacao
            variacoes = (self.db.query(TemplateVariacao)
                         .filter(TemplateVariacao.template_id == passo.template_id).all())
            sorteada = sortear_variacao(variacoes)
            if sorteada:
                corpo = sorteada.corpo
        valores = {"link": mensagem.short_link or passo.oferta_url or ""}
        texto = montar_texto(corpo, valores, prefixo, sufixo)
        # Oferta sem {link} no corpo: o link entra no fim — nunca some.
        link = valores["link"]
        if link and link not in texto:
            texto = f"{texto}\n\n{link}" if texto else link
        return texto

    def _instancias_elegiveis(self, user_id: int, inicio, fim) -> List:
        elegiveis = []
        for inst in self.repo_instancias.por_usuario(user_id):
            if inst.status != INSTANCIA_CONECTADA:
                continue
            teto = inst.teto_diario or settings.WHATSAPP_TETO_POR_INSTANCIA
            usadas = self.repo.enviadas_na_janela(user_id, inicio, fim,
                                                  instancia_id=inst.id)
            if usadas < teto:
                elegiveis.append((usadas, inst))
        elegiveis.sort(key=lambda par: par[0])   # menor carga do dia primeiro
        return [inst for _, inst in elegiveis]

    def _instancia_para_grupo(self, grupo_id: int, elegiveis: List,
                              vinculos: Dict[int, List[int]]) -> Optional[object]:
        membros = set(vinculos.get(grupo_id, []))
        for inst in elegiveis:
            if inst.id in membros:
                return inst
        return None

    def _devolver(self, mensagem: RoteiroMensagem) -> None:
        """Linha claimada que não deu para enviar por motivo GLOBAL volta a
        pendente — o problema não é dela."""
        mensagem.status = MSG_PENDENTE
        self.db.add(mensagem)
        self.db.commit()

    # --- a fatia -------------------------------------------------------------

    def processar_fatia(self, execucao_id: int,
                        orcamento_s: Optional[int] = None) -> Dict:
        r = ResultadoDaFatia()
        execucao = self.repo.execucao_por_id(execucao_id)
        if not execucao or execucao.status != EXEC_ENVIANDO:
            r.motivo_parada = "estado"
            return r.to_dict()

        inicio_fatia = time_mod.monotonic()
        orcamento = orcamento_s or settings.WHATSAPP_FATIA_ORCAMENTO_S

        self.repo.liberar_presas(execucao.id)
        self._resolver_short_links(execucao)

        from app.models.user_settings import UserSettings
        us = (self.db.query(UserSettings)
              .filter(UserSettings.user_id == execucao.user_id).first())
        config_janela = carregar_config(getattr(us, "whatsapp_envio_config", None))

        sub = SubscriptionRepository(self.db).get_by_user_id(execucao.user_id)
        teto_plano = plan_limit(normalize_plan(sub.plan if sub else None),
                                "whatsapp_msgs_dia")

        vinculos = self.repo_grupos.instancias_por_grupo(execucao.user_id)
        na_rodada = 0

        while True:
            if time_mod.monotonic() - inicio_fatia > orcamento:
                r.reagendar = True
                r.motivo_parada = "orcamento"
                break

            # Re-lê o estado: pausar/cancelar valem no meio do lote.
            self.db.expire(execucao)
            execucao = self.repo.execucao_por_id(execucao_id)
            if execucao.status in (EXEC_PAUSADA, EXEC_CANCELADA):
                r.motivo_parada = execucao.status
                break

            agora = datetime.now(timezone.utc)

            if not janela_aberta(config_janela, agora):
                abertura = proxima_abertura(config_janela, agora)
                if abertura is None:
                    # Janela que nunca abre (todos os dias inativos): pausar
                    # com motivo claro — re-agendar seria livelock no tick.
                    self._pausar(execucao, r, "janela_sem_dia_ativo")
                    break
                execucao.status = EXEC_AGENDADA
                execucao.proxima_execucao_em = abertura
                self.db.commit()
                r.motivo_parada = "janela"
                logger.info("Execução %s fora da janela — retoma %s",
                            execucao.id, abertura.isoformat())
                break

            inicio_dia, fim_dia = _janela_do_dia_brt(agora)
            if not is_unlimited(teto_plano):
                usadas = self.repo.enviadas_na_janela(execucao.user_id,
                                                      inicio_dia, fim_dia)
                if usadas >= max(teto_plano, 0):
                    # Teto DIÁRIO reseta sozinho: parquear para amanhã (na
                    # abertura da janela), não pausar — pausada exige clique
                    # da afiliada e o roteiro de vários dias morreria no teto.
                    self._parquear_para_amanha(execucao, config_janela, fim_dia, r,
                                               "teto_plano")
                    break
            if (self.repo.enviadas_globais_na_janela(inicio_dia, fim_dia)
                    >= settings.WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA):
                self._parquear_para_amanha(execucao, config_janela, fim_dia, r,
                                           "teto_global")
                break

            elegiveis = self._instancias_elegiveis(execucao.user_id,
                                                   inicio_dia, fim_dia)
            if not elegiveis:
                self._pausar(execucao, r, "sem_instancia")
                break

            mensagem = self.repo.claim_proxima(execucao.id, agora)
            if mensagem is None:
                proxima = self.repo.proxima_pendente_em(execucao.id)
                if proxima is None:
                    self._concluir(execucao)
                    r.motivo_parada = "concluida"
                else:
                    execucao.status = EXEC_AGENDADA
                    execucao.proxima_execucao_em = proxima
                    self.db.commit()
                    r.motivo_parada = "aguardando_proximo_passo"
                break

            instancia = self._instancia_para_grupo(mensagem.grupo_id, elegiveis,
                                                   vinculos)
            if instancia is None:
                self.repo.marcar(mensagem, MSG_PULADA, erro="sem_instancia_no_grupo")
                r.puladas += 1
                continue

            # Flip local (abrir/fechar entrada) não toca o WhatsApp: pagar
            # pausa anti-ban por ele só desperdiça o orçamento da fatia.
            passo_previa = self._passo_de(mensagem)
            e_flip_local = (
                passo_previa.tipo_conteudo == CONTEUDO_ACAO
                and passo_previa.acao in ("abrir_entrada", "fechar_entrada")
            )
            # Ritmo: jitter dentro da rodada; pausa longa entre rodadas.
            if e_flip_local:
                pass
            elif na_rodada >= settings.WHATSAPP_RODADA_TAMANHO:
                self.dormir(random.uniform(settings.WHATSAPP_GRUPO_PAUSA_MIN_S,
                                           settings.WHATSAPP_GRUPO_PAUSA_MAX_S))
                na_rodada = 0
            elif r.enviadas or r.falhas:
                self.dormir(random.uniform(settings.WHATSAPP_GRUPO_JITTER_MIN_S,
                                           settings.WHATSAPP_GRUPO_JITTER_MAX_S))

            grupo = self._grupo_de(mensagem)
            texto = self._texto_final(execucao, mensagem)
            mensagem.texto_final = texto
            mensagem.instancia_id = instancia.id
            passo = self._passo_de(mensagem)

            try:
                cliente = self._cliente(instancia.nome_instancia)
                if passo.tipo_conteudo == CONTEUDO_ACAO:
                    self._executar_acao(cliente, passo, grupo)
                elif passo.tipo_conteudo == CONTEUDO_MIDIA and passo.midia_url:
                    cliente.enviar_imagem(grupo.jid, passo.midia_url, legenda=texto)
                else:
                    cliente.enviar_texto(grupo.jid, texto)
            except ErroWhatsapp as e:
                self._tratar_erro(execucao, mensagem, instancia, e, r, elegiveis)
                continue
            except ValueError as e:
                self.repo.marcar(mensagem, MSG_PULADA, erro=f"jid: {e}")
                r.puladas += 1
                continue

            instancia.falhas_seguidas = 0
            self.repo.marcar(mensagem, MSG_ENVIADA)
            r.enviadas += 1
            if not e_flip_local:
                na_rodada += 1

        self._atualizar_contadores(execucao)
        return r.to_dict()

    # --- desfechos ----------------------------------------------------------

    def _executar_acao(self, cliente, passo, grupo) -> None:
        """Ações do roteiro (spec §4.8): renomear o grupo faz parte da régua de
        lançamento ("ABRE ÀS 20H" → "ABERTO"); abrir/fechar entrada é flip
        local em campanha_grupos — não passa pelo WhatsApp."""
        from app.models.campanha_grupos import CampanhaGrupo
        from app.models.roteiro import Roteiro

        acao = (passo.acao or "").strip()
        if acao == "renomear_grupo":
            novo = (passo.acao_parametro or "").strip()
            if not novo:
                raise ValueError("renomear_grupo sem nome")
            cliente.renomear_grupo(grupo.jid, novo)
            grupo.nome = novo[:255]
            self.db.add(grupo)
            return
        if acao in ("abrir_entrada", "fechar_entrada"):
            roteiro = self.db.query(Roteiro).get(passo.roteiro_id)
            if not roteiro or not roteiro.campanha_id:
                raise ValueError(f"{acao} exige roteiro vinculado a uma campanha")
            vinculo = (
                self.db.query(CampanhaGrupo)
                .filter(CampanhaGrupo.campanha_id == roteiro.campanha_id,
                        CampanhaGrupo.grupo_id == grupo.id)
                .first()
            )
            if not vinculo:
                raise ValueError("grupo fora da campanha do roteiro")
            vinculo.aberto = acao == "abrir_entrada"
            self.db.add(vinculo)
            return
        raise ValueError(f"ação desconhecida: {acao!r}")

    # Motivos que são problema do GRUPO, não do número: pulam a linha e não
    # contam para o disjuntor (5 grupos sem admin não podem desconectar a sessão).
    MOTIVOS_DO_GRUPO = {"grupo_invalido", "sem_permissao", "acao"}

    def _tratar_erro(self, execucao, mensagem, instancia, e: ErroWhatsapp,
                     r: ResultadoDaFatia, elegiveis: List) -> None:
        if e.motivo in ("sem_permissao", "acao"):
            self.repo.marcar(mensagem, MSG_PULADA, erro=e.motivo)
            r.puladas += 1
            return
        if e.motivo == "grupo_invalido":
            grupo = self._grupo_de(mensagem)
            grupo.ativo = False
            self.db.add(grupo)
            self.repo.marcar(mensagem, MSG_PULADA, erro="grupo_invalido")
            r.puladas += 1
            return
        instancia.falhas_seguidas = (instancia.falhas_seguidas or 0) + 1
        if e.fatal or instancia.falhas_seguidas >= settings.WHATSAPP_FALHAS_PARA_PARAR:
            logger.error("Disjuntor: instância %s desconectada (%s, %s seguidas)",
                         instancia.nome_instancia, e.motivo, instancia.falhas_seguidas)
            instancia.status = INSTANCIA_DESCONECTADA
            self.repo_instancias.salvar(instancia)
            if instancia in elegiveis:
                elegiveis.remove(instancia)
            # O problema é da INSTÂNCIA: a linha volta pra fila e outro número
            # (membro do grupo) tenta na próxima volta.
            self._devolver(mensagem)
            return
        self.repo_instancias.salvar(instancia)
        self.repo.marcar(mensagem, MSG_FALHOU, erro=e.motivo)
        r.falhas += 1

    def _parquear_para_amanha(self, execucao, config_janela, fim_dia_utc,
                              r: ResultadoDaFatia, motivo: str) -> None:
        """Teto diário atingido: volta a `agendada` na primeira abertura de
        janela do PRÓXIMO dia BRT — retomada automática, sem clique."""
        retomada = proxima_abertura(config_janela, fim_dia_utc.astimezone(BRT))
        if retomada is None:
            self._pausar(execucao, r, motivo)
            return
        execucao.status = EXEC_AGENDADA
        execucao.proxima_execucao_em = retomada
        self.db.commit()
        r.motivo_parada = motivo
        logger.info("Execução %s no %s — retoma %s", execucao.id, motivo,
                    retomada.isoformat())

    def _pausar(self, execucao, r: ResultadoDaFatia, motivo: str) -> None:
        execucao.status = EXEC_PAUSADA
        self.db.commit()
        r.motivo_parada = motivo
        logger.warning("Execução %s pausada: %s", execucao.id, motivo)

    def _concluir(self, execucao) -> None:
        execucao.status = EXEC_CONCLUIDA
        execucao.concluido_em = datetime.now(timezone.utc)
        execucao.proxima_execucao_em = None
        self.db.commit()

    def _atualizar_contadores(self, execucao) -> None:
        c = self.repo.contadores(execucao.id)
        execucao.enviados = c.get(MSG_ENVIADA, 0)
        execucao.erros = c.get(MSG_FALHOU, 0)
        execucao.pulados = c.get(MSG_PULADA, 0)
        execucao.total = sum(c.values())
        self.db.commit()
