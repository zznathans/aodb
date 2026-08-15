from app.store import Effect, NanoStore, Requirement, _effects_from_json, _requirements_from_json, make_nano


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


async def test_search_matches_substring_case_insensitively(fake_redis):
    store = NanoStore()
    await _seed(store)

    assert len(await store.search(query="death", ql=0, school="", profession=None, limit=50)) == 1
    assert len(await store.search(query="DEATH", ql=0, school="", profession=None, limit=50)) == 1
    assert len(await store.search(query="gaze", ql=0, school="", profession=None, limit=50)) == 1  # suffix match
    assert len(await store.search(query="xyz", ql=0, school="", profession=None, limit=50)) == 0


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
    raw = '[{"hook": "To Use", "attribute": "Profession", "operator": "exactly", "value": "5"}]'
    assert _requirements_from_json(raw) == (
        Requirement(hook="To Use", attribute="Profession", operator="exactly", value="5"),
    )


def test_effects_from_json_handles_missing_field():
    assert _effects_from_json(None) == ()
    assert _effects_from_json("") == ()


def test_effects_from_json_round_trips_real_effects():
    raw = '[{"hook": "Wear", "target": "Self", "action": "Modify", "attribute": "Strength", "value": "10"}]'
    assert _effects_from_json(raw) == (
        Effect(hook="Wear", target="Self", action="Modify", attribute="Strength", value="10"),
    )


async def test_load_does_not_flush_existing_data(fake_redis):
    store = NanoStore()
    await store.load([make_nano(id=1, name="Old Nano", crystal_id=1, description="x")])
    await store.load([make_nano(id=2, name="New Nano", crystal_id=2, description="y")])

    assert await store.get(1) is not None
    assert await store.get(2) is not None


async def test_load_skips_ids_that_already_exist(fake_redis):
    store = NanoStore()
    await store.load([make_nano(id=1, name="Original Name", crystal_id=1, description="x", ql=100)])
    await store.load([make_nano(id=1, name="Changed Name", crystal_id=1, description="x", ql=200)])

    nano = await store.get(1)
    assert nano.name == "Original Name"
    assert nano.ql == 100


async def test_load_school_and_profession_counts_do_not_double_count_on_reload(fake_redis):
    # school_counts used to be HINCRBY'd per item on every load() call -
    # once load() stopped flushing first, that would keep adding onto
    # itself forever on repeated loads of the same nano. Both counts are
    # now recomputed from the full incoming list and overwritten instead.
    store = NanoStore()
    nano = make_nano(id=1, name="Death's Gaze", crystal_id=1, description="x", school="Combat", profession=5)
    await store.load([nano])
    await store.load([nano])

    assert await store.school_counts() == {"Combat": 1}
    assert await store.profession_counts() == {5: 1}
