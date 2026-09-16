import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app import mongo_client, redis_client
from app.main import app


@pytest.fixture()
def fake_redis(monkeypatch):
    # No DUMP_URL/DUMP_PATH set - main.py's lifespan hook leaves Mongo/Redis
    # untouched on startup (it returns before reaching either client), so
    # it's safe to point every test at an isolated fake Redis instance
    # regardless.
    monkeypatch.delenv("DUMP_URL", raising=False)
    monkeypatch.delenv("DUMP_PATH", raising=False)

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_client.set_redis(fake)
    yield fake
    redis_client.set_redis(None)


@pytest.fixture()
def fake_mongo(monkeypatch):
    monkeypatch.delenv("DUMP_URL", raising=False)
    monkeypatch.delenv("DUMP_PATH", raising=False)

    fake = AsyncMongoMockClient().get_database("aodb_test")
    mongo_client.set_mongo(fake)
    yield fake
    mongo_client.set_mongo(None)


@pytest.fixture()
def client(fake_redis, fake_mongo):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def no_cache(monkeypatch):
    """Store-level tests care about Mongo-backed correctness, not the
    Redis cache-aside layer in front of search()/count() (see
    app/store.py's _cached_json) - a test that calls search()/count() with
    the same args before and after a write would otherwise see a stale
    cached result within the TTL. Makes get_redis() fail immediately so
    _cached_json's fallback path (log + skip caching) kicks in
    deterministically, without depending on nothing actually listening on
    the default Redis URL. The cache-aside behavior itself is covered
    directly in test_cache.py."""

    def _raise() -> None:
        raise RuntimeError("Redis disabled for this test")

    monkeypatch.setattr("app.store.get_redis", _raise)
