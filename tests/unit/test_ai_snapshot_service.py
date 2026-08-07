"""
Snapshot: o retrato dos números que vai pro prompt.

Precisa refletir EXATAMENTE a classificação do campaign_service — se o snapshot
divergir do dashboard, a IA vai narrar um número que a aluna não vê na tela.
"""
from datetime import date
from types import SimpleNamespace

from app.services.ai_snapshot_service import AiSnapshotService


def _campanha(nome, health, roas, spend, commission_net, profit, orders=0):
    return SimpleNamespace(
        name=nome, health=health, linked=True, is_active=True,
        fb_campaign_id="1", sub_id="sub",
        metrics=SimpleNamespace(
            roas=roas, spend=spend, spend_with_tax=spend, clicks=10, impressions=100,
            commission=commission_net, commission_net=commission_net, revenue=0.0,
            orders=orders, direct_orders=0, profit=profit, cpc=None, ctr=None, reach=0,
        ),
    )


def _servico(campanhas=None, kpis=None, tops=None, tem_meta=True):
    svc = AiSnapshotService(db=None)
    svc._campanhas_do_periodo = lambda u, i, f: (campanhas or [])
    svc._kpis_do_periodo = lambda u, i, f: (kpis or {
        "comissao_liquida": 1000.0, "comissao_bruta": 1063.0, "receita": 5000.0,
        "gasto_com_imposto": 400.0, "roas_real": 2.5, "pedidos_diretos": 5,
        "lucro": 600.0, "pedidos": 20,
    })
    svc._tops = lambda u, i, f: (tops or {"canal": [], "categoria": [], "sub_id": []})
    svc._tem_meta = lambda u: tem_meta
    return svc


def test_snapshot_tem_as_secoes_esperadas():
    s = _servico().montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert set(s) >= {"periodo", "kpis", "tops", "campanhas", "tem_meta", "vazio"}


def test_periodo_vem_no_snapshot():
    s = _servico().montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["periodo"] == {"inicio": "2026-08-01", "fim": "2026-08-05"}


def test_sem_meta_nao_cria_bloco_de_campanha():
    s = _servico(tem_meta=False).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["tem_meta"] is False
    assert s["campanhas"] == []


def test_campanhas_preservam_a_classificacao_do_backend():
    campanhas = [
        _campanha("escala", "healthy", 2.4, 100.0, 240.0, 140.0),
        _campanha("perde", "loss", 0.4, 100.0, 40.0, -60.0),
        _campanha("limite", "warning", 1.1, 100.0, 110.0, 10.0),
    ]
    s = _servico(campanhas).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    por_nome = {c["nome"]: c for c in s["campanhas"]}
    assert por_nome["escala"]["classificacao"] == "healthy"
    assert por_nome["perde"]["classificacao"] == "loss"
    assert por_nome["limite"]["classificacao"] == "warning"
    assert por_nome["perde"]["roas"] == 0.4
    assert por_nome["perde"]["lucro"] == -60.0


def test_periodo_sem_dado_marca_vazio():
    s = _servico(kpis={"comissao_liquida": 0.0, "comissao_bruta": 0.0, "receita": 0.0,
                       "gasto_com_imposto": 0.0, "roas_real": 0.0, "pedidos_diretos": 0,
                       "lucro": 0.0, "pedidos": 0}).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["vazio"] is True


def test_periodo_com_dado_nao_marca_vazio():
    s = _servico().montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["vazio"] is False


def test_gasto_de_anuncio_sem_venda_nao_marca_vazio():
    # Caso do defeito: gastou em anúncio e não vendeu nada, sem campanha
    # sincronizada. Antes da correção, isso caía como "vazio" e a análise
    # nem era gerada — exatamente o período mais acionável (prejuízo puro).
    s = _servico(
        kpis={"comissao_liquida": 0.0, "comissao_bruta": 0.0, "receita": 0.0,
                       "gasto_com_imposto": 400.0, "roas_real": 0.0, "pedidos_diretos": 0,
              "lucro": 0.0, "pedidos": 0},
        tem_meta=False,
    ).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["vazio"] is False


def test_gasto_de_anuncio_aparece_no_snapshot():
    # Não basta usar o gasto para decidir a flag: a IA precisa do número para
    # poder narrar o prejuízo. Fica em kpis["gasto_com_imposto"], separado de
    # kpis["gasto"] (que vem do rateio de DatasetRow.cost e não existe sem
    # venda para ratear).
    s = _servico(
        kpis={"comissao_liquida": 0.0, "comissao_bruta": 0.0, "receita": 0.0,
                       "gasto_com_imposto": 400.0, "roas_real": 0.0, "pedidos_diretos": 0,
              "lucro": 0.0, "pedidos": 0},
        tem_meta=False,
    ).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["kpis"]["gasto_com_imposto"] == 400.0


def test_sem_pedido_sem_campanha_e_sem_gasto_de_anuncio_marca_vazio():
    # Confirma que a nova condição não afrouxou o caso realmente vazio.
    s = _servico(
        kpis={"comissao_liquida": 0.0, "comissao_bruta": 0.0, "receita": 0.0,
                       "gasto_com_imposto": 0.0, "roas_real": 0.0, "pedidos_diretos": 0,
              "lucro": 0.0, "pedidos": 0},
        tem_meta=False,
    ).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["vazio"] is True


def test_snapshot_e_serializavel_em_json():
    import json
    s = _servico([_campanha("x", "healthy", 2.0, 10.0, 20.0, 10.0)]).montar(
        1, date(2026, 8, 1), date(2026, 8, 5))
    assert json.loads(json.dumps(s))["periodo"]["inicio"] == "2026-08-01"
