# Imagem do backend — construída no GitHub Actions, NUNCA no VPS.
#
# Por que este arquivo mudou (16/09/2026). O Coolify construía a imagem dentro
# do VPS que serve produção (4 vCPU, compartilhado com homologação, WAHA, dois
# Redis, Traefik e o próprio Coolify). Isso derrubou produção duas vezes em
# cinco dias: em 11/09 cinco `docker build` simultâneos levaram a CPU a 100%, a
# Hostinger aplicou teto de 20% e a API ficou ~20h inalcançável; em 16/09, sob
# esse teto, um `pip install` levou 16 minutos, morreu com exit 255, e a
# tentativa deixou a máquina com load 32 e steal 94% por 40 minutos — no
# início do expediente.
#
# Agora o build acontece no runner (CPU de graça) e o VPS só PUXA a imagem
# pronta: segundos de rede contra 5-10 minutos de CPU.
#
# ## Dois targets, uma base
#
# `worker` e `api` partem do mesmo stage `base` — mesmas dependências, mesmo
# código, mesmas camadas no registry. Antes eram dois arquivos (`Dockerfile` e
# `Dockerfile.worker`) e eles já tinham divergido: o do worker não tinha o
# retry do pip nem o gunicorn. Duas imagens que deveriam ser irmãs virando
# primas distantes é como o worker roda código diferente da API sem ninguém
# perceber.
#
# ⚠️ `api` é o ÚLTIMO stage DE PROPÓSITO. `docker build` sem `--target`
# constrói o último — então, enquanto alguma app ainda estiver no modo antigo
# (build no VPS a partir do git), ela continua produzindo a imagem da API, e
# não a do worker. É a ponte que torna a migração reversível.

# ── Estágio de compilação: gcc só existe aqui ────────────────────────────────
FROM python:3.11-slim AS builder

# gcc é necessário para as extensões C de algumas dependências (psycopg2,
# polars). Ele pesa ~250 MB e NÃO vai para a imagem final — é essa separação
# que derruba a imagem de 1,37 GB para ~450 MB, e imagem menor é pull mais
# rápido no VPS, que é o ponto de toda esta mudança.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# venv isolado: copiar um diretório inteiro para o runtime é mais previsível do
# que garimpar site-packages espalhado pelo sistema.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
# Retry no pip por causa de "Broken pipe" — herdado do tempo em que o build
# rodava no VPS com rede sofrível. No runner é raro, mas custa nada.
# `gunicorn[gevent]` já está em requirements.txt; o segundo `pip install` que
# existia aqui era redundante e saiu.
RUN for i in 1 2 3; do pip install --no-cache-dir --retries 5 --timeout 120 -r requirements.txt && break; done

# ── Base comum de runtime ────────────────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

# `curl` é o HEALTHCHECK da API; `postgresql-client` é usado em runtime por
# app/api/v1/routes/admin_proxies.py. gcc NÃO entra aqui.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY . .

# ARG DEPOIS do COPY, e isso importa: o SHA muda a cada commit, então declarar
# antes invalidaria o cache de dependências em todo build. Aqui ele só
# invalida a última camada, que é barata.
#
# `APP_VERSION` é o que o /health devolve. Sem ele, "CI verde" continuaria
# sendo a única prova de deploy — e já falhou três vezes dizendo que o código
# novo estava no ar quando não estava.
ARG GIT_SHA=dev
ENV APP_VERSION=$GIT_SHA

LABEL org.opencontainers.image.source=https://github.com/joaoivson/marketdash-backend \
      org.opencontainers.image.revision=$GIT_SHA

EXPOSE 8000

# ── Worker Celery ────────────────────────────────────────────────────────────
FROM base AS worker

# uid 1000 fixo: o Celery recusa rodar como root e o `--uid=1000` do
# entrypoint precisa casar com este usuário.
RUN adduser --disabled-password --gecos "" --uid 1000 celeryuser \
    && chmod +x /app/scripts/worker-entrypoint.sh

# HOME explícito corrige "postgresql.crt: Permission denied" do libpq.
#
# O par DB_POOL_SIZE/DB_MAX_OVERFLOW mora AQUI, junto da concorrência que ele
# dimensiona: cada processo do worker abre o próprio pool, então o total de
# conexões no Supabase é concorrência × (pool + overflow). Separar os dois em
# arquivos diferentes é como alguém sobe um sem o outro e o banco recusa
# conexão numa segunda-feira.
ENV HOME=/home/celeryuser \
    DB_POOL_SIZE=2 \
    DB_MAX_OVERFLOW=3

CMD ["/app/scripts/worker-entrypoint.sh"]

# ── API (ÚLTIMO stage — ver o aviso no topo) ─────────────────────────────────
FROM base AS api

# Healthcheck interno, de dentro do container. Ele NÃO enxerga rota de proxy
# quebrada (em 11/09 dizia "healthy" com a API inalcançável há horas) — quem
# cobre isso é a sonda externa em .github/workflows/monitor-producao.yml. Este
# aqui serve ao rolling update do Coolify: é o que segura o container novo
# até ele responder, para o swap não derrubar a API por ~20s.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["gunicorn", "app.main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]
