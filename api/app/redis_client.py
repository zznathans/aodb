"""Lazily-constructed shared Redis client. A module-level singleton (rather
than e.g. FastAPI dependency injection) because ItemStore/NanoStore are
themselves module-level singletons (see store.py) constructed before the
app - and because tests need to swap in a fake client via set_redis()."""

import os

from redis.asyncio import Redis

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _client = Redis.from_url(url, decode_responses=True)
    return _client


def set_redis(client: Redis | None) -> None:
    """Test hook: inject a fake/alternate client, or None to reset to
    lazy-default construction on next get_redis() call."""
    global _client
    _client = client
