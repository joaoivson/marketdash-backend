"""
Campanha de uma conta de anúncio DESMARCADA em Configurações → Facebook não
deve contar como ativa nem aparecer na lista — mesmo com status ACTIVE
congelado (o sync para de tocar na campanha quando a conta é desmarcada, mas
nada limpava esse status antigo).

Caso real: usuária com 2 contas Facebook, só "Conta 01" selecionada. O card
Orçamento/dia mostrava "10 campanhas ativas" (o Facebook mostra 8 pra Conta
01) — as 2 extras eram campanhas órfãs da outra conta, desmarcada.
"""
import json
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.campaign import Campaign, CampaignDailyInsight
from app.models.facebook_integration import FacebookIntegration
from app.models.user_settings import UserSettings
from app.repositories.campaign_repository import CampaignRepository
from app.services.campaign_service import CampaignService

USUARIA = 1
CONTA_SELECIONADA = "act_111"
CONTA_DESMARCADA = "act_222"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Campaign.__table__.create(engine)
    CampaignDailyInsight.__table__.create(engine)
    FacebookIntegration.__table__.create(engine)
    UserSettings.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _campanha(db, **campos):
    padrao = dict(
        user_id=USUARIA, fb_campaign_id="fb", name="Campanha",
        status="ACTIVE", effective_status="ACTIVE", daily_budget=10.0,
    )
    padrao.update(campos)
    c = Campaign(**padrao)
    db.add(c)
    db.flush()
    return c


def _integracao(db, account_ids):
    db.add(FacebookIntegration(
        user_id=USUARIA, encrypted_access_token="x",
        ad_accounts_json=json.dumps(account_ids),
    ))


def test_campanha_de_conta_desmarcada_nao_conta_como_ativa(db):
    _campanha(db, fb_campaign_id="a1", ad_account_id=CONTA_SELECIONADA)
    _campanha(db, fb_campaign_id="a2", ad_account_id=CONTA_SELECIONADA)
    # Órfã: status ACTIVE congelado de quando a conta ainda estava marcada.
    _campanha(db, fb_campaign_id="orfa1", ad_account_id=CONTA_DESMARCADA)
    _integracao(db, [CONTA_SELECIONADA])
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 2
    assert resp.kpis.total_daily_budget == 20.0


def test_campanha_sem_integracao_nenhuma_nao_conta_como_ativa(db):
    # Facebook nunca conectado ou já desconectado — zero contas selecionadas,
    # zero campanhas devem contar (mesmo com linhas órfãs na tabela).
    _campanha(db, fb_campaign_id="a1", ad_account_id=CONTA_SELECIONADA)
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 0
    assert resp.kpis.total_daily_budget == 0.0


def test_list_by_user_sem_filtro_retorna_tudo_com_filtro_so_a_conta_certa(db):
    _campanha(db, fb_campaign_id="a1", ad_account_id=CONTA_SELECIONADA)
    _campanha(db, fb_campaign_id="a2", ad_account_id=CONTA_DESMARCADA)
    db.commit()

    repo = CampaignRepository(db)
    assert len(repo.list_by_user(USUARIA)) == 2
    assert len(repo.list_by_user(USUARIA, ad_account_ids=[CONTA_SELECIONADA])) == 1
