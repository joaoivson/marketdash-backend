"""Startup não roda DDL em produção (app/db/base.py).
Run: pytest tests/unit/test_init_db_sem_ddl.py -v
"""
from unittest.mock import MagicMock, patch

from app.db import base


def _engine():
    eng = MagicMock()
    conn = MagicMock()
    eng.connect.return_value.__enter__.return_value = conn
    return eng, conn


def test_startup_padrao_so_testa_conexao():
    eng, conn = _engine()
    with patch("app.db.session.engine", eng), \
         patch.object(base.Base.metadata, "create_all") as create_all, \
         patch("app.core.config.settings.DB_SCHEMA_NO_STARTUP", False):
        base.init_db()
    conn.execute.assert_called_once()
    assert "SELECT 1" in str(conn.execute.call_args.args[0])
    create_all.assert_not_called()
    assert not hasattr(base, "_apply_safe_migrations"), "ALTER TABLE no startup foi removido"


def test_flag_liga_create_all_para_dev():
    eng, _ = _engine()
    with patch("app.db.session.engine", eng), \
         patch.object(base.Base.metadata, "create_all") as create_all, \
         patch.object(base, "_importar_modelos"), \
         patch("app.core.config.settings.DB_SCHEMA_NO_STARTUP", True):
        base.init_db()
    create_all.assert_called_once_with(bind=eng)
