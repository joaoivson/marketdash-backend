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


def test_broker_fora_do_ar_nao_deixa_upload_preso_em_pending(monkeypatch):
    """Bug real (26/08/2026): o hostname do Redis parou de resolver e o upload de
    cliques passou a criar o dataset, falhar ao enfileirar e devolver o arquivo
    para o limbo — 33 uploads de 4 alunas presos em "pending", sem erro nenhum
    na tela. Elas repetiam o upload achando que era o navegador.

    Arquivo pequeno já processava na request; só quem passava de CSV_SYNC_MAX_BYTES
    caía na fila e sumia. Com o broker fora, a rota tem que processar inline em vez
    de fingir que enfileirou.
    """
    import kombu.exceptions
    from fastapi.testclient import TestClient

    from app.api.v1.dependencies import require_active_subscription
    from app.api.v1.routes import clicks as rota
    from app.db.session import get_db
    from app.main import app

    processados = []

    def _fake_inline(dataset_id, user_id, filename, conteudo):
        processados.append((dataset_id, filename, len(conteudo)))

    class _FakeDataset:
        id = 4242
        status = "pending"

    class _FakeDatasetRepo:
        def __init__(self, db):
            pass

        def create(self, dataset):
            return _FakeDataset()

    def _explode(*a, **k):
        raise kombu.exceptions.OperationalError("Error -3 connecting to redis: name resolution")

    monkeypatch.setattr(rota, "_processar_click_csv_inline", _fake_inline)
    monkeypatch.setattr(rota, "DatasetRepository", _FakeDatasetRepo)
    monkeypatch.setattr(rota.process_click_csv_task, "delay", _explode)
    # força o caminho da fila (acima do limite do processamento síncrono)
    monkeypatch.setattr(rota.settings, "CSV_SYNC_MAX_BYTES", 10)
    monkeypatch.setattr(rota.settings, "PROCESS_CSV_SYNC", False)
    monkeypatch.setattr(rota.settings, "UPLOAD_TEMP_DIR", "")

    class _FakeDb:
        def commit(self):
            pass

        def refresh(self, _):
            pass

        def rollback(self):
            pass

    app.dependency_overrides[get_db] = lambda: _FakeDb()
    app.dependency_overrides[require_active_subscription] = lambda: SimpleNamespace(id=7)
    try:
        client = TestClient(app)
        conteudo = b"data,canal,sub_id\n" + b"2026-08-26,Instagram,abc\n" * 50
        resp = client.post(
            "/api/v1/clicks/upload",
            files={"file": ("cliques.csv", conteudo, "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201, resp.text
    assert processados, (
        "broker fora do ar e nada foi processado: o dataset ficaria preso em pending"
    )
    assert processados[0][0] == 4242

