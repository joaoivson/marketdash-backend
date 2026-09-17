#!/usr/bin/env bash
#
# Tetos de recurso das aplicações de HOMOLOGAÇÃO no Coolify.
#
# Uso:  COOLIFY_TOKEN=... scripts/limites-coolify.sh [--aplicar]
#       sem --aplicar ele só MOSTRA o que mudaria (padrão: ensaio)
#
# ## Por que isto existe
#
# Produção e homologação dividem o mesmo VPS (4 vCPU). Homologação é 5 das 8
# aplicações do servidor — e é onde vive o módulo de grupos de WhatsApp, a
# coisa mais pesada que temos (WAHA + 2 workers). Sem teto, um teste de disparo
# em hml consome CPU do orçamento de produção.
#
# Isso não é hipótese: em 11/09 e 16/09/2026 a Hostinger aplicou o teto de 20%
# no VPS inteiro por uso sustentado de CPU, e produção ficou inalcançável. O
# gatilho daquelas vezes foi build dentro do servidor (resolvido levando o build
# para o GitHub Actions); este script trata o outro vetor, o de RUNTIME.
#
# ## As duas alavancas, e a diferença entre elas
#
#   limits_cpus        TETO ABSOLUTO. `--cpus` do Docker: a app nunca passa
#                      disso, mesmo com a máquina ociosa.
#   limits_cpu_shares  PESO RELATIVO, só vale quando há DISPUTA. O padrão do
#                      Docker é 1024; pondo hml em 256, na hora em que os dois
#                      brigam por CPU, produção leva 4 partes para cada 1 de
#                      homologação. Com a máquina folgada, hml continua usando
#                      o que quiser até o teto.
#
# Produção fica INTOCADA de propósito: 1024 já é o padrão do Docker, então
# baixar hml para 256 basta para estabelecer a proporção. Mexer em produção
# para escrever um valor que já é o vigente seria risco sem ganho.
#
# ## CELERY_CONCURRENCY
#
# `worker-entrypoint.sh` sobe com `--concurrency=8` quando a variável não
# existe — 8 processos filhos, cada um com polars/pandas na memória, vezes DOIS
# workers em hml. Em homologação isso nunca foi necessário: a fila de hml é de
# teste.
#
# ## O que este script NÃO faz
#
# Nada aqui reinicia container. Limite de Docker se aplica na CRIAÇÃO do
# container e variável de ambiente entra no boot do processo — ou seja, os
# valores ficam GRAVADOS e passam a valer no próximo deploy. É de propósito:
# aplicar hoje e valer quando hml voltar é exatamente o que se quer com o
# ambiente parado.

set -euo pipefail

BASE="${COOLIFY_API_URL:-http://31.97.22.173:8000}"
APLICAR=0
[ "${1:-}" = "--aplicar" ] && APLICAR=1

if [ -z "${COOLIFY_TOKEN:-}" ]; then
  echo "::error::COOLIFY_TOKEN não definido (precisa do token RAIZ, não o de leitura)."
  exit 1
fi

# uuid | rótulo | limits_cpus | CELERY_CONCURRENCY ('-' = não aplicar)
#
# MEMÓRIA NÃO ENTRA AQUI, de propósito. Os tetos de memória já estavam
# configurados (WAHA 2048m, API 1536m, frontend 512m…) e mexer neles sem medir o
# consumo real seria trocar um risco conhecido por um desconhecido — WAHA com
# metade da memória e sessões de WhatsApp dentro é convite a OOM. O que este
# script governa é CPU, que é o recurso que a Hostinger estrangulou.
APPS=(
  "r448swsggoock0wg80csws0k|API hml               |0.5 |-"
  "jos0k8so0gw4c8okkgg8kskg|Worker Celery hml     |0.5 |2"
  "cogwsgwocwk8k4wkswokks0s|Worker WhatsApp hml   |0.5 |2"
  "mws0c0g4kkw00cwg88o00kw4|Frontend hml          |0.25|-"
  "hw88gc8ocsko04k8wkocs8kc|WAHA hml              |0.5 |-"
)
PESO_HML=256   # produção fica no padrão do Docker (1024) — ver cabeçalho

api() {  # api <método> <caminho> [corpo]
  local metodo="$1" caminho="$2" corpo="${3:-}"
  if [ -n "$corpo" ]; then
    curl --silent --show-error --max-time 30 -X "$metodo" \
      -H "Authorization: Bearer $COOLIFY_TOKEN" -H "Content-Type: application/json" \
      -d "$corpo" "$BASE$caminho"
  else
    curl --silent --show-error --max-time 30 -X "$metodo" \
      -H "Authorization: Bearer $COOLIFY_TOKEN" "$BASE$caminho"
  fi
}

# O Coolify parado é o modo de falha mais provável deste script (ele fica fora
# de propósito entre janelas de deploy). Sem esta checagem, o erro chega como
# "exit 28" e manda investigar o lugar errado.
sonda="$(api GET "/api/v1/applications" || true)"
if ! jq -e 'type == "array"' >/dev/null 2>&1 <<<"$sonda"; then
  echo "::error::o Coolify não respondeu em $BASE."
  echo "  Ele está de pé?  docker start coolify-db coolify-redis coolify-realtime coolify"
  echo "  (se o painel não abrir a frio, um 'docker restart coolify' resolve — já foi preciso 2× em 16/09)"
  echo "  Resposta recebida: $(head -c 200 <<<"$sonda")"
  exit 1
fi

[ "$APLICAR" = "1" ] || echo "── ENSAIO (nada será gravado; use --aplicar) ──"
printf '\n%-22s %-16s %-16s %-12s %s\n' "APLICAÇÃO" "CPUs" "PESO (shares)" "MEMÓRIA" "CONCURRENCY"

for linha in "${APPS[@]}"; do
  IFS='|' read -r uuid rotulo cpus concorrencia <<<"$linha"
  cpus="${cpus// /}"; concorrencia="${concorrencia// /}"

  app="$(api GET "/api/v1/applications/$uuid" || true)"
  if ! jq -e 'has("uuid")' >/dev/null 2>&1 <<<"$app"; then
    echo "::warning::não li '$rotulo' ($uuid) — pulando."
    continue
  fi
  # ── TRAVA: só homologação ────────────────────────────────────────────────
  # A lista acima só tem uuid de hml, mas lista é conferida por humano e uuid do
  # Coolify é uma sopa de 24 caracteres — trocar um por outro não dá erro, dá
  # produção com teto de 0,25 CPU. Aqui se CONFERE no próprio Coolify: a app tem
  # de estar na branch `develop` ou ter `hml` no domínio. Qualquer outra coisa
  # aborta o script inteiro, não só a linha.
  branch="$(jq -r '.git_branch // ""' <<<"$app")"
  dominio="$(jq -r '.fqdn // ""' <<<"$app")"
  nome="$(jq -r '.name // ""' <<<"$app")"
  if [ "$branch" != "develop" ] && [[ "$dominio" != *hml* ]] && [[ "$nome" != *hml* ]]; then
    echo "::error::'$rotulo' ($uuid) NÃO parece homologação:"
    echo "::error::  branch='$branch' fqdn='$dominio' name='$nome'"
    echo "::error::Abortando sem gravar nada. Confira o uuid na lista APPS."
    exit 1
  fi

  cpus_hoje="$(jq -r '.limits_cpus // "" ' <<<"$app")"
  mem_hoje="$(jq -r '.limits_memory // ""' <<<"$app")"   # só para exibir — não é alterado
  shares_hoje="$(jq -r '.limits_cpu_shares // ""' <<<"$app")"

  conc_hoje="-"
  if [ "$concorrencia" != "-" ]; then
    envs="$(api GET "/api/v1/applications/$uuid/envs" || echo '[]')"
    conc_hoje="$(jq -r '[.[] | select(.key=="CELERY_CONCURRENCY") | .value][0] // "(ausente → 8)"' <<<"$envs")"
  fi

  printf '%-22s %-16s %-16s %-12s %s\n' "$rotulo" \
    "${cpus_hoje:-(sem)} → $cpus" "${shares_hoje:-(sem)} → $PESO_HML" \
    "${mem_hoje:-(sem)} (intacta)" "$conc_hoje → $concorrencia"

  [ "$APLICAR" = "1" ] || continue

  corpo="$(jq -nc --arg c "$cpus" --argjson s "$PESO_HML" \
    '{limits_cpus: $c, limits_cpu_shares: $s}')"
  resp="$(api PATCH "/api/v1/applications/$uuid" "$corpo" || true)"
  if jq -e '.errors' >/dev/null 2>&1 <<<"$resp"; then
    echo "::error::Coolify recusou os limites de '$rotulo': $(jq -c '.errors' <<<"$resp")"
    exit 1
  fi

  if [ "$concorrencia" != "-" ]; then
    env_corpo="$(jq -nc --arg v "$concorrencia" \
      '{key: "CELERY_CONCURRENCY", value: $v, is_preview: false}')"
    # PATCH atualiza a variável que já existe; POST cria a que não existe. O
    # Coolify não tem upsert, e qual das duas serve depende do estado da app —
    # por isso as duas, nesta ordem.
    # POST primeiro (cria), PATCH depois (atualiza a que já existe). A ordem
    # inversa parecia funcionar e não funcionava: o PATCH numa variável
    # inexistente devolve um corpo COM campo `message`, e a checagem antiga
    # aceitava isso como sucesso — então o POST nunca rodava e a env nunca era
    # gravada, com o script relatando ✅. Descoberto em 17/09 lendo de volta.
    api POST  "/api/v1/applications/$uuid/envs" "$env_corpo" >/dev/null 2>&1 || true
    api PATCH "/api/v1/applications/$uuid/envs" "$env_corpo" >/dev/null 2>&1 || true

    # A ÚNICA prova é reler. Resposta de API não é estado gravado.
    gravado="$(api GET "/api/v1/applications/$uuid/envs" \
      | jq -r '[.[] | select(.key=="CELERY_CONCURRENCY") | .value][0] // empty')"
    if [ "$gravado" != "$concorrencia" ]; then
      echo "::error::CELERY_CONCURRENCY em '$rotulo' ficou '${gravado:-ausente}', esperado '$concorrencia'."
      exit 1
    fi
  fi
done

echo
if [ "$APLICAR" = "1" ]; then
  echo "✅ Gravado. Os valores passam a valer no PRÓXIMO deploy de cada app —"
  echo "   limite de Docker se aplica na criação do container, env no boot do processo."
else
  echo "Nada gravado. Para aplicar:  COOLIFY_TOKEN=... $0 --aplicar"
fi
