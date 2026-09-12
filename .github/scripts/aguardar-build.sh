#!/usr/bin/env bash
#
# Segura o job até o Coolify terminar de construir tudo que está na fila.
#
# Uso: aguardar-build.sh <uma-das-urls-de-deploy>   (só para derivar a base)
#      COOLIFY_TOKEN precisa estar no ambiente.
#
# Por que existe. Em 11/09/2026 `develop` e `main` foram empurradas com 21
# segundos de diferença. Isso virou dois `docker build` simultâneos no MESMO
# VPS de 4 vCPU que serve produção; a CPU foi a 100%, a Hostinger ativou a
# limitação e a API de produção ficou ~20h inalcançável.
#
# O `concurrency` do GitHub sozinho NÃO resolve: o job dispara o webhook e
# termina em segundos, enquanto o Coolify constrói em background. O lock é
# liberado antes do build começar. Este script mantém o job vivo — e portanto o
# lock preso — até a fila do Coolify esvaziar, o que serializa os builds de
# verdade.
#
# Efeito colateral bem-vindo: "CI verde" passa a significar "o build terminou",
# não só "o webhook foi aceito".

set -uo pipefail   # de propósito SEM -e: falha transitória de rede aqui não
                   # pode derrubar um deploy que já foi disparado com sucesso.

url="${1:-}"
if [ -z "$url" ]; then
  echo "::warning title=Espera pulada::Sem URL para derivar a base da API do Coolify."
  exit 0
fi

# http://IP:8000/api/v1/deploy?uuid=... -> http://IP:8000
base="${url%%/api/v1/deploy*}"

LIMITE_SEGUNDOS=900   # 15 min. Build normal leva ~3.
INTERVALO=15
MAX_FALHAS_SEGUIDAS=8 # ~2 min de rede ruim antes de desistir da espera.

inicio=$(date +%s)
falhas=0

echo "→ Aguardando a fila de builds do Coolify esvaziar (limite ${LIMITE_SEGUNDOS}s)"

while true; do
  decorrido=$(( $(date +%s) - inicio ))
  if [ "$decorrido" -ge "$LIMITE_SEGUNDOS" ]; then
    echo "::warning title=Espera esgotada::A fila do Coolify ainda tinha build em andamento após ${LIMITE_SEGUNDOS}s. Seguindo sem falhar o job — confira o deploy no painel."
    exit 0
  fi

  resposta=$(curl --silent --max-time 20 \
               -H "Authorization: Bearer ${COOLIFY_TOKEN:-}" \
               "$base/api/v1/deployments" 2>/dev/null)

  # Uma resposta vazia é falha de rede, NÃO fila vazia. Confundir os dois já
  # produziu um "todos os deployments terminaram" com o frontend ainda em fila.
  # A conexão runner→Coolify é comprovadamente intermitente, então isso não é
  # hipotético: só conta como fila vazia se o JSON for válido.
  if ! printf '%s' "$resposta" | jq -e 'type == "array"' >/dev/null 2>&1; then
    falhas=$(( falhas + 1 ))
    if [ "$falhas" -ge "$MAX_FALHAS_SEGUIDAS" ]; then
      echo "::warning title=Não deu para acompanhar o build::${falhas} respostas inválidas seguidas da API do Coolify. Seguindo sem falhar o job."
      exit 0
    fi
    echo "   (resposta inválida ${falhas}/${MAX_FALHAS_SEGUIDAS}, tentando de novo)"
    sleep "$INTERVALO"
    continue
  fi
  falhas=0

  pendentes=$(printf '%s' "$resposta" \
    | jq '[.[] | select(.status == "in_progress" or .status == "queued")] | length')

  if [ "${pendentes:-1}" = "0" ]; then
    echo "✅ Fila do Coolify vazia após ${decorrido}s — nenhum build em andamento."
    exit 0
  fi

  # Mostra o que está construindo: ajuda a entender espera longa sem abrir o painel.
  printf '%s' "$resposta" \
    | jq -r '.[] | select(.status == "in_progress" or .status == "queued")
             | "   … \(.application_name) [\(.status)]"'
  sleep "$INTERVALO"
done
