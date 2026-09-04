"""
`/datasets/all/rows` corta por data no BANCO — e a consulta por coluna serializa
igual à por entidade.

Contexto (04/09/2026): o dashboard pedia a base inteira e filtrava no cliente. Na
conta do Luiz são 67.631 linhas (~30 MB de JSON) para exibir as ~3.900 dos
últimos 7 dias — 2.018 ms só de banco, contra 14 ms com o filtro de data (mesmo
índice `idx_dataset_rows_v2_user_date`). O front passou a mandar o período; estes
testes travam o comportamento do lado do backend:

1. o filtro de data realmente recorta (senão o front economiza no papel e o banco
   continua lendo tudo);
2. `list_by_user` agora consulta COLUNAS, não a entidade ORM — `serialize_row`
   precisa continuar produzindo exatamente o mesmo dicionário.
"""
from datetime import date, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.dataset_row import DatasetRow
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.services.dataset_service import DatasetService

USUARIA = 1
OUTRA = 2


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    DatasetRow.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _linha(db, dia: date, user_id: int = USUARIA, **campos):
    padrao = dict(
        dataset_id=1,
        user_id=user_id,
        date=dia,
        time=time(10, 30, 0),
        product="Produto",
        platform="Shopee",
        category="Casa",
        status="Concluído",
        channel="Instagram",
        attribution_type="ORDERED_IN_SAME_SHOP",
        sub_id1="sub1",
        order_id=f"ord-{dia.isoformat()}-{user_id}",
        product_id="p1",
        revenue=100.0,
        commission=10.0,
        cost=0.0,
        profit=10.0,
        quantity=1,
        row_hash=f"h-{dia.isoformat()}-{user_id}-{campos.get('order_id', '')}",
    )
    padrao.update(campos)
    linha = DatasetRow(**padrao)
    db.add(linha)
    db.commit()
    return linha


def _service(db):
    return DatasetService(None, DatasetRowRepository(db))


def test_periodo_recorta_no_banco(db):
    _linha(db, date(2026, 9, 3))
    _linha(db, date(2026, 9, 1))
    _linha(db, date(2026, 6, 15))
    _linha(db, date(2026, 2, 2))

    todas = _service(db).list_all_rows(USUARIA, None, None, None, 0)
    janela = _service(db).list_all_rows(USUARIA, date(2026, 9, 1), date(2026, 9, 3), None, 0)

    assert len(todas) == 4
    assert len(janela) == 2
    assert {r["date"] for r in janela} == {date(2026, 9, 1), date(2026, 9, 3)}


def test_periodo_nao_vaza_linha_de_outra_usuaria(db):
    _linha(db, date(2026, 9, 2), user_id=USUARIA)
    _linha(db, date(2026, 9, 2), user_id=OUTRA)

    linhas = _service(db).list_all_rows(USUARIA, date(2026, 9, 1), date(2026, 9, 3), None, 0)

    assert len(linhas) == 1
    assert linhas[0]["user_id"] == USUARIA


def test_consulta_por_coluna_serializa_todos_os_campos(db):
    _linha(db, date(2026, 9, 3))

    (linha,) = _service(db).list_all_rows(USUARIA, None, None, None, 0)

    assert set(linha) == {
        "id", "dataset_id", "user_id", "date", "time", "product", "platform",
        "category", "status", "channel", "attribution_type", "sub_id1",
        "order_id", "product_id", "revenue", "commission", "cost", "profit",
        "quantity",
    }
    assert linha["date"] == date(2026, 9, 3)
    assert linha["time"] == time(10, 30, 0)
    assert linha["revenue"] == 100.0
    assert linha["commission"] == 10.0
    assert linha["attribution_type"] == "ORDERED_IN_SAME_SHOP"
    assert linha["quantity"] == 1


def test_data_inicial_maior_que_final_e_erro(db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as erro:
        _service(db).list_all_rows(USUARIA, date(2026, 9, 3), date(2026, 9, 1), None, 0)

    assert erro.value.status_code == 400
