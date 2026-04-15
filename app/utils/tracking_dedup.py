import hashlib
import logging

from app.core.cache import get_client

logger = logging.getLogger(__name__)


def should_count(
    namespace: str,
    entity_id: str | int,
    ip: str | None,
    user_agent: str | None,
    window_seconds: int = 60,
) -> bool:
    client = get_client()
    if client is None:
        return True

    fingerprint_src = f"{ip or ''}|{user_agent or ''}".encode("utf-8")
    fingerprint = hashlib.sha1(fingerprint_src).hexdigest()[:16]
    key = f"trackdedup:{namespace}:{entity_id}:{fingerprint}"

    try:
        created = client.set(key, "1", nx=True, ex=window_seconds)
        return bool(created)
    except Exception as e:
        if "Authentication" in str(e) or "AuthenticationError" in str(type(e).__name__):
            return True
        logger.warning(f"Dedup Redis error: {e}")
        return True
