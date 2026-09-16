"""Lazily-constructed shared Mongo database handle. A module-level singleton
(rather than e.g. FastAPI dependency injection) because ItemStore/NanoStore
are themselves module-level singletons (see store.py) constructed before the
app - and because tests need to swap in a fake database via set_mongo()."""

import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

_db: AsyncIOMotorDatabase | None = None


def get_mongo() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        url = os.environ.get("MONGO_URL", "mongodb://localhost:27017/aodb")
        _db = AsyncIOMotorClient(url).get_default_database()
    return _db


def set_mongo(db: AsyncIOMotorDatabase | None) -> None:
    """Test hook: inject a fake/alternate database, or None to reset to
    lazy-default construction on next get_mongo() call."""
    global _db
    _db = db
