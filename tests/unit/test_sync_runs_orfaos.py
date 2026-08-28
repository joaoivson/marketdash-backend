"""Runs presos em 'running' para sempre.

O ciclo do cron roda como BackgroundTask do processo da API. Um deploy, um
restart ou um OOM mata a task no meio e a linha do `sync_runs` fica 'running'
sem nada nunca fechá-la. Em 28/08/2026 havia 50 delas acumuladas desde 28/07 —
o painel contava todas como "rodando agora".
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.sync_run import SyncRun
from app.repositories.sync_run_repository import (
    STALE_RUNNING_SECONDS,
    STATUS_INTERROMPIDO,
    SyncRunRepository,
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(type_, compiler, **kw):
    return "JSON"


AGORA = datetime.now(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SyncRun.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


_proximo_id = itertools.count(1)


def _run(db, status="running", ha_segundos=0, source="facebook", trigger="cron"):
    # id explícito: a PK é BigInteger e o sqlite só autoincrementa INTEGER.
    run = SyncRun(
        id=next(_proximo_id),
        source=source,
        trigger=trigger,
        status=status,
        started_at=AGORA - timedelta(seconds=ha_segundos),
    )
    db.add(run)
    db.flush()
    return run


class TestFecharOrfaos:
    def test_fecha_run_running_mais_velho_que_o_limiar(self, db):
        velho = _run(db, ha_segundos=STALE_RUNNING_SECONDS + 60)

        assert SyncRunRepository(db).fechar_orfaos() == 1

        db.refresh(velho)
        assert velho.status == STATUS_INTERROMPIDO
        assert velho.finished_at is not None
        assert "interrompido" in velho.error_message.lower()

    def test_nao_fecha_run_que_ainda_pode_estar_vivo(self, db):
        """Ciclo em andamento: o maior sync real levou ~8 min, o limiar é 1h."""
        recente = _run(db, ha_segundos=300)

        assert SyncRunRepository(db).fechar_orfaos() == 0

        db.refresh(recente)
        assert recente.status == "running"
        assert recente.finished_at is None

    def test_nao_toca_run_ja_terminado(self, db):
        """Nem sucesso antigo nem falha antiga podem ser reescritos."""
        sucesso = _run(db, status="success", ha_segundos=STALE_RUNNING_SECONDS * 10)
        falha = _run(db, status="failed", ha_segundos=STALE_RUNNING_SECONDS * 10)

        assert SyncRunRepository(db).fechar_orfaos() == 0

        db.refresh(sucesso)
        db.refresh(falha)
        assert sucesso.status == "success"
        assert falha.status == "failed"

    def test_fecha_orfao_de_qualquer_origem(self, db):
        """Roda no ciclo do Facebook, mas o Shopee sofre do mesmo problema."""
        _run(db, ha_segundos=STALE_RUNNING_SECONDS + 60, source="facebook")
        _run(db, ha_segundos=STALE_RUNNING_SECONDS + 60, source="shopee",
             trigger="cron_incremental")

        assert SyncRunRepository(db).fechar_orfaos() == 2

    def test_e_idempotente(self, db):
        _run(db, ha_segundos=STALE_RUNNING_SECONDS + 60)
        repo = SyncRunRepository(db)

        assert repo.fechar_orfaos() == 1
        assert repo.fechar_orfaos() == 0

    def test_limiar_customizado(self, db):
        _run(db, ha_segundos=120)

        assert SyncRunRepository(db).fechar_orfaos(idade_segundos=60) == 1


class TestLimiarCompartilhado:
    def test_painel_e_limpeza_usam_o_mesmo_numero(self):
        """Duas cópias sairiam de sincronia: o painel marcaria "(travada?)" numa
        faixa e a limpeza fecharia noutra."""
        from app.services import sync_monitoring_service

        assert sync_monitoring_service.STALE_RUNNING_SECONDS == STALE_RUNNING_SECONDS

    def test_interrompido_nao_e_falha(self):
        """`errors_24h` e a aba de erros contam status == 'failed'; um processo
        morto por deploy não é erro de sincronização e não pode entrar lá."""
        assert STATUS_INTERROMPIDO != "failed"
