"""
Isolamento da fila do Celery entre ambientes.

Homologação e produção compartilham a mesma instância de Redis no mesmo índice.
Com a fila default, os dois workers consumiam do mesmo lugar e metade das tasks
ia parar no worker do banco errado, que retornava em silêncio — upload preso em
"pending" pra sempre, sem nunca virar erro.

A fila passa a ser derivada do banco: dois bancos diferentes, duas filas.
"""
import pytest

from app.tasks.celery_app import _fila_do_banco


def _fila(url, monkeypatch):
    monkeypatch.setattr("app.tasks.celery_app.settings.DATABASE_URL", url, raising=False)
    return _fila_do_banco()


PROD = "postgresql://postgres:senha@db.iprdyorxqdiivthtcvxf.supabase.co:5432/postgres"
HML = "postgresql://postgres.ytjpdvjuxtvxacredekk:senha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres"


def test_prod_e_hml_caem_em_filas_diferentes(monkeypatch):
    """O bug: mesma fila para bancos diferentes."""
    assert _fila(PROD, monkeypatch) != _fila(HML, monkeypatch)


def test_fila_identifica_o_projeto_supabase(monkeypatch):
    assert "iprdyorxqdiivthtcvxf" in _fila(PROD, monkeypatch)
    assert "ytjpdvjuxtvxacredekk" in _fila(HML, monkeypatch)


def test_conexao_direta_e_pooler_do_mesmo_banco_dao_a_mesma_fila(monkeypatch):
    """A API usa conexão direta e o worker usa pooler — não podem divergir."""
    direta = "postgresql://postgres:s@db.ytjpdvjuxtvxacredekk.supabase.co:5432/postgres"
    assert _fila(direta, monkeypatch) == _fila(HML, monkeypatch)


def test_mesmo_banco_e_estavel_entre_chamadas(monkeypatch):
    assert _fila(PROD, monkeypatch) == _fila(PROD, monkeypatch)


def test_url_fora_do_padrao_nao_quebra(monkeypatch):
    fila = _fila("postgresql://user:pw@meu-postgres-local:5432/marketdash", monkeypatch)
    assert fila.startswith("marketdash-")
    assert len(fila) > len("marketdash-")


def test_url_vazia_nao_quebra(monkeypatch):
    assert _fila("", monkeypatch).startswith("marketdash-")


def test_bancos_locais_distintos_nao_colidem(monkeypatch):
    a = _fila("postgresql://u:p@localhost:5432/banco_a", monkeypatch)
    b = _fila("postgresql://u:p@localhost:5432/banco_b", monkeypatch)
    assert a != b
