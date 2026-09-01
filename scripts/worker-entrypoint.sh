#!/bin/sh
# Entrypoint do worker Celery — uma imagem, três papéis.
#
# `CELERY_PAPEL` decide de quais filas este container consome:
#
#   comum      só a fila geral (CSV, Shopee, Facebook, jobs)
#   whatsapp   só a fila de envio em grupos
#   todas      as duas (default)
#
# Por que não dois Dockerfiles: duplicar a imagem é como o worker dedicado fica
# para trás quando alguém mexe no outro. Já aconteceu aqui, por semanas.
#
# ⚠️ `todas` é o default de propósito: um container que suba SEM a variável
# consome tudo e nada fica parado. Só saia dele quando os dois papéis estiverem
# de pé — com `comum` e `whatsapp` separados NÃO existe mais rede: se o worker
# de WhatsApp cair, os envios ficam enfileirados até alguém perceber. Isso é
# isolamento de verdade, e o preço dele é vigilância.
#
# O nome da fila deriva do BANCO em runtime (ver _fila_do_banco em
# app/tasks/celery_app.py): hardcodar faria homologação consumir a fila de
# produção, que dividem o mesmo Redis.
set -e

PAPEL="${CELERY_PAPEL:-todas}"
# Compatibilidade com a variável anterior, para não haver janela durante a troca.
if [ "${CELERY_SOMENTE_WHATSAPP}" = "true" ]; then
    PAPEL="whatsapp"
fi

CONCURRENCY="${CELERY_CONCURRENCY:-8}"
ARGS="--loglevel=info --uid=1000 --concurrency=${CONCURRENCY}"

filas() {
    python -c "from app.tasks.celery_app import $1; print($1)"
}

case "$PAPEL" in
    whatsapp)
        F=$(filas FILA_WHATSAPP)
        ARGS="${ARGS} -Q ${F}"
        ;;
    comum)
        F=$(filas FILA)
        ARGS="${ARGS} -Q ${F}"
        ;;
    todas)
        F="(todas as filas de task_queues)"
        ;;
    *)
        echo "ERRO: CELERY_PAPEL='${PAPEL}' inválido. Use comum, whatsapp ou todas." >&2
        exit 1
        ;;
esac

if [ -z "$F" ]; then
    echo "ERRO: não consegui resolver o nome da fila para o papel '${PAPEL}'." >&2
    exit 1
fi

echo "worker [${PAPEL}]: consumindo ${F} (concorrência ${CONCURRENCY})"

# shellcheck disable=SC2086
exec celery -A app.tasks.celery_app worker ${ARGS}
