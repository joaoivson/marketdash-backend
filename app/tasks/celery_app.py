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
)

# Explicitly include task modules so the worker always registers them (avoids "unregistered task" in production).
celery_app.conf.include = [
    "app.tasks.job_tasks",
    "app.tasks.csv_tasks",
]
# Auto-discover any other tasks under app.tasks
celery_app.autodiscover_tasks(["app.tasks"], force=True)
