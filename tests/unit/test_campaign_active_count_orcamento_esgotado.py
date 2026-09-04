"""
Campanha ACTIVE com orçamento VITALÍCIO esgotado fica com `effective_status`
"ACTIVE" travado no Facebook pra sempre, mesmo sem entregar nada há semanas —
`ad_review_issue` não pega esse caso (olha status do ANÚNCIO, não histórico de
entrega). Isso inflava o card "campanhas ativas" e o orçamento somado (que,
por sua vez, batia certo porque o campo somado é `daily_budget`, que fica NULL
quando o orçamento é vitalício — só a CONTAGEM ficava errada).

Caso real (13/08/2026): usuária via "13 campanhas ativas" no MarketDash contra
"11 campanhas ativas" no Gerenciador do Facebook. As 2 extras eram campanhas
de 23/07 com `lifetime_budget` de R$12 já todo gasto e zero insight desde
04-05/07.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.campaign import Campaign, CampaignDailyInsight
# `list_campaigns` consulta o vínculo campanha-de-grupos ↔ anúncio para tirar
# essas campanhas do Lucro e do ROAS Real — sem as tabelas, a query estoura.
from app.models.campanha_anuncio import CampanhaAnuncio
from app.models.campanha_grupos import Campanha
from app.models.facebook_integration import FacebookIntegration
from app.models.user_settings import UserSettings
from app.repositories.campaign_repository import CampaignRepository
from app.services.campaign_service import CampaignService

USUARIA = 1
CONTA = "act_111"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Campaign.__table__.create(engine)
    CampaignDailyInsight.__table__.create(engine)
    FacebookIntegration.__table__.create(engine)
    UserSettings.__table__.create(engine)
    Campanha.__table__.create(engine)
    CampanhaAnuncio.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _campanha(db, **campos):
    padrao = dict(
        user_id=USUARIA, fb_campaign_id="fb", name="Campanha", ad_account_id=CONTA,
        status="ACTIVE", effective_status="ACTIVE",
    )
    padrao.update(campos)
    c = Campaign(**padrao)
    db.add(c)
    db.flush()
    return c


def _insight(db, campaign, dias_atras, spend=10.0):
    db.add(CampaignDailyInsight(
        user_id=USUARIA, campaign_id=campaign.id, fb_campaign_id=campaign.fb_campaign_id,
        date=date.today() - timedelta(days=dias_atras), spend=spend, clicks=1, impressions=10,
    ))


def _integracao(db):
    import json
    db.add(FacebookIntegration(
        user_id=USUARIA, encrypted_access_token="x", ad_accounts_json=json.dumps([CONTA]),
    ))


def test_campanha_orcamento_vitalicio_esgotado_ha_semanas_nao_conta_como_ativa(db):
    velha = timedelta(days=40)
    zumbi = _campanha(
        db, fb_campaign_id="zumbi", lifetime_budget=12.0,
        created_at=datetime.now(timezone.utc) - velha,
    )
    _insight(db, zumbi, dias_atras=38, spend=11.8)  # gastou tudo há mais de um mês
    viva = _campanha(
        db, fb_campaign_id="viva", daily_budget=10.0,
        created_at=datetime.now(timezone.utc) - velha,
    )
    _insight(db, viva, dias_atras=1, spend=5.0)  # entregou ontem
    _integracao(db)
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 1
    assert resp.kpis.total_daily_budget == 10.0


def test_campanha_recem_sincronizada_sem_insight_ainda_conta_como_ativa(db):
    # Acabou de ser criada/sincronizada pela 1ª vez — ainda não teve tempo de
    # acumular insight. Não é o mesmo problema da campanha zumbi; não deve
    # ser penalizada.
    nova = _campanha(db, fb_campaign_id="nova", daily_budget=10.0)  # created_at = agora (default)
    _integracao(db)
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 1
    assert nova.id  # sanity: linha foi criada


def test_campanha_antiga_com_insight_recente_continua_ativa(db):
    velha = timedelta(days=40)
    c = _campanha(
        db, fb_campaign_id="ativa-de-verdade", daily_budget=10.0,
        created_at=datetime.now(timezone.utc) - velha,
    )
    _insight(db, c, dias_atras=2, spend=3.0)
    _integracao(db)
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 1


def test_campanha_antiga_pausada_e_reativada_ainda_sem_insight_conta_como_ativa(db):
    """Campanha criada há semanas, ficou PAUSADA um tempo e o usuário acabou de
    reativar (sync trouxe effective_status=ACTIVE de novo) — ainda não teve
    tempo de acumular insight novo. `created_at` é de semanas atrás, então só
    `status_active_since` (bumpado na transição PAUSED->ACTIVE pelo repositório)
    evita que ela seja confundida com uma campanha zumbi."""
    repo = CampaignRepository(db)
    velha = datetime.now(timezone.utc) - timedelta(days=40)
    campanha = Campaign(
        user_id=USUARIA, fb_campaign_id="reativada", name="Campanha", ad_account_id=CONTA,
        status="PAUSED", effective_status="PAUSED", daily_budget=10.0, created_at=velha,
    )
    db.add(campanha)
    db.flush()
    _insight(db, campanha, dias_atras=20, spend=8.0)  # última entrega antes de pausar

    # Sync detecta a reativação: PAUSED -> ACTIVE.
    repo.upsert_campaign(
        USUARIA, "reativada",
        {"ad_account_id": CONTA, "name": "Campanha", "status": "ACTIVE", "effective_status": "ACTIVE", "daily_budget": 10.0},
    )
    _integracao(db)
    db.commit()

    resp = CampaignService(repo).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 1
    assert resp.kpis.total_daily_budget == 10.0
