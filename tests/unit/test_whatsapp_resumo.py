"""
Texto do resumo, com os números vindo de SQL de verdade.

O que se prova aqui é que a mensagem no celular bate com a tela: mesmos
impostos, mesma contagem de pedido distinto, mesmo ROAS. Um resumo que
contradiz o dashboard destrói a confiança no produto inteiro.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ad_spend import AdSpend
from app.models.dataset_row import DatasetRow
from app.models.user_settings import UserSettings
from app.services.whatsapp_resumo_service import (
    WhatsappResumoService, _reais, dia_do_resumo,
)

DIA = date(2026, 8, 6)
USUARIA = 1


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for modelo in (DatasetRow, AdSpend, UserSettings):
        modelo.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _linha(db, order_id, commission, revenue=0, status="concluído"):
    db.add(DatasetRow(dataset_id=1, user_id=USUARIA, date=DIA, product="p",
                      status=status, order_id=order_id,
                      revenue=revenue, commission=commission))


def _sem_campanhas(svc):
    # Campanha exige o repositório real; o alerta tem teste próprio abaixo.
    svc._campanhas_em_prejuizo = lambda user_id, dia: []
    return svc


def test_dia_do_resumo_e_sempre_ontem():
    assert dia_do_resumo(date(2026, 8, 7)) == date(2026, 8, 6)
    assert dia_do_resumo(date(2026, 1, 1)) == date(2025, 12, 31)


def test_valor_sai_no_padrao_brasileiro():
    assert _reais(3658.9) == "R$ 3.658,90"
    assert _reais(0) == "R$ 0,00"
    assert _reais(1234567.05) == "R$ 1.234.567,05"


def test_dia_sem_nada_e_uma_linha_so(db):
    r = _sem_campanhas(WhatsappResumoService(db)).montar(USUARIA, "Maria", DIA)
    assert r.tem_movimento is False
    assert "não houve venda nem gasto" in r.texto
    assert "R$ 0,00" not in r.texto     # tabela de zeros todo dia gera SAIR
    assert "Maria" in r.texto


def test_venda_sem_anuncio_nao_fala_de_roas(db):
    _linha(db, "A", commission=100, revenue=500)
    db.commit()

    r = _sem_campanhas(WhatsappResumoService(db)).montar(USUARIA, "Maria", DIA)
    assert "R$ 100,00" in r.texto
    assert "Sem gasto de anúncio no dia." in r.texto
    assert "ROAS" not in r.texto        # ROAS 0 sem investir não é informação
    assert "Lucro" not in r.texto


def test_com_anuncio_mostra_lucro_e_roas_com_imposto(db):
    db.add(UserSettings(user_id=USUARIA, ad_tax_rate=10.0, commission_tax_rate=20.0))
    _linha(db, "A", commission=1000, revenue=5000)
    db.add(AdSpend(user_id=USUARIA, date=DIA, amount=200.0, source="manual"))
    db.commit()

    r = _sem_campanhas(WhatsappResumoService(db)).montar(USUARIA, "Maria", DIA)
    assert "R$ 800,00" in r.texto       # comissão líquida: 1000 × 0,80
    assert "R$ 220,00" in r.texto       # gasto com imposto: 200 × 1,10
    assert "R$ 580,00" in r.texto       # lucro
    assert "3.64" in r.texto            # ROAS
    assert "⚠️" not in r.texto          # acima do breakeven


def test_roas_abaixo_do_breakeven_ganha_marca(db):
    _linha(db, "A", commission=50)
    db.add(AdSpend(user_id=USUARIA, date=DIA, amount=100.0, source="manual"))
    db.commit()

    r = _sem_campanhas(WhatsappResumoService(db)).montar(USUARIA, "Maria", DIA)
    assert "⚠️" in r.texto


def test_pedido_com_varios_itens_conta_uma_vez(db):
    # A Shopee grava uma linha por item: contar linha infla ~49%.
    for _ in range(4):
        _linha(db, "MESMO", commission=10)
    db.commit()

    r = _sem_campanhas(WhatsappResumoService(db)).montar(USUARIA, "Maria", DIA)
    assert "Pedidos: *1*" in r.texto


def test_toda_mensagem_ensina_a_sair(db):
    _linha(db, "A", commission=10)
    db.commit()
    svc = _sem_campanhas(WhatsappResumoService(db))

    for texto in (svc.montar(USUARIA, "Maria", DIA).texto,
                  svc.montar(99, "Ana", DIA).texto):     # 99 = dia vazio
        assert "SAIR" in texto


def test_alerta_lista_no_maximo_tres_campanhas(db):
    from types import SimpleNamespace
    svc = WhatsappResumoService(db)
    svc._campanhas_em_prejuizo = lambda uid, dia: ["c1 (ROAS 0.40)", "c2 (ROAS 0.50)",
                                                   "c3 (ROAS 0.60)"]
    _linha(db, "A", commission=10)
    db.commit()

    texto = svc.montar(USUARIA, "Maria", DIA).texto
    assert "Abaixo do ponto de equilíbrio" in texto
    assert texto.count("• ") == 3
