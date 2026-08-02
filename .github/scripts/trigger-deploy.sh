#!/usr/bin/env bash
#
# Dispara o deploy de UM recurso no Coolify e confere o resultado.
#
# Uso: trigger-deploy.sh <nome-do-alvo> <webhook-url>
#      COOLIFY_TOKEN precisa estar no ambiente.
#
# Por que esse script existe: o passo antigo era
#   curl -X POST "$URL" ... || echo "Deployment triggered"
# que engolia QUALQUER falha (URL vazia, 404, timeout) e ainda imprimia
# uma mensagem de sucesso. Some isso com o worker nunca ter tido um passo
# de deploy e o resultado foi o Celery rodando código de semanas atrás com
# o CI todo verde. Aqui a falha é barulhenta.

set -euo pipefail

alvo="${1:?informe o nome do alvo (ex: api, worker)}"
url="${2:-}"

if [ -z "$url" ]; then
  echo "::error title=Deploy do '$alvo' não configurado::A URL de webhook do Coolify para '$alvo' está vazia. O recurso NÃO foi atualizado. Cadastre o secret correspondente no repositório — sem ele o deploy fica pela metade e ninguém percebe."
  exit 1
fi

echo "→ Disparando deploy: $alvo"

resposta=$(mktemp)
http=$(
  curl --silent --show-error --location \
    --output "$resposta" \
    --write-out '%{http_code}' \
    --max-time 60 \
    --retry 2 --retry-delay 5 --retry-connrefused \
    -X POST "$url" \
    -H "Authorization: Bearer ${COOLIFY_TOKEN:-}" \
    -H "Content-Type: application/json"
) || {
  echo "::error title=Deploy do '$alvo' falhou::curl não conseguiu completar a requisição para o Coolify."
  exit 1
}

corpo=$(head -c 500 "$resposta")
rm -f "$resposta"

if [ "$http" -ge 400 ] || [ "$http" -eq 000 ]; then
  echo "::error title=Deploy do '$alvo' falhou (HTTP $http)::Coolify respondeu: $corpo"
  exit 1
fi

echo "✅ $alvo — HTTP $http"
echo "   $corpo"
