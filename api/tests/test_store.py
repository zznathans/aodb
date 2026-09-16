import pytest
from pymongo.errors import BulkWriteError

from app.store import _BATCH_SIZE, ItemStore, make_item


async def test_search_matches_substring_case_insensitively(fake_mongo, no_cache):
    store = ItemStore()
    await store.load([make_item(id=1, name="Notum Tank Armor", ql=200)])

    assert len(await store.search(query="notum", ql=0, limit=50)) == 1
    assert len(await store.search(query="NOTUM", ql=0, limit=50)) == 1
    assert len(await store.search(query="tank", ql=0, limit=50)) == 1  # mid-string, not just a prefix
    assert len(await store.search(query="Armor", ql=0, limit=50)) == 1  # suffix
    assert len(await store.search(query="nope", ql=0, limit=50)) == 0


async def test_search_matches_short_substrings_below_the_trigram_window(fake_mongo, no_cache):
    store = ItemStore()
    await store.load([make_item(id=1, name="Notum Tank Armor", ql=200)])

    # Below the 3-char trigram window (see module docstring) - exercises
    # the full-scan fallback path instead of the trigram index.
    assert len(await store.search(query="an", ql=0, limit=50)) == 1
    assert len(await store.search(query="a", ql=0, limit=50)) == 1
    assert len(await store.search(query="zz", ql=0, limit=50)) == 0


async def test_search_blank_query_filters_by_ql_alone(fake_mongo, no_cache):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200),
            make_item(id=2, name="Notum Splitter", ql=150),
        ]
    )

    results = await store.search(query="", ql=200, limit=50)
    assert [i.id for i in results] == [1]


async def test_search_blank_query_filters_by_category_alone(fake_mongo, no_cache):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200, category="armor"),
            make_item(id=2, name="Notum Splitter", ql=150, category="general"),
        ]
    )

    results = await store.search(query="", ql=0, category="armor", limit=50)
    assert [i.id for i in results] == [1]


async def test_search_blank_query_filters_by_category_and_ql(fake_mongo, no_cache):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200, category="armor"),
            make_item(id=2, name="Notum Chest Armor", ql=150, category="armor"),
            make_item(id=3, name="Notum Splitter", ql=200, category="general"),
        ]
    )

    results = await store.search(query="", ql=200, category="armor", limit=50)
    assert [i.id for i in results] == [1]


async def test_search_filters_by_ql(fake_mongo, no_cache):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200),
            make_item(id=2, name="Notum Splitter", ql=150),
        ]
    )

    results = await store.search(query="notum", ql=200, limit=50)
    assert [i.id for i in results] == [1]


async def test_search_respects_limit_and_sorts_by_name(fake_mongo, no_cache):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Item Zeta"),
            make_item(id=2, name="Item Alpha"),
            make_item(id=3, name="Item Beta"),
        ]
    )

    results = await store.search(query="Item", ql=0, limit=2)
    assert [i.name for i in results] == ["Item Alpha", "Item Beta"]


async def test_count_reflects_total_loaded(fake_mongo, no_cache):
    store = ItemStore()
    assert await store.count(query="", ql=0) == 0

    await store.load([make_item(id=1, name="x")])
    assert await store.count(query="", ql=0) == 1


async def test_search_respects_offset(fake_mongo, no_cache):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Item Alpha"),
            make_item(id=2, name="Item Beta"),
            make_item(id=3, name="Item Gamma"),
        ]
    )

    results = await store.search(query="Item", ql=0, limit=2, offset=1)
    assert [i.name for i in results] == ["Item Beta", "Item Gamma"]


async def test_count_reflects_total_matches_not_limit(fake_mongo, no_cache):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Item Alpha"),
            make_item(id=2, name="Item Beta"),
            make_item(id=3, name="Item Gamma"),
        ]
    )

    assert await store.count(query="Item", ql=0) == 3
    assert len(await store.search(query="Item", ql=0, limit=1)) == 1


async def test_get_returns_item_by_id_or_none(fake_mongo, no_cache):
    store = ItemStore()
    await store.load([make_item(id=42, name="Notum Tank Armor")])

    item = await store.get(42)
    assert item.name == "Notum Tank Armor"
    assert await store.get(999) is None


async def test_list_ids_returns_every_id_regardless_of_name(fake_mongo, no_cache):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Zeta Item"),
            make_item(id=2, name="Alpha Item"),
            make_item(id=3, name="Middle Item"),
        ]
    )

    ids = await store.list_ids(limit=50)
    assert set(ids) == {1, 2, 3}


async def test_list_ids_respects_limit_and_offset(fake_mongo, no_cache):
    store = ItemStore()
    await store.load([make_item(id=i, name=f"Item{i}") for i in range(1, 6)])

    first_page = await store.list_ids(limit=2, offset=0)
    second_page = await store.list_ids(limit=2, offset=2)
    assert len(first_page) == 2
    assert len(second_page) == 2
    assert set(first_page) & set(second_page) == set()


async def test_load_does_not_flush_existing_data(fake_mongo, no_cache):
    store = ItemStore()
    await store.load([make_item(id=1, name="Old Item")])
    await store.load([make_item(id=2, name="New Item")])

    assert await store.get(1) is not None
    assert await store.get(2) is not None


async def test_load_skips_ids_that_already_exist(fake_mongo, no_cache):
    store = ItemStore()
    await store.load([make_item(id=1, name="Original Name", ql=100)])
    await store.load([make_item(id=1, name="Changed Name", ql=200)])

    item = await store.get(1)
    assert item.name == "Original Name"
    assert item.ql == 100


async def test_load_reraises_non_duplicate_key_bulk_write_errors(fake_mongo, no_cache, monkeypatch):
    store = ItemStore()

    # _collection() builds a new proxy object on every call (verified
    # against mongomock-motor) - pin it to always return this one instance
    # so the insert_many patch below is actually visible to load().
    real_collection = store._collection()
    monkeypatch.setattr(store, "_collection", lambda: real_collection)

    async def boom(_docs, ordered=False):
        raise BulkWriteError(
            {"writeErrors": [{"index": 0, "code": 12345, "errmsg": "not a duplicate key"}], "nInserted": 0}
        )

    monkeypatch.setattr(real_collection, "insert_many", boom)

    with pytest.raises(BulkWriteError):
        await store.load([make_item(id=1, name="x")])


async def test_insert_items_builds_docs_lazily_batch_at_a_time(fake_mongo, no_cache, monkeypatch):
    """insert_items() used to build a Mongo doc (with its trigrams array -
    see _trigrams) for every incoming item upfront, in one list
    comprehension, before writing any of them - on top of the already-
    resident parsed Item objects, that doubled peak memory for the full
    dump and OOMKilled a real pod mid-load. Confirms doc construction now
    stays interleaved with each batch's insert instead of running ahead
    of it."""
    store = ItemStore()
    items = [make_item(id=i, name=f"Item {i}") for i in range(1, _BATCH_SIZE * 2 + 5)]

    doc_calls = {"n": 0}
    real_item_to_doc = store._item_to_doc

    def counting_item_to_doc(item):
        doc_calls["n"] += 1
        return real_item_to_doc(item)

    monkeypatch.setattr(store, "_item_to_doc", counting_item_to_doc)

    # _collection() builds a new proxy object on every call (verified
    # against mongomock-motor) - pin it so the insert_many patch below is
    # actually visible to insert_items().
    real_collection = store._collection()
    monkeypatch.setattr(store, "_collection", lambda: real_collection)
    real_insert_many = real_collection.insert_many
    calls_at_first_insert = {}

    async def recording_insert_many(batch, **kwargs):
        calls_at_first_insert.setdefault("n", doc_calls["n"])
        return await real_insert_many(batch, **kwargs)

    monkeypatch.setattr(real_collection, "insert_many", recording_insert_many)

    await store.insert_items(items)

    # Only the first batch's worth of docs should exist by the time the
    # first insert_many fires - not every doc for the whole incoming list.
    assert calls_at_first_insert["n"] == _BATCH_SIZE
    assert doc_calls["n"] == len(items)


async def test_load_category_counts_reflect_the_full_incoming_list_each_time(fake_mongo, no_cache):
    store = ItemStore()
    await store.load([make_item(id=1, name="Item One", category="armor")])
    await store.load(
        [
            make_item(id=1, name="Item One", category="armor"),
            make_item(id=2, name="Item Two", category="armor"),
        ]
    )

    assert await store.category_counts() == {"armor": 2}
