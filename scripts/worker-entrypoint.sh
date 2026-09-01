#!/bin/sh
# Entrypoint do worker Celery — uma imagem só, dois papéis.
#
# Existem dois workers em cada ambiente e a diferença entre eles é UMA variável:
#
#   worker           (padrão)                   consome TODAS as filas
#   worker-whatsapp  CELERY_SOMENTE_WHATSAPP=true  consome só a fila de envio
#
# Por que não dois Dockerfiles: duplicar a imagem é como o dedicado fica para
# trás quando alguém mexe no outro. Foi assim que o worker rodou com código de
# semanas atrás — recurso separado que ninguém lembrava de atualizar.
#
# ⚠️ O worker PADRÃO continua sem `-Q` de propósito, e isso é o fail-safe: sem
# `-Q` ele consome todas as filas declaradas em `task_queues`, inclusive a do
# WhatsApp. Ou seja, se o worker dedicado cair ou nunca for criado, os envios
# continuam saindo — mais devagar, mas saem. Restringir o padrão a `-Q $FILA`
# daria isolamento total e é o passo seguinte, DEPOIS de o dedicado provar que
# fica de pé. Não faça os dois no mesmo dia.
#
# O nome da fila deriva do BANCO em runtime (ver _fila_do_banco em
# app/tasks/celery_app.py): hardcodar aqui faria homologação consumir a fila de
# produção, que dividem o mesmo Redis.
set -e

CONCURRENCY="${CELERY_CONCURRENCY:-8}"
ARGS="--loglevel=info --uid=1000 --concurrency=${CONCURRENCY}"

if [ "${CELERY_SOMENTE_WHATSAPP}" = "true" ]; then
    FILA=$(python -c 'from app.tasks.celery_app import FILA_WHATSAPP; print(FILA_WHATSAPP)')
    if [ -z "$FILA" ]; then
        echo "ERRO: não consegui resolver o nome da fila do WhatsApp." >&2
        exit 1
    fi
    echo "worker dedicado: consumindo apenas ${FILA} (concorrência ${CONCURRENCY})"
    ARGS="${ARGS} -Q ${FILA}"
else
    echo "worker padrão: consumindo todas as filas de task_queues (concorrência ${CONCURRENCY})"
fi

# shellcheck disable=SC2086
exec celery -A app.tasks.celery_app worker ${ARGS}
