from app.store import NanoStore, Requirement, make_nano


async def _seed(store: NanoStore) -> None:
    await store.load(
        [
            make_nano(
                id=1,
                name="Death's Gaze",
                ql=142,
                description="Holds the target in place.",
                school="Combat",
                crystal_id=101,
                profession=5,
            ),
            make_nano(
                id=2,
                name="Complete Heal",
                ql=100,
                description="Heals the target.",
                school="Healing",
                crystal_id=102,
                profession=3,
            ),
            make_nano(
                id=3,
                name="Combat Boost",
                ql=142,
                description="Boosts combat skills.",
                school="Combat",
                crystal_id=103,
                profession=3,
            ),
        ]
    )


async def test_search_matches_substring_case_insensitively(fake_mongo, no_cache):
    store = NanoStore()
    await _seed(store)

    assert len(await store.search(query="death", ql=0, school="", profession=None, limit=50)) == 1
    assert len(await store.search(query="DEATH", ql=0, school="", profession=None, limit=50)) == 1
    assert len(await store.search(query="gaze", ql=0, school="", profession=None, limit=50)) == 1  # suffix match
    assert len(await store.search(query="xyz", ql=0, school="", profession=None, limit=50)) == 0


async def test_search_filters_by_school(fake_mongo, no_cache):
    store = NanoStore()
    await _seed(store)

    results = await store.search(query="", ql=0, school="Combat", profession=None, limit=50)
    assert {n.id for n in results} == {1, 3}


async def test_search_filters_by_profession(fake_mongo, no_cache):
    store = NanoStore()
    await _seed(store)

    results = await store.search(query="", ql=0, school="", profession=3, limit=50)
    assert {n.id for n in results} == {2, 3}


async def test_search_combines_filters(fake_mongo, no_cache):
    store = NanoStore()
    await _seed(store)

    results = await store.search(query="", ql=0, school="Combat", profession=3, limit=50)
    assert [n.id for n in results] == [3]


async def test_count_reflects_total_matches_not_limit(fake_mongo, no_cache):
    store = NanoStore()
    await _seed(store)

    assert await store.count(query="", ql=142, school="", profession=None) == 2
    assert len(await store.search(query="", ql=142, school="", profession=None, limit=1)) == 1


async def test_get_returns_nano_by_id_or_none(fake_mongo, no_cache):
    store = NanoStore()
    await _seed(store)

    nano = await store.get(1)
    assert nano.name == "Death's Gaze"
    assert await store.get(999) is None


async def test_list_ids_returns_every_id(fake_mongo, no_cache):
    store = NanoStore()
    await _seed(store)

    ids = await store.list_ids(limit=50)
    assert set(ids) == {1, 2, 3}


async def test_school_counts_returns_per_school_totals(fake_mongo, no_cache):
    store = NanoStore()
    await _seed(store)

    assert await store.school_counts() == {"Combat": 2, "Healing": 1}


async def test_school_counts_ignores_nanos_without_a_school(fake_mongo, no_cache):
    store = NanoStore()
    await store.load([make_nano(id=1, name="No School Nano", ql=1)])

    assert await store.school_counts() == {}


async def test_load_does_not_flush_existing_data(fake_mongo, no_cache):
    store = NanoStore()
    await store.load([make_nano(id=1, name="Old Nano", crystal_id=1, description="x")])
    await store.load([make_nano(id=2, name="New Nano", crystal_id=2, description="y")])

    assert await store.get(1) is not None
    assert await store.get(2) is not None


async def test_load_skips_ids_that_already_exist(fake_mongo, no_cache):
    store = NanoStore()
    await store.load([make_nano(id=1, name="Original Name", crystal_id=1, description="x", ql=100)])
    await store.load([make_nano(id=1, name="Changed Name", crystal_id=1, description="x", ql=200)])

    nano = await store.get(1)
    assert nano.name == "Original Name"
    assert nano.ql == 100


async def test_load_school_and_profession_counts_do_not_double_count_on_reload(fake_mongo, no_cache):
    # school_counts/profession_counts are recomputed from the full incoming
    # list and overwritten (not incremented) on every load() call - a
    # counter that kept adding onto itself across repeated loads of the
    # same nano would double-count here.
    store = NanoStore()
    nano = make_nano(id=1, name="Death's Gaze", crystal_id=1, description="x", school="Combat", profession=5)
    await store.load([nano])
    await store.load([nano])

    assert await store.school_counts() == {"Combat": 1}
    assert await store.profession_counts() == {5: 1}


async def _seed_with_generic(store: NanoStore) -> None:
    await store.load(
        [
            make_nano(id=1, name="Death's Gaze", crystal_id=1, description="x", profession=5),
            # No explicit profession and no profession-shaped requirement -
            # this is the "General" bucket every profession gets.
            make_nano(id=2, name="Generic Heal", crystal_id=2, description="x"),
            # No explicit profession, but a "Visual profession" requirement
            # - not generic either (see _profession_bucket), so it belongs
            # to no profession bucket at all.
            make_nano(
                id=3,
                name="Visual Only Nano",
                crystal_id=3,
                description="x",
                requirements=(
                    Requirement(hook="To Use", attribute="Visual profession", operator="exactly", value="5"),
                ),
            ),
        ]
    )


async def test_search_filters_by_profession_zero_returns_generic_nanos(fake_mongo, no_cache):
    store = NanoStore()
    await _seed_with_generic(store)

    results = await store.search(query="", ql=0, school="", profession=0, limit=50)
    assert {n.id for n in results} == {2}


async def test_count_with_profession_matches_search(fake_mongo, no_cache):
    store = NanoStore()
    await _seed_with_generic(store)

    assert await store.count(query="", ql=0, school="", profession=5) == 1
    assert await store.count(query="", ql=0, school="", profession=0) == 1
    assert await store.count(query="", ql=0, school="", profession=999) == 0


async def test_profession_counts_includes_generic_bucket(fake_mongo, no_cache):
    store = NanoStore()
    await _seed_with_generic(store)

    assert await store.profession_counts() == {5: 1, 0: 1}


async def test_load_profession_counts_do_not_duplicate_on_reload(fake_mongo, no_cache):
    store = NanoStore()
    await _seed_with_generic(store)
    await _seed_with_generic(store)

    assert await store.profession_counts() == {5: 1, 0: 1}
    results = await store.search(query="", ql=0, school="", profession=0, limit=50)
    assert [n.id for n in results] == [2]
