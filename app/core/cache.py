import json
from typing import Any, Optional

import redis

from app.core.config import settings


_client: Optional[redis.Redis] = None


def get_client() -> Optional[redis.Redis]:
    global _client
    if _client is not None:
        return _client
    if not settings.REDIS_URL:
        return None
    _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def cache_get(key: str) -> Optional[Any]:
    client = get_client()
    if client is None:
        return None
    data = client.get(key)
    if data is None:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    client = get_client()
    if client is None:
        return
    payload = json.dumps(value, ensure_ascii=False, default=str)
    client.setex(key, ttl or settings.CACHE_TTL_SECONDS, payload)


def cache_delete_prefix(prefix: str) -> None:
    client = get_client()
    if client is None:
        return
    cursor = 0
    pattern = f"{prefix}*"
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=500)
        if keys:
            client.delete(*keys)
        if cursor == 0:
            break
