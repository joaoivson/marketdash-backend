"""Acesso ao extrato de créditos de IA."""
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.ai_credit_ledger import AiCreditLedger

# Namespace fixo do advisory lock de débito de créditos de IA — usado com o
# user_id como segunda chave (forma de 2 inteiros do Postgres). Distinto dos
# namespaces já usados no projeto para outros locks (Shopee: 819100/819101)
# para não colidir com eles na mesma base.
_LOCK_NAMESPACE = 819200


class AiCreditRepository:
    def __init__(self, db: Session):
        self.db = db

    def total_gasto_no_mes(self, user_id: int, inicio_do_mes: datetime) -> int:
        total = (
            self.db.query(func.coalesce(func.sum(AiCreditLedger.creditos), 0))
            .filter(
                AiCreditLedger.user_id == user_id,
                AiCreditLedger.criado_em >= inicio_do_mes,
            )
            .scalar()
        )
        return int(total or 0)

    def registrar(self, user_id, diagnostic_id, tipo, creditos, saldo_apos) -> AiCreditLedger:
        linha = AiCreditLedger(
            user_id=user_id,
            diagnostic_id=diagnostic_id,
            tipo=tipo,
            creditos=creditos,
            saldo_apos=saldo_apos,
        )
        self.db.add(linha)
        self.db.commit()
        return linha

    def debitar_atomico(
        self,
        user_id: int,
        inicio_do_mes: datetime,
        cota: int,
        creditos: int,
        tipo: str,
        diagnostic_id: Optional[int] = None,
    ) -> Tuple[int, bool]:
        """
        Soma o gasto do mês e grava o débito dentro de UMA transação, com o
        advisory lock por usuária adquirido ANTES da soma.

        Por que: sem isso, `debitar()` fazia check-then-act — somava o saldo
        (SELECT), comparava, e só depois gravava (INSERT). Duas requisições
        concorrentes da mesma usuária (dois cliques em "gerar", ou geração e
        chat em abas diferentes) podiam ambas ler o mesmo saldo antes de
        qualquer commit, ambas passar na verificação e ambas gravar — furando
        a cota do mês em silêncio, e cada crédito furado é uma chamada paga
        à OpenAI. O lock serializa por usuária: só uma transação por vez soma
        e grava para o mesmo user_id.

        Usamos pg_advisory_xact_lock (não a variante manual pg_advisory_lock)
        porque ele é liberado sozinho no fim da transação — commit OU
        rollback — mesmo se algo lançar exceção no meio do caminho. A versão
        manual exige unlock explícito e vaza lock se algo levantar exceção
        antes dele.

        Retorna (saldo, sucesso). Se sucesso é False, nada foi gravado e
        `saldo` é o saldo ANTES da tentativa de débito — usado pelo service
        para montar SaldoInsuficiente.
        """
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :uid)"),
            {"ns": _LOCK_NAMESPACE, "uid": user_id},
        )
        gasto = self.total_gasto_no_mes(user_id, inicio_do_mes)
        saldo_atual = max(cota - gasto, 0)
        if saldo_atual < creditos:
            # Só leitura até aqui (nada pra desfazer), mas precisamos encerrar
            # a transação já para liberar o lock — não esperar o caller decidir.
            self.db.rollback()
            return saldo_atual, False
        restante = saldo_atual - creditos
        self.registrar(user_id, diagnostic_id, tipo, creditos, restante)  # já dá commit
        return restante, True
