from app.store import NanoStore, Requirement, _requirements_from_json, make_nano


async def _seed(store: NanoStore) -> None:
    await store.load(
        [
            make_nano(id=1, name="Death's Gaze", ql=142, school="Combat", profession=5),
            make_nano(id=2, name="Complete Heal", ql=100, school="Healing", profession=3),
            make_nano(id=3, name="Combat Boost", ql=142, school="Combat", profession=3),
        ]
    )


async def test_search_matches_prefix_case_insensitively(fake_redis):
    store = NanoStore()
    await _seed(store)

    assert len(await store.search(query="death", ql=0, school="", profession=None, limit=50)) == 1
    assert len(await store.search(query="DEATH", ql=0, school="", profession=None, limit=50)) == 1
    assert len(await store.search(query="gaze", ql=0, school="", profession=None, limit=50)) == 0


async def test_search_filters_by_school(fake_redis):
    store = NanoStore()
    await _seed(store)

    results = await store.search(query="", ql=0, school="Combat", profession=None, limit=50)
    assert {n.id for n in results} == {1, 3}


async def test_search_filters_by_profession(fake_redis):
    store = NanoStore()
    await _seed(store)

    results = await store.search(query="", ql=0, school="", profession=3, limit=50)
    assert {n.id for n in results} == {2, 3}


async def test_search_combines_filters(fake_redis):
    store = NanoStore()
    await _seed(store)

    results = await store.search(query="", ql=0, school="Combat", profession=3, limit=50)
    assert [n.id for n in results] == [3]


async def test_count_reflects_total_matches_not_limit(fake_redis):
    store = NanoStore()
    await _seed(store)

    assert await store.count(query="", ql=142, school="", profession=None) == 2
    assert len(await store.search(query="", ql=142, school="", profession=None, limit=1)) == 1


async def test_get_returns_nano_by_id_or_none(fake_redis):
    store = NanoStore()
    await _seed(store)

    nano = await store.get(1)
    assert nano.name == "Death's Gaze"
    assert await store.get(999) is None


async def test_list_ids_returns_every_id(fake_redis):
    store = NanoStore()
    await _seed(store)

    ids = await store.list_ids(limit=50)
    assert set(ids) == {1, 2, 3}


async def test_school_counts_returns_per_school_totals(fake_redis):
    store = NanoStore()
    await _seed(store)

    assert await store.school_counts() == {"Combat": 2, "Healing": 1}


async def test_school_counts_ignores_nanos_without_a_school(fake_redis):
    store = NanoStore()
    await store.load([make_nano(id=1, name="No School Nano", ql=1)])

    assert await store.school_counts() == {}


def test_requirements_from_json_handles_missing_field():
    # A hash written before "requirements" was ever populated (or any
    # other malformed/partial data) has no such key at all - h.get()
    # returns None, not "[]" - distinct from the empty-list case.
    assert _requirements_from_json(None) == ()
    assert _requirements_from_json("") == ()


def test_requirements_from_json_round_trips_real_requirements():
    raw = '[{"attribute": "Profession", "operator": "exactly", "value": "5"}]'
    assert _requirements_from_json(raw) == (Requirement(attribute="Profession", operator="exactly", value="5"),)
