import hashlib
import json
import logging
import time
from typing import Any, Optional

import httpx
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

SHOPEE_GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"
REQUEST_TIMEOUT = 30.0

# Códigos de erro da Shopee que NÃO adiantam retentar: a credencial do usuário está
# inválida/revogada e só volta a funcionar quando ele reconectar a conta. Sem isso, cada
# cron horário vira 1 tentativa + 3 retries de 5 em 5 min pra sempre — era a origem da
# maior parte dos "erros de sync" do painel (2 contas geravam ~106 falhas/dia sozinhas).
SHOPEE_PERMANENT_ERROR_CODES = {10020}


class ShopeePermanentError(Exception):
    """Erro da Shopee que não se resolve sozinho (credencial inválida/revogada).

    Sinaliza pro Celery NÃO reagendar: retentar só gera ruído e carga.
    """

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def _build_headers(app_id: str, secret: str, payload_str: str) -> dict:
    """
    Monta os headers de autenticação para a Shopee Affiliate Open API.
    Formato: SHA256 Credential={AppId}, Timestamp={ts}, Signature={sig}
    Signature = SHA256(AppId + Timestamp + PayloadString + Secret)
    """
    timestamp = str(int(time.time()))
    raw = f"{app_id}{timestamp}{payload_str}{secret}"
    signature = hashlib.sha256(raw.encode()).hexdigest()
    return {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={app_id}, Timestamp={timestamp}, Signature={signature}",
    }


async def execute_graphql(
    app_id: str,
    password: str,
    query: str,
    variables: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Executa uma query GraphQL contra a Shopee Affiliate Open API.
    Retorna dict com chave 'data' (ou vazio) e 'errors' (None ou lista).
    Lança HTTPException em caso de falha de rede ou erro GraphQL.
    """
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    # A assinatura inclui o payload serializado
    payload_str = json.dumps(payload)
    headers = _build_headers(app_id, password, payload_str)

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                SHOPEE_GRAPHQL_URL,
                content=payload_str,
                headers=headers,
            )
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        logger.error("Shopee API timeout: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Timeout ao conectar com a API da Shopee.",
        )
    except httpx.HTTPStatusError as exc:
        logger.error("Shopee API HTTP error %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erro HTTP {exc.response.status_code} da API Shopee.",
        )
    except httpx.RequestError as exc:
        logger.error("Shopee API request error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Erro de conexão com a API da Shopee.",
        )

    body = response.json()
    if "errors" in body and body["errors"]:
        logger.warning("Shopee GraphQL errors: %s", body["errors"])
        codes = set()
        for err in body["errors"]:
            if not isinstance(err, dict):
                continue
            ext = err.get("extensions")
            code = ext.get("code") if isinstance(ext, dict) else None
            if code is None:
                code = err.get("code")
            try:
                codes.add(int(code))
            except (TypeError, ValueError):
                continue
        permanent = codes & SHOPEE_PERMANENT_ERROR_CODES
        if permanent:
            raise ShopeePermanentError(
                f"Credencial Shopee inválida (código {sorted(permanent)[0]}). "
                "É preciso reconectar a conta Shopee — retentar não resolve.",
                code=sorted(permanent)[0],
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erros GraphQL Shopee: {body['errors']}",
        )

    return {"data": body.get("data"), "errors": None}
