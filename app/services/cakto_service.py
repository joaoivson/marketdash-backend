import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.core.config import settings


class CaktoError(RuntimeError):
    pass


_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0.0}


def _parse_product_ids() -> List[str]:
    raw = settings.CAKTO_SUBSCRIPTION_PRODUCT_IDS or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _get_token() -> str:
    if not settings.CAKTO_CLIENT_ID or not settings.CAKTO_CLIENT_SECRET:
        raise CaktoError("Cakto credentials not configured")

    now = time.time()
    cached = _token_cache.get("token")
    expires_at = float(_token_cache.get("expires_at") or 0)
    if cached and now < (expires_at - 60):
        return cached

    url = f"{settings.CAKTO_API_BASE}/public_api/token/"
    resp = requests.post(
        url,
        data={
            "client_id": settings.CAKTO_CLIENT_ID,
            "client_secret": settings.CAKTO_CLIENT_SECRET,
        },
        timeout=10,
    )
    if resp.status_code >= 400:
        raise CaktoError(f"Failed to get Cakto token: {resp.status_code}")

    data = resp.json() if resp.content else {}
    token = data.get("access_token") or data.get("token")
    if not token:
        raise CaktoError("Cakto token missing in response")

    expires_in = int(data.get("expires_in") or 3600)
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token


def _extract_orders(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "orders"):
            if isinstance(payload.get(key), list):
                return payload.get(key)
    return []


def _is_subscription(order: Dict[str, Any]) -> bool:
    if str(order.get("type", "")).lower() == "subscription":
        return True
    offer = order.get("offer") or {}
    if str(offer.get("type", "")).lower() == "subscription":
        return True
    return False


def _is_active(order: Dict[str, Any]) -> bool:
    status = str(order.get("status", "")).lower()
    payment_status = str(order.get("payment_status", "")).lower()
    subscription_status = str(order.get("subscription_status", "")).lower()
    return status in {"approved", "paid", "active"} or payment_status in {"approved", "paid"} or subscription_status in {"active", "approved"}


def _matches_product(order: Dict[str, Any], allowed_ids: List[str]) -> bool:
    if not allowed_ids:
        return True
    candidates = []
    if order.get("product_id"):
        candidates.append(str(order.get("product_id")))
    product = order.get("product") or {}
    if product.get("id"):
        candidates.append(str(product.get("id")))
    return any(c in allowed_ids for c in candidates)


def check_active_subscription(email: str) -> Tuple[bool, Optional[str]]:
    token = _get_token()
    url = f"{settings.CAKTO_API_BASE}/public_api/orders/"
    params = {"email": email, "type": "subscription"}
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    if resp.status_code >= 400:
        raise CaktoError(f"Cakto orders error: {resp.status_code}")

    payload = resp.json() if resp.content else {}
    orders = _extract_orders(payload)
    allowed_ids = _parse_product_ids()

    for order in orders:
        if _is_subscription(order) and _is_active(order) and _matches_product(order, allowed_ids):
            return True, None

    return False, "Assinatura ativa não encontrada"
