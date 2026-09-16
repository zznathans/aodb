from motor.motor_asyncio import AsyncIOMotorDatabase

from app import mongo_client


def test_get_mongo_lazily_constructs_a_real_database_from_default_url(monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    mongo_client.set_mongo(None)
    try:
        db = mongo_client.get_mongo()
        assert isinstance(db, AsyncIOMotorDatabase)
        assert db.name == "aodb"
        # Constructing doesn't connect - get_mongo() must not require a
        # real Mongo server to be reachable.
        assert mongo_client.get_mongo() is db  # cached, not rebuilt
    finally:
        mongo_client.set_mongo(None)


def test_get_mongo_honors_mongo_url_env_var(monkeypatch):
    monkeypatch.setenv("MONGO_URL", "mongodb://example-mongo:27018/example_db")
    mongo_client.set_mongo(None)
    try:
        db = mongo_client.get_mongo()
        assert db.name == "example_db"
    finally:
        mongo_client.set_mongo(None)
