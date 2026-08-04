"""
Upload de cliques sempre termina num estado terminal.

Bug real (04/08/2026): usuária relatou upload travado na tela "Processando
Inteligência". O `process_click_csv` só gravava `status` quando havia linhas:

    if click_rows:
        dataset.status = "completed"

Arquivo que não sobrevive ao agrupamento (nenhuma data válida, só cabeçalho)
saía do processamento com o dataset ainda em "pending". Nada marcava erro, o
front continuava perguntando "já terminou?" e a tela girava pra sempre.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.click_service import ClickService


class _FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class _FakeDatasetRepo:
    def __init__(self, dataset):
        self.dataset = dataset
        self.db = _FakeDb()

    def get_by_id(self, dataset_id, user_id):
        return self.dataset


class _FakeClickRepo:
    def __init__(self):
        self.criadas = []

    def get_existing_hashes(self, user_id):
        return set()

    def bulk_create(self, rows):
        self.criadas.extend(rows)


def _dataset():
    return SimpleNamespace(id=1, status="pending", row_count=0, error_message=None)


def _servico(dataset):
    return ClickService(_FakeDatasetRepo(dataset), _FakeClickRepo())


def _csv(linhas: str) -> bytes:
    cabecalho = "ID dos Cliques,Tempo dos Cliques,Região dos Cliques,Sub_id,Referenciador\n"
    return (cabecalho + linhas).encode("utf-8")


def test_arquivo_valido_fica_completed():
    d = _dataset()
    conteudo = _csv(
        "abc,2026-07-28 10:00:00,Brazil,CUPOM----,WhatsApp\n"
        "def,2026-07-29 11:00:00,Brazil,CUPOM----,WhatsApp\n"
    )
    _servico(d).process_click_csv(1, 10, conteudo, "cliques.csv")
    assert d.status == "completed"
    assert d.row_count == 2


def test_arquivo_so_com_cabecalho_nao_fica_pending():
    """Arquivo vazio já era barrado na validação — garante que segue assim."""
    d = _dataset()
    _servico(d).process_click_csv(1, 10, _csv(""), "vazio.csv")
    assert d.status != "pending", "dataset preso em pending faz a tela girar pra sempre"
    assert d.status == "error"
    assert d.error_message


def test_todas_as_datas_invalidas_nao_fica_pending():
    """Sem data válida o groupby descarta tudo — tem que virar erro, não pending."""
    d = _dataset()
    conteudo = _csv(
        "abc,data-quebrada,Brazil,CUPOM----,WhatsApp\n"
        "def,outra-coisa,Brazil,CUPOM----,WhatsApp\n"
    )
    _servico(d).process_click_csv(1, 10, conteudo, "datas_ruins.csv")
    assert d.status == "error"
    assert d.status != "pending"


def test_mensagem_de_erro_orienta_o_usuario():
    """Quem chega no agrupamento vazio recebe instrução, não silêncio."""
    d = _dataset()
    conteudo = _csv("abc,data-quebrada,Brazil,CUPOM----,WhatsApp\n")
    _servico(d).process_click_csv(1, 10, conteudo, "datas_ruins.csv")
    assert "Nenhum clique válido" in (d.error_message or "")
    assert "data" in (d.error_message or "").lower()


def test_datas_iso_com_dia_alto_sobrevivem():
    """Regressão do dayfirst: dias 28-31 em ISO não podem sumir."""
    d = _dataset()
    conteudo = _csv(
        "a,2026-07-28 10:00:00,Brazil,X----,WhatsApp\n"
        "b,2026-07-29 10:00:00,Brazil,X----,WhatsApp\n"
        "c,2026-07-30 10:00:00,Brazil,X----,WhatsApp\n"
        "d,2026-07-31 10:00:00,Brazil,X----,WhatsApp\n"
    )
    servico = _servico(d)
    servico.process_click_csv(1, 10, conteudo, "cliques.csv")
    assert d.status == "completed"
    dias = sorted(r.date.day for r in servico.click_repo.criadas)
    assert dias == [28, 29, 30, 31]
    assert all(r.date.month == 7 for r in servico.click_repo.criadas)
