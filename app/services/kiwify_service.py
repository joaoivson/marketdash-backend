"""
Serviço de integração com a API Kiwify.

Espelha a interface do cakto_service para ser intercambiável via payment_provider_service.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


class KiwifyError(RuntimeError):
    pass


_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0.0}


def _get_token() -> str:
    """Obtém bearer token via OAuth2 client credentials. Cache por 90h (expira em 96h)."""
    if not settings.KIWIFY_CLIENT_SECRET:
        raise KiwifyError("Kiwify credentials not configured (KIWIFY_CLIENT_SECRET)")

    now = time.time()
    cached = _token_cache.get("token")
    expires_at = float(_token_cache.get("expires_at") or 0)
    if cached and now < (expires_at - 3600):  # Refresh 1h antes
        return cached

    url = f"{settings.KIWIFY_API_BASE}/oauth/token"
    resp = requests.post(
        url,
        json={"client_secret": settings.KIWIFY_CLIENT_SECRET},
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise KiwifyError(f"Failed to get Kiwify token: {resp.status_code} - {resp.text}")

    data = resp.json() if resp.content else {}
    token = data.get("access_token") or data.get("token")
    if not token:
        raise KiwifyError("Kiwify token missing in response")

    # Token expira em 96h, cacheamos por 90h
    expires_in = int(data.get("expires_in") or 345600)  # 96h default
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token


def _get_headers() -> Dict[str, str]:
    """Headers padrão para requests autenticadas."""
    token = _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    if settings.KIWIFY_ACCOUNT_ID:
        headers["x-kiwify-account-id"] = settings.KIWIFY_ACCOUNT_ID
    return headers


def _parse_product_ids() -> List[str]:
    raw = settings.KIWIFY_SUBSCRIPTION_PRODUCT_IDS or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def check_active_subscription(email: str) -> Tuple[bool, Optional[str]]:
    """
    Verifica se existe assinatura ativa para o email na Kiwify.

    Usa GET /sales com status=approved e filtra por email client-side
    (API Kiwify não tem filtro por email).
    """
    headers = _get_headers()
    allowed_ids = _parse_product_ids()
    email_lower = email.strip().lower()

    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S.000")
    end_date = now.strftime("%Y-%m-%d %H:%M:%S.000")

    page = 1
    max_pages = 10  # Safety limit

    while page <= max_pages:
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "status": "approved",
            "page_number": page,
            "page_size": 100,
        }

        try:
            resp = requests.get(
                f"{settings.KIWIFY_API_BASE}/sales",
                headers=headers,
                params=params,
                timeout=15,
            )
        except requests.RequestException as e:
            raise KiwifyError(f"Kiwify API request failed: {str(e)}")

        if resp.status_code == 429:
            # Rate limited - wait and retry once
            import time as _time
            _time.sleep(2)
            resp = requests.get(
                f"{settings.KIWIFY_API_BASE}/sales",
                headers=headers,
                params=params,
                timeout=15,
            )

        if resp.status_code >= 400:
            raise KiwifyError(f"Kiwify sales error: {resp.status_code} - {resp.text}")

        data = resp.json() if resp.content else {}
        sales = data.get("data", [])

        if not sales:
            break

        for sale in sales:
            customer = sale.get("customer") or {}
            sale_email = (customer.get("email") or "").strip().lower()

            if sale_email != email_lower:
                continue

            # Check product filter
            if allowed_ids:
                product = sale.get("product") or {}
                product_id = product.get("id") or sale.get("product_id") or ""
                if product_id and product_id not in allowed_ids:
                    continue

            sale_status = (sale.get("status") or "").lower()
            if sale_status in ("approved", "paid"):
                return True, None

        # Check pagination
        pagination = data.get("pagination", {})
        total_count = pagination.get("count", 0)
        if page * 100 >= total_count:
            break
        page += 1

    return False, "Assinatura ativa não encontrada"


def get_customer_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Busca dados do cliente na Kiwify pelo email (via sales endpoint)."""
    try:
        headers = _get_headers()
        email_lower = email.strip().lower()

        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S.000")
        end_date = now.strftime("%Y-%m-%d %H:%M:%S.000")

        resp = requests.get(
            f"{settings.KIWIFY_API_BASE}/sales",
            headers=headers,
            params={
                "start_date": start_date,
                "end_date": end_date,
                "page_size": 100,
            },
            timeout=15,
        )
        if resp.status_code >= 400:
            raise KiwifyError(f"Kiwify sales error: {resp.status_code}")

        data = resp.json() if resp.content else {}
        for sale in data.get("data", []):
            customer = sale.get("customer") or {}
            if (customer.get("email") or "").strip().lower() == email_lower:
                return {
                    "customer_id": customer.get("id"),
                    "email": email,
                    "name": customer.get("name"),
                    "cpf_cnpj": customer.get("cpf") or customer.get("cnpj"),
                    "phone": customer.get("mobile"),
                }
        return None
    except KiwifyError:
        raise
    except Exception as e:
        raise KiwifyError(f"Error getting customer: {str(e)}")


def create_checkout_url(plan_id: str = "mensal", **kwargs) -> str:
    """
    Retorna URL de checkout do Kiwify.

    Kiwify não suporta pre-fill de dados no checkout, então retorna URL estática.
    """
    plans = settings.KIWIFY_PLANS
    plan = plans.get(plan_id)

    if not plan:
        # Fallback: tentar mensal
        plan = plans.get("mensal")
        if not plan:
            raise KiwifyError(f"Plano '{plan_id}' não encontrado e plano mensal não configurado")

    return plan["checkout_url"]
