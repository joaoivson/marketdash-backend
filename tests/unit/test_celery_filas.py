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

def test_filas_nao_compartilham_exchange_nem_routing_key():
    """O bug que o banner de boot do worker denunciou.

    `Queue(nome)` sem exchange/routing_key herda o DEFAULT, que vem de
    `task_default_queue`. As duas filas ficavam no MESMO exchange direct com a
    MESMA key — e exchange direct entrega a TODAS as filas que casam com a key.
    A task de envio caía nas duas listas e era executada DUAS VEZES, uma por
    worker: mensagem duplicada no grupo da afiliada, que é o caminho mais curto
    para o número ser banido.

    Nenhuma asserção sobre `task_routes` pegava isso — o roteamento estava
    certo; errado era a ligação da fila.
    """
    q = celery_app.amqp.queues
    comum, wpp = q[FILA], q[FILA_WHATSAPP]
    assert comum.routing_key != wpp.routing_key, "routing_key compartilhada duplica a task"
    assert comum.exchange.name != wpp.exchange.name, "exchange compartilhado duplica a task"
    assert wpp.routing_key == FILA_WHATSAPP


def test_task_de_envio_e_entregue_a_UMA_fila_so():
    """Prova de entrega real, não de configuração.

    Publica numa transport de memória e conta em quantas filas a mensagem caiu.
    É o único teste aqui que teria pegado o bug de exchange compartilhado — os
    outros olham config, e a config *parecia* certa.
    """
    from kombu import Connection

    rota = celery_app.amqp.router.route({}, "roteiros.processar_execucao")
    fila = rota["queue"]

    with Connection("memory://") as conn:
        canal = conn.channel()
        destinos = []
        for nome in (FILA, FILA_WHATSAPP):
            q = celery_app.amqp.queues[nome](canal)
            q.declare()
            destinos.append((nome, q))

        produtor = conn.Producer(canal)
        produtor.publish(
            {"teste": True},
            exchange=fila.exchange,
            routing_key=fila.routing_key,
            declare=[d[1] for d in destinos],
        )

        caiu = [nome for nome, q in destinos if q.get() is not None]

    assert caiu == [FILA_WHATSAPP], (
        f"a task de envio foi entregue a {caiu}; em mais de uma fila ela executa "
        "uma vez por worker e a afiliada recebe a mensagem duplicada"
    )
