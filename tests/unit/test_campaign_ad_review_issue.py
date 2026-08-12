"""
Campanha com anúncio reprovado na moderação da Meta não deve contar como
ativa no card Orçamento/dia — a Meta não rebaixa o status da CAMPANHA
quando é o ANÚNCIO que é reprovado, então ela fica com effective_status
ACTIVE pra sempre, mesmo nunca tendo entregue nada.

Caso real: campanha "Publicação do Instagram: Comente 'CADEIRA' para..."
(orçamento R$12) aparecia como "Recentemente rejeitada" no Gerenciador de
Anúncios (zero gasto/clique/impressão em 30 dias), mas MarketDash contava
como uma das 23 campanhas ativas — o Facebook só reportava 22.

`issues_info` no nível da CAMPANHA veio vazio pra esse caso real (testado
contra a API real da Meta) — o sinal correto é o `effective_status` do
ANÚNCIO (`derive_ad_review_issue`), não um campo direto da campanha.
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
from app.services.facebook_integration_service import derive_ad_review_issue

USUARIA = 1
CONTA = "act_111"


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
        ad_account_id=CONTA,
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


def test_campanha_com_anuncio_reprovado_nao_conta_como_ativa(db):
    _campanha(db, fb_campaign_id="a1", daily_budget=97.0)
    _campanha(db, fb_campaign_id="a2", daily_budget=100.0)
    # ACTIVE no nível da campanha, mas o anúncio foi reprovado — nunca entregou.
    _campanha(
        db, fb_campaign_id="rejeitada", daily_budget=12.0,
        ad_review_issue="DISAPPROVED",
    )
    _integracao(db, [CONTA])
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 2
    assert resp.kpis.total_daily_budget == 197.0


def test_campanha_sem_problema_reportado_continua_contando(db):
    _campanha(db, fb_campaign_id="a1", daily_budget=50.0, ad_review_issue=None)
    _integracao(db, [CONTA])
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 1
    assert resp.kpis.total_daily_budget == 50.0


def test_campanha_reprovada_ainda_aparece_na_lista_mas_fora_da_contagem(db):
    """A campanha continua visível na lista (não é escondida) — só sai do card."""
    reprovada = _campanha(
        db, fb_campaign_id="rejeitada", daily_budget=12.0,
        ad_review_issue="DISAPPROVED",
    )
    _integracao(db, [CONTA])
    # has_movement exige gasto/clique/impressão/pedido — sem isso a campanha
    # some da lista independente do ad_review_issue. Um insight com clique
    # já basta pra passar no filtro de movimento (sem range de datas, o
    # repositório devolve tudo).
    db.add(CampaignDailyInsight(
        user_id=USUARIA, campaign_id=reprovada.id, fb_campaign_id="rejeitada",
        date=date(2026, 1, 1), spend=0.0, clicks=1, impressions=10,
    ))
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA)

    assert resp.kpis.active_campaigns_count == 0
    assert resp.kpis.total_daily_budget == 0.0
    assert len(resp.campaigns) == 1
    assert resp.campaigns[0].ad_review_issue == "DISAPPROVED"


# --------------------------------------------------------------------------- #
# derive_ad_review_issue — decisão pura a partir de effective_status + anúncios
# --------------------------------------------------------------------------- #


def test_campanha_pausada_nunca_tem_issue_mesmo_sem_ad_ativo():
    """Só campanhas ACTIVE são candidatas — pausada de propósito não é 'reprovada'."""
    ads = [{"id": "ad1", "effective_status": "DISAPPROVED"}]
    assert derive_ad_review_issue("PAUSED", ads) is None


def test_campanha_active_sem_nenhum_anuncio_nao_e_flagada():
    """Campanha nova, ainda sem anúncio criado — não é reprovação, é 'em configuração'."""
    assert derive_ad_review_issue("ACTIVE", []) is None


def test_campanha_active_com_ad_ativo_nao_e_flagada():
    ads = [
        {"id": "ad1", "effective_status": "PAUSED"},
        {"id": "ad2", "effective_status": "ACTIVE"},
    ]
    assert derive_ad_review_issue("ACTIVE", ads) is None


def test_campanha_active_sem_nenhum_ad_ativo_e_flagada_com_o_status_real():
    """Caso real: campanha CADEIRA — ACTIVE, 1 anúncio DISAPPROVED, zero entrega."""
    ads = [{"id": "ad1", "effective_status": "DISAPPROVED"}]
    assert derive_ad_review_issue("ACTIVE", ads) == "DISAPPROVED"


def test_varios_ads_reprovados_juntam_os_status_distintos():
    ads = [
        {"id": "ad1", "effective_status": "DISAPPROVED"},
        {"id": "ad2", "effective_status": "PENDING_REVIEW"},
    ]
    assert derive_ad_review_issue("ACTIVE", ads) == "DISAPPROVED, PENDING_REVIEW"


def test_ad_sem_effective_status_nao_quebra():
    ads = [{"id": "ad1"}]
    assert derive_ad_review_issue("ACTIVE", ads) == "DESCONHECIDO"


def test_effective_status_case_insensitive():
    ads = [{"id": "ad1", "effective_status": "active"}]
    assert derive_ad_review_issue("active", ads) is None
