#!/usr/bin/env bash
#
# Prova que a API no ar é a deste commit.
#
# Uso: confirmar-versao.sh <url-do-health> <sha-esperado>
#
# Irmão do `confirmar-bundle.sh` do frontend, pela mesma razão: "CI verde" só
# dizia que o webhook foi aceito. Três vezes registradas o deploy não trocou o
# container e o ambiente seguiu servindo código velho — em silêncio, com o
# Actions verde. No frontend o hash do bundle denunciava; no backend não havia
# nada equivalente até o `/health` passar a devolver `version`.
#
# Espera o valor MUDAR para o SHA esperado. Não basta a API responder: ela
# respondia bem durante todos os deploys que não aconteceram.

set -uo pipefail   # sem -e: uma falha de rede não pode derrubar o job sozinha

url="${1:?informe a URL do /health}"
esperado="${2:?informe o SHA esperado}"

LIMITE_SEGUNDOS=300   # o swap do container leva ~30s; 5 min é folga larga
INTERVALO=10

versao_agora() {
  curl --silent --max-time 20 "$url" 2>/dev/null | jq -r '.version // empty' 2>/dev/null
}

echo "→ Confirmando que $url passou a responder versão $esperado"

decorrido=0
while [ "$decorrido" -lt "$LIMITE_SEGUNDOS" ]; do
  atual="$(versao_agora)"
  if [ "$atual" = "$esperado" ]; then
    echo "✅ [${decorrido}s] versão $atual — é o código deste commit."
    exit 0
  fi
  echo "   [${decorrido}s] servindo: ${atual:-(sem resposta)}"
  sleep "$INTERVALO"; decorrido=$((decorrido + INTERVALO))
done

atual="$(versao_agora)"
if [ -z "$atual" ]; then
  echo "::error::Depois de ${LIMITE_SEGUNDOS}s, $url não respondeu com JSON contendo 'version'."
  echo "::error::A API pode estar fora do ar, ou é uma versão anterior à que devolve 'version'."
else
  echo "::error::Depois de ${LIMITE_SEGUNDOS}s, $url ainda responde versão '$atual' (esperada '$esperado')."
  echo "::error::O deploy foi aceito mas o container NÃO trocou."
fi
exit 1
