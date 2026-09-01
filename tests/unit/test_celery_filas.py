"""
Filas do Celery — os invariantes que já custaram semanas de bug silencioso.

Este arquivo existe porque as duas falhas históricas do broker foram do MESMO
tipo: task aceita, enfileirada e **nunca executada**, sem erro, sem log, sem
timeout. Não existe alerta que pegue isso; só teste.

  1. `priority=5` caía num step intermediário do Redis que ninguém consumia —
     o sync manual da Shopee "não fazia nada" por dias;
  2. fila derivada de ENVIRONMENT faria hml e produção dividirem consumidor,
     e a task de um banco morreria em silêncio no worker do outro.

A fila dedicada do WhatsApp é uma terceira chance de repetir o padrão: rotear
`roteiros.processar_execucao` para uma fila que nenhum worker consome pararia
TODO envio em grupo sem levantar um único erro. O teste principal aqui trava
exatamente isso.
"""
from app.tasks.celery_app import FILA, FILA_WHATSAPP, celery_app


def test_worker_sem_Q_consome_as_duas_filas():
    """O fail-safe do deploy: subir o roteamento ANTES de existir o worker
    dedicado não pode perder task nenhuma.

    `consume_from` é o que o worker realmente usa no boot para decidir de onde
    puxar. Com as duas filas em `task_queues`, o worker atual (que sobe sem
    `-Q`) cobre as duas sozinho — o worker dedicado vira otimização, não
    pré-requisito.
    """
    consumidas = set(celery_app.amqp.queues.consume_from.keys())
    assert FILA in consumidas
    assert FILA_WHATSAPP in consumidas, (
        "fila do WhatsApp fora do consumo padrão: task roteada para lá sumiria "
        "em silêncio se o worker dedicado não estivesse no ar"
    )


def test_toda_task_roteada_vai_para_uma_fila_consumida():
    """Roteamento para fila não declarada é o bug do `priority=5` de novo."""
    consumidas = set(celery_app.amqp.queues.consume_from.keys())
    for nome, destino in (celery_app.conf.task_routes or {}).items():
        fila = destino.get("queue")
        assert fila in consumidas, f"{nome} roteada para fila não consumida: {fila}"


def test_envio_em_grupo_sai_da_fila_comum():
    """O motivo de existir a fila dedicada: `processar_execucao` segura o slot
    por até 15 min dormindo (ritmo anti-ban). Na fila única, o upload de CSV da
    afiliada fica atrás desse sono."""
    rotas = celery_app.conf.task_routes or {}
    assert rotas.get("roteiros.processar_execucao", {}).get("queue") == FILA_WHATSAPP


def test_fila_deriva_do_banco_e_nao_do_ambiente():
    """hml e produção dividem o MESMO Redis/0. Fila por ENVIRONMENT faria os
    dois workers disputarem a mesma fila — e os dois reportam 'development'."""
    from app.core.ambiente import identidade_do_banco

    assert FILA == f"marketdash-{identidade_do_banco()}"
    assert FILA_WHATSAPP.startswith(FILA), "a fila do WhatsApp também é por banco"


def test_prioridade_default_e_zero():
    """0 e 9 são as únicas pontas consumidas; qualquer outro valor cai num step
    intermediário que ninguém lê. `.delay()` sem prioridade precisa ser seguro."""
    assert celery_app.conf.task_default_priority == 0


def test_tasks_de_whatsapp_estao_no_include():
    """`autodiscover` não basta: sem o include explícito aparece
    `unregistered task` só no ambiente real."""
    incluidos = set(celery_app.conf.include or [])
    for modulo in ("app.tasks.roteiro_tasks",
                   "app.tasks.monitoramento_tasks",
                   "app.tasks.proxy_tasks"):
        assert modulo in incluidos, f"{modulo} fora do include"
