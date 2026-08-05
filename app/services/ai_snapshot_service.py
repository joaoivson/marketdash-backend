"""
Monta o retrato dos números que a IA vai narrar.

Regra de ouro: aqui é onde a MATEMÁTICA acontece. Tudo que sai daqui já está
calculado e classificado — a IA recebe fatos e só escreve o texto. A
classificação de campanha vem inteira do campaign_service, então o que a IA
narra é exatamente o que a aluna vê na tela de Campanhas.
"""
from datetime import date
from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dataset_row import DatasetRow
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
from app.schemas.dashboard import DashboardFilters
from app.services.campaign_service import CampaignService
from app.services.dashboard_service import DashboardService

LIMITE_TOP = 5


class AiSnapshotService:
    def __init__(self, db: Session):
        self.db = db

    # -- coleta -----------------------------------------------------------

    def _tem_meta(self, user_id: int) -> bool:
        integ = FacebookIntegrationRepository(self.db).get_by_user_id(user_id)
        return bool(integ and integ.is_active)

    def _campanhas_do_periodo(self, user_id: int, inicio: date, fim: date) -> List[Any]:
        svc = CampaignService(CampaignRepository(self.db))
        return svc.list_campaigns(user_id, start_date=inicio, end_date=fim).campaigns

    def _kpis_do_periodo(self, user_id: int, inicio: date, fim: date) -> Dict[str, float]:
        kpis = DashboardService.get_kpis(
            self.db, user_id, DashboardFilters(start_date=inicio, end_date=fim)
        )
        return {
            "comissao_liquida": round(kpis.total_commission, 2),
            "receita": round(kpis.total_revenue, 2),
            "gasto": round(kpis.total_cost, 2),
            "lucro": round(kpis.total_profit, 2),
            "pedidos": int(kpis.total_rows),
        }

    def _tops(self, user_id: int, inicio: date, fim: date) -> Dict[str, List[Dict[str, Any]]]:
        def agrupar(coluna):
            linhas = (
                self.db.query(
                    coluna.label("chave"),
                    func.coalesce(func.sum(DatasetRow.commission), 0).label("comissao"),
                    func.count(DatasetRow.id).label("pedidos"),
                )
                .filter(
                    DatasetRow.user_id == user_id,
                    DatasetRow.date >= inicio,
                    DatasetRow.date <= fim,
                    coluna.isnot(None),
                )
                .group_by(coluna)
                .order_by(func.coalesce(func.sum(DatasetRow.commission), 0).desc())
                .limit(LIMITE_TOP)
                .all()
            )
            return [
                {"nome": r.chave, "comissao": float(r.comissao or 0), "pedidos": int(r.pedidos)}
                for r in linhas
            ]

        return {
            "canal": agrupar(DatasetRow.channel),
            "categoria": agrupar(DatasetRow.category),
            "sub_id": agrupar(DatasetRow.sub_id1),
        }

    # -- montagem ---------------------------------------------------------

    def montar(self, user_id: int, inicio: date, fim: date) -> Dict[str, Any]:
        kpis = self._kpis_do_periodo(user_id, inicio, fim)
        tops = self._tops(user_id, inicio, fim)
        tem_meta = self._tem_meta(user_id)

        campanhas: List[Dict[str, Any]] = []
        if tem_meta:
            for c in self._campanhas_do_periodo(user_id, inicio, fim):
                m = c.metrics
                campanhas.append({
                    "nome": c.name,
                    # classificação do backend, intocada — a IA não reclassifica
                    "classificacao": c.health,
                    "ativa": bool(c.is_active),
                    "vinculada": bool(c.linked),
                    "roas": round(float(m.roas), 2),
                    "gasto": round(float(m.spend_with_tax), 2),
                    "comissao_liquida": round(float(m.commission_net), 2),
                    "lucro": round(float(m.profit), 2),
                    "pedidos": int(m.orders),
                    "cliques": int(m.clicks),
                })

        vazio = kpis["pedidos"] == 0 and kpis["comissao_liquida"] == 0 and not campanhas

        return {
            "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
            "kpis": kpis,
            "tops": tops,
            "campanhas": campanhas,
            "tem_meta": tem_meta,
            "vazio": vazio,
        }
