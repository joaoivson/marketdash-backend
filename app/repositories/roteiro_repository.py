"""Acesso a roteiros, passos, execuções e mensagens — o chão do motor (F3)."""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.roteiro import (
    EXEC_AGENDADA, EXEC_ENVIANDO, MSG_ENVIADA, MSG_ENVIANDO, MSG_FALHOU,
    MSG_PENDENTE, PassoBloco, Roteiro, RoteiroExecucao, RoteiroMensagem,
    RoteiroPasso,
)


class RoteiroRepository:
    def __init__(self, db: Session):
        self.db = db

    # --- roteiros -----------------------------------------------------------

    def por_usuario(self, user_id: int, campanha_id: Optional[int] = None,
                    incluir_envio_rapido: bool = False) -> List[Roteiro]:
        q = self.db.query(Roteiro).filter(Roteiro.user_id == user_id)
        if campanha_id is not None:
            q = q.filter(Roteiro.campanha_id == campanha_id)
        if not incluir_envio_rapido:
            q = q.filter(Roteiro.origem != "envio_rapido")
        return q.order_by(Roteiro.criado_em.desc()).all()

    def por_id(self, user_id: int, roteiro_id: int) -> Optional[Roteiro]:
        return (
            self.db.query(Roteiro)
            .filter(Roteiro.id == roteiro_id, Roteiro.user_id == user_id)
            .first()
        )

    def passos(self, roteiro_id: int) -> List[RoteiroPasso]:
        return (
            self.db.query(RoteiroPasso)
            .filter(RoteiroPasso.roteiro_id == roteiro_id)
            .order_by(RoteiroPasso.ordem)
            .all()
        )

    def passo_por_id(self, passo_id: int) -> Optional[RoteiroPasso]:
        return self.db.query(RoteiroPasso).get(passo_id)

    def adicionar(self, obj) -> object:
        self.db.add(obj)
        self.db.flush()
        return obj

    def remover_passos(self, roteiro_id: int) -> None:
        """DELETE em massa dos passos.

        ⚠️ `roteiro_mensagens.passo_id` é ON DELETE CASCADE: isto leva junto
        TODA a fila materializada do roteiro, inclusive mensagens pendentes de
        execução em andamento. Foi exatamente assim que, em 06/09, um `salvar`
        às 12:04:39 apagou a mensagem que sairia às 12:05. `definir_passos` NÃO
        usa mais este caminho — faz diff por id. Só chame ao descartar o
        roteiro inteiro.
        """
        self.db.query(RoteiroPasso).filter(
            RoteiroPasso.roteiro_id == roteiro_id
        ).delete(synchronize_session=False)

    # --- blocos do passo ----------------------------------------------------

    def blocos(self, passo_id: int) -> List[PassoBloco]:
        return (
            self.db.query(PassoBloco)
            .filter(PassoBloco.passo_id == passo_id)
            .order_by(PassoBloco.ordem, PassoBloco.id)
            .all()
        )

    def blocos_por_passo(self, passo_ids: List[int]) -> Dict[int, List[PassoBloco]]:
        """Uma query para a lista inteira — o editor abre 22 passos de uma vez
        e um SELECT por passo transformava abrir o roteiro em 22 idas ao banco."""
        if not passo_ids:
            return {}
        mapa: Dict[int, List[PassoBloco]] = {pid: [] for pid in passo_ids}
        linhas = (
            self.db.query(PassoBloco)
            .filter(PassoBloco.passo_id.in_(passo_ids))
            .order_by(PassoBloco.passo_id, PassoBloco.ordem, PassoBloco.id)
            .all()
        )
        for b in linhas:
            mapa.setdefault(b.passo_id, []).append(b)
        return mapa

    def zerar_retomada_dos_blocos(self, passo_id: int) -> int:
        """`blocos_enviados` é posicional. Ao trocar os blocos do passo, uma
        linha que parou no bloco 2 retomaria do "bloco 3" de uma lista nova."""
        n = (
            self.db.query(RoteiroMensagem)
            .filter(RoteiroMensagem.passo_id == passo_id,
                    RoteiroMensagem.blocos_enviados > 0)
            .update({"blocos_enviados": 0}, synchronize_session=False)
        )
        return int(n or 0)

    def remover_blocos(self, passo_id: int) -> None:
        """Blocos são conteúdo puro do passo — não há FK apontando para eles,
        então trocar por substituição é seguro (ao contrário dos passos)."""
        self.db.query(PassoBloco).filter(
            PassoBloco.passo_id == passo_id
        ).delete(synchronize_session=False)

    # --- execuções ----------------------------------------------------------

    def execucao(self, user_id: int, execucao_id: int) -> Optional[RoteiroExecucao]:
        return (
            self.db.query(RoteiroExecucao)
            .filter(RoteiroExecucao.id == execucao_id,
                    RoteiroExecucao.user_id == user_id)
            .first()
        )

    def execucao_por_id(self, execucao_id: int) -> Optional[RoteiroExecucao]:
        """Uso interno do worker (Celery não tem current_user; o user_id vem
        da própria execução e TODAS as queries de mensagem filtram por ele)."""
        return self.db.query(RoteiroExecucao).get(execucao_id)

    def execucoes_por_roteiro(self, roteiro_ids: List[int]
                              ) -> Dict[int, List[RoteiroExecucao]]:
        """Execuções de vários roteiros em UMA query, mais recente primeiro.

        A listagem chamava `execucao_ativa` + `ultima_execucao` por roteiro,
        dentro do laço — 2 SELECTs por linha da tela, mais o `passos()` que já
        existia. Com 20 roteiros era 60 idas ao banco para desenhar 20 chips.
        """
        if not roteiro_ids:
            return {}
        linhas = (
            self.db.query(RoteiroExecucao)
            .filter(RoteiroExecucao.roteiro_id.in_(roteiro_ids))
            .order_by(RoteiroExecucao.criado_em.desc(), RoteiroExecucao.id.desc())
            .all()
        )
        mapa: Dict[int, List[RoteiroExecucao]] = {rid: [] for rid in roteiro_ids}
        for e in linhas:
            mapa.setdefault(e.roteiro_id, []).append(e)
        return mapa

    def total_de_passos(self, roteiro_ids: List[int]) -> Dict[int, int]:
        """Contagem por roteiro — a listagem só precisa do número, e trazia
        todas as linhas de `roteiro_passos` para chamar `len()`."""
        if not roteiro_ids:
            return {}
        linhas = (
            self.db.query(RoteiroPasso.roteiro_id, func.count(RoteiroPasso.id))
            .filter(RoteiroPasso.roteiro_id.in_(roteiro_ids))
            .group_by(RoteiroPasso.roteiro_id)
            .all()
        )
        base = {rid: 0 for rid in roteiro_ids}
        base.update({rid: int(n) for rid, n in linhas})
        return base

    def execucoes_do_roteiro(self, roteiro_id: int) -> List[RoteiroExecucao]:
        return (
            self.db.query(RoteiroExecucao)
            .filter(RoteiroExecucao.roteiro_id == roteiro_id)
            .order_by(RoteiroExecucao.criado_em.desc())
            .all()
        )

    def flip_agendadas_para_enviando(self, agora: datetime) -> List[int]:
        """O TICK: flip atômico agendada→enviando das execuções due.

        UPDATE ... RETURNING sobre o índice parcial — dois ticks simultâneos
        nunca pegam a mesma execução, então nunca enfileiram em dobro.
        """
        linhas = self.db.execute(
            text("""
                UPDATE roteiro_execucoes
                   SET status = :enviando,
                       iniciado_em = COALESCE(iniciado_em, :agora)
                 WHERE status = :agendada
                   AND proxima_execucao_em IS NOT NULL
                   AND proxima_execucao_em <= :agora
             RETURNING id
            """),
            {"enviando": EXEC_ENVIANDO, "agendada": EXEC_AGENDADA, "agora": agora},
        ).fetchall()
        self.db.commit()
        return [r[0] for r in linhas]

    def reagendar_parqueadas_da_campanha(self, campanha_id: int, agora: datetime) -> int:
        """Traz para AGORA as execuções agendadas dos roteiros de uma campanha.

        Chamada quando a campanha sai de `pausada`. A fatia parqueia a execução
        com `proxima_execucao_em` uma hora à frente, e sem isto despausar não
        teria efeito visível — a afiliada ficaria olhando um roteiro parado com
        a campanha ativa, sem nada na tela explicando a espera.

        Só toca `agendada` com data FUTURA: execução já due não precisa de
        empurrão, e `pausada` é decisão dela sobre aquela execução específica —
        despausar a campanha não pode desfazer um `POST /pausar` manual.
        """
        linhas = self.db.execute(
            text("""
                UPDATE roteiro_execucoes e
                   SET proxima_execucao_em = :agora
                  FROM roteiros r
                 WHERE e.roteiro_id = r.id
                   AND r.campanha_id = :campanha_id
                   AND e.status = :agendada
                   AND e.proxima_execucao_em > :agora
             RETURNING e.id
            """),
            {"campanha_id": campanha_id, "agendada": EXEC_AGENDADA, "agora": agora},
        ).fetchall()
        self.db.commit()
        return len(linhas)

    def enviando_estagnadas(self, agora: datetime, limite_s: int = 1800) -> List[int]:
        """Execuções em `enviando` sem atividade há 2× o orçamento da fatia:
        worker morreu depois do flip ou o apply_async falhou. Re-enfileirar é
        seguro — o claim atômico impede duplicação; estagnada = sem worker vivo."""
        linhas = self.db.execute(
            text("""
                SELECT e.id FROM roteiro_execucoes e
                 WHERE e.status = :enviando
                   AND COALESCE(e.iniciado_em, e.criado_em) < :corte
                   AND NOT EXISTS (
                       SELECT 1 FROM roteiro_mensagens m
                        WHERE m.execucao_id = e.id
                          AND m.enviado_em >= :corte
                   )
            """),
            {"enviando": EXEC_ENVIANDO,
             "corte": agora - __import__("datetime").timedelta(seconds=limite_s)},
        ).fetchall()
        return [r[0] for r in linhas]

    # --- mensagens ----------------------------------------------------------

    def materializar_mensagens(self, mensagens: List[RoteiroMensagem]) -> None:
        self.db.add_all(mensagens)
        self.db.flush()

    def claim_proxima(self, execucao_id: int, agora: datetime) -> Optional[RoteiroMensagem]:
        """
        Claim atômico: pega UMA pendente due e marca `enviando` — SKIP LOCKED
        garante que dois workers nunca seguram a mesma linha. Nunca
        IntegrityError: o custo do erro aqui é mensagem duplicada em grupo
        alheio, alto demais para "tentar e ver".
        """
        linha = self.db.execute(
            text("""
                UPDATE roteiro_mensagens
                   SET status = :enviando
                 WHERE id = (
                     SELECT id FROM roteiro_mensagens
                      WHERE execucao_id = :execucao_id
                        AND status = :pendente
                        AND agendado_para <= :agora
                      ORDER BY agendado_para, id
                      LIMIT 1
                        FOR UPDATE SKIP LOCKED
                 )
             RETURNING id
            """),
            {"enviando": MSG_ENVIANDO, "pendente": MSG_PENDENTE,
             "execucao_id": execucao_id, "agora": agora},
        ).fetchone()
        if not linha:
            return None
        self.db.commit()
        return self.db.query(RoteiroMensagem).get(linha[0])

    def liberar_presas(self, execucao_id: int) -> int:
        """Linha presa em `enviando` = worker morreu entre o claim e a
        confirmação. Vira `falhou` e NUNCA é reenviada — não há como saber se
        a mensagem saiu ("na dúvida, não manda")."""
        n = (
            self.db.query(RoteiroMensagem)
            .filter(RoteiroMensagem.execucao_id == execucao_id,
                    RoteiroMensagem.status == MSG_ENVIANDO)
            .update({"status": MSG_FALHOU, "erro_motivo": "interrompida"},
                    synchronize_session=False)
        )
        self.db.commit()
        return int(n or 0)

    def pendentes_sem_short_link(self, execucao_id: int) -> List[RoteiroMensagem]:
        return (
            self.db.query(RoteiroMensagem)
            .filter(RoteiroMensagem.execucao_id == execucao_id,
                    RoteiroMensagem.status == MSG_PENDENTE,
                    RoteiroMensagem.short_link.is_(None))
            .all()
        )

    def proxima_pendente_em(self, execucao_id: int) -> Optional[datetime]:
        return (
            self.db.query(func.min(RoteiroMensagem.agendado_para))
            .filter(RoteiroMensagem.execucao_id == execucao_id,
                    RoteiroMensagem.status == MSG_PENDENTE)
            .scalar()
        )

    def contadores(self, execucao_id: int) -> Dict[str, int]:
        linhas = (
            self.db.query(RoteiroMensagem.status, func.count(RoteiroMensagem.id))
            .filter(RoteiroMensagem.execucao_id == execucao_id)
            .group_by(RoteiroMensagem.status)
            .all()
        )
        return {status: int(n) for status, n in linhas}

    def enviadas_na_janela(self, user_id: int, inicio: datetime, fim: datetime,
                           instancia_id: Optional[int] = None) -> int:
        """Tetos SEM func.date(): a janela (inicio,fim) chega pronta, em BRT
        calculado em Python. Cai no índice parcial (user_id, enviado_em)."""
        q = self.db.query(func.count(RoteiroMensagem.id)).filter(
            RoteiroMensagem.user_id == user_id,
            RoteiroMensagem.status == MSG_ENVIADA,
            RoteiroMensagem.enviado_em >= inicio,
            RoteiroMensagem.enviado_em < fim,
        )
        if instancia_id is not None:
            q = q.filter(RoteiroMensagem.instancia_id == instancia_id)
        return q.scalar() or 0

    def enviadas_globais_na_janela(self, inicio: datetime, fim: datetime) -> int:
        """Teto GLOBAL da plataforma — a única contagem sem user_id, de
        propósito (protege o servidor WAHA inteiro)."""
        return (
            self.db.query(func.count(RoteiroMensagem.id))
            .filter(RoteiroMensagem.status == MSG_ENVIADA,
                    RoteiroMensagem.enviado_em >= inicio,
                    RoteiroMensagem.enviado_em < fim)
            .scalar()
        ) or 0

    def marcar(self, mensagem: RoteiroMensagem, status: str,
               erro: Optional[str] = None) -> None:
        """Commit POR LINHA — é o que dá progresso ao vivo e torna cada fatia
        retomável do ponto exato onde parou."""
        mensagem.status = status
        mensagem.erro_motivo = erro
        if status == MSG_ENVIADA:
            mensagem.enviado_em = datetime.now(timezone.utc)
        self.db.add(mensagem)
        self.db.commit()
