#!/usr/bin/env python3
"""
Script para aplicar migrations SQL manualmente no Supabase.

Executa migrations na ordem e registra sucesso/erro.
"""
import os
import sys
import logging
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
load_dotenv()

# Conectar ao Supabase
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL não configurado em .env")
    sys.exit(1)

engine = create_engine(DATABASE_URL)

# Migrations em ordem
# 029 virou no-op (incidente 20/07 — ver comentário no arquivo).
# 030 (sync horário seguro) fica FORA da lista: aplicar manualmente só depois
# do deploy do fix de retry no backend do Vault (api.hml).
MIGRATIONS = [
    ("027_shopee_sync_full_and_incremental.sql", "Shopee sync dividido (full + incremental)"),
    ("028_trigger_shopee_sync_parametrized.sql", "Parametrizar trigger_shopee_sync"),
    ("034_plan_tiers_and_demo.sql", "Planos Essencial/Pro + is_demo + kiwify_plan_products"),
    ("035_admin_panel.sql", "Painel admin: events, logins, expenses, DRE support"),
]

def apply_migrations():
    """Aplica todas as migrations em ordem."""
    migrations_dir = Path(__file__).parent.parent / "migrations"

    for migration_file, description in MIGRATIONS:
        migration_path = migrations_dir / migration_file

        if not migration_path.exists():
            logger.warning(f"❌ {migration_file} não encontrado")
            continue

        try:
            with open(migration_path, "r", encoding="utf-8") as f:
                sql = f.read()

            logger.info(f"📋 Aplicando: {migration_file}")
            logger.info(f"   Descrição: {description}")

            with engine.begin() as conn:
                # Executar SQL completo sem dividir (permite PL/pgSQL blocks com DO $$...$$)
                # SQLAlchemy text() preserva blocos dollar-quoted
                if sql.strip():
                    conn.execute(text(sql))

            logger.info(f"✅ {migration_file} aplicado com sucesso")

        except Exception as e:
            logger.error(f"❌ Erro ao aplicar {migration_file}: {e}")
            return False

    logger.info("✅ Todas as migrations foram aplicadas com sucesso!")
    return True

if __name__ == "__main__":
    success = apply_migrations()
    sys.exit(0 if success else 1)
