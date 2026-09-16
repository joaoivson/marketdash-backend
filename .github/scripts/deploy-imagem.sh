#!/usr/bin/env bash
#
# Aponta uma aplicação do Coolify para uma imagem JÁ CONSTRUÍDA e espera o
# deploy terminar de verdade.
#
# Uso: deploy-imagem.sh <alvo> <url-de-deploy> <imagem> <tag>
#   alvo   rótulo para o log (api, worker, frontend…)
#   url    o mesmo secret COOLIFY_DEPLOY_URL_* de sempre; base e uuid saem dele
#   imagem ghcr.io/joaoivson/marketdash-backend
#   tag    SHA do commit (imutável) — é ela que identifica a versão
#
# ## Por que este script existe
#
# Substitui `trigger-deploy.sh` + `aguardar-build.sh`, que eram o aparato de um
# mundo em que o Coolify CONSTRUÍA a imagem dentro do VPS de produção. Esse
# mundo derrubou produção duas vezes em cinco dias (11/09 e 16/09/2026).
#
# Três coisas que o fluxo antigo não fazia:
#
#   1. TRAVA. Se a aplicação ainda estiver em `build_pack: dockerfile`, este
#      script recusa e sai SEM disparar nada. Um POST numa app nesse estado
#      manda o VPS compilar — exatamente o que estamos eliminando. Vale
#      principalmente na janela de migração, quando algumas apps já viraram
#      imagem e outras não.
#   2. ESPERA O ESTADO REAL. O webhook é assíncrono: ele responde 200 e o
#      deploy pode falhar minutos depois. Aqui se lê
#      `GET /deployments/<uuid>` até `finished` — e um `failed` imprime o log
#      do Coolify, em vez de deixar o job verde com produção parada.
#   3. CONFIRMA A TAG. No fim, relê a aplicação e verifica que a tag gravada é
#      a que pedimos e que o container está `running`.
#
# `set -e` de propósito (o `aguardar-build.sh` não tinha, porque ali falhar era
# aceitável): aqui, qualquer erro tem que derrubar o job.

set -euo pipefail

alvo="${1:?informe o alvo (api, worker, frontend...)}"
url="${2:-}"
imagem="${3:?informe a imagem, ex: ghcr.io/joaoivson/marketdash-backend}"
tag="${4:?informe a tag, ex: o SHA do commit}"

if [ -z "$url" ]; then
  echo "::error::URL de deploy vazia para '$alvo' — o secret COOLIFY_DEPLOY_URL_* não chegou ao job."
  exit 1
fi
if [ -z "${COOLIFY_TOKEN:-}" ]; then
  echo "::error::COOLIFY_TOKEN não definido."
  exit 1
fi

# A URL do secret é http://IP:8000/api/v1/deploy?uuid=XXXX&force=false — dela
# saem a base da API e o uuid da aplicação, sem precisar de secret novo.
base="${url%%/api/v1/deploy*}"
uuid="$(sed -n 's/.*[?&]uuid=\([^&]*\).*/\1/p' <<<"$url")"
if [ -z "$uuid" ]; then
  echo "::error::não consegui extrair o uuid da URL de deploy de '$alvo'."
  exit 1
fi

api() {  # api <método> <caminho> [corpo-json]
  local metodo="$1" caminho="$2" corpo="${3:-}"
  if [ -n "$corpo" ]; then
    curl --silent --show-error --max-time 60 -X "$metodo" \
      -H "Authorization: Bearer $COOLIFY_TOKEN" -H "Content-Type: application/json" \
      -d "$corpo" "$base$caminho"
  else
    curl --silent --show-error --max-time 60 -X "$metodo" \
      -H "Authorization: Bearer $COOLIFY_TOKEN" "$base$caminho"
  fi
}

echo "→ $alvo: $imagem:$tag  (app $uuid)"

# ── 1. Trava: a app precisa estar em modo imagem ─────────────────────────────
app="$(api GET "/api/v1/applications/$uuid")"
if ! jq -e . >/dev/null 2>&1 <<<"$app"; then
  echo "::error::resposta inválida do Coolify ao ler a aplicação '$alvo' (rede? token?)."
  echo "$app" | head -c 300
  exit 1
fi
build_pack="$(jq -r '.build_pack // "?"' <<<"$app")"
if [ "$build_pack" != "dockerimage" ]; then
  echo "::error::'$alvo' está com build_pack='$build_pack' e não 'dockerimage'."
  echo "::error::Disparar assim faria o VPS COMPILAR a imagem — a causa dos apagões de 11/09 e 16/09."
  echo "::error::Migre a aplicação para Docker Image antes (ver docs/PROMOCAO_PARA_PRODUCAO.md)."
  exit 1
fi

# ── 2. Aponta para a tag desta execução ──────────────────────────────────────
patch="$(jq -nc --arg n "$imagem" --arg t "$tag" \
  '{docker_registry_image_name: $n, docker_registry_image_tag: $t}')"
resposta="$(api PATCH "/api/v1/applications/$uuid" "$patch")"
if jq -e '.errors // .message | select(. != null)' >/dev/null 2>&1 <<<"$resposta"; then
  # `message` sozinho pode ser sucesso ("Application updated"); só reclama se houver errors.
  if jq -e '.errors' >/dev/null 2>&1 <<<"$resposta"; then
    echo "::error::Coolify recusou o PATCH da tag em '$alvo': $(jq -c '.errors' <<<"$resposta")"
    exit 1
  fi
fi

# ── 3. Dispara e acompanha até o estado terminal ─────────────────────────────
disparo="$(api POST "/api/v1/deploy?uuid=$uuid&force=false")"
deployment="$(jq -r '.deployments[0].deployment_uuid // .deployment_uuid // empty' <<<"$disparo")"
if [ -z "$deployment" ]; then
  echo "::error::Coolify não devolveu deployment_uuid para '$alvo': $(head -c 300 <<<"$disparo")"
  exit 1
fi
echo "   deployment $deployment"

LIMITE=600   # 10 min. Puxar imagem leva segundos; só a primeira vez (camadas
             # novas) chega a minutos. Build era 5-10 min — e é o que acabou.
INTERVALO=10
decorrido=0
status="queued"
while [ "$decorrido" -lt "$LIMITE" ]; do
  sleep "$INTERVALO"; decorrido=$((decorrido + INTERVALO))
  d="$(api GET "/api/v1/deployments/$deployment" || true)"
  if ! jq -e . >/dev/null 2>&1 <<<"$d"; then
    echo "   [${decorrido}s] sem resposta do Coolify (rede) — seguindo"
    continue
  fi
  status="$(jq -r '.status // "?"' <<<"$d")"
  echo "   [${decorrido}s] $status"
  case "$status" in
    finished) break ;;
    failed)
      echo "::error::deploy de '$alvo' FALHOU. Últimas linhas do Coolify:"
      jq -r '.logs // "[]"' <<<"$d" \
        | jq -r '.[-40:][] | "     " + (.output // "" | tostring)' 2>/dev/null \
        || echo "     (sem logs estruturados)"
      exit 1 ;;
  esac
done

if [ "$status" != "finished" ]; then
  echo "::error::deploy de '$alvo' não terminou em ${LIMITE}s (último estado: $status)."
  exit 1
fi

# ── 4. Confirma o que ficou gravado ──────────────────────────────────────────
app="$(api GET "/api/v1/applications/$uuid")"
tag_gravada="$(jq -r '.docker_registry_image_tag // "?"' <<<"$app")"
estado="$(jq -r '.status // "?"' <<<"$app")"
if [ "$tag_gravada" != "$tag" ]; then
  echo "::error::'$alvo' terminou com a tag '$tag_gravada', esperada '$tag'."
  exit 1
fi
case "$estado" in
  running*) ;;
  *) echo "::warning::'$alvo' está com status '$estado' logo após o deploy." ;;
esac

echo "✅ $alvo em $imagem:$tag (status: $estado)"
