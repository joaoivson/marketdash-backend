"""Que ambiente é este? — derivado do BANCO, nunca de `ENVIRONMENT`.

`settings.ENVIRONMENT` reporta `"development"` em produção **e** em
homologação, então não serve para distinguir os dois (mesma armadilha
documentada em `app/tasks/celery_app.py`). O que identifica o ambiente sem
ambiguidade é a **ref do projeto Supabase** dentro do `DATABASE_URL`.

Este módulo é a fonte única dessa extração: `celery_app._fila_do_banco()`
usa a mesma função, então o nome da fila e a detecção de ambiente nunca
divergem.
"""

import hashlib
import re

from app.core.config import settings

# Refs dos dois projetos Supabase. Homologação ganhou projeto próprio em
# 25/07 — antes disso os dois ambientes dividiam o mesmo banco, que foi
# como o cron horário derrubou produção junto.
REF_HOMOLOGACAO = "ytjpdvjuxtvxacredekk"
REF_PRODUCAO = "iprdyorxqdiivthtcvxf"

_REF_NA_URL = re.compile(r"(?:db\.)?([a-z0-9]{20})\.supabase|postgres\.([a-z0-9]{20})")


def identidade_do_banco(url: str | None = None) -> str:
    """Ref do projeto Supabase no `DATABASE_URL`, ou um hash estável da URL.

    O hash é o caso do Postgres local (dev/teste/docker-compose), que não tem
    ref — e que por isso nunca é confundido com produção nem com homologação.
    """
    url = settings.DATABASE_URL or "" if url is None else url
    achado = _REF_NA_URL.search(url)
    if achado:
        return achado.group(1) or achado.group(2)
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def is_homologacao(url: str | None = None) -> bool:
    """True só no ambiente de homologação — identificado pela ref do banco.

    Deliberadamente restritivo: dev local e produção respondem False. Um gate
    que ligasse por engano em produção seria bem pior do que um que não liga
    em homologação.
    """
    return identidade_do_banco(url) == REF_HOMOLOGACAO


def is_producao(url: str | None = None) -> bool:
    return identidade_do_banco(url) == REF_PRODUCAO
