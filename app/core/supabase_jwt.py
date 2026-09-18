"""Verificação LOCAL do JWT emitido pelo Supabase Auth.

Incidente de 18/09/2026: dos 6.622 requests de Auth em 24h, 5.895 eram
`GET /auth/v1/user` — a API chamando `supabase.auth.get_user(token)` em TODA
requisição autenticada. Cada chamada dessas bate no Postgres do Supabase.
Quando o banco engasgou (disco no mínimo, fila de travas dos cliques), não foi
só o login que caiu: toda requisição de quem JÁ ESTAVA logada falhou também,
porque validar o token dependia do banco.

Aqui o token é verificado com a chave pública do projeto (JWKS, ES256/RS256)
ou, para projetos ainda no formato antigo, com o segredo HS256. Sem chamada de
rede por requisição: o JWKS é baixado uma vez e guardado em memória.

Ordem de tentativa:
1. JWKS (`SUPABASE_JWKS_URL` ou derivado de `SUPABASE_URL`) — só quando o
   token traz `kid`/`alg` assimétrico.
2. `SUPABASE_JWT_SECRET` (HS256) — formato legado.
3. Nada configurado → `TokenNaoVerificavelLocalmente`, e quem chama cai no
   `auth.get_user` de antes. Assim o deploy é seguro mesmo antes de a variável
   existir no Coolify.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

AUDIENCIA = "authenticated"
_JWKS_TTL_S = 600  # 10 min; rotação de chave no Supabase mantém a antiga por dias


class TokenInvalido(Exception):
    """Assinatura, expiração ou audiência inválidas."""


class TokenNaoVerificavelLocalmente(Exception):
    """Não há chave configurada para verificar este token localmente."""


class _CacheJWKS:
    def __init__(self) -> None:
        self._chaves: dict[str, dict] = {}
        self._baixado_em: float = 0.0
        self._lock = threading.Lock()

    def url(self) -> Optional[str]:
        if settings.SUPABASE_JWKS_URL:
            return settings.SUPABASE_JWKS_URL
        if settings.SUPABASE_URL:
            return settings.SUPABASE_URL.rstrip("/") + "/auth/v1/.well-known/jwks.json"
        return None

    def _baixar(self) -> None:
        import httpx

        url = self.url()
        if not url:
            return
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        dados = resp.json()
        chaves = {k["kid"]: k for k in dados.get("keys", []) if k.get("kid")}
        with self._lock:
            self._chaves = chaves
            self._baixado_em = time.monotonic()
        logger.info("JWKS do Supabase carregado (%d chave(s))", len(chaves))

    def chave(self, kid: str) -> Optional[dict]:
        expirado = (time.monotonic() - self._baixado_em) > _JWKS_TTL_S
        if kid not in self._chaves or expirado:
            try:
                self._baixar()
            except Exception as exc:  # rede fora → usa o que tem em memória
                logger.warning("Falha ao baixar JWKS do Supabase: %s", exc)
        return self._chaves.get(kid)

    def limpar(self) -> None:
        with self._lock:
            self._chaves = {}
            self._baixado_em = 0.0


_jwks = _CacheJWKS()


def _decodificar(token: str, chave: Any, algoritmos: list[str]) -> dict:
    try:
        return jwt.decode(
            token,
            chave,
            algorithms=algoritmos,
            audience=AUDIENCIA,
            options={"verify_sub": False, "leeway": 30},
        )
    except JWTError as exc:
        raise TokenInvalido(str(exc)) from exc


def verificar_token(token: str) -> dict:
    """Devolve as claims do token ou levanta TokenInvalido /
    TokenNaoVerificavelLocalmente. Nunca faz chamada ao GoTrue."""
    if not token or token.count(".") != 2:
        raise TokenInvalido("formato inválido")
    try:
        cabecalho = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise TokenInvalido(f"cabeçalho inválido: {exc}") from exc

    alg = (cabecalho.get("alg") or "").upper()
    kid = cabecalho.get("kid")

    # 1) Chave assimétrica (novo padrão do Supabase: ES256; RS256 também aceito)
    if alg in ("ES256", "RS256") and kid:
        jwk = _jwks.chave(kid)
        if jwk is None:
            if _jwks.url() is None:
                raise TokenNaoVerificavelLocalmente("JWKS não configurado")
            raise TokenInvalido(f"kid {kid} não encontrado no JWKS")
        return _decodificar(token, jwk, [alg])

    # 2) Segredo compartilhado (formato legado do Supabase)
    if alg == "HS256":
        segredo = settings.SUPABASE_JWT_SECRET
        if not segredo:
            raise TokenNaoVerificavelLocalmente("SUPABASE_JWT_SECRET ausente")
        return _decodificar(token, segredo, ["HS256"])

    raise TokenInvalido(f"algoritmo não suportado: {alg or '?'}")


def email_das_claims(claims: dict) -> Optional[str]:
    email = claims.get("email")
    if not email:
        email = (claims.get("user_metadata") or {}).get("email")
    return email
