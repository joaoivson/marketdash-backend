"""Vínculo Anúncios×Grupos e as métricas que vêm dele (F7)."""
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.campanha_anuncio import CampanhaAnuncio
from app.models.campanha_grupos import Campanha
from app.models.campaign import Campaign, CampaignDailyInsight


class CampanhaAnuncioRepository:
    def __init__(self, db: Session):
        self.db = db

    def campaign_ids(self, campanha_id: int) -> List[int]:
        return [
            cid for (cid,) in
            self.db.query(CampanhaAnuncio.campaign_id)
            .filter(CampanhaAnuncio.campanha_id == campanha_id).all()
        ]

    def campanha_por_campaign(self, user_id: int) -> Dict[int, Dict]:
        """campaign_id (Meta) → {id, nome} da campanha de grupos vinculada.

        É o que põe o selo na tela de Anúncios sem N+1.
        """
        linhas = (
            self.db.query(CampanhaAnuncio.campaign_id, Campanha.id, Campanha.nome)
            .join(Campanha, Campanha.id == CampanhaAnuncio.campanha_id)
            .filter(Campanha.user_id == user_id)
            .all()
        )
        return {cid: {"id": gid, "nome": nome} for cid, gid, nome in linhas}

    def vinculos_de_outras_campanhas(self, campanha_id: int,
                                     campaign_ids: List[int]) -> Dict[int, int]:
        """campaign_id → campanha de grupos que já o tem, fora esta."""
        if not campaign_ids:
            return {}
        linhas = (
            self.db.query(CampanhaAnuncio.campaign_id, CampanhaAnuncio.campanha_id)
            .filter(CampanhaAnuncio.campaign_id.in_(campaign_ids),
                    CampanhaAnuncio.campanha_id != campanha_id)
            .all()
        )
        return {cid: gid for cid, gid in linhas}

    def definir(self, campanha_id: int, campaign_ids: List[int]) -> None:
        """Substitui o conjunto de anúncios vinculados (multi-select da tela)."""
        atuais = {
            v.campaign_id: v for v in
            self.db.query(CampanhaAnuncio)
            .filter(CampanhaAnuncio.campanha_id == campanha_id).all()
        }
        desejados = set(campaign_ids)
        for cid in desejados - set(atuais):
            self.db.add(CampanhaAnuncio(campanha_id=campanha_id, campaign_id=cid))
        for cid, vinculo in atuais.items():
            if cid not in desejados:
                self.db.delete(vinculo)

    def metricas(self, user_id: int, campanha_id: int, inicio: date, fim: date) -> Dict:
        """
        Gasto, gasto com imposto, Leads e nº de anúncios vinculados no período.

        `gasto_com_imposto` sai daqui e de nenhum outro lugar: quando a mesma
        conta de dinheiro existe em duas camadas, uma delas fica para trás na
        próxima mudança de regra de imposto.
        """
        ids = self.campaign_ids(campanha_id)
        if not ids:
            return {"gasto": 0.0, "gasto_com_imposto": 0.0, "leads": None, "campanhas": 0}
        linha = (
            self.db.query(func.coalesce(func.sum(CampaignDailyInsight.spend), 0.0),
                          func.sum(CampaignDailyInsight.leads))
            .filter(CampaignDailyInsight.user_id == user_id,
                    CampaignDailyInsight.campaign_id.in_(ids),
                    CampaignDailyInsight.date >= inicio,
                    CampaignDailyInsight.date <= fim)
            .one()
        )
        from app.services.kpi_service import KpiService

        gasto, leads = float(linha[0] or 0.0), linha[1]
        ad_rate, _comm = KpiService(self.db).taxas(user_id)
        # leads NULL (nenhum dia reportou) ≠ 0 (reportou zero): a tela mostra
        # "configure o pixel" no primeiro caso.
        return {"gasto": gasto, "gasto_com_imposto": gasto * (1 + ad_rate),
                "leads": int(leads) if leads is not None else None,
                "campanhas": len(ids)}

    def gasto_com_imposto(self, user_id: int, campanha_id: int,
                          inicio: date, fim: date) -> float:
        """Atalho para quem só quer o número; a conta vive em `metricas`."""
        return self.metricas(user_id, campanha_id, inicio, fim)["gasto_com_imposto"]

    def filtrar_por_vinculo(self, user_id: int, campanhas: List,
                            vinculo: str) -> List:
        """
        Filtra campanhas de anúncio por terem (ou não) vínculo com uma campanha
        de grupos. `vinculo` fora de {com_grupo, sem_grupo} devolve tudo.

        Existe para o EXPORT bater com a tela: exportar com "Vinculadas a grupo"
        ativo e receber todas as campanhas é pior do que não ter export.
        """
        if vinculo not in ("com_grupo", "sem_grupo"):
            return list(campanhas)
        vinculadas = set(self.campanha_por_campaign(user_id))
        quer = vinculo == "com_grupo"
        return [c for c in campanhas if (c.id in vinculadas) == quer]

    def campanhas_de_anuncio(self, user_id: int,
                             ad_account_ids: Optional[List[str]] = None) -> List[Campaign]:
        """
        Campanhas de anúncio da usuária, opcionalmente só das contas escolhidas.

        `ad_account_ids` vem de `FacebookIntegration.account_ids_list()` (spec
        §4.6): sem o filtro, esta lista diverge da tela de Anúncios, que já o
        aplica — a afiliada via aqui contas que desmarcou lá.
        Lista VAZIA ≠ None: vazia significa "nenhuma conta escolhida", e aí não
        há o que listar; None é "não filtrar".
        """
        q = self.db.query(Campaign).filter(Campaign.user_id == user_id)
        if ad_account_ids is not None:
            if not ad_account_ids:
                return []
            q = q.filter(Campaign.ad_account_id.in_(ad_account_ids))
        return q.order_by(Campaign.name).all()

    def gasto_por_campaign(self, user_id: int, campaign_ids: List[int],
                           inicio: date, fim: date) -> Dict[int, float]:
        """campaign_id → gasto no período. Uma query para a lista toda (sem N+1)."""
        if not campaign_ids:
            return {}
        linhas = (
            self.db.query(CampaignDailyInsight.campaign_id,
                          func.coalesce(func.sum(CampaignDailyInsight.spend), 0.0))
            .filter(CampaignDailyInsight.user_id == user_id,
                    CampaignDailyInsight.campaign_id.in_(campaign_ids),
                    CampaignDailyInsight.date >= inicio,
                    CampaignDailyInsight.date <= fim)
            .group_by(CampaignDailyInsight.campaign_id)
            .all()
        )
        return {cid: float(total or 0.0) for cid, total in linhas}
