"""
Campanha com anúncio reprovado na moderação da Meta não deve contar como
ativa no card Orçamento/dia — a Meta não rebaixa o status da CAMPANHA
quando é o ANÚNCIO que é reprovado, então ela fica com effective_status
ACTIVE pra sempre, mesmo nunca tendo entregue nada.

Caso real: campanha "Publicação do Instagram: Comente 'CADEIRA' para..."
(orçamento R$12) aparecia como "Recentemente rejeitada" no Gerenciador de
Anúncios (zero gasto/clique/impressão em 30 dias), mas MarketDash contava
como uma das 23 campanhas ativas — o Facebook só reportava 22.
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
from app.services.facebook_integration_service import extract_ad_review_issue

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
        ad_review_issue="Personal attributes",
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
        ad_review_issue="Personal attributes",
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
    assert resp.campaigns[0].ad_review_issue == "Personal attributes"


# --------------------------------------------------------------------------- #
# extract_ad_review_issue — extração pura do payload da Graph API
# --------------------------------------------------------------------------- #


def test_extract_sem_issues_info():
    assert extract_ad_review_issue({}) is None


def test_extract_issues_info_vazio():
    assert extract_ad_review_issue({"issues_info": []}) is None


def test_extract_issues_info_nao_e_lista():
    assert extract_ad_review_issue({"issues_info": "algo"}) is None


def test_extract_prefere_error_summary():
    payload = {
        "issues_info": [
            {
                "error_summary": "Personal attributes",
                "error_message": "Ad was disapproved",
                "error_type": "AD_STATUS_ISSUES_AD_DISAPPROVED",
            }
        ]
    }
    assert extract_ad_review_issue(payload) == "Personal attributes"


def test_extract_cai_pra_error_message_sem_summary():
    payload = {"issues_info": [{"error_message": "Ad was disapproved"}]}
    assert extract_ad_review_issue(payload) == "Ad was disapproved"


def test_extract_cai_pra_error_type_sem_summary_nem_message():
    payload = {"issues_info": [{"error_type": "AD_STATUS_ISSUES_AD_DISAPPROVED"}]}
    assert extract_ad_review_issue(payload) == "AD_STATUS_ISSUES_AD_DISAPPROVED"


def test_extract_entrada_sem_chaves_uteis_retorna_none():
    payload = {"issues_info": [{"level": "AD"}]}
    assert extract_ad_review_issue(payload) is None


def test_extract_entrada_nao_e_dict_retorna_none():
    assert extract_ad_review_issue({"issues_info": ["string solta"]}) is None
