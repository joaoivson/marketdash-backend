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
    # Optimize for small tasks
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

# Auto-discover tasks from the tasks directory
celery_app.autodiscover_tasks(['app.tasks'], force=True)

# Also import it explicitly to be sure
import app.tasks.csv_tasks
