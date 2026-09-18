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
         patch.object(base, "_apply_safe_migrations") as ddl, \
         patch("app.core.config.settings.DB_SCHEMA_NO_STARTUP", False):
        base.init_db()
    conn.execute.assert_called_once()
    assert "SELECT 1" in str(conn.execute.call_args.args[0])
    create_all.assert_not_called()
    ddl.assert_not_called(), "nenhum ALTER TABLE no boot de produção"


def test_flag_liga_schema_no_startup_para_dev_e_hml():
    """HML depende da rede de proteção do módulo de Grupos (código costuma
    chegar antes da migration). Produção não liga a flag."""
    eng, _ = _engine()
    with patch("app.db.session.engine", eng), \
         patch.object(base.Base.metadata, "create_all") as create_all, \
         patch.object(base, "_apply_safe_migrations") as ddl, \
         patch.object(base, "_importar_modelos"), \
         patch("app.core.config.settings.DB_SCHEMA_NO_STARTUP", True):
        base.init_db()
    create_all.assert_called_once_with(bind=eng)
    ddl.assert_called_once()


def test_nenhum_alter_column_type_sem_guarda():
    """`ALTER COLUMN ... TYPE` pega ACCESS EXCLUSIVE na tabela inteira MESMO
    quando o tipo já é o desejado — e com DB_SCHEMA_NO_STARTUP ligado em HML a
    lista roda a cada boot. Todo statement desse tipo precisa vir embrulhado num
    DO/IF que consulte o information_schema antes.

    Os `ADD COLUMN IF NOT EXISTS` não precisam: o próprio Postgres já sai cedo.
    """
    import inspect
    import re

    fonte = inspect.getsource(base._apply_safe_migrations)
    # Statements "crus": uma string que começa com ALTER TABLE e contém
    # ALTER COLUMN ... TYPE, sem estar dentro de um bloco DO.
    crus = [
        linha.strip()
        for linha in fonte.splitlines()
        if re.search(r'^\s*"ALTER TABLE .*ALTER COLUMN .*\bTYPE\b', linha)
    ]
    assert not crus, (
        "ALTER COLUMN ... TYPE sem guarda de information_schema: "
        f"{crus}. Pega lock exclusivo a cada boot de HML."
    )
