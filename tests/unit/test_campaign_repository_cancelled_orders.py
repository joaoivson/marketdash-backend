"""
CampaignRepository.aggregate_by_subids / daily_by_subid contra um banco de
verdade (SQLite em memória), cobrindo a exclusão de pedidos cancelados da
contagem de "Pedidos" — a comissão da linha continua somando (a venda
existiu), só o pedido não entra no set. Mesma regra de app/services/kpi_service.py.

Caso real que motivou o fix: Sub ID `clareadorretinal12060826`, 06/08, com 8
pedidos, todos cancelados — o Dashboard já mostrava 0 pedidos, Campanhas
mostrava 8 (bug).
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.dataset_row import DatasetRow
from app.repositories.campaign_repository import CampaignRepository
from app.utils.shopee_normalize import DIRECT_ATTRIBUTION

USUARIA = 1


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    DatasetRow.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _linha(db, **campos):
    padrao = dict(
        dataset_id=1, user_id=USUARIA, date=date(2026, 8, 6), product="p",
        status="concluído", revenue=0, commission=0, sub_id1="clareadorretinal12060826",
    )
    padrao.update(campos)
    db.add(DatasetRow(**padrao))


def test_pedido_totalmente_cancelado_nao_conta_mas_soma_comissao(db):
    _linha(db, order_id="CANC", status="cancelado", revenue=50, commission=5)
    _linha(db, order_id="OK", status="concluído", revenue=20, commission=2)
    db.commit()

    result = CampaignRepository(db).aggregate_by_subids(
        USUARIA, ["clareadorretinal12060826"], date(2026, 8, 1), date(2026, 8, 31)
    )
    agg = result["clareadorretinal12060826"]
    assert agg["orders"] == 1
    assert agg["commission"] == 7.0


def test_pedido_com_item_cancelado_e_item_valido_ainda_conta(db):
    _linha(db, order_id="MISTO", status="cancelado", commission=5)
    _linha(db, order_id="MISTO", status="concluído", commission=2)
    db.commit()

    result = CampaignRepository(db).aggregate_by_subids(
        USUARIA, ["clareadorretinal12060826"], date(2026, 8, 1), date(2026, 8, 31)
    )
    assert result["clareadorretinal12060826"]["orders"] == 1


def test_dia_com_todos_pedidos_cancelados_mostra_zero_pedidos_mas_soma_comissao(db):
    # Caso real: 06/08, 8 pedidos, todos cancelados.
    for i in range(8):
        _linha(db, order_id=f"P{i}", status="cancelado", commission=10, revenue=100)
    db.commit()

    daily = CampaignRepository(db).daily_by_subid(
        USUARIA, "clareadorretinal12060826", date(2026, 8, 1), date(2026, 8, 31)
    )
    day = daily[date(2026, 8, 6)]
    assert day["orders"] == 0
    assert day["commission"] == 80.0


def test_direct_orders_tambem_exclui_cancelado(db):
    _linha(db, order_id="D_CANC", status="cancelado", attribution_type=DIRECT_ATTRIBUTION, commission=5)
    _linha(db, order_id="D_OK", status="concluído", attribution_type=DIRECT_ATTRIBUTION, commission=2)
    _linha(db, order_id="I_OK", status="concluído", attribution_type="outro", commission=1)
    db.commit()

    result = CampaignRepository(db).aggregate_by_subids(
        USUARIA, ["clareadorretinal12060826"], date(2026, 8, 1), date(2026, 8, 31)
    )
    agg = result["clareadorretinal12060826"]
    assert agg["orders"] == 2
    assert agg["direct_orders"] == 1
