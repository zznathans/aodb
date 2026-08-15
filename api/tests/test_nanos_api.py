from app.store import make_nano, nano_store


async def _seed():
    await nano_store.load(
        [
            make_nano(
                id=25980,
                name="Death's Gaze",
                ql=142,
                icon=16248,
                description="Holds the target in place.",
                school="Combat",
                strain=147,
                nanocost=265,
                ncu=44,
                crystal_id=26017,
                duration=453,
                profession=5,
                requirements=(),
            ),
            make_nano(id=25982, name="Change Form: Opifex", ql=-1, school="Healing", profession=None),
        ]
    )


async def test_search_returns_nano_specific_fields(client):
    await _seed()

    resp = client.get("/nanos", params={"q": "death"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    nano = body[0]
    assert nano["id"] == 25980
    assert nano["school"] == "Combat"
    assert nano["strain"] == 147
    assert nano["nanocost"] == 265
    assert nano["ncu"] == 44
    assert nano["crystal_id"] == 26017
    assert nano["duration"] == 453
    assert nano["profession"] == 5


async def test_search_filters_by_school(client):
    await _seed()

    resp = client.get("/nanos", params={"school": "Healing"})

    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Change Form: Opifex"


async def test_search_filters_by_profession(client):
    await _seed()

    resp = client.get("/nanos", params={"profession": 5})

    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == 25980


async def test_search_sets_total_count_header(client):
    await _seed()

    resp = client.get("/nanos", params={"limit": 1})

    assert resp.headers["X-Total-Count"] == "2"
    assert len(resp.json()) == 1


async def test_post_search_matches_get_behavior(client):
    await _seed()

    resp = client.post("/nanos", json={"school": "Combat"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Death's Gaze"


async def test_get_by_id_returns_nano(client):
    await _seed()

    resp = client.get("/nanos/25980")

    assert resp.status_code == 200
    assert resp.json()["name"] == "Death's Gaze"


async def test_get_by_id_404s_for_unknown_id(client):
    await _seed()

    resp = client.get("/nanos/999999")

    assert resp.status_code == 404
    assert "999999" in resp.json()["detail"]


async def test_items_endpoints_unaffected_by_nano_data(client):
    await _seed()

    resp = client.get("/items", params={"q": "gaze"})

    assert resp.status_code == 200
    assert resp.json() == []
