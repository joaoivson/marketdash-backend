"""
KpiService contra um banco de verdade.

Os outros testes da feature usam fakes, o que prova a orquestração mas não a
consulta. Aqui o SQL roda: GROUP BY, COUNT(DISTINCT), filtro de status e o
recorte por data são executados, não simulados. É o que pega o erro que
motivou o serviço — `COUNT(*)` contando item em vez de pedido inflava 49%.

Roda em SQLite em memória: as três tabelas envolvidas (`dataset_rows_v2`,
`ad_spends`, `user_settings`) só têm tipos portáveis, sem JSONB. Não substitui
um teste contra Postgres para questões de dialeto — cobre a lógica da query.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ad_spend import AdSpend
from app.models.dataset_row import DatasetRow
from app.models.user_settings import UserSettings
from app.services.kpi_service import KpiService, normalizar_sub_id
from app.utils.shopee_normalize import DIRECT_ATTRIBUTION

INICIO, FIM = date(2026, 8, 1), date(2026, 8, 7)
USUARIA = 1
OUTRA = 2


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for modelo in (DatasetRow, AdSpend, UserSettings):
        modelo.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _linha(db, **campos):
    padrao = dict(
        dataset_id=1, user_id=USUARIA, date=date(2026, 8, 3), product="p",
        status="concluído", revenue=0, commission=0,
    )
    padrao.update(campos)
    db.add(DatasetRow(**padrao))


def _gasto(db, valor, dia=date(2026, 8, 3), user_id=USUARIA):
    db.add(AdSpend(user_id=user_id, date=dia, amount=valor, source="manual"))


def _impostos(db, anuncio, comissao, user_id=USUARIA):
    db.add(UserSettings(user_id=user_id, ad_tax_rate=anuncio, commission_tax_rate=comissao))


# --- contagem de pedidos ----------------------------------------------------

def test_pedido_com_varios_itens_conta_uma_vez(db):
    # A Shopee grava uma linha por item: 3 linhas, 1 pedido.
    for _ in range(3):
        _linha(db, order_id="ABC", revenue=10, commission=1)
    db.commit()

    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert k.pedidos == 1
    assert k.faturamento == 30.0  # a receita continua somando item a item
    assert k.comissao_bruta == 3.0


def test_pedido_totalmente_cancelado_nao_conta_mas_soma_comissao(db):
    _linha(db, order_id="CANC", status="cancelado", revenue=50, commission=5)
    _linha(db, order_id="OK", status="concluído", revenue=20, commission=2)
    db.commit()

    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert k.pedidos == 1
    assert k.comissao_bruta == 7.0


def test_pedido_com_item_cancelado_e_item_valido_ainda_conta(db):
    _linha(db, order_id="MISTO", status="cancelado", commission=5)
    _linha(db, order_id="MISTO", status="concluído", commission=2)
    db.commit()

    assert KpiService(db).kpis(USUARIA, INICIO, FIM).pedidos == 1


def test_status_fora_da_lista_fica_de_fora(db):
    _linha(db, order_id="X", status="devolvido", revenue=100, commission=10)
    _linha(db, order_id="Y", status="pendente", revenue=10, commission=1)
    db.commit()

    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert k.pedidos == 1
    assert k.faturamento == 10.0


def test_pedidos_diretos_saem_do_attribution_type(db):
    _linha(db, order_id="D", attribution_type=DIRECT_ATTRIBUTION, commission=1)
    _linha(db, order_id="I", attribution_type="outro", commission=1)
    db.commit()

    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert (k.pedidos, k.pedidos_diretos) == (2, 1)


# --- recortes ---------------------------------------------------------------

def test_periodo_e_usuario_recortam_tudo(db):
    _linha(db, order_id="DENTRO", commission=10)
    _linha(db, order_id="ANTES", date=date(2026, 7, 31), commission=99)
    _linha(db, order_id="DEPOIS", date=date(2026, 8, 8), commission=99)
    _linha(db, order_id="ALHEIO", user_id=OUTRA, commission=99)
    _gasto(db, 100.0)
    _gasto(db, 500.0, dia=date(2026, 7, 31))
    _gasto(db, 700.0, user_id=OUTRA)
    db.commit()

    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert k.comissao_bruta == 10.0
    assert k.pedidos == 1
    assert k.gasto_pago == 100.0


# --- impostos e derivados ---------------------------------------------------

def test_impostos_saem_das_configuracoes_da_usuaria(db):
    _impostos(db, anuncio=10.0, comissao=20.0)
    _linha(db, order_id="A", commission=1000)
    _gasto(db, 200.0)
    db.commit()

    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert k.gasto_pago == 200.0
    assert k.gasto_com_imposto == 220.0        # 200 × 1,10
    assert k.comissao_liquida == 800.0         # 1000 × 0,80
    assert k.lucro == 580.0                    # 800 − 220
    assert k.roas == 3.64                      # 800 ÷ 220


def test_sem_configuracao_as_taxas_sao_zero(db):
    _linha(db, order_id="A", commission=100)
    _gasto(db, 50.0)
    db.commit()

    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert (k.comissao_liquida, k.gasto_com_imposto, k.lucro) == (100.0, 50.0, 50.0)


def test_sem_gasto_o_roas_e_zero_e_nao_estoura(db):
    # Divisão por zero aqui viraria 500 na tela de quem não anuncia.
    _linha(db, order_id="A", commission=100)
    db.commit()

    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert k.roas == 0.0
    assert k.lucro == 100.0


def test_periodo_sem_nada_devolve_zeros(db):
    k = KpiService(db).kpis(USUARIA, INICIO, FIM)
    assert (k.faturamento, k.comissao_liquida, k.pedidos, k.roas) == (0.0, 0.0, 0, 0.0)


# --- tops -------------------------------------------------------------------

def test_tops_agrupam_por_comissao_liquida_e_pedido_distinto(db):
    _impostos(db, anuncio=0.0, comissao=50.0)
    _linha(db, order_id="1", channel="Instagram", category="Beleza", commission=100)
    _linha(db, order_id="1", channel="Instagram", category="Beleza", commission=100)
    _linha(db, order_id="2", channel="Instagram", category="Beleza", commission=40)
    _linha(db, order_id="3", channel="TikTok", category="Casa", commission=60)
    db.commit()

    tops = KpiService(db).tops(USUARIA, INICIO, FIM)
    canal = {c["nome"]: c for c in tops["canal"]}
    assert canal["Instagram"]["comissao"] == 120.0   # (100+100+40) × 0,50
    assert canal["Instagram"]["pedidos"] == 2        # 3 linhas, 2 pedidos
    assert tops["canal"][0]["nome"] == "Instagram"   # ordenado por comissão
    assert {c["nome"] for c in tops["categoria"]} == {"Beleza", "Casa"}


def test_tops_respeitam_o_limite(db):
    for i in range(8):
        _linha(db, order_id=str(i), channel=f"canal{i}", commission=10 * (i + 1))
    db.commit()

    tops = KpiService(db).tops(USUARIA, INICIO, FIM, limite=3)
    assert [c["nome"] for c in tops["canal"]] == ["canal7", "canal6", "canal5"]


def test_sub_ids_equivalentes_viram_uma_linha_so(db):
    # Sem normalizar, "SUTIA-" e "sutia" apareceriam separados no mesmo top.
    _linha(db, order_id="1", sub_id1="SUTIA-", commission=10)
    _linha(db, order_id="2", sub_id1="sutia", commission=10)
    _linha(db, order_id="3", sub_id1=None, commission=5)
    _linha(db, order_id="4", sub_id1="  ", commission=5)
    db.commit()

    sub = {s["nome"]: s for s in KpiService(db).tops(USUARIA, INICIO, FIM)["sub_id"]}
    assert sub["sutia"]["comissao"] == 20.0
    assert sub["sutia"]["pedidos"] == 2
    assert sub["Sem Sub ID"]["comissao"] == 10.0


@pytest.mark.parametrize("entrada,esperado", [
    ("SUTIA", "sutia"),
    ("  Blusa--  ", "blusa"),
    ("", "Sem Sub ID"),
    (None, "Sem Sub ID"),
    ("NaN", "Sem Sub ID"),
    ("null", "Sem Sub ID"),
    ("---", "Sem Sub ID"),
])
def test_normalizar_sub_id(entrada, esperado):
    assert normalizar_sub_id(entrada) == esperado
