"""
Texto do resumo diário.

Todo número aqui vem do `KpiService` e a classificação de campanha vem do
`campaign_service` — os mesmos que alimentam a tela e o Diagnóstico IA. Nenhuma
conta é refeita neste arquivo. Se o WhatsApp e o dashboard discordarem, o bug
está num lugar só, não em dois que precisam ser mantidos em sincronia.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from app.repositories.campaign_repository import CampaignRepository
from app.services.campaign_service import ROAS_BREAKEVEN, CampaignService
from app.services.kpi_service import KpiService

logger = logging.getLogger(__name__)

BRASILIA = ZoneInfo("America/Sao_Paulo")

# Quantas campanhas problemáticas citar por nome. Acima disso a mensagem vira
# uma parede de texto que ninguém lê no celular.
MAXIMO_DE_ALERTAS = 3

RODAPE = "_Responda SAIR para parar de receber._"


def dia_do_resumo(hoje: Optional[date] = None) -> date:
    """
    Ontem em Brasília.

    O resumo sai às 9h e cobre o dia FECHADO. Usar "hoje" daria um dia pela
    metade, e usar UTC faria o resumo da madrugada apontar para o dia errado —
    o mesmo erro de fuso que já apareceu nos atalhos de período do dashboard.
    """
    base = hoje or date.today()
    return base - timedelta(days=1)


def _reais(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@dataclass
class Resumo:
    texto: str
    tem_movimento: bool


class WhatsappResumoService:
    def __init__(self, db):
        self.db = db

    def _campanhas_em_prejuizo(self, user_id: int, dia: date) -> List[str]:
        try:
            campanhas = CampaignService(CampaignRepository(self.db)).list_campaigns(
                user_id, start_date=dia, end_date=dia
            ).campaigns
        except Exception:
            # Campanha é enfeite do resumo, não o resumo. Se a listagem falhar,
            # a afiliada ainda recebe os números do dia.
            logger.exception("Resumo %s: falha ao listar campanhas", user_id)
            return []

        ruins = [
            c for c in campanhas
            if getattr(c, "linked", False)
            and float(getattr(c.metrics, "spend_with_tax", 0) or 0) > 0
            and float(getattr(c.metrics, "roas", 0) or 0) < ROAS_BREAKEVEN
        ]
        ruins.sort(key=lambda c: float(c.metrics.spend_with_tax or 0), reverse=True)
        return [
            f"{c.name[:40]} (ROAS {float(c.metrics.roas or 0):.2f})"
            for c in ruins[:MAXIMO_DE_ALERTAS]
        ]

    def montar(self, user_id: int, primeiro_nome: str, dia: date) -> Resumo:
        k = KpiService(self.db).kpis(user_id, dia, dia)
        data_br = dia.strftime("%d/%m")

        if k.pedidos == 0 and k.comissao_liquida == 0 and k.gasto_com_imposto == 0:
            # Dia sem nada: uma linha, sem tabela de zeros. Mandar "R$ 0,00" em
            # cinco linhas todo dia é o caminho mais curto para o SAIR.
            return Resumo(
                texto=(
                    f"Oi, {primeiro_nome}! Em {data_br} não houve venda nem gasto "
                    f"de anúncio registrado.\n\n{RODAPE}"
                ),
                tem_movimento=False,
            )

        linhas = [
            f"*Seu {data_br} no MarketDash*",
            "",
            f"Comissão líquida: *{_reais(k.comissao_liquida)}*",
            f"Pedidos: *{k.pedidos}*",
        ]
        if k.gasto_com_imposto > 0:
            linhas += [
                f"Gasto com anúncios: {_reais(k.gasto_com_imposto)}",
                f"Lucro: *{_reais(k.lucro)}*",
                f"ROAS: *{k.roas:.2f}*" + ("" if k.roas >= ROAS_BREAKEVEN else "  ⚠️"),
            ]
        else:
            linhas.append("Sem gasto de anúncio no dia.")

        alertas = self._campanhas_em_prejuizo(user_id, dia)
        if alertas:
            linhas += ["", "*Abaixo do ponto de equilíbrio:*"]
            linhas += [f"• {a}" for a in alertas]

        linhas += ["", RODAPE]
        return Resumo(texto="\n".join(linhas), tem_movimento=True)
