"""Sorteio ponderado de variações + placeholders (anti-ban, spec §4.10)."""
import random
from types import SimpleNamespace

from app.services.template_mensagem_service import (
    montar_texto, preencher, sortear_variacao,
)


def _v(corpo, peso=1, ativa=True):
    return SimpleNamespace(corpo=corpo, peso=peso, ativa=ativa)


def test_sorteio_respeita_peso():
    variacoes = [_v("A", peso=1), _v("B", peso=99)]
    rng = random.Random(42)
    escolhas = [sortear_variacao(variacoes, rng).corpo for _ in range(100)]
    assert escolhas.count("B") > 80


def test_inativa_e_vazia_nao_entram():
    variacoes = [_v("", peso=9), _v("ok"), _v("off", ativa=False)]
    assert sortear_variacao(variacoes, random.Random(1)).corpo == "ok"
    assert sortear_variacao([]) is None


def test_placeholder_sem_valor_vira_vazio_nunca_vaza_cru():
    corpo = "{produto} por {preco_por} — {link}"
    texto = preencher(corpo, {"link": "https://s.shopee/x"})
    assert "{" not in texto
    assert "https://s.shopee/x" in texto


def test_montar_com_prefixo_e_sufixo():
    texto = montar_texto("corpo {link}", {"link": "L"}, "🔥 Achadinho", "— Maria")
    assert texto.startswith("🔥 Achadinho")
    assert texto.endswith("— Maria")
    assert "corpo L" in texto
