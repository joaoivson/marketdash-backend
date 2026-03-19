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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erros GraphQL Shopee: {body['errors']}",
        )

    return {"data": body.get("data"), "errors": None}
