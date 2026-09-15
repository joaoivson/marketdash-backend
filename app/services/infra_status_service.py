"""
Status da infraestrutura num lugar só — Coolify, containers, VPS e pontas HTTP.

**Só leitura.** Este módulo emite exclusivamente `GET`. Não existe função de
restart, deploy ou escrita de env var aqui, e isso é deliberado: o token do
Coolify já dá esse poder, e um botão desses atrás de uma tela web é como se
perde um domingo.

## Por que os quatro blocos, e qual deles importa

O incidente de 11/09/2026 (`STATUS-login-producao-fora.md`) é o desenho deste
arquivo. Naquele dia o Coolify dizia `running:healthy` enquanto a API estava
**inalcançável há horas**: o `HEALTHCHECK` do Dockerfile testa
`localhost:8000/health` de DENTRO do container, e por dentro a app estava sã —
o que havia quebrado era a rota do Traefik. Healthcheck interno não enxerga
isso.

Daí a ordem de importância, que é o inverso da intuição:

1. **`pontas`** — `GET` na URL pública. É o único bloco que pega "container de
   pé e usuária sem acesso". Roda de dentro do container, mas sai pela
   internet e volta pelo Traefik, então exercita o caminho real.
2. **`recursos`** — o que o Docker acha de cada container, com os tetos de CPU
   que nasceram do mesmo incidente.
3. **`coolify`** — fila de deploy e estado do proxy. Build simultâneo foi o
   gatilho de 11/09; ver a fila cheia é ver o gatilho armado.
4. **`hostinger`** — CPU/RAM/disco do VPS. A "CPU limitation" da Hostinger é o
   que transformou um pico de 11 minutos em 20 horas de apagão.

## O buraco que não fecha aqui

A API do Coolify beta.463 **não tem endpoint de métricas** — medido em
15/09/2026: `/servers/{uuid}/metrics`, `/usage` e
`/applications/{uuid}/metrics` devolvem 404. O Sentinel coleta CPU/memória por
container (`is_metrics_enabled` ligado em 12/09), mas só a UI lê. Por isso
CPU/RAM do host só vem pela API da Hostinger, com token que precisa ser
gerado à mão no hPanel.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.core.ambiente import REF_HOMOLOGACAO, REF_PRODUCAO, identidade_do_banco
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Timeout curto por chamada: o painel inteiro precisa responder antes de a
#: pessoa achar que a tela travou. Bloco que estourar aparece com o erro no
#: lugar do dado — nunca derruba os outros.
TIMEOUT_COOLIFY = 8.0
TIMEOUT_PONTA = 6.0
TIMEOUT_HOSTINGER = 10.0

#: Janela das métricas do VPS. 12 h para o DEGRAU aparecer — ver
#: `_metricas_hostinger`.
JANELA_METRICAS_HORAS = 12

#: Nome que o Coolify guarda é o que o deploy criou um dia — `celery:dsc8ko…`,
#: `cerely-qs8480s…` (com o typo), `http://api.marketdash.com.br`. Ninguém
#: consegue ler um painel assim, e adivinhar ambiente pelo nome erra: o
#: `waha-hml` está na branch `main`. Os 9 recursos são estáveis desde
#: 01/09/2026 — mapear à mão é mais honesto do que heurística.
#:
#: UUID desconhecido não é descartado: cai em `_rotulo_provisorio()` e aparece
#: na tela como "não mapeado", que é o aviso de que nasceu recurso novo.
RECURSOS_CONHECIDOS: dict[str, tuple[str, str, str]] = {
    # uuid                          rótulo                ambiente         papel
    "toow0co8g40gkc44w84c4skw": ("API", "producao", "api"),
    "pgg440ogkco04ks4ww88swgs": ("Worker Celery", "producao", "worker"),
    "qs0404g4g40gk80csg4gwo8c": ("Frontend", "producao", "frontend"),
    "r448swsggoock0wg80csws0k": ("API", "homologacao", "api"),
    "jos0k8so0gw4c8okkgg8kskg": ("Worker Celery", "homologacao", "worker"),
    "cogwsgwocwk8k4wkswokks0s": ("Worker Celery (WhatsApp)", "homologacao", "worker"),
    "mws0c0g4kkw00cwg88o00kw4": ("Frontend", "homologacao", "frontend"),
    "hw88gc8ocsko04k8wkocs8kc": ("WAHA (WhatsApp)", "homologacao", "waha"),
    # Existem DUAS instâncias de Redis de pé, e só uma é usada: conferido em
    # 15/09/2026 pelo `REDIS_URL` das três apps (api prod, worker prod, api
    # hml) — as três apontam para `h0cw0…`. A `y4so…` (criada em 06/02, um dia
    # antes da outra) não tem referência conhecida e continua consumindo RAM
    # do mesmo VPS que a Hostinger estrangulou em 11/09. Fica no painel com o
    # rótulo dizendo isso; remover é decisão do João, não deste código.
    "h0cw0gc8owws004480g0sog8": ("Redis (broker do Celery)", "compartilhado", "redis"),
    "y4so0kk48sg8woskskok8owo": ("Redis (2ª instância, sem uso conhecido)", "compartilhado", "redis"),
}

#: As quatro pontas públicas. `tipo` decide o que é "resposta certa": a API tem
#: de dizer `healthy` no corpo, o frontend tem de trazer o `div#root`.
#:
#: Checar só o código HTTP não serve — foi exatamente o modo de falha de
#: 11/09: o Traefik sem rota devolve `404 page not found` em `text/plain`, e um
#: proxy mal configurado pode devolver 200 de outra coisa qualquer.
PONTAS = (
    ("API", "producao", "https://api.marketdash.com.br/health", "api"),
    ("Frontend", "producao", "https://marketdash.com.br/", "frontend"),
    ("API", "homologacao", "https://api.hml.marketdash.com.br/health", "api"),
    ("Frontend", "homologacao", "https://hml.marketdash.com.br/", "frontend"),
)

AMBIENTE_POR_REF = {REF_PRODUCAO: "producao", REF_HOMOLOGACAO: "homologacao"}


# ─────────────────────────────── helpers ────────────────────────────────


def _dividir_status(status: Optional[str]) -> tuple[str, Optional[str]]:
    """`"running:healthy"` → `("running", "healthy")`.

    O Coolify junta duas coisas diferentes num campo: o estado do container
    (docker) e o veredito do healthcheck. `running:unknown` é o caso dos
    workers — container de pé, sem healthcheck configurado — e não é problema;
    tratar como "desconhecido = ruim" pintaria metade do painel de vermelho
    todo dia.
    """
    if not status:
        return ("desconhecido", None)
    partes = status.split(":", 1)
    return (partes[0], partes[1] if len(partes) > 1 else None)


def _rotulo_provisorio(nome: Optional[str], branch: Optional[str]) -> tuple[str, str, str]:
    """Fallback para recurso que nasceu depois deste código."""
    nome = (nome or "sem nome").strip()
    baixo = nome.lower()
    if "hml" in baixo or "homolog" in baixo:
        ambiente = "homologacao"
    elif branch == "main":
        ambiente = "producao"
    elif branch == "develop":
        ambiente = "homologacao"
    else:
        ambiente = "desconhecido"
    return (nome, ambiente, "nao_mapeado")


def _limite(valor: Any) -> Optional[str]:
    """`"0"` e `0` no Coolify significam SEM teto, não "teto zero"."""
    if valor in (None, "", "0", 0, "0m"):
        return None
    return str(valor)


def _commit(sha: Optional[str]) -> Optional[str]:
    if not sha or sha.strip().upper() == "HEAD":
        return None
    return sha[:8]


def _ambiente_do_banco() -> str:
    return AMBIENTE_POR_REF.get(identidade_do_banco(), "local")


# ─────────────────────────────── Coolify ────────────────────────────────


async def _coolify_get(client: httpx.AsyncClient, caminho: str) -> Any:
    resp = await client.get(caminho)
    resp.raise_for_status()
    tipo = resp.headers.get("content-type", "")
    if "json" in tipo:
        return resp.json()
    return resp.text.strip()


async def coletar_coolify() -> dict:
    """Servidor, containers e fila de deploy.

    Uma chamada só por endpoint, todas em paralelo. `version` vem como texto
    puro (`4.0.0-beta.463`), não JSON — daí o `_coolify_get` olhar o
    content-type.
    """
    token = settings.coolify_token_leitura
    if not token:
        return {
            "configurado": False,
            "erro": None,
            "instrucao": (
                "Defina COOLIFY_API_TOKEN_GET no ambiente da API — um token "
                "read-only criado em Coolify › Security › API tokens (o painel "
                "só lê). `COOLIFY_TOKEN`, que é root, serve de fallback."
            ),
            "url": settings.COOLIFY_URL,
            "versao": None,
            "servidor": None,
            "recursos": [],
            "fila_de_deploy": [],
        }

    base = settings.COOLIFY_URL.rstrip("/") + "/api/v1"
    cabecalhos = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(
            base_url=base, headers=cabecalhos, timeout=TIMEOUT_COOLIFY
        ) as client:
            versao, servidores, apps, bancos, deploys = await asyncio.gather(
                _coolify_get(client, "/version"),
                _coolify_get(client, "/servers"),
                _coolify_get(client, "/applications"),
                _coolify_get(client, "/databases"),
                _coolify_get(client, "/deployments"),
            )
            # `resources` é o que sabe o status de TODO recurso do servidor,
            # inclusive os que não aparecem em /applications.
            # A LISTA de servidores vem sem proxy, Traefik nem Sentinel — esses
            # campos só existem no detalhe. Ler só a lista dava um bloco de
            # host com metade dos campos em `null`, que na tela é
            # indistinguível de "proxy caiu".
            servidor = (servidores or [{}])[0]
            uuid_servidor = servidor.get("uuid")
            recursos_do_servidor = []
            if uuid_servidor:
                detalhe, recursos_do_servidor = await asyncio.gather(
                    _coolify_get(client, f"/servers/{uuid_servidor}"),
                    _coolify_get(client, f"/servers/{uuid_servidor}/resources"),
                )
                # Os dois endpoints são complementares, e cada um omite o que o
                # outro tem: `is_reachable`/`is_usable` só vêm na LISTA, e
                # proxy/Traefik/Sentinel só vêm no DETALHE. Ler um só deixava
                # metade do bloco em `null` — que na tela é indistinguível de
                # "o proxy caiu".
                servidor = {**servidor, **{k: v for k, v in detalhe.items() if v is not None}}
    except Exception as e:  # noqa: BLE001 — bloco degrada, painel não cai
        logger.warning("Painel de infra: Coolify indisponível (%s)", e)
        return {
            "configurado": True,
            "erro": f"{type(e).__name__}: {str(e)[:200]}",
            "instrucao": None,
            "url": settings.COOLIFY_URL,
            "versao": None,
            "servidor": None,
            "recursos": [],
            "fila_de_deploy": [],
        }

    return {
        "configurado": True,
        "erro": None,
        "instrucao": None,
        "url": settings.COOLIFY_URL,
        "versao": versao if isinstance(versao, str) else None,
        "servidor": _montar_servidor(servidor),
        "recursos": _montar_recursos(apps or [], bancos or [], recursos_do_servidor or []),
        "fila_de_deploy": [
            {
                "aplicacao": d.get("application_name"),
                "status": d.get("status"),
                "commit": (d.get("commit") or "")[:8] or None,
            }
            for d in (deploys or [])
        ],
    }


def _montar_servidor(servidor: dict) -> dict:
    cfg = servidor.get("settings") or {}
    proxy = servidor.get("proxy") or {}
    return {
        "nome": servidor.get("name"),
        "ip": servidor.get("ip"),
        "alcancavel": servidor.get("is_reachable"),
        "utilizavel": servidor.get("is_usable"),
        "eh_host_coolify": servidor.get("is_coolify_host"),
        "proxy_tipo": proxy.get("type"),
        "proxy_status": proxy.get("status"),
        "traefik_versao": servidor.get("detected_traefik_version"),
        # Sentinel velho = a UI do Coolify está mostrando métrica congelada.
        "sentinel_em": servidor.get("sentinel_updated_at"),
        # Dois builds ao mesmo tempo foi o gatilho literal de 11/09. O CI
        # serializa por fora; este número é o que o servidor permitiria.
        "builds_simultaneos": cfg.get("concurrent_builds"),
        "fila_de_deploy_limite": cfg.get("deployment_queue_limit"),
        "alerta_disco_pct": cfg.get("docker_cleanup_threshold"),
        "disco_cheio_avisado": servidor.get("high_disk_usage_notification_sent"),
    }


def _montar_recursos(apps: list, bancos: list, recursos: list) -> list[dict]:
    """Junta os três endpoints numa linha por container.

    `/applications` traz limites, branch e commit; `/databases` traz o Redis;
    `/servers/{uuid}/resources` é o que fecha a lista — se um recurso aparecer
    só lá, ele entra com o que houver, porque recurso invisível no painel é
    exatamente o que ninguém vai monitorar.
    """
    por_uuid: dict[str, dict] = {}

    for app in apps:
        por_uuid[app.get("uuid")] = {"tipo": "aplicacao", "bruto": app}
    for banco in bancos:
        por_uuid[banco.get("uuid")] = {"tipo": "banco", "bruto": banco}
    for r in recursos:
        uuid = r.get("uuid")
        if uuid not in por_uuid:
            por_uuid[uuid] = {"tipo": r.get("type") or "desconhecido", "bruto": r}
        # O status de /resources é o mais fresco: é ele que a UI mostra.
        por_uuid[uuid]["status_servidor"] = r.get("status")

    linhas = []
    for uuid, dados in por_uuid.items():
        bruto = dados["bruto"]
        status = dados.get("status_servidor") or bruto.get("status")
        estado, saude = _dividir_status(status)
        rotulo, ambiente, papel = RECURSOS_CONHECIDOS.get(
            uuid, _rotulo_provisorio(bruto.get("name"), bruto.get("git_branch"))
        )
        linhas.append(
            {
                "uuid": uuid,
                "rotulo": rotulo,
                "ambiente": ambiente,
                "papel": papel,
                "tipo": dados["tipo"],
                "nome_coolify": bruto.get("name"),
                "status": status,
                "estado": estado,
                "saude": saude,
                "limite_cpu": _limite(bruto.get("limits_cpus")),
                "limite_memoria": _limite(bruto.get("limits_memory")),
                "reinicios": bruto.get("restart_count"),
                "reiniciado_em": bruto.get("last_restart_at"),
                "online_em": bruto.get("last_online_at"),
                "branch": bruto.get("git_branch"),
                # `git_commit_sha` é literalmente "HEAD" quando o deploy
                # segue a branch — mostrar isso como commit é ruído que
                # parece informação.
                "commit": _commit(bruto.get("git_commit_sha")),
                "fqdn": bruto.get("fqdn"),
                "healthcheck_ligado": bruto.get("health_check_enabled"),
                "atualizado_em": bruto.get("updated_at"),
                # Preenchido por `_cruzar()` depois que as pontas respondem.
                "contradicao": None,
            }
        )

    # Produção primeiro, e dentro do ambiente a ordem dos papéis — quem abre o
    # painel quer ver produção sem procurar.
    ordem_ambiente = {"producao": 0, "homologacao": 1, "compartilhado": 2}
    ordem_papel = {"api": 0, "worker": 1, "frontend": 2, "waha": 3, "redis": 4}
    linhas.sort(
        key=lambda l: (
            ordem_ambiente.get(l["ambiente"], 9),
            ordem_papel.get(l["papel"], 8),
            l["rotulo"],
        )
    )
    return linhas


# ───────────────────────── pontas públicas (HTTP) ────────────────────────


def _saude_interna(corpo: str) -> Optional[dict]:
    """O que o `/health` diz sobre banco e Redis, de dentro da app.

    É informação que nenhum outro bloco tem: o container pode estar no ar,
    respondendo, e com o Redis caído — nesse caso o upload da afiliada é
    aceito e nunca processa (foi o que aconteceu em 26/08, com o broker em
    crashloop devolvendo 201 e perdendo o arquivo).
    """
    import json

    try:
        dados = json.loads(corpo)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(dados, dict):
        return None
    interna = {
        k: dados.get(k) for k in ("database", "redis") if isinstance(dados.get(k), str)
    }
    return interna or None


def _avaliar_ponta(tipo: str, resp: httpx.Response) -> tuple[bool, str]:
    """Resposta certa é a NOSSA resposta, não um 200 qualquer.

    Mesma regra da sonda externa (`.github/workflows/monitor-producao.yml`):
    para a API, a palavra `healthy` no corpo; para o frontend, o `div#root`
    que prova que é o nosso `index.html` e não uma página do proxy.
    """
    corpo = resp.text[:4000]
    if resp.status_code >= 400:
        return (False, f"HTTP {resp.status_code}")
    if tipo == "api":
        if '"status"' in corpo and "healthy" in corpo:
            return (True, "healthy")
        return (False, f"HTTP {resp.status_code} sem 'healthy' no corpo")
    if 'id="root"' in corpo:
        return (True, "index.html servido")
    return (False, f"HTTP {resp.status_code} sem div#root")


async def _medir_ponta(
    client: httpx.AsyncClient, rotulo: str, ambiente: str, url: str, tipo: str
) -> dict:
    inicio = time.perf_counter()
    linha = {"rotulo": rotulo, "ambiente": ambiente, "url": url, "tipo": tipo}
    try:
        resp = await client.get(url)
        ok, detalhe = _avaliar_ponta(tipo, resp)
        linha.update(
            {
                "http": resp.status_code,
                "ok": ok,
                "detalhe": detalhe,
                "latencia_ms": round((time.perf_counter() - inicio) * 1000),
                "saude_interna": _saude_interna(resp.text) if tipo == "api" else None,
            }
        )
    except Exception as e:  # noqa: BLE001
        linha.update(
            {
                "http": None,
                "ok": False,
                "detalhe": f"{type(e).__name__}: {str(e)[:120]}",
                "latencia_ms": round((time.perf_counter() - inicio) * 1000),
                "saude_interna": None,
            }
        )
    return linha


async def coletar_pontas() -> list[dict]:
    async with httpx.AsyncClient(
        timeout=TIMEOUT_PONTA, follow_redirects=True
    ) as client:
        return list(
            await asyncio.gather(
                *(_medir_ponta(client, *ponta) for ponta in PONTAS)
            )
        )


# ─────────────────────────── filas do Celery ─────────────────────────────


def coletar_filas() -> dict:
    """Comprimento de cada fila no Redis, lida do Redis e não do Celery.

    Produção e homologação dividem o MESMO Redis no mesmo índice `/0` — é por
    isso que o nome da fila carrega a ref do banco (`_fila_do_banco()`), e é
    por isso que este bloco mostra os dois ambientes de uma vez.

    O que se procura aqui é fila que só cresce: task aceita e nunca executada
    foi a falha de `priority=5` (fila intermediária que ninguém consome) e a
    do worker morto de 26/08. Um número grande e parado é o sintoma.
    """
    from app.core import cache

    bloco: dict[str, Any] = {
        "configurado": bool(settings.REDIS_URL),
        "erro": None,
        "ambiente_desta_api": _ambiente_do_banco(),
        "ping_ms": None,
        "filas": [],
    }
    if not settings.REDIS_URL:
        return bloco

    client = cache.get_client()
    if client is None:
        bloco["erro"] = "REDIS_URL definida, mas o client não subiu."
        return bloco

    try:
        inicio = time.perf_counter()
        client.ping()
        bloco["ping_ms"] = round((time.perf_counter() - inicio) * 1000, 1)

        filas = []
        for chave in client.scan_iter(match="marketdash-*", count=200):
            nome = chave if isinstance(chave, str) else chave.decode("utf-8", "replace")
            try:
                if client.type(chave) != "list":
                    continue
                filas.append({"nome": _nome_legivel_da_fila(nome), "tamanho": client.llen(chave)})
            except Exception:  # noqa: BLE001 — chave que sumiu no meio do scan
                continue
        bloco["filas"] = sorted(filas, key=lambda f: f["nome"])
    except Exception as e:  # noqa: BLE001
        bloco["erro"] = f"{type(e).__name__}: {str(e)[:200]}"
    return bloco


def _nome_legivel_da_fila(nome: str) -> str:
    """`marketdash-ytjp…\\x06\\x169` → `marketdash-ytjp… (prioridade 9)`.

    O Redis não tem prioridade nativa: o Celery emula criando uma fila por
    step, com um separador de dois bytes de controle. Sem traduzir, a tela
    mostra caracteres invisíveis e duas filas parecem a mesma.
    """
    if "\x06\x16" in nome:
        base, _, prioridade = nome.partition("\x06\x16")
        return f"{base} (prioridade {prioridade})"
    return nome


# ───────────────────────────── Hostinger ─────────────────────────────────


async def coletar_hostinger() -> dict:
    """VPS, métricas e as AÇÕES da Hostinger sobre a máquina.

    Único caminho para CPU/RAM/disco: a API do Coolify não as expõe (404 em
    `/servers/{uuid}/metrics`, medido em 15/09/2026) e o Sentinel só alimenta
    a UI dele.

    O bloco de **ações** (`/actions`) é o achado de 15/09 e vale mais do que
    parece: é ali que aparece o `ct_set_limits` — a "CPU limitation" que a
    Hostinger aplica sozinha quando a máquina satura. Foi ela que transformou
    um pico de 11 minutos em ~20 h de apagão em 11/09, e até hoje só dava para
    saber pelo painel ou pelo e-mail deles.
    """
    bloco: dict[str, Any] = {
        "configurado": bool(settings.HOSTINGER_API_TOKEN),
        "erro": None,
        "instrucao": (
            "Gere um token em hPanel › VPS › API e defina HOSTINGER_API_TOKEN "
            "no ambiente da API. Sem ele não há CPU/RAM/disco do VPS: a API do "
            "Coolify não expõe métricas."
        ),
        "vps": None,
        "metricas": None,
        "acoes": [],
        "limitacao_de_cpu": None,
    }
    if not settings.HOSTINGER_API_TOKEN:
        return bloco

    base = settings.HOSTINGER_API_URL.rstrip("/")
    cabecalhos = {
        "Authorization": f"Bearer {settings.HOSTINGER_API_TOKEN}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_HOSTINGER, headers=cabecalhos) as client:
            resp = await client.get(f"{base}/api/vps/v1/virtual-machines")
            resp.raise_for_status()
            maquinas = resp.json()
            if isinstance(maquinas, dict):  # algumas rotas embrulham em {data: []}
                maquinas = maquinas.get("data") or []
            if not maquinas:
                bloco["erro"] = "A conta não retornou nenhuma VPS."
                return bloco

            vm = maquinas[0]
            vm_id = vm.get("id")
            bloco["vps"] = {
                "id": vm_id,
                "hostname": vm.get("hostname"),
                "estado": vm.get("state"),
                "plano": vm.get("plan"),
                "vcpus": vm.get("cpus"),
                "memoria_mb": vm.get("memory"),
                "disco_mb": vm.get("disk"),
                "ip": (vm.get("ipv4") or [{}])[0].get("address"),
                "criada_em": vm.get("created_at"),
            }
            metricas, acoes = await asyncio.gather(
                _metricas_hostinger(client, base, vm_id),
                _acoes_hostinger(client, base, vm_id),
                return_exceptions=True,
            )
            bloco["metricas"] = metricas if isinstance(metricas, dict) else None
            bloco["acoes"] = acoes if isinstance(acoes, list) else []
            bloco["limitacao_de_cpu"] = _limitacao_de_cpu(bloco["acoes"])
    except Exception as e:  # noqa: BLE001
        bloco["erro"] = f"{type(e).__name__}: {str(e)[:200]}"
    return bloco


async def _metricas_hostinger(
    client: httpx.AsyncClient, base: str, vm_id: Any
) -> Optional[dict]:
    """Últimas 12 h de métricas, reduzidas ao valor atual e ao pico.

    Janela de 12 h, não de 1 h: o que importa não é o instante, é ver o
    **degrau**. Em 15/09 a CPU passou de ~9% para 85%+ entre 11:46 e 12:50 sem
    mexer em rede nem em memória — com janela de uma hora esse degrau some e
    sobra um número alto sem história.
    """
    if not vm_id:
        return None
    fim = datetime.now(timezone.utc)
    inicio = fim - timedelta(hours=JANELA_METRICAS_HORAS)
    resp = await client.get(
        f"{base}/api/vps/v1/virtual-machines/{vm_id}/metrics",
        params={
            "date_from": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "date_to": fim.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    resp.raise_for_status()
    dados = resp.json()
    if isinstance(dados, dict) and isinstance(dados.get("data"), dict):
        dados = dados["data"]
    if not isinstance(dados, dict):
        return {"formato_inesperado": True}

    saida: dict[str, Any] = {"janela_horas": JANELA_METRICAS_HORAS}
    for chave in ("cpu_usage", "ram_usage", "disk_space", "uptime"):
        resumo = _resumir_serie(dados.get(chave))
        if resumo is not None:
            saida[chave] = resumo
    if len(saida) == 1:
        return {"formato_inesperado": True}
    return saida


def _resumir_serie(serie: Any) -> Optional[dict]:
    """`{"unit": "%", "usage": {"<epoch>": valor, ...}}` → atual, pico e média.

    O formato real (medido em 15/09 contra a API) é um **dicionário chaveado
    por timestamp**, não uma lista ordenada — então "atual" é o maior
    timestamp, nunca o último item iterado: dict de chave string não tem ordem
    garantida de tempo e o "agora" sairia aleatório.
    """
    if not isinstance(serie, dict):
        return None
    pontos = serie.get("usage")
    if not isinstance(pontos, dict):
        return None
    numeros: list[tuple[int, float]] = []
    for t, v in pontos.items():
        try:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numeros.append((int(t), float(v)))
        except (TypeError, ValueError):
            continue
    if not numeros:
        return None
    numeros.sort()
    valores = [v for _, v in numeros]
    return {
        "atual": round(valores[-1], 2),
        "pico": round(max(valores), 2),
        "media": round(sum(valores) / len(valores), 2),
        "unidade": serie.get("unit"),
        "pontos": len(valores),
        "medido_em": datetime.fromtimestamp(numeros[-1][0], timezone.utc).isoformat(),
    }


async def _acoes_hostinger(client: httpx.AsyncClient, base: str, vm_id: Any) -> list[dict]:
    """Histórico de ações da Hostinger sobre a VPS (mais recentes primeiro)."""
    if not vm_id:
        return []
    resp = await client.get(f"{base}/api/vps/v1/virtual-machines/{vm_id}/actions")
    resp.raise_for_status()
    dados = resp.json()
    itens = dados.get("data") if isinstance(dados, dict) else dados
    if not isinstance(itens, list):
        return []
    return [
        {
            "nome": it.get("name"),
            "estado": it.get("state"),
            "em": it.get("created_at"),
        }
        for it in itens[:10]
        if isinstance(it, dict)
    ]


def _limitacao_de_cpu(acoes: list[dict]) -> Optional[dict]:
    """`ct_set_limits` nas últimas 24 h — a "CPU limitation" da Hostinger.

    Por que isso é o sinal mais importante do painel inteiro: a limitação é
    **auto-sustentável**. Com o teto reduzido, a carga rotineira já satura a
    fração liberada, o gráfico marca 100% para sempre e a máquina não se
    recupera sozinha — depende de alguém remover no painel da Hostinger. Foi
    isso que fez o incidente de 11/09 durar ~20 h em vez de 11 minutos.
    """
    recentes = []
    limite = datetime.now(timezone.utc) - timedelta(hours=24)
    for a in acoes:
        if a.get("nome") != "ct_set_limits":
            continue
        try:
            quando = datetime.fromisoformat((a.get("em") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if quando >= limite:
            recentes.append(quando)
    if not recentes:
        return None
    recentes.sort()
    return {
        "ocorrencias_24h": len(recentes),
        "ultima_em": recentes[-1].isoformat(),
        "explicacao": (
            "A Hostinger aplicou limitação de CPU nesta VPS. Com o teto "
            "reduzido, a carga normal já satura a fração liberada e a máquina "
            "NÃO se recupera sozinha — é preciso remover a limitação no painel "
            "da Hostinger. Foi o que prolongou o apagão de 11/09 por ~20 h."
        ),
    }


# ───────────────── cruzamento: onde o Coolify discorda de nós ────────────


def _host_do_redis() -> Optional[str]:
    """Hostname do `REDIS_URL` — que dentro da rede do Coolify é o UUID do
    recurso (`redis://…@h0cw0gc8owws004480g0sog8:6379/0`). É o que permite
    dizer QUAL das duas instâncias de Redis é a nossa."""
    url = settings.REDIS_URL or ""
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname
    except Exception:  # noqa: BLE001
        return None


def _cruzar(recursos: list[dict], pontas: list[dict], filas: dict) -> None:
    """Marca, recurso por recurso, onde a medição contradiz o Coolify.

    **É a razão de este painel existir.** Em 11/09/2026 o Coolify mostrou
    `running:healthy` durante horas com a API inalcançável, e um painel que
    só repetisse aquele verde teria atrasado o diagnóstico em vez de
    encurtá-lo. Aqui as duas fontes aparecem juntas e a divergência é dita em
    voz alta.

    A contradição vale nos DOIS sentidos, e o segundo apareceu em 15/09 na
    primeira execução real: o Coolify marcava as duas instâncias de Redis
    como `exited:unhealthy` enquanto o `/health` de produção E de homologação
    respondia `redis: connected`. Pintar de vermelho o que está funcionando
    também ensina a ignorar o painel.
    """
    por_ambiente_tipo = {(p["ambiente"], p["tipo"]): p for p in pontas}
    redis_conectado = [
        p["ambiente"]
        for p in pontas
        if (p.get("saude_interna") or {}).get("redis") == "connected"
    ]
    host_redis = _host_do_redis()

    for r in recursos:
        no_ar = r["estado"] == "running"

        if r["papel"] in ("api", "frontend"):
            ponta = por_ambiente_tipo.get((r["ambiente"], r["papel"]))
            if ponta is None:
                continue
            if no_ar and not ponta["ok"]:
                r["contradicao"] = (
                    f"O Coolify vê o container de pé, mas {ponta['url']} não responde "
                    f"({ponta['detalhe']}). É o modo de falha de 11/09: healthcheck "
                    "interno não vê rota de proxy quebrada."
                )
            elif not no_ar and ponta["ok"]:
                r["contradicao"] = (
                    f"O Coolify diz `{r['status']}`, mas {ponta['url']} respondeu em "
                    f"{ponta['latencia_ms']} ms agora."
                )

        elif r["papel"] == "redis" and not no_ar:
            provas = []
            if r["uuid"] and host_redis == r["uuid"] and filas.get("ping_ms") is not None:
                provas.append(f"respondeu ping em {filas['ping_ms']} ms para esta API")
            if redis_conectado:
                provas.append(
                    "o /health de " + " e ".join(sorted(set(redis_conectado)))
                    + " diz `redis: connected`"
                )
            if provas:
                r["contradicao"] = (
                    f"O Coolify diz `{r['status']}`, mas " + "; ".join(provas) + "."
                )


# ────────────────────────────── orquestração ─────────────────────────────


async def coletar() -> dict:
    """O painel inteiro. Bloco que falhar leva o erro dele, e só ele."""
    coolify, hostinger, pontas = await asyncio.gather(
        coletar_coolify(),
        coletar_hostinger(),
        coletar_pontas(),
    )
    # Redis é síncrono e rápido (ping + LLEN); roda fora do loop para não
    # bloquear o event loop por mais do que o timeout do socket.
    filas = await asyncio.to_thread(coletar_filas)
    _cruzar(coolify["recursos"], pontas, filas)
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "somente_leitura": True,
        "coolify": coolify,
        "hostinger": hostinger,
        "pontas": pontas,
        "filas": filas,
    }
