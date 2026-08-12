"""
Comissão de pedido "UNPAID" (comprador ainda não confirmou pagamento) não
deve entrar nos totais de Comissão/Lucro/ROAS — nem no Dashboard (já correto,
`src/shared/lib/kpi.ts::KPI_STATUSES` não inclui "unpaid") nem em Campanhas
(bug: `aggregate_by_subids`/`daily_by_subid`/`sub_id_sales_summary` somavam
comissão de QUALQUER status, sem allowlist nenhuma).

Caso real: usuária Evellyn (user_id 51), 11/08/2026 — Dashboard mostrava
Comissão R$60,06, Campanhas mostrava R$61,31. Diferença de R$1,25 batia
exato com a comissão de 1 linha "UNPAID" daquele dia (R$1,2474). As duas
telas precisam bater — `STATUS_DO_KPI` (app/utils/order_status.py) agora é a
allowlist única, usada pelos dois cálculos.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.dataset_row import DatasetRow
from app.repositories.campaign_repository import CampaignRepository

USUARIA = 1
SUB_ID = "luminarialed"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    DatasetRow.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _linha(db, **campos):
    padrao = dict(
        dataset_id=1, user_id=USUARIA, date=date(2026, 8, 11), product="p",
        status="concluído", revenue=0, commission=0, sub_id1=SUB_ID,
    )
    padrao.update(campos)
    db.add(DatasetRow(**padrao))


def test_unpaid_nao_entra_na_soma_de_comissao_de_aggregate_by_subids(db):
    _linha(db, order_id="P1", status="Pendente", commission=59.2642, revenue=200)
    _linha(db, order_id="U1", status="UNPAID", commission=1.2474, revenue=10)
    _linha(db, order_id="C1", status="Concluído", commission=0.7916, revenue=5)
    db.commit()

    result = CampaignRepository(db).aggregate_by_subids(
        USUARIA, [SUB_ID], date(2026, 8, 11), date(2026, 8, 11)
    )
    agg = result[SUB_ID]

    # 59.2642 + 0.7916 = 60.0558 — bate com o Dashboard (R$60,06), não R$61,31.
    assert round(agg["commission"], 2) == 60.06
    assert agg["orders"] == 2  # UNPAID não conta como pedido do KPI


def test_unpaid_nao_entra_na_soma_de_comissao_diaria(db):
    _linha(db, order_id="P1", status="Pendente", commission=59.2642)
    _linha(db, order_id="U1", status="UNPAID", commission=1.2474)
    _linha(db, order_id="C1", status="Concluído", commission=0.7916)
    db.commit()

    daily = CampaignRepository(db).daily_by_subid(
        USUARIA, SUB_ID, date(2026, 8, 11), date(2026, 8, 11)
    )
    assert round(daily[date(2026, 8, 11)]["commission"], 2) == 60.06


def test_unpaid_nao_entra_no_resumo_de_sub_ids_do_modal_de_vinculo(db):
    _linha(db, order_id="P1", status="Pendente", commission=59.2642)
    _linha(db, order_id="U1", status="UNPAID", commission=1.2474)
    db.commit()

    resumo = CampaignRepository(db).sub_id_sales_summary(USUARIA)
    item = next(r for r in resumo if r["sub_id"] == SUB_ID)
    assert round(item["commission"], 2) == 59.26


def test_cancelado_continua_somando_comissao_mesmo_com_o_novo_filtro(db):
    """Regressão: STATUS_DO_KPI inclui 'cancelado' de propósito — a venda
    existiu, só não conta como pedido (ver test_campaign_repository_cancelled_orders.py)."""
    _linha(db, order_id="CANC", status="cancelado", commission=5.0)
    _linha(db, order_id="UNPAID_X", status="UNPAID", commission=1.25)
    db.commit()

    result = CampaignRepository(db).aggregate_by_subids(
        USUARIA, [SUB_ID], date(2026, 8, 11), date(2026, 8, 11)
    )
    agg = result[SUB_ID]

    assert agg["commission"] == 5.0  # cancelado soma, UNPAID não
    assert agg["orders"] == 0  # cancelado não conta como pedido tampouco


def test_status_desconhecido_tambem_fica_fora_por_seguranca(db):
    """Qualquer status fora da allowlist (não só UNPAID) fica de fora — não é
    uma denylist de 'só UNPAID', é uma allowlist como o frontend já usa."""
    _linha(db, order_id="X1", status="Alguma Coisa Nova Da Shopee", commission=99.0)
    _linha(db, order_id="P1", status="Pendente", commission=1.0)
    db.commit()

    result = CampaignRepository(db).aggregate_by_subids(
        USUARIA, [SUB_ID], date(2026, 8, 11), date(2026, 8, 11)
    )
    assert result[SUB_ID]["commission"] == 1.0
