import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app import redis_client
from app.main import app


@pytest.fixture()
def fake_redis(monkeypatch):
    # No DUMP_URL/DUMP_PATH set - main.py's lifespan hook leaves Redis empty
    # on startup and never touches the client, so it's safe to point every
    # test at an isolated fake Redis instance regardless.
    monkeypatch.delenv("DUMP_URL", raising=False)
    monkeypatch.delenv("DUMP_PATH", raising=False)

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_client.set_redis(fake)
    yield fake
    redis_client.set_redis(None)


@pytest.fixture()
def client(fake_redis):
    with TestClient(app) as test_client:
        yield test_client
