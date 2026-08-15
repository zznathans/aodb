from app.store import ItemStore, make_item


async def test_search_matches_substring_case_insensitively(fake_redis):
    store = ItemStore()
    await store.load([make_item(id=1, name="Notum Tank Armor", ql=200)])

    assert len(await store.search(query="notum", ql=0, limit=50)) == 1
    assert len(await store.search(query="NOTUM", ql=0, limit=50)) == 1
    assert len(await store.search(query="tank", ql=0, limit=50)) == 1  # mid-string, not just a prefix
    assert len(await store.search(query="Armor", ql=0, limit=50)) == 1  # suffix
    assert len(await store.search(query="nope", ql=0, limit=50)) == 0


async def test_search_matches_short_substrings_below_the_trigram_window(fake_redis):
    store = ItemStore()
    await store.load([make_item(id=1, name="Notum Tank Armor", ql=200)])

    # Below the 3-char trigram window (see module docstring) - exercises
    # the full-scan fallback path instead of the trigram index.
    assert len(await store.search(query="an", ql=0, limit=50)) == 1
    assert len(await store.search(query="a", ql=0, limit=50)) == 1
    assert len(await store.search(query="zz", ql=0, limit=50)) == 0


async def test_search_blank_query_filters_by_ql_alone(fake_redis):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200),
            make_item(id=2, name="Notum Splitter", ql=150),
        ]
    )

    results = await store.search(query="", ql=200, limit=50)
    assert [i.id for i in results] == [1]


async def test_search_blank_query_filters_by_category_alone(fake_redis):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200, category="armor"),
            make_item(id=2, name="Notum Splitter", ql=150, category="general"),
        ]
    )

    results = await store.search(query="", ql=0, category="armor", limit=50)
    assert [i.id for i in results] == [1]


async def test_search_blank_query_filters_by_category_and_ql(fake_redis):
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


async def test_search_filters_by_ql(fake_redis):
    store = ItemStore()
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200),
            make_item(id=2, name="Notum Splitter", ql=150),
        ]
    )

    results = await store.search(query="notum", ql=200, limit=50)
    assert [i.id for i in results] == [1]


async def test_search_respects_limit_and_sorts_by_name(fake_redis):
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


async def test_count_reflects_total_loaded(fake_redis):
    store = ItemStore()
    assert await store.count(query="", ql=0) == 0

    await store.load([make_item(id=1, name="x")])
    assert await store.count(query="", ql=0) == 1


async def test_search_respects_offset(fake_redis):
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


async def test_count_reflects_total_matches_not_limit(fake_redis):
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


async def test_get_returns_item_by_id_or_none(fake_redis):
    store = ItemStore()
    await store.load([make_item(id=42, name="Notum Tank Armor")])

    item = await store.get(42)
    assert item.name == "Notum Tank Armor"
    assert await store.get(999) is None


async def test_list_ids_returns_every_id_regardless_of_name(fake_redis):
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


async def test_list_ids_respects_limit_and_offset(fake_redis):
    store = ItemStore()
    await store.load([make_item(id=i, name=f"Item{i}") for i in range(1, 6)])

    first_page = await store.list_ids(limit=2, offset=0)
    second_page = await store.list_ids(limit=2, offset=2)
    assert len(first_page) == 2
    assert len(second_page) == 2
    assert set(first_page) & set(second_page) == set()
