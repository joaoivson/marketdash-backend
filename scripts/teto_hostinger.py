#!/usr/bin/env python3
"""Diz se o teto de CPU da Hostinger está ativo — sem gerar carga na VPS.

Uso:  python3 scripts/teto_hostinger.py [dias]      (padrão: 14)

## Por que este script existe

O teto **não aparece como campo em lugar nenhum da API**: `GET
/virtual-machines/{id}` devolve `cpus: 4` estrangulada ou não. E as duas
medições à mão enganam, cada uma para um lado:

- **Latência de produção não prova nada.** Com a máquina ociosa um teto de 20%
  é invisível — ele só aparece quando se pede CPU. Em 16/09/2026 produção
  respondia `/health` em 0,16 s com o teto ativo.
- **`ct_set_limits` parar de se repetir ≠ teto removido.** Durante o episódio
  ele reaparece de hora em hora. A remoção tem assinatura PRÓPRIA: em 12/09 foi
  `ct_restart` 13:01 + 13:03 e `ct_set_limits` 13:05. O episódio de 15/09 não
  tem essa assinatura.

O que funciona é a **média diária de CPU**, porque o percentual da Hostinger é
da fatia LIBERADA, não dos 4 vCPU: estrangular **sobe** o número. O par
controlado de 12→13/09 mostra isso sem depender de teoria — mesma máquina,
mesma carga, 58,4% → 8,7% no dia seguinte à remoção.

## O limite honesto deste script

Ele dá um VEREDITO PROVÁVEL, não o estado. Quem afirma é o painel
(`hpanel.hostinger.com/vps/<id>/overview` → "CPU Limitations"). Use isto para
saber quando VALE a pena olhar o painel, e para registrar o antes/depois.

## E o que NÃO fazer

Nunca "medir" rodando benchmark no host. Carga artificial pede CPU e pode
reiniciar o relógio das ~3 h que a Hostinger espera de uso normalizado antes de
soltar o teto — atrasando exatamente o que se quer.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE = "https://developers.hostinger.com"
VM = os.environ.get("HOSTINGER_VM_ID", "1239939")

#: Baseline medido em 27/08–10/09 e confirmado em 13–14/09 (logo após a remoção
#: manual), sempre com a stack COMPLETA de pé: prod + hml + Coolify + WAHA.
BASELINE = 8.0
#: Acima disto, sem build nem incidente conhecido, o teto é a explicação provável.
SUSPEITO = 15.0


def token() -> str:
    t = os.environ.get("HOSTINGER_API_TOKEN")
    if t:
        return t
    env = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for linha in env.read_text().splitlines():
            if linha.startswith("HOSTINGER_API_TOKEN="):
                return linha.split("=", 1)[1].strip()
    sys.exit("HOSTINGER_API_TOKEN não encontrado (nem no ambiente, nem no .env).")


def get(caminho: str) -> dict | list:
    # O User-Agent não é enfeite: a API fica atrás de Cloudflare, que responde
    # 403 "error code: 1010" ao UA padrão do urllib (`Python-urllib/3.x`).
    req = urllib.request.Request(
        f"{BASE}{caminho}",
        headers={
            "Authorization": f"Bearer {token()}",
            "User-Agent": "marketdash-infra/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"API da Hostinger devolveu {e.code} em {caminho}: {e.read()[:200]!r}")


def main() -> None:
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    fim = dt.datetime.now(dt.timezone.utc)
    ini = fim - dt.timedelta(days=dias)

    bruto = get(
        f"/api/vps/v1/virtual-machines/{VM}/metrics"
        f"?date_from={ini:%Y-%m-%dT%H:%M:%SZ}&date_to={fim:%Y-%m-%dT%H:%M:%SZ}"
    )
    dados = bruto.get("data", bruto) if isinstance(bruto, dict) else {}
    uso = (dados.get("cpu_usage") or {}).get("usage") or {}
    if not uso:
        sys.exit("a API não devolveu série de cpu_usage — janela grande demais?")

    por_dia: dict[dt.date, list[float]] = collections.defaultdict(list)
    for epoch, valor in uso.items():
        d = dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc).date()
        por_dia[d].append(float(valor))

    print(f"\nVPS {VM} — CPU média diária ({dias} dias)")
    print(f"baseline sem teto, stack completa: ~{BASELINE:.0f}%\n")
    for d in sorted(por_dia):
        v = por_dia[d]
        m = sum(v) / len(v)
        marca = "  ← acima do baseline" if m > SUSPEITO else ""
        print(f"  {d:%d/%m}  média {m:5.1f}%  pico {max(v):5.1f}%  {'█' * int(m / 2)}{marca}")

    # ── as ações, para achar a assinatura de remoção ──────────────────────────
    limites: list[str] = []
    reinicios: list[str] = []
    for pagina in (1, 2):
        corpo = get(f"/api/vps/v1/virtual-machines/{VM}/actions?page={pagina}")
        for acao in (corpo.get("data") if isinstance(corpo, dict) else corpo) or []:
            if acao.get("name") == "ct_set_limits":
                limites.append(acao["created_at"])
            elif acao.get("name") == "ct_restart":
                reinicios.append(acao["created_at"])

    print("\nAções de limite (as 6 últimas):")
    for quando in sorted(limites, reverse=True)[:6]:
        print(f"  {quando}  ct_set_limits")

    # ── veredito ──────────────────────────────────────────────────────────────
    hoje = sorted(por_dia)[-1]
    media_hoje = sum(por_dia[hoje]) / len(por_dia[hoje])
    ultimo = max(limites) if limites else None

    print("\n" + "─" * 64)
    if media_hoje <= SUSPEITO:
        print(f"PROVÁVEL: teto NÃO ativo — hoje em {media_hoje:.1f}%, na faixa do baseline.")
        print("Confirme no painel e só então retome deploys.")
    else:
        print(f"PROVÁVEL: teto ATIVO — hoje em {media_hoje:.1f}%, {media_hoje / BASELINE:.1f}× o baseline.")
        if ultimo:
            idade = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
                ultimo.replace("Z", "+00:00")
            )
            print(f"Último ct_set_limits há {idade.days}d {idade.seconds // 3600}h ({ultimo}).")
        print("NÃO rode build nem benchmark. Abra chamado pedindo a remoção manual.")
    print("Palavra final é o painel: hpanel → VPS → Overview → 'CPU Limitations'.")
    print("─" * 64)


if __name__ == "__main__":
    main()
