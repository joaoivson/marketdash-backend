"""
O MOTOR (F3): processa uma execução em fatias com orçamento de tempo.

Cada fatia re-lê o estado do banco — é assim que pausar/cancelar funciona no
meio de um lote de 30 minutos. As decisões de segurança, na ordem em que
aparecem no loop:

  claim atômico   dois workers nunca seguram a mesma linha (SKIP LOCKED);
  presa=falhou    worker morto entre claim e envio NUNCA gera reenvio;
  janela          decidida UMA vez, no INÍCIO da fatia (spec §7.4): começou
                  dentro → o lote CONCLUI, mesmo passando do fim; começou
                  fora → volta a `agendada` na próxima abertura;
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
    ACAO_DESCRICAO, ACAO_IMAGEM, ACAO_RENOMEAR, ACOES_VALIDAS, BLOCO_ARQUIVO,
    BLOCO_AUDIO, BLOCO_IMAGEM, BLOCO_TEXTO, BLOCO_VIDEO, BLOCOS_DE_MIDIA,
    CONTEUDO_ACAO, CONTEUDO_MENSAGEM, CONTEUDO_MIDIA,
    CONTEUDO_OFERTA, EXEC_AGENDADA, EXEC_CANCELADA, EXEC_CONCLUIDA,
    EXEC_ENVIANDO, EXEC_FALHOU, EXEC_PAUSADA, MSG_ENVIADA, MSG_FALHOU,
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
from app.services.whatsapp_grupo_service import garantir_atribuicao
from app.services.waha_client import ErroWhatsapp, nome_e_tipo
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
        # Falhas de REDE desta fatia, agrupadas por proxy: {proxy_id: {inst_id: n}}.
        # É o que separa "o proxy caiu" (todos os chips dele falhando) de
        # "instabilidade pontual num chip" — ver `_tratar_erro`.
        self._rede_por_proxy: Dict[int, Dict[int, int]] = {}
        # Chips cujo TRANSPORTE acabou de falhar nesta fatia. Serve a duas
        # coisas: não martelar o chip que acabou de dar timeout, e permitir que
        # a próxima linha caia noutro chip — sem essa alternância, "todos os
        # chips do proxy falharam" nunca seria observável (o motor escolheria
        # sempre o mesmo número, já que falha não consome cota do dia).
        self._rede_recente: set = set()

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
                # Auto-cura da atribuição: desde que o sub_id passou a nascer na
                # ATIVAÇÃO (spec §6.3), o sync não backfilla mais — um grupo que
                # chegou aqui por caminho que não passou pelo toggle (vínculo por
                # API, dado legado) enviaria com sub_id NULL e a comissão do
                # grupo cairia em lugar nenhum, sem erro. É idempotente: quem já
                # tem sub_id passa ileso, e sub_id existente NUNCA é regenerado.
                if not grupo.sub_id or not grupo.custom_link_id:
                    garantir_atribuicao(self.db, grupo)
                    self.db.commit()
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

    def _blocos_de(self, passo) -> List:
        if not hasattr(self, "_blocos_cache"):
            self._blocos_cache = {}
        if passo.id not in self._blocos_cache:
            self._blocos_cache[passo.id] = self.repo.blocos(passo.id)
        return self._blocos_cache[passo.id]

    def _preparar_saida(self, execucao, mensagem: RoteiroMensagem, passo) -> List[Dict]:
        """O que sai neste passo, em ordem, com tudo já resolvido.

        Um envio real é 4 imagens + um texto. Prefixo e sufixo da campanha
        entram UMA vez cada — prefixo no primeiro bloco com texto, sufixo no
        último —, nunca em todos: repetir a assinatura em cinco mensagens
        seguidas é exatamente o padrão que o WhatsApp pune.
        """
        prefixo, sufixo = self._prefixo_sufixo(passo)
        blocos = self._blocos_de(passo) if passo.tipo_conteudo == CONTEUDO_MENSAGEM else []

        if not blocos:
            # Oferta, e passo pré-082 que a migration não alcançou: uma
            # mensagem só, exatamente como antes.
            return [{
                "tipo": BLOCO_IMAGEM if (passo.tipo_conteudo == CONTEUDO_MIDIA
                                         and passo.midia_url) else BLOCO_TEXTO,
                "url": passo.midia_url,
                "texto": self._texto_de(execucao, mensagem, passo.texto,
                                        passo.template_id, prefixo, sufixo,
                                        com_link=True),
            }]

        saida: List[Dict] = []
        for bloco in blocos:
            midia = bloco.tipo in BLOCOS_DE_MIDIA
            saida.append({
                "tipo": bloco.tipo,
                "url": bloco.conteudo if midia else None,
                "texto": self._texto_de(execucao, mensagem,
                                        bloco.legenda if midia else bloco.conteudo,
                                        bloco.template_id, None, None,
                                        com_link=False),
            })
        # Índices de quem carrega texto: num passo que abre com imagem sem
        # legenda, o prefixo vira a legenda dela em vez de sumir.
        if prefixo:
            saida[0]["texto"] = self._costurar(prefixo, saida[0]["texto"])
        if sufixo:
            saida[-1]["texto"] = self._costurar(saida[-1]["texto"], sufixo)
        self._marcar_quem_menciona(passo, saida)
        return saida

    @staticmethod
    def _marcar_quem_menciona(passo, saida: List[Dict]) -> None:
        """Decide QUAL bloco carrega o "marcar todos" — no máximo um.

        A menção viaja presa a um corpo de texto: o WhatsApp precisa de texto
        para pendurar o `mentionedJid`. Então ela vai no PRIMEIRO bloco que tem
        texto — bloco de texto, ou legenda de imagem/vídeo/arquivo. Sem nenhum
        bloco com texto, ninguém é mencionado (a tela desabilita o toggle nesse
        caso, mas o motor não pode depender disso).

        Marcar em todos os blocos notificaria o grupo N vezes pelo mesmo passo.
        """
        if getattr(passo, "marcar_todos", "nunca") != "sempre":
            return
        for bloco in saida:
            # Nota de voz não tem legenda — pular sem consumir a menção.
            if bloco["tipo"] == BLOCO_AUDIO:
                continue
            if (bloco.get("texto") or "").strip():
                bloco["mencionar"] = True
                return

    @staticmethod
    def _costurar(*partes) -> str:
        return "\n\n".join(p.strip() for p in partes if (p or "").strip())

    @staticmethod
    def _resumo_da_saida(saida: List[Dict]) -> str:
        """O que fica gravado em `texto_final` — auditoria do que saiu, não
        fonte de reenvio (o reenvio recompõe do passo)."""
        partes = []
        for b in saida:
            if b["tipo"] in BLOCOS_DE_MIDIA:
                partes.append(f"[{b['tipo']}] {b['url'] or ''}".strip())
            if b["texto"]:
                partes.append(b["texto"])
        return "\n\n".join(partes)[:8000]

    def _enviar_saida(self, cliente, mensagem: RoteiroMensagem, grupo,
                      saida: List[Dict]) -> None:
        """Envia os blocos em sequência, RETOMANDO de onde parou.

        `blocos_enviados` é gravado a cada bloco: falhar no bloco 3 e reenviar
        do zero mandaria os blocos 1 e 2 de novo no grupo — mensagem repetida é
        o erro que a afiliada vê e que o WhatsApp pune.
        """
        ja_saiu = int(mensagem.blocos_enviados or 0)
        for i, bloco in enumerate(saida, start=1):
            if i <= ja_saiu:
                continue
            if i > 1:
                # Cinco mídias no mesmo segundo é padrão de robô. Não é
                # configurável por ela — é ritmo de sistema.
                self.dormir(random.uniform(settings.WHATSAPP_BLOCO_PAUSA_MIN_S,
                                           settings.WHATSAPP_BLOCO_PAUSA_MAX_S))
            tipo = bloco["tipo"]
            mencionar = bool(bloco.get("mencionar"))
            if tipo in BLOCOS_DE_MIDIA and not bloco["url"]:
                raise ErroWhatsapp("acao", f"bloco {i} sem {tipo}")
            # Nome e mimetype SEMPRE derivados da URL. Os defaults do cliente
            # ("arquivo", `application/octet-stream`) entregavam no grupo um
            # documento sem extensão que o celular não abria — medido em
            # 19/09, com um PDF chegando como "arquivo" e sem conteúdo.
            if tipo == BLOCO_IMAGEM:
                nome, mime = nome_e_tipo(bloco["url"], "imagem.jpeg", "image/jpeg")
                cliente.enviar_imagem(grupo.jid, bloco["url"],
                                      legenda=bloco["texto"] or "",
                                      mimetype=mime, nome_arquivo=nome,
                                      mencionar_todos=mencionar)
            elif tipo == BLOCO_TEXTO:
                cliente.enviar_texto(grupo.jid, bloco["texto"] or "",
                                     mencionar_todos=mencionar)
            elif tipo == BLOCO_VIDEO:
                nome, mime = nome_e_tipo(bloco["url"], "video.mp4", "video/mp4")
                cliente.enviar_video(grupo.jid, bloco["url"],
                                     legenda=bloco["texto"] or "",
                                     mimetype=mime, nome_arquivo=nome,
                                     mencionar_todos=mencionar)
            elif tipo == BLOCO_AUDIO:
                # Sem legenda e sem menção: nota de voz não carrega texto.
                # O mimetype vai o REAL do arquivo (mp3, m4a, webm…) — é ele
                # que diz ao WAHA de onde converter; mentir "já é opus" faria a
                # conversão ser pulada e o áudio virar anexo.
                nome, mime = nome_e_tipo(bloco["url"], "audio.ogg",
                                         "audio/ogg; codecs=opus")
                cliente.enviar_voz(grupo.jid, bloco["url"],
                                   mimetype=mime, nome_arquivo=nome)
            elif tipo == BLOCO_ARQUIVO:
                nome, mime = nome_e_tipo(bloco["url"])
                cliente.enviar_arquivo(grupo.jid, bloco["url"],
                                       legenda=bloco["texto"] or "",
                                       mimetype=mime, nome_arquivo=nome,
                                       mencionar_todos=mencionar)
            else:
                # `oferta` continua reservado para quando a fila de ofertas for
                # definida. Falhar aqui com motivo próprio é melhor que enviar
                # coisa errada.
                raise ErroWhatsapp("bloco_nao_suportado", tipo)
            mensagem.blocos_enviados = i
            self.db.add(mensagem)
            self.db.commit()

    def _prefixo_sufixo(self, passo):
        """Prefixo e sufixo da campanha do roteiro — a assinatura da afiliada."""
        from app.models.roteiro import Roteiro
        roteiro = self.db.query(Roteiro).get(passo.roteiro_id)
        if not (roteiro and roteiro.campanha_id):
            return None, None
        from app.models.campanha_grupos import Campanha
        campanha = self.db.query(Campanha).get(roteiro.campanha_id)
        return (campanha.prefixo, campanha.sufixo) if campanha else (None, None)

    def _texto_de(self, execucao, mensagem: RoteiroMensagem, corpo: Optional[str],
                  template_id: Optional[int], prefixo: Optional[str],
                  sufixo: Optional[str], com_link: bool) -> str:
        """Resolve UM corpo: variação do template + placeholders + link."""
        corpo = corpo or ""
        if template_id:
            from app.models.roteiro import TemplateMensagem, TemplateVariacao

            # JOIN com o dono, não só `template_id`: o id vem do passo, que veio
            # do cliente. Sem isto, o texto do template de OUTRA usuária sairia
            # nos grupos de quem copiou o id.
            variacoes = (self.db.query(TemplateVariacao)
                         .join(TemplateMensagem,
                               TemplateMensagem.id == TemplateVariacao.template_id)
                         .filter(TemplateVariacao.template_id == template_id,
                                 TemplateMensagem.user_id == execucao.user_id).all())
            sorteada = sortear_variacao(variacoes)
            if sorteada:
                corpo = sorteada.corpo

        passo = self._passo_de(mensagem)
        link = (mensagem.short_link or passo.oferta_url or "") if com_link else ""
        texto = montar_texto(corpo, {"link": link}, prefixo, sufixo)
        # Oferta sem {link} no corpo: o link entra no fim — nunca some.
        if link and link not in texto:
            texto = f"{texto}\n\n{link}" if texto else link
        return texto

    def _instancias_elegiveis(self, user_id: int, inicio, fim) -> List:
        elegiveis = []
        for inst in self.repo_instancias.por_usuario(user_id):
            # Pausa é intenção da afiliada e vale mesmo com o chip conectado —
            # ela pausa justamente o número saudável que está sendo usado
            # demais. Filtrar aqui já cobre os dois caminhos de erro do loop:
            # pool vazio vira `sem_instancia`, grupo sem candidato vira
            # `sem_instancia_no_grupo`.
            if inst.status != INSTANCIA_CONECTADA or inst.envio_pausado:
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
        candidatos = [i for i in elegiveis if i.id in membros]
        # Preferir quem ainda não falhou por rede nesta fatia. Só uma
        # preferência: se todos já falharam, tenta de novo em vez de pular o
        # grupo — o objetivo é alternar, não reduzir a capacidade.
        for inst in candidatos:
            if inst.id not in self._rede_recente:
                return inst
        return candidatos[0] if candidatos else None

    def _devolver(self, mensagem: RoteiroMensagem) -> None:
        """Linha claimada que não deu para enviar por motivo GLOBAL volta a
        pendente — o problema não é dela."""
        mensagem.status = MSG_PENDENTE
        self.db.add(mensagem)
        self.db.commit()

    # --- a fatia -------------------------------------------------------------

    def _campanha_pausada(self, execucao) -> bool:
        """A campanha de grupos desta execução está pausada?

        Lê pelo roteiro: `roteiro_execucoes` não tem `campanha_id`, e é o
        `Roteiro` que carrega o vínculo. Roteiro sem campanha (envio rápido
        avulso) nunca está pausado — não há campanha para pausar.
        """
        from app.models.campanha_grupos import CAMPANHA_PAUSADA, Campanha
        from app.models.roteiro import Roteiro

        campanha_id = (
            self.db.query(Roteiro.campanha_id)
            .filter(Roteiro.id == execucao.roteiro_id)
            .scalar()
        )
        if not campanha_id:
            return False
        status = (
            self.db.query(Campanha.status)
            .filter(Campanha.id == campanha_id)
            .scalar()
        )
        return status == CAMPANHA_PAUSADA

    #: Reavaliação de execução parqueada por campanha pausada. Longo de
    #: propósito: o caminho normal de volta é o reagendamento do `atualizar()`
    #: ao despausar, não este relógio — que existe só para o caso de a campanha
    #: ser despausada por fora (SQL direto, import) e ninguém reagendar.
    PARQUEIO_CAMPANHA_PAUSADA_S = 3600

    def processar_fatia(self, execucao_id: int,
                        orcamento_s: Optional[int] = None) -> Dict:
        r = ResultadoDaFatia()
        execucao = self.repo.execucao_por_id(execucao_id)
        if not execucao or execucao.status != EXEC_ENVIANDO:
            r.motivo_parada = "estado"
            return r.to_dict()

        # Campanha pausada não dispara (05/09). Antes, `pausada` era um select
        # que não desligava nada: a entrada continuava e o roteiro também.
        # O guard fica AQUI, no mesmo lugar do estado da execução, para valer
        # também na fatia seguinte — pausar no meio de um lote para o resto.
        #
        # PARQUEIA, não devolve seco. A primeira versão só marcava o motivo e
        # retornava, deixando a linha em `enviando` com o `iniciado_em` antigo
        # — e essa é exatamente a assinatura que `enviando_estagnadas()` (o
        # tick de 5 em 5 minutos) procura para "resgatar" execução cujo worker
        # morreu. A execução era re-enfileirada a cada tick, gravava um
        # `sync_runs` bem-sucedido, batia no guard de novo, e não convergia
        # enquanto a campanha ficasse pausada. Era o único caminho de parada da
        # fatia que não movia a execução de estado.
        #
        # `EXEC_AGENDADA` + `proxima_execucao_em`, não `EXEC_PAUSADA`: pausada
        # exige `POST /retomar` manual, uma por uma. Quem pausou a campanha
        # espera que despausar volte tudo — e volta: `atualizar()` reagenda
        # para agora ao sair de `pausada`, e este parqueamento é só a rede
        # para o caso de ninguém despausar.
        if self._campanha_pausada(execucao):
            self._adiar(
                execucao, r, "campanha_pausada",
                datetime.now(timezone.utc)
                + timedelta(seconds=self.PARQUEIO_CAMPANHA_PAUSADA_S),
            )
            return r.to_dict()

        inicio_fatia = time_mod.monotonic()
        orcamento = orcamento_s or settings.WHATSAPP_FATIA_ORCAMENTO_S

        self.repo.liberar_presas(execucao.id)
        self._resolver_short_links(execucao)

        from app.models.user_settings import UserSettings
        us = (self.db.query(UserSettings)
              .filter(UserSettings.user_id == execucao.user_id).first())
        config_janela = carregar_config(getattr(us, "whatsapp_envio_config", None))

        # Janela UMA vez, no início da fatia (spec §7.4) — e a unidade da regra
        # é a EXECUÇÃO, não a fatia: "execução que começa dentro da janela é
        # concluída, mesmo que ultrapasse o horário de fim". Um lote grande
        # gasta várias fatias (cada uma limitada por orçamento de tempo), então
        # olhar só o início da fatia atual pararia às 22:05 um envio começado
        # às 21:50 — metade dos grupos com a oferta, metade sem, que é
        # exatamente o corte no meio que a regra existe para evitar.
        #
        # `iniciado_em` é gravado no primeiro flip agendada→enviando e nunca
        # mais muda (COALESCE no tick), então a comparação de dia civil BRT
        # impede que execução parqueada ONTEM se considere liberada hoje de
        # madrugada.
        agora = datetime.now(timezone.utc)
        comecou_dentro_da_janela = (
            execucao.iniciado_em is not None
            and execucao.iniciado_em.astimezone(BRT).date() == agora.astimezone(BRT).date()
            and janela_aberta(config_janela, execucao.iniciado_em)
        )
        if not janela_aberta(config_janela, agora) and not comecou_dentro_da_janela:
            abertura = proxima_abertura(config_janela, agora)
            if abertura is None:
                # Janela que nunca abre (todos os dias inativos): pausar com
                # motivo claro — re-agendar seria livelock no tick.
                self._pausar(execucao, r, "janela_sem_dia_ativo")
            else:
                self._adiar(execucao, r, "janela", abertura)
            self._atualizar_contadores(execucao)
            return r.to_dict()

        # Irmã de `liberar_presas`: uma fecha o que ficou preso em `enviando`,
        # a outra fecha o que passou da hora sem o passo ter começado. As duas
        # antes de trabalhar, para a fatia não gastar número com o que não deve
        # mais sair.
        #
        # DEPOIS dos guards de campanha pausada e de janela, de propósito: cada
        # um deles expira com o SEU motivo ("campanha pausada", "fora da janela
        # de envio"), e essa é a informação que ela lê no passo que falhou.
        # Varrer antes carimbaria tudo com o genérico "passou do horário" e a
        # causa se perderia.
        self._expirar_atrasadas(execucao)

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
                # Preserva um motivo mais específico já registrado nesta fatia
                # (ex.: `proxy_degradado`, que pausa a execução de dentro do
                # tratamento de erro). Sobrescrever com "pausada" apagaria
                # justamente o diagnóstico que explica a parada.
                r.motivo_parada = r.motivo_parada or execucao.status
                break

            agora = datetime.now(timezone.utc)

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

            grupo = self._grupo_de(mensagem)
            if not grupo.ativado:
                # Toggle "Ativo" da usuária (spec §6.3): desativar DEPOIS do
                # agendamento também vale — grupo desativado NUNCA recebe
                # envio, mesmo com a linha já materializada por uma campanha
                # antiga. Mesmo padrão de skip do `permite_envio` no agendar.
                self.repo.marcar(mensagem, MSG_PULADA, erro="grupo_desativado")
                r.puladas += 1
                continue

            instancia = self._instancia_para_grupo(mensagem.grupo_id, elegiveis,
                                                   vinculos)
            if instancia is None:
                self.repo.marcar(mensagem, MSG_PULADA, erro="sem_instancia_no_grupo")
                r.puladas += 1
                continue

            # Ritmo: jitter dentro da rodada; pausa longa entre rodadas.
            #
            # Não há mais exceção de "flip local": desde a 082 TODA ação de
            # grupo (renomear, descrição, imagem) é uma chamada real ao
            # WhatsApp e paga o mesmo ritmo anti-ban que uma mensagem.
            if na_rodada >= settings.WHATSAPP_RODADA_TAMANHO:
                self.dormir(random.uniform(settings.WHATSAPP_GRUPO_PAUSA_MIN_S,
                                           settings.WHATSAPP_GRUPO_PAUSA_MAX_S))
                na_rodada = 0
            elif r.enviadas or r.falhas:
                self.dormir(random.uniform(settings.WHATSAPP_GRUPO_JITTER_MIN_S,
                                           settings.WHATSAPP_GRUPO_JITTER_MAX_S))

            passo = self._passo_de(mensagem)
            # Congelado ANTES de sair: a variação de template é sorteada uma
            # vez, e a retomada por bloco reenvia exatamente o que já estava
            # decidido — nunca um sorteio novo no meio de uma mensagem.
            saida = self._preparar_saida(execucao, mensagem, passo)
            mensagem.texto_final = self._resumo_da_saida(saida)
            mensagem.instancia_id = instancia.id

            try:
                cliente = self._cliente(instancia.nome_instancia)
                if passo.tipo_conteudo == CONTEUDO_ACAO:
                    self._executar_acao(cliente, passo, grupo)
                else:
                    self._enviar_saida(cliente, mensagem, grupo, saida)
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
            na_rodada += 1

        self._atualizar_contadores(execucao)
        return r.to_dict()

    # --- desfechos ----------------------------------------------------------

    def _executar_acao(self, cliente, passo, grupo) -> None:
        """Ações do roteiro: renomear, alterar a descrição e alterar a imagem.

        Todas fazem parte da régua de lançamento ("ABRE ÀS 20H" → "ABERTO") e
        todas exigem admin no grupo.

        `abrir_entrada`/`fechar_entrada` SAÍRAM (082). Eram ambíguas: fecha o
        quê — o grupo daquele passo, o toggle "Aberto" da aba Grupos, ou o link
        de entrada da campanha? Três controles de nome parecido governando
        coisas diferentes. Passo antigo com essas ações não é executado: vira
        `pulado` com motivo próprio, visível no relatório, em vez de mexer em
        silêncio num estado que hoje tem dono na tela.
        """
        acao = (passo.acao or "").strip()
        if passo.acao_descontinuada or acao not in ACOES_VALIDAS:
            raise ErroWhatsapp("acao_descontinuada", acao or "sem ação")

        parametro = (passo.acao_parametro or "").strip()
        if not parametro:
            raise ErroWhatsapp("acao", f"{acao} sem parâmetro")

        # As três gravam no registro local ASSIM QUE o WAHA confirma, sem
        # esperar o próximo ciclo de sync. O sync roda 1×/dia: até a rodada 2 a
        # afiliada renomeava o grupo às 10:26 e o painel seguia mostrando o
        # nome antigo — inclusive na prévia do passo. Sem o valor novo na tela
        # ela não tem como saber se o passo funcionou.
        if acao == ACAO_RENOMEAR:
            cliente.renomear_grupo(grupo.jid, parametro)
            grupo.nome = parametro[:255]
            self.db.add(grupo)
            return
        if acao == ACAO_DESCRICAO:
            cliente.alterar_descricao(grupo.jid, parametro)
            # O mesmo corte que o cliente aplica no corpo do PUT — gravar o
            # texto inteiro faria a tela mostrar o que o WhatsApp truncou.
            grupo.descricao = parametro[:2000]
            self.db.add(grupo)
            return
        if acao == ACAO_IMAGEM:
            cliente.alterar_imagem(grupo.jid, parametro)
            # `parametro` é a URL da imagem que ela subiu — a mesma que o WAHA
            # baixou. O sync sobrescreve depois com a URL do WhatsApp, que só
            # existe depois de o WhatsApp processar.
            grupo.foto_url = parametro
            self.db.add(grupo)
            return
        raise ErroWhatsapp("acao", f"ação desconhecida: {acao!r}")

    # Motivos que são problema do GRUPO, não do número: pulam a linha e não
    # contam para o disjuntor (5 grupos sem admin não podem desconectar a sessão).
    MOTIVOS_DO_GRUPO = {"grupo_invalido", "sem_permissao", "acao",
                        "acao_descontinuada", "bloco_nao_suportado"}

    # Erros de TRANSPORTE. Não são sinal de banimento: o WhatsApp não recusou
    # nada — a conversa não chegou lá. Tratá-los como banimento (contando para
    # o disjuntor) desconectava o número por instabilidade de rede.
    MOTIVOS_DE_REDE = {"timeout", "rede"}

    # Repetições na MESMA fatia antes de acusar o proxy. Uma falha isolada é
    # ruído de rede; a segunda, com todos os chips daquele IP falhando, é o IP.
    FALHAS_DE_REDE_PARA_ACUSAR_PROXY = 2

    def _diagnosticar_proxy(self, execucao, instancia, r: ResultadoDaFatia) -> bool:
        """Falha de rede repetida em TODOS os chips do mesmo proxy = o proxy caiu.

        Marca o proxy como degradado e PAUSA a execução; a realocação fica com
        a sonda de saúde. Trocar de IP aqui seria trocar no meio de um envio —
        e IP novo com lote em andamento é o pior momento possível.

        Devolve True quando assumiu o desfecho (execução pausada).
        """
        proxy_id = getattr(instancia, "proxy_id", None)
        if proxy_id is None:
            return False
        por_instancia = self._rede_por_proxy.setdefault(proxy_id, {})
        por_instancia[instancia.id] = por_instancia.get(instancia.id, 0) + 1
        self._rede_recente.add(instancia.id)
        chips = [i for i in self.repo_instancias.por_usuario(execucao.user_id)
                 if getattr(i, "proxy_id", None) == proxy_id]
        # "Todos os chips do proxy" é dentro DESTA usuária: por afinidade, um
        # proxy só atende chips de uma afiliada (proxy_pool_service).
        todos_falharam = chips and all(i.id in por_instancia for i in chips)
        repetiu = sum(por_instancia.values()) >= self.FALHAS_DE_REDE_PARA_ACUSAR_PROXY
        if not (todos_falharam and repetiu):
            return False
        from app.services import proxy_pool_service

        proxy_pool_service.marcar_degradado(
            self.db, proxy_id,
            f"execucao {execucao.id}: falha de rede em todos os chips do proxy",
        )
        self._pausar(execucao, r, "proxy_degradado")
        return True

    def _tratar_erro(self, execucao, mensagem, instancia, e: ErroWhatsapp,
                     r: ResultadoDaFatia, elegiveis: List) -> None:
        if e.motivo in self.MOTIVOS_DO_GRUPO - {"grupo_invalido"}:
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
        if e.motivo in self.MOTIVOS_DE_REDE and getattr(instancia, "proxy_id", None):
            # Chip ATRÁS DE PROXY: rede não é banimento. A tabela do plano
            # (§2.6) separa três coisas que o código tratava como uma só —
            # proxy caído (pausa a execução), instabilidade pontual num chip
            # (falha a linha e segue) e `desconectado`/`auth`, que continuam
            # no disjuntor abaixo. Trocar de proxy porque o NÚMERO caiu
            # queimaria o IP seguinte também.
            #
            # Sem proxy o diagnóstico é impossível (não há como distinguir "o
            # IP caiu" de "o WAHA caiu"), então o caminho segue o disjuntor
            # antigo — que é o comportamento em produção hoje.
            if self._diagnosticar_proxy(execucao, instancia, r):
                self._devolver(mensagem)
                return
            self.repo.marcar(mensagem, MSG_FALHOU, erro=e.motivo)
            r.falhas += 1
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

    # Motivo legível por caminho de parada — é o que a afiliada lê no passo que
    # falhou, então é português, não o slug interno.
    MOTIVO_LEGIVEL = {
        "janela": "fora da janela de envio",
        "janela_sem_dia_ativo": "fora da janela de envio",
        "teto_plano": "teto diário do plano",
        "teto_global": "teto diário da plataforma",
        "teto_instancias": "teto diário dos números",
        "campanha_pausada": "campanha pausada",
        "sem_instancia": "nenhum número conectado",
        "proxy_degradado": "conexão do número instável",
    }

    def _expirar_atrasadas(self, execucao, motivo_parada: Optional[str] = None) -> int:
        """Fecha como `falhou` o que passou da hora sem o passo ter começado.

        Roda em TODO caminho de parada e no início de toda fatia, ao lado de
        `liberar_presas` — as duas são a mesma ideia ("na dúvida, não manda"),
        aplicada a momentos diferentes.
        """
        n = self.repo.expirar_passos_que_nao_comecaram(
            execucao.id,
            datetime.now(timezone.utc),
            settings.ROTEIRO_ATRASO_MAX_S,
            motivo=self.MOTIVO_LEGIVEL.get(motivo_parada or ""),
        )
        if n:
            logger.info("Execução %s: %s mensagem(ns) expiradas (%s)",
                        execucao.id, n, motivo_parada or "atraso")
        return n

    def _adiar(self, execucao, r: ResultadoDaFatia, motivo: str,
               retomada: Optional[datetime]) -> None:
        """Expira o que já venceu e reagenda SÓ o que sobrou.

        Antes da rodada 2 os caminhos de parada adiavam tudo em bloco: a
        execução ficava parqueada até amanhã, a tela dizia "Na fila 60" a noite
        inteira, e a falha só aparecia de manhã. A afiliada precisa ver a falha
        no minuto em que ela acontece — e "nunca envia atrasado" não pode
        conviver com um adiamento que ressuscita mensagem de ontem.

        Roteiro que atravessa dias continua funcionando: o passo de daqui a
        três dias não venceu, não expira, e é ele que define a retomada.
        """
        self._expirar_atrasadas(execucao, motivo)
        proxima = self.repo.proxima_pendente_em(execucao.id)
        if proxima is None:
            self._concluir(execucao)
            r.motivo_parada = motivo
            logger.info("Execução %s encerrada em %s — nada pendente",
                        execucao.id, motivo)
            return
        execucao.status = EXEC_AGENDADA
        # `max`: não adianta acordar antes de a janela abrir, nem antes de a
        # próxima mensagem vencer. O que vier depois manda.
        execucao.proxima_execucao_em = max(proxima, retomada) if retomada else proxima
        self.db.commit()
        r.motivo_parada = motivo
        logger.info("Execução %s adiada por %s — retoma %s", execucao.id,
                    motivo, execucao.proxima_execucao_em.isoformat())

    def _parquear_para_amanha(self, execucao, config_janela, fim_dia_utc,
                              r: ResultadoDaFatia, motivo: str) -> None:
        """Teto diário atingido: expira o que venceu e volta a `agendada` na
        primeira abertura de janela do PRÓXIMO dia BRT, se ainda houver o que
        mandar — retomada automática, sem clique."""
        retomada = proxima_abertura(config_janela, fim_dia_utc.astimezone(BRT))
        if retomada is None:
            self._pausar(execucao, r, motivo)
            return
        self._adiar(execucao, r, motivo, retomada)

    def _pausar(self, execucao, r: ResultadoDaFatia, motivo: str) -> None:
        # Expira ANTES de pausar: execução pausada não recebe mais fatia (o
        # tick só pega `agendada`), então sem isto as mensagens ficariam
        # `pendente` para sempre, e a tela diria "Na fila" até alguém retomar.
        #
        # É auto-limitado: só morre o que JÁ passou da tolerância. Linha que
        # venceu há 10 segundos sobrevive e sai se ela religar o número agora.
        self._expirar_atrasadas(execucao, motivo)
        execucao.status = EXEC_PAUSADA
        self.db.commit()
        r.motivo_parada = motivo
        logger.warning("Execução %s pausada: %s", execucao.id, motivo)

    def _concluir(self, execucao) -> None:
        """Fecha a execução — `falhou` quando NADA saiu e houve erro.

        `EXEC_FALHOU` existia na constante desde a 060 e nunca era atribuído:
        o chip "Falhou" da listagem estava no código do frontend sem nenhum
        caminho no backend que o produzisse. Um roteiro em que tudo deu errado
        aparecia como "Concluído com falhas", que é outra coisa.
        """
        self._atualizar_contadores(execucao)
        tudo_falhou = execucao.enviados == 0 and (execucao.erros or 0) > 0
        execucao.status = EXEC_FALHOU if tudo_falhou else EXEC_CONCLUIDA
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
