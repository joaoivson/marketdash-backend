"""
Saldo e débito de créditos de IA.

O saldo é DERIVADO: cota do plano menos o que já foi gasto no mês corrente.
Não existe contador guardado nem job de reset — virou o mês, a soma recomeça
sozinha. Um job de reset é mais uma coisa pra falhar em silêncio.
"""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.plans import plan_limit
from app.repositories.ai_credit_repository import AiCreditRepository

CUSTO_GERACAO = 10
CUSTO_CHAT = 1

# Corte de mês em horário de Brasília, não em UTC: perto da virada do mês, UTC
# adianta o reset em até 3h (21h do dia 31 em Brasília já é dia 1 em UTC), o que
# resetaria o saldo da aluna cedo demais. Já tivemos essa mesma classe de bug em
# outro corte por data no projeto (ver histórico de fuso em cortes de período).
_TZ_BR = ZoneInfo("America/Sao_Paulo")


class SaldoInsuficiente(Exception):
    def __init__(self, saldo: int, necessario: int):
        self.saldo = saldo
        self.necessario = necessario
        super().__init__(f"Saldo insuficiente: {saldo} disponível, {necessario} necessário")


def _inicio_do_mes() -> datetime:
    # Calcula o 1º dia do mês corrente no calendário de Brasília e só então
    # converte para UTC — é em UTC que a comparação com `criado_em` acontece.
    agora_br = datetime.now(_TZ_BR)
    inicio_br = agora_br.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return inicio_br.astimezone(timezone.utc)


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
        # Soma do saldo + gravação do débito precisam acontecer atomicamente
        # (lock + soma + insert na mesma transação) para não furar a cota sob
        # requisições concorrentes da mesma usuária — daí delegar ao repository,
        # que é quem detém a sessão do banco e pode abrir essa transação.
        restante, sucesso = self.repo.debitar_atomico(
            user_id=user_id,
            inicio_do_mes=_inicio_do_mes(),
            cota=self.cota(plano),
            creditos=creditos,
            tipo=tipo,
            diagnostic_id=diagnostic_id,
        )
        if not sucesso:
            raise SaldoInsuficiente(saldo=restante, necessario=creditos)
        return restante
