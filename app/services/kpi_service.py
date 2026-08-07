"""
Fonte única dos KPIs que a aluna vê.

Até aqui essa regra existia só no frontend (`src/shared/lib/kpi.ts` e
`chart-utils.ts`), e o backend tinha uma segunda versão — `DashboardService.get_kpis`,
que soma as colunas cruas de `dataset_rows_v2`. As duas davam números diferentes
com os mesmos nomes:

- `dataset_rows_v2.cost` e `.profit` estão MORTAS. Medido em homologação, 30 dias,
  52.077 linhas: `cost != 0` em zero linhas, `profit != 0` em 83. O único caminho
  que as escrevia (`apply_ad_spend`) ficou órfão quando a tela "Custos de Anúncios"
  saiu. Gasto real vive em `ad_spends`.
- A Shopee grava uma linha por ITEM. `COUNT(*)` dava 52.077 onde havia 34.962
  pedidos distintos — 49% de inflação.
- O imposto nunca era aplicado: `commission` cru é bruto, não líquido.

Um relatório de IA narrando "seu lucro foi R$ 0,00" ao lado de uma tela mostrando
lucro real é a falha que o produto não pode ter. Daí este módulo: as fórmulas
ficam aqui, e quem precisar dos números da tela consome daqui.

Fórmulas (espelham `src/shared/lib/tax.ts`):
    gasto_com_imposto = gasto_pago      × (1 + ad_tax_rate)
    comissao_liquida  = comissao_bruta  × (1 − commission_tax_rate)
    lucro             = comissao_liquida − gasto_com_imposto
    ROAS Real         = comissao_liquida ÷ gasto_com_imposto   (breakeven 1,0)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ad_spend import AdSpend
from app.models.dataset_row import DatasetRow
from app.utils.shopee_normalize import DIRECT_ATTRIBUTION

# Status que entram no KPI. Espelha KPI_STATUSES do frontend: cancelado entra na
# soma de comissão (a venda existiu) mas não conta como pedido.
STATUS_DO_KPI = {
    "pendente", "concluído", "concluido", "cancelado",
    "pending", "completed", "cancelled",
}
STATUS_CANCELADO = {"cancelado", "cancelled"}

SEM_SUB_ID = "Sem Sub ID"


def normalizar_sub_id(valor: Optional[str]) -> str:
    """Espelha normalizeSubId do frontend: sem isso o mesmo sub_id vira várias linhas."""
    if not valor:
        return SEM_SUB_ID
    limpo = str(valor).strip()
    if limpo in ("", "NaN", "null"):
        return SEM_SUB_ID
    limpo = re.sub(r"-+$", "", limpo).strip()
    return limpo.lower() if limpo else SEM_SUB_ID


def _duas_casas(valor: float) -> float:
    return round(float(valor or 0), 2)


@dataclass
class KpisDoPeriodo:
    faturamento: float
    comissao_bruta: float
    comissao_liquida: float
    gasto_pago: float
    gasto_com_imposto: float
    lucro: float
    roas: float
    pedidos: int
    pedidos_diretos: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KpiService:
    def __init__(self, db: Session):
        self.db = db

    def _taxas(self, user_id: int) -> tuple[float, float]:
        """(ad_rate, comm_rate) em fração. Mesma fonte usada pelo campaign_service."""
        from app.repositories.user_settings_repository import UserSettingsRepository
        from app.services.user_settings_service import UserSettingsService

        s = UserSettingsService(UserSettingsRepository(self.db)).get_settings(user_id)
        return (s.get("ad_tax_rate") or 0.0) / 100.0, (s.get("commission_tax_rate") or 0.0) / 100.0

    def _linhas_do_kpi(self, user_id: int, inicio: date, fim: date):
        """Linhas do período com status que conta para KPI."""
        return (
            self.db.query(
                DatasetRow.order_id,
                DatasetRow.status,
                DatasetRow.attribution_type,
                DatasetRow.revenue,
                DatasetRow.commission,
            )
            .filter(
                DatasetRow.user_id == user_id,
                DatasetRow.date >= inicio,
                DatasetRow.date <= fim,
                func.lower(func.coalesce(DatasetRow.status, "")).in_(STATUS_DO_KPI),
            )
            .all()
        )

    def gasto_pago(self, user_id: int, inicio: date, fim: date) -> float:
        """Gasto de mídia lançado pela aluna, SEM imposto. Vem de ad_spends."""
        total = (
            self.db.query(func.coalesce(func.sum(AdSpend.amount), 0.0))
            .filter(
                AdSpend.user_id == user_id,
                AdSpend.date >= inicio,
                AdSpend.date <= fim,
            )
            .scalar()
        )
        return _duas_casas(total)

    def kpis(self, user_id: int, inicio: date, fim: date) -> KpisDoPeriodo:
        ad_rate, comm_rate = self._taxas(user_id)
        linhas = self._linhas_do_kpi(user_id, inicio, fim)

        faturamento = _duas_casas(sum(float(l.revenue or 0) for l in linhas))
        comissao_bruta = _duas_casas(sum(float(l.commission or 0) for l in linhas))

        # Pedido distinto, excluindo os 100% cancelados: um pedido só conta se tem
        # ao menos um item não-cancelado. A Shopee grava uma linha por item.
        pedidos: set[str] = set()
        diretos: set[str] = set()
        for l in linhas:
            if not l.order_id:
                continue
            if (l.status or "").strip().lower() in STATUS_CANCELADO:
                continue
            oid = str(l.order_id)
            pedidos.add(oid)
            if (l.attribution_type or "") == DIRECT_ATTRIBUTION:
                diretos.add(oid)

        pago = self.gasto_pago(user_id, inicio, fim)
        gasto_com_imposto = _duas_casas(pago * (1 + ad_rate))
        comissao_liquida = _duas_casas(comissao_bruta * (1 - comm_rate))
        lucro = _duas_casas(comissao_liquida - gasto_com_imposto)
        roas = _duas_casas(comissao_liquida / gasto_com_imposto) if gasto_com_imposto > 0 else 0.0

        return KpisDoPeriodo(
            faturamento=faturamento,
            comissao_bruta=comissao_bruta,
            comissao_liquida=comissao_liquida,
            gasto_pago=pago,
            gasto_com_imposto=gasto_com_imposto,
            lucro=lucro,
            roas=roas,
            pedidos=len(pedidos),
            pedidos_diretos=len(diretos),
        )

    def tops(self, user_id: int, inicio: date, fim: date, limite: int = 5) -> Dict[str, List[Dict[str, Any]]]:
        """
        Top canal / categoria / sub_id por comissão LÍQUIDA.

        Usa as mesmas linhas do KPI (filtro de status) e aplica o imposto, para não
        divergir dos cards. sub_id passa por normalizar_sub_id, senão o mesmo
        identificador se parte em várias linhas.
        """
        _, comm_rate = self._taxas(user_id)

        def agrupar(coluna, normalizar=None) -> List[Dict[str, Any]]:
            linhas = (
                self.db.query(
                    coluna.label("chave"),
                    func.coalesce(func.sum(DatasetRow.commission), 0).label("comissao"),
                    func.count(func.distinct(DatasetRow.order_id)).label("pedidos"),
                )
                .filter(
                    DatasetRow.user_id == user_id,
                    DatasetRow.date >= inicio,
                    DatasetRow.date <= fim,
                    func.lower(func.coalesce(DatasetRow.status, "")).in_(STATUS_DO_KPI),
                )
                .group_by(coluna)
                .all()
            )
            acumulado: Dict[str, Dict[str, Any]] = {}
            for r in linhas:
                nome = normalizar(r.chave) if normalizar else (r.chave or "—")
                item = acumulado.setdefault(nome, {"nome": nome, "comissao": 0.0, "pedidos": 0})
                item["comissao"] += float(r.comissao or 0) * (1 - comm_rate)
                item["pedidos"] += int(r.pedidos or 0)
            ordenado = sorted(acumulado.values(), key=lambda x: x["comissao"], reverse=True)
            for item in ordenado:
                item["comissao"] = _duas_casas(item["comissao"])
            return ordenado[:limite]

        return {
            "canal": agrupar(DatasetRow.channel),
            "categoria": agrupar(DatasetRow.category),
            "sub_id": agrupar(DatasetRow.sub_id1, normalizar_sub_id),
        }
