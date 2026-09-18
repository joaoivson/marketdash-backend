from sqlalchemy.orm import declarative_base

# Create declarative base
Base = declarative_base()

# Models are imported in app/models/__init__.py to avoid circular imports


def _apply_safe_migrations(engine, logger):
    """Rede de proteção de schema para DEV/HML — NUNCA para produção.

    Cada statement é idempotente no resultado, mas NÃO é de graça: todo
    `ALTER TABLE` pega ACCESS EXCLUSIVE na tabela mesmo quando não há nada a
    mudar, e `ALTER COLUMN ... TYPE` pode reescrever a tabela. No incidente de
    18/09/2026 isso rodou 20x em 24h num banco já sufocado (a API reiniciava em
    loop) e ajudou a derrubar o Auth junto.

    Continua existindo porque o módulo de Grupos depende dela em HML: lá o
    código costuma chegar antes da migration, e sem a coluna QUALQUER query da
    tabela quebra. Em produção a ordem é a inversa — migration primeiro — então
    o chamador só executa isto com `DB_SCHEMA_NO_STARTUP=true`."""
    migrations = [
        "ALTER TABLE capture_sites ADD COLUMN IF NOT EXISTS facebook_pixel_id VARCHAR",
        "ALTER TABLE facebook_integrations ADD COLUMN IF NOT EXISTS ad_accounts_json TEXT",
        # 075 (Facebook §4.2): nomes das contas selecionadas — a tabela já existe
        # em todo ambiente e `create_all` NÃO adiciona coluna, mesmo padrão do
        # ad_accounts_json acima.
        "ALTER TABLE facebook_integrations ADD COLUMN IF NOT EXISTS ad_accounts_names_json TEXT",
        # 060 (grupos F3): sem esta coluna, TODA query de UserSettings quebra
        # se o código chegar antes da migration — rede extra do protocolo.
        "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS whatsapp_envio_config JSONB",
        # 065 (grupos F7): Leads do Meta por dia.
        "ALTER TABLE campaign_daily_insights ADD COLUMN IF NOT EXISTS leads INTEGER",
        # 068 (proxy por sessão): `whatsapp_instancias` já existe em todo
        # ambiente, e `create_all` NÃO adiciona coluna a tabela existente —
        # sem isto, qualquer query de instância quebra com UndefinedColumn
        # se o deploy chegar antes da migration.
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_id INTEGER",
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_fixado_em TIMESTAMPTZ",
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS "
        "proxy_trocas INTEGER NOT NULL DEFAULT 0",
        # 074 (grupos, toggle Ativo): `ativado` é a escolha da USUÁRIA — eixo
        # separado de `ativo`, que é lifecycle do sync (todo sync revive com
        # ativo=True). A tabela já existe em todo ambiente e `create_all` NÃO
        # adiciona coluna — sem isto, GET /grupos quebra antes da migration.
        "ALTER TABLE whatsapp_grupos ADD COLUMN IF NOT EXISTS "
        "ativado BOOLEAN NOT NULL DEFAULT FALSE",
        # 076 (assinatura 10.2): compra de tier menor com maior vigente fica
        # pendente em vez de rebaixar na hora. `subscriptions` existe em todo
        # ambiente e `create_all` NÃO adiciona coluna — sem isto, qualquer
        # leitura de Subscription quebra se o deploy chegar antes da migration.
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_plan VARCHAR",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_periodo VARCHAR(32)",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_vence_em TIMESTAMPTZ",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS "
        "pending_provider_transaction_id VARCHAR",
        # 079 (campanhas de grupos): as duas tabelas já existem em hml, e
        # `create_all` NÃO adiciona coluna — sem isto, qualquer leitura de
        # Campanha ou GrupoEvento quebra se o deploy chegar antes da migration.
        # (`campanha_numeros` é tabela NOVA: essa o create_all cria — e é por
        # isso que a 079 precisa chegar antes, para nascer com RLS.)
        "ALTER TABLE campanhas ADD COLUMN IF NOT EXISTS limite_participantes INTEGER",
        "ALTER TABLE grupo_eventos ADD COLUMN IF NOT EXISTS identificador VARCHAR(64)",
        "ALTER TABLE grupo_eventos ADD COLUMN IF NOT EXISTS "
        "identificador_tipo VARCHAR(12)",
        # 080 (Cheio × Aberto): `campanha_grupos` já existe em todo ambiente e
        # `create_all` NÃO adiciona coluna — sem isto, QUALQUER leitura de
        # vínculo (a aba Grupos inteira, e o roteamento do /g) quebra se o
        # deploy chegar antes da migration. As tabelas `grupo_participantes` e
        # `campanha_sub_ids` são NOVAS: essas o create_all cria — e é por isso
        # que a 080 precisa chegar antes, para nascerem com RLS.
        "ALTER TABLE campanha_grupos ADD COLUMN IF NOT EXISTS cheio_override BOOLEAN",
        # 081 (fallback lotado + Sub ID legível): as duas tabelas já existem em
        # todo ambiente. `create_all` não adiciona coluna NEM altera tipo — sem
        # isto, gravar um clique quebra e ativar grupo com nome comprido
        # estoura `value too long` no meio da transação do toggle.
        "ALTER TABLE campanha_link_eventos ADD COLUMN IF NOT EXISTS resultado VARCHAR(24)",
        "ALTER TABLE whatsapp_grupos ALTER COLUMN sub_id TYPE VARCHAR(64)",
    ]
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            for stmt in migrations:
                conn.execute(text(stmt))
        logger.info("Safe migrations applied successfully")
    except Exception as e:
        logger.warning(f"Safe migrations skipped: {e}")


def _importar_modelos() -> None:
    """Registra todos os modelos em Base.metadata (necessário para create_all)."""
    from app.models import User, Dataset, DatasetRow, Subscription, AdSpend, ClickRow, Job, JobChunk, CaptureSite, CustomLink, CustomLinkEvent, PageEvent  # noqa: F401
    from app.models.user_settings import UserSettings  # noqa: F401
    from app.models.shopee_integration import ShopeeIntegration  # noqa: F401
    from app.models.facebook_integration import FacebookIntegration  # noqa: F401
    from app.models.campaign import Campaign, CampaignDailyInsight  # noqa: F401


def init_db():
    """Verifica a conexão com o banco no startup.

    Incidente de 18/09/2026: este startup rodava `create_all` (dezenas de
    consultas ao catálogo) mais dois `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    a CADA boot da API e do worker. Com o healthcheck do Coolify reiniciando o
    container porque o banco estava lento, foram 20 execuções em 24h — cada
    `ALTER TABLE` pega lock exclusivo na tabela, mesmo sem alterar nada, e
    bloqueava tudo que usava `capture_sites` e `facebook_integrations` num
    banco já sufocado.

    Agora, por padrão, o startup só confirma que o banco responde. Schema é
    migration (`migrations/*.sql`); as colunas de `capture_sites` e
    `facebook_integrations` viraram a 087.

    `DB_SCHEMA_NO_STARTUP=true` devolve `create_all` + `_apply_safe_migrations`
    — é o modo de DEV e HML, onde o código costuma chegar antes da migration e
    a rede de proteção do módulo de Grupos ainda é necessária. Em produção a
    variável NÃO existe.
    """
    from app.db.session import engine
    from app.core.config import settings
    from sqlalchemy import text
    import time
    import logging

    logger = logging.getLogger(__name__)

    max_retries = 30
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection successful")
            if settings.DB_SCHEMA_NO_STARTUP:
                _importar_modelos()
                Base.metadata.create_all(bind=engine)
                _apply_safe_migrations(engine, logger)
                logger.info("Schema garantido no startup (DB_SCHEMA_NO_STARTUP=true)")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database not ready, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise
