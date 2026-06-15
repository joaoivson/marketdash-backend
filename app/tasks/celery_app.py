from celery import Celery
from app.core.config import settings

# Initialize Celery app
celery_app = Celery(
    "marketdash",
    broker=settings.REDIS_URL or "redis://localhost:6379/0",
    backend=settings.REDIS_URL or "redis://localhost:6379/0"
)

# Celery configurations
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='America/Sao_Paulo',
    enable_utc=True,
    # Optimize for small tasks and chunk processing
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=1200,
    task_soft_time_limit=1100,
    # PRIORIDADE DE FILA (Redis): o sync manual do Facebook (botão "Atualizar dados")
    # usa priority=0 (máxima) e fura a fila na frente dos full-refresh pesados da Shopee
    # (cron, priority=9). Sem isso o botão fica minutos atrás do batch da Shopee.
    # priority menor = mais prioritário. Steps padrão do Redis: [0,3,6,9].
    broker_transport_options={"queue_order_strategy": "priority"},
    task_default_priority=5,
)

# Explicitly include task modules so the worker always registers them (avoids "unregistered task" in production).
celery_app.conf.include = [
    "app.tasks.job_tasks",
    "app.tasks.csv_tasks",
    "app.tasks.shopee_tasks",
    "app.tasks.facebook_tasks",
]

# Auto-discover any other tasks under app.tasks
# NOTE: Beat schedule removido — sync Shopee agora é disparado via pg_cron + pg_net no Supabase
# (migration 018), chamando POST /api/v1/internal/cron/shopee-sync às 10h UTC (= 7h BRT).
celery_app.autodiscover_tasks(["app.tasks"], force=True)
