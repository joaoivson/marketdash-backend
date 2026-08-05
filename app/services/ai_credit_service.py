"""
Saldo e débito de créditos de IA.

O saldo é DERIVADO: cota do plano menos o que já foi gasto no mês corrente.
Não existe contador guardado nem job de reset — virou o mês, a soma recomeça
sozinha. Um job de reset é mais uma coisa pra falhar em silêncio.
"""
from datetime import datetime, timezone
from typing import Optional

from app.core.plans import plan_limit
from app.repositories.ai_credit_repository import AiCreditRepository

CUSTO_GERACAO = 10
CUSTO_CHAT = 1


class SaldoInsuficiente(Exception):
    def __init__(self, saldo: int, necessario: int):
        self.saldo = saldo
        self.necessario = necessario
        super().__init__(f"Saldo insuficiente: {saldo} disponível, {necessario} necessário")


def _inicio_do_mes() -> datetime:
    agora = datetime.now(timezone.utc)
    return agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class AiCreditService:
    def __init__(self, repo: AiCreditRepository):
        self.repo = repo

    def cota(self, plano: str) -> int:
        return plan_limit(plano, "creditos_ia")

    def saldo(self, user_id: int, plano: str) -> int:
        gasto = self.repo.total_gasto_no_mes(user_id, _inicio_do_mes())
        return max(self.cota(plano) - gasto, 0)

    def tem_saldo(self, user_id: int, plano: str, custo: int) -> bool:
        return self.saldo(user_id, plano) >= custo

    def debitar(
        self,
        user_id: int,
        plano: str,
        tipo: str,
        creditos: int,
        diagnostic_id: Optional[int] = None,
    ) -> int:
        atual = self.saldo(user_id, plano)
        if atual < creditos:
            raise SaldoInsuficiente(saldo=atual, necessario=creditos)
        restante = atual - creditos
        self.repo.registrar(user_id, diagnostic_id, tipo, creditos, restante)
        return restante
