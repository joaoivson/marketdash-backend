from celery import Celery
from app.core.ambiente import identidade_do_banco
from app.core.config import settings


def _fila_do_banco() -> str:
    """
    Nome da fila derivado do BANCO DE DADOS, não de ENVIRONMENT.

    Homologação e produção compartilham a MESMA instância de Redis no MESMO
    índice (/0). Como os dois workers consumiam a fila default, cada task caía
    num deles mais ou menos meio a meio — e quando a task de produção caía no
    worker de homologação, ele procurava o registro no banco dele, não achava e
    retornava em silêncio:

        dataset = repo.get_by_id(dataset_id, user_id)
        if not dataset:
            return          # status fica "pending" pra sempre, sem erro

    Era a causa dos ~50% de uploads travados e de a tabela datasets nunca ter
    registrado um único status='error'.

    Amarrar a fila à identidade do banco torna o problema impossível por
    construção: dois workers em bancos diferentes nunca dividem fila. Derivar de
    ENVIRONMENT não resolveria — hoje os DOIS ambientes reportam "development".
    """
    # A extração vive em app/core/ambiente.py — mesma função que responde
    # "isto é homologação?". Duas cópias da regex divergiriam com o tempo.
    # A ref do projeto entra por extenso: dá pra identificar o ambiente
    # olhando o Redis.
    return f"marketdash-{identidade_do_banco()}"


FILA = _fila_do_banco()

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
    #
    # ATENÇÃO — o Redis não tem prioridade nativa: o Celery emula criando uma fila por
    # step ("celery", "celery\x06\x163", ...). Só as pontas (0 e 9) estão sendo
    # consumidas neste ambiente; o default anterior (5) caía num step intermediário e
    # as tasks ficavam enfileiradas PARA SEMPRE, sem erro nenhum — era o que derrubava
    # o sync manual da Shopee (aceito com 202 e nunca executado). Default 0 garante que
    # qualquer `.delay()` sem prioridade explícita (upload de CSV, jobs) caia na fila
    # base, que é consumida. Batches pesados continuam pedindo priority=9 explícito.
    broker_transport_options={"queue_order_strategy": "priority"},
    task_default_priority=0,
    # Fila por banco — ver _fila_do_banco(). O worker sem -Q consome exatamente
    # esta fila, então produtor e consumidor andam juntos.
    task_default_queue=FILA,
)

# Explicitly include task modules so the worker always registers them (avoids "unregistered task" in production).
celery_app.conf.include = [
    "app.tasks.job_tasks",
    "app.tasks.csv_tasks",
    "app.tasks.shopee_tasks",
    "app.tasks.facebook_tasks",
    "app.tasks.instagram_tasks",
    "app.tasks.roteiro_tasks",
    "app.tasks.monitoramento_tasks",
]

# Auto-discover any other tasks under app.tasks
# NOTE: Beat schedule removido — sync Shopee agora é disparado via pg_cron + pg_net no Supabase
# (migration 018), chamando POST /api/v1/internal/cron/shopee-sync às 10h UTC (= 7h BRT).
celery_app.autodiscover_tasks(["app.tasks"], force=True)
