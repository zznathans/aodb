"""Covers the Redis cache-aside layer in front of ItemStore/NanoStore's
search()/count() (see app/store.py's _cached_json) - Mongo is the actual
source of truth (see other test_*_store.py modules), Redis here is purely
an optional accelerator that must never make a request fail or return
wrong data outright, only possibly stale data within its short TTL."""

from app.store import ItemStore, make_item


async def test_search_result_is_served_from_cache_within_ttl(fake_redis, fake_mongo):
    store = ItemStore()
    await store.load([make_item(id=1, name="Notum Tank Armor", ql=200)])
    assert len(await store.search(query="notum", ql=0, limit=50)) == 1

    # A second matching item added straight to Mongo, bypassing load() -
    # the point is only to prove the *previous* call's cached result is
    # what's served back on an identical call, not to exercise indexing.
    await store._collection().insert_one(store._item_to_doc(make_item(id=2, name="Notum Splitter", ql=150)))

    same_args_again = await store.search(query="notum", ql=0, limit=50)
    assert len(same_args_again) == 1  # stale (cached) result, not re-queried against Mongo

    # A query string that was never cached still hits Mongo directly and
    # sees the item written above.
    assert await store.count(query="splitter", ql=0) == 1


async def test_redis_read_failure_falls_back_to_mongo(fake_redis, fake_mongo, monkeypatch):
    store = ItemStore()
    await store.load([make_item(id=1, name="Notum Tank Armor", ql=200)])

    async def boom(_key):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(fake_redis, "get", boom)

    # Must still return the right answer even though every cache read blows up.
    assert len(await store.search(query="notum", ql=0, limit=50)) == 1
    assert await store.count(query="notum", ql=0) == 1


async def test_redis_write_failure_does_not_break_the_request(fake_redis, fake_mongo, monkeypatch):
    store = ItemStore()
    await store.load([make_item(id=1, name="Notum Tank Armor", ql=200)])

    async def boom(*_args, **_kwargs):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(fake_redis, "set", boom)

    assert len(await store.search(query="notum", ql=0, limit=50)) == 1
