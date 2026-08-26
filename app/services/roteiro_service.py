"""
Roteiros (F3): resolução de tempos, materialização da execução, preview e
duplicação — o que a spec chama de "coração do módulo" (§9).

Regras de tempo:
  * passo `ancora`: (data_fixa ou data_ancora) + hora_fixa, em BRT;
  * passo `relativo`: horário do passo ANTERIOR + offset_minutos;
  * relativo que cai depois da próxima âncora gera AVISO (a tela mostra antes
    de confirmar; o agendar exige `ignorar_avisos` para prosseguir).

Materialização: TODAS as mensagens nascem com `agendado_para` absoluto no
momento do agendar — o tick só compara timestamps, nunca recalcula. Envio
rápido é um roteiro de 1 passo com âncora "agora": mesma tabela, mesmo motor.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.roteiro import (
    CONTEUDO_ACAO, EXEC_AGENDADA, MSG_PENDENTE, MSG_PULADA, ORIGEM_ENVIO_RAPIDO,
    Roteiro, RoteiroExecucao, RoteiroMensagem, RoteiroPasso, TEMPO_ANCORA,
    TEMPO_RELATIVO,
)
from app.repositories.campanha_grupos_repository import CampanhaGruposRepository
from app.repositories.roteiro_repository import RoteiroRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository
from app.services.janela_envio_service import BRT

logger = logging.getLogger(__name__)


class RoteiroInvalido(Exception):
    pass


class CampanhaInvalida(Exception):
    """campanha_id inexistente ou de outra usuária."""


def _horario_brt(dia: date, hora) -> datetime:
    return datetime(dia.year, dia.month, dia.day, hora.hour, hora.minute,
                    tzinfo=BRT)


def resolver_horarios(passos: List[RoteiroPasso], data_ancora: date
                      ) -> Tuple[List[Tuple[RoteiroPasso, datetime]], List[str]]:
    """[(passo, horário absoluto BRT)] na ordem + avisos de configuração."""
    resolvidos: List[Tuple[RoteiroPasso, datetime]] = []
    avisos: List[str] = []
    anterior: Optional[datetime] = None

    for passo in passos:
        if passo.tipo_tempo == TEMPO_ANCORA:
            if passo.hora_fixa is None:
                raise RoteiroInvalido(f"O passo {passo.ordem} é âncora sem hora fixa.")
            momento = _horario_brt(passo.data_fixa or data_ancora, passo.hora_fixa)
        elif passo.tipo_tempo == TEMPO_RELATIVO:
            if anterior is None:
                raise RoteiroInvalido("O primeiro passo do roteiro precisa ser uma âncora.")
            momento = anterior + timedelta(minutes=int(passo.offset_minutos or 0))
        else:
            raise RoteiroInvalido(f"tipo_tempo desconhecido: {passo.tipo_tempo!r}")

        if anterior is not None and momento < anterior:
            avisos.append(
                f"O passo {passo.ordem} ficou ANTES do passo anterior "
                f"({momento:%d/%m %H:%M}) — confira as âncoras."
            )
        resolvidos.append((passo, momento))
        anterior = momento

    # Relativo que "atravessa" a âncora seguinte (spec §9.2)
    for i, (passo, momento) in enumerate(resolvidos[:-1]):
        proxima_ancora = next(
            (m for p, m in resolvidos[i + 1:] if p.tipo_tempo == TEMPO_ANCORA), None
        )
        if (passo.tipo_tempo == TEMPO_RELATIVO and proxima_ancora
                and momento > proxima_ancora):
            avisos.append(
                f"O passo {passo.ordem} (relativo, {momento:%H:%M}) cai depois "
                f"da próxima âncora ({proxima_ancora:%H:%M})."
            )
    return resolvidos, avisos


def estimativa_de_duracao_s(total_mensagens: int) -> int:
    """O que a afiliada VÊ (o ritmo em si é config de sistema, nunca UI)."""
    pausa_media = (settings.WHATSAPP_GRUPO_PAUSA_MIN_S
                   + settings.WHATSAPP_GRUPO_PAUSA_MAX_S) / 2
    jitter_media = (settings.WHATSAPP_GRUPO_JITTER_MIN_S
                    + settings.WHATSAPP_GRUPO_JITTER_MAX_S) / 2
    por_msg = jitter_media + pausa_media / max(settings.WHATSAPP_RODADA_TAMANHO, 1)
    return int(total_mensagens * por_msg)


class RoteiroService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = RoteiroRepository(db)
        self.repo_grupos = WhatsappGrupoRepository(db)

    def validar_campanha(self, user_id: int, campanha_id: Optional[int]) -> None:
        """Ownership do vínculo roteiro↔campanha — sem isso, prefixo/sufixo de
        OUTRA usuária vazaria para dentro das mensagens enviadas."""
        if campanha_id is None:
            return
        from app.repositories.campanha_grupos_repository import (
            CampanhaGruposRepository as _Repo,
        )
        if _Repo(self.db).por_id(user_id, campanha_id) is None:
            raise CampanhaInvalida("Campanha não encontrada.")

    def definir_passos(self, roteiro: Roteiro, passos_in: List) -> None:
        """Substitui os passos (a camada de rota não constrói ORM)."""
        self.repo.remover_passos(roteiro.id)
        for p in passos_in:
            self.repo.adicionar(RoteiroPasso(
                roteiro_id=roteiro.id, ordem=p.ordem, tipo_tempo=p.tipo_tempo,
                hora_fixa=p.hora_fixa, data_fixa=p.data_fixa,
                offset_minutos=p.offset_minutos, tipo_conteudo=p.tipo_conteudo,
                texto=p.texto, midia_url=p.midia_url, oferta_url=p.oferta_url,
                template_id=p.template_id, acao=p.acao,
                acao_parametro=p.acao_parametro, grupos_alvo=p.grupos_alvo,
                grupos_alvo_ids=p.grupos_alvo_ids, marcar_todos=p.marcar_todos,
            ))
        self.db.commit()

    # --- leitura / composição ----------------------------------------------

    def grupos_do_passo(self, roteiro: Roteiro, passo: RoteiroPasso) -> List:
        """Grupos-alvo do passo. Sem permissão de envio ≠ excluído: a linha
        nasce `pulado` (transparência no relatório) — decidido na materialização."""
        if roteiro.campanha_id:
            vinculos = CampanhaGruposRepository(self.db).vinculos(roteiro.campanha_id)
            ids_da_campanha = [v.grupo_id for v in vinculos]
            grupos = [
                g for g in self.repo_grupos.por_usuario(roteiro.user_id,
                                                        apenas_ativos=False)
                if g.id in set(ids_da_campanha)
            ]
            # preserva a ordem de posicao da campanha
            ordem = {gid: i for i, gid in enumerate(ids_da_campanha)}
            grupos.sort(key=lambda g: ordem.get(g.id, 1_000_000))
        else:
            # apenas_ativos=False: inativo entra como linha `pulado` no
            # relatório em vez de sumir em silêncio.
            grupos = self.repo_grupos.por_usuario(roteiro.user_id, apenas_ativos=False)

        if passo.grupos_alvo == "selecao":
            alvo = set(passo.grupos_alvo_ids or [])
            grupos = [g for g in grupos if g.id in alvo]
        return grupos

    def preview(self, roteiro: Roteiro, data_ancora: date) -> Dict:
        passos = self.repo.passos(roteiro.id)
        if not passos:
            raise RoteiroInvalido("O roteiro não tem passos.")
        resolvidos, avisos = resolver_horarios(passos, data_ancora)
        linhas = []
        total = 0
        for passo, momento in resolvidos:
            grupos = self.grupos_do_passo(roteiro, passo)
            n = len(grupos) if passo.tipo_conteudo != CONTEUDO_ACAO else len(grupos)
            total += n
            linhas.append({
                "ordem": passo.ordem,
                "tipo_conteudo": passo.tipo_conteudo,
                "quando": momento.isoformat(),
                "grupos": n,
            })
        return {
            "passos": linhas,
            "total_mensagens": total,
            "duracao_estimada_s": estimativa_de_duracao_s(total),
            "avisos": avisos,
        }

    # --- agendar ------------------------------------------------------------

    def agendar(self, roteiro: Roteiro, data_ancora: date,
                ignorar_avisos: bool = False,
                agora: Optional[datetime] = None) -> Tuple[RoteiroExecucao, List[str]]:
        passos = self.repo.passos(roteiro.id)
        if not passos:
            raise RoteiroInvalido("O roteiro não tem passos.")
        resolvidos, avisos = resolver_horarios(passos, data_ancora)
        if avisos and not ignorar_avisos:
            return None, avisos   # a rota devolve 422 com os avisos

        execucao = RoteiroExecucao(
            roteiro_id=roteiro.id,
            user_id=roteiro.user_id,
            data_ancora=data_ancora,
            status=EXEC_AGENDADA,
        )
        self.repo.adicionar(execucao)

        mensagens: List[RoteiroMensagem] = []
        for passo, momento in resolvidos:
            for grupo in self.grupos_do_passo(roteiro, passo):
                status = MSG_PENDENTE
                motivo = None
                if not grupo.ativo:
                    status, motivo = MSG_PULADA, "grupo_inativo"
                elif not grupo.permite_envio and passo.tipo_conteudo != CONTEUDO_ACAO:
                    status, motivo = MSG_PULADA, "sem_permissao"
                elif passo.tipo_conteudo == CONTEUDO_ACAO:
                    # Executor de ações entra na F4 — a linha nasce pulada com
                    # motivo explícito em vez de sumir em silêncio.
                    status, motivo = MSG_PULADA, "acao_indisponivel"
                mensagens.append(RoteiroMensagem(
                    execucao_id=execucao.id,
                    passo_id=passo.id,
                    grupo_id=grupo.id,
                    user_id=roteiro.user_id,
                    agendado_para=momento,
                    status=status,
                    erro_motivo=motivo,
                ))
        if not mensagens:
            raise RoteiroInvalido("Nenhum grupo-alvo para este roteiro.")
        self.repo.materializar_mensagens(mensagens)

        pendentes = [m for m in mensagens if m.status == MSG_PENDENTE]
        execucao.total = len(mensagens)
        execucao.pulados = len(mensagens) - len(pendentes)
        if pendentes:
            execucao.proxima_execucao_em = min(m.agendado_para for m in pendentes)
        else:
            # Tudo pulado (ex.: nenhum grupo permite envio): concluir JÁ —
            # "agendada" com proxima NULL seria invisível ao tick para sempre.
            from app.models.roteiro import EXEC_CONCLUIDA
            from datetime import datetime as _dt, timezone as _tz
            execucao.status = EXEC_CONCLUIDA
            execucao.concluido_em = _dt.now(_tz.utc)
        self.db.commit()
        return execucao, avisos

    # --- envio rápido -------------------------------------------------------

    def criar_envio_rapido(self, user_id: int, texto: Optional[str],
                           midia_url: Optional[str], oferta_url: Optional[str],
                           grupo_ids: List[int],
                           campanha_id: Optional[int] = None) -> Roteiro:
        """Roteiro de 1 passo — mesma tabela, mesmo motor (spec §9.4)."""
        if not (texto or midia_url or oferta_url):
            raise RoteiroInvalido("Informe um texto, uma imagem ou um link de oferta.")
        if not grupo_ids:
            raise RoteiroInvalido("Escolha ao menos um grupo.")
        agora_brt = datetime.now(BRT)
        roteiro = self.repo.adicionar(Roteiro(
            user_id=user_id,
            campanha_id=campanha_id,
            nome=f"Envio rápido {agora_brt:%d/%m %H:%M}",
            status="pronto",
            origem=ORIGEM_ENVIO_RAPIDO,
        ))
        tipo = "oferta" if oferta_url else ("midia" if midia_url else "texto")
        self.repo.adicionar(RoteiroPasso(
            roteiro_id=roteiro.id, ordem=1,
            tipo_tempo=TEMPO_ANCORA, hora_fixa=agora_brt.time(),
            data_fixa=agora_brt.date(),
            tipo_conteudo=tipo, texto=texto, midia_url=midia_url,
            oferta_url=oferta_url, grupos_alvo="selecao",
            grupos_alvo_ids=list(grupo_ids),
        ))
        self.db.commit()
        return roteiro

    # --- duplicar (funcionalidade central p/ lançamento, spec §9.3) ---------

    def duplicar(self, roteiro: Roteiro) -> Roteiro:
        novo = self.repo.adicionar(Roteiro(
            user_id=roteiro.user_id,
            campanha_id=roteiro.campanha_id,
            nome=f"{roteiro.nome} (cópia)"[:120],
            status="rascunho",
            origem=roteiro.origem,
        ))
        for p in self.repo.passos(roteiro.id):
            self.repo.adicionar(RoteiroPasso(
                roteiro_id=novo.id, ordem=p.ordem, tipo_tempo=p.tipo_tempo,
                hora_fixa=p.hora_fixa, data_fixa=None,   # data resolve na nova âncora
                offset_minutos=p.offset_minutos, tipo_conteudo=p.tipo_conteudo,
                texto=p.texto, midia_url=p.midia_url, oferta_url=p.oferta_url,
                template_id=p.template_id, acao=p.acao,
                acao_parametro=p.acao_parametro, grupos_alvo=p.grupos_alvo,
                grupos_alvo_ids=p.grupos_alvo_ids, marcar_todos=p.marcar_todos,
            ))
        self.db.commit()
        return novo
