from redis.asyncio import Redis

from app import redis_client


def test_get_redis_lazily_constructs_a_real_client_from_default_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_client.set_redis(None)
    try:
        client = redis_client.get_redis()
        assert isinstance(client, Redis)
        assert client.connection_pool.connection_kwargs["host"] == "localhost"
        assert client.connection_pool.connection_kwargs["port"] == 6379
        # Constructing doesn't connect - get_redis() must not require a
        # real Redis server to be reachable.
        assert redis_client.get_redis() is client  # cached, not rebuilt
    finally:
        redis_client.set_redis(None)


def test_get_redis_honors_redis_url_env_var(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example-redis:6380/2")
    redis_client.set_redis(None)
    try:
        client = redis_client.get_redis()
        assert client.connection_pool.connection_kwargs["host"] == "example-redis"
        assert client.connection_pool.connection_kwargs["port"] == 6380
        assert client.connection_pool.connection_kwargs["db"] == 2
    finally:
        redis_client.set_redis(None)
