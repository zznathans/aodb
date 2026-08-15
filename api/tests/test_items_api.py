from app.store import make_item, store


async def _seed():
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200, icon=12345, description="Sturdy."),
            make_item(id=2, name="Notum Splitter", ql=150, icon=54321),
            make_item(id=3, name="Alpha Item", ql=1, icon=1),
        ]
    )


async def test_get_search_returns_json_array_with_expected_fields(client):
    await _seed()

    resp = client.get("/api/items", params={"q": "notum"})

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert body[0] == {
        "id": 2,
        "name": "Notum Splitter",
        "ql": 150,
        "icon": 54321,
        "description": None,
        "category": "general",
        "subcategory": "",
        "effects": [],
        "requirements": [],
        "damage_min": None,
        "damage_max": None,
        "damage_critical": None,
    }
    assert body[1]["description"] == "Sturdy."


async def test_get_search_sets_total_count_header(client):
    await _seed()

    resp = client.get("/api/items", params={"q": "notum", "limit": 1})

    assert resp.headers["X-Total-Count"] == "2"
    assert len(resp.json()) == 1


async def test_get_search_ql_filter(client):
    await _seed()

    resp = client.get("/api/items", params={"q": "notum", "ql": 200})

    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Notum Tank Armor"


async def test_get_search_offset(client):
    await _seed()

    resp = client.get("/api/items", params={"q": "", "limit": 10, "offset": 1})

    names = [i["name"] for i in resp.json()]
    assert names == ["Notum Splitter", "Notum Tank Armor"]


async def test_get_search_no_matches_returns_empty_array_not_error(client):
    await _seed()

    resp = client.get("/api/items", params={"q": "nonexistent"})

    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"


async def test_post_search_matches_get_behavior(client):
    await _seed()

    resp = client.post("/api/items", json={"q": "notum", "ql": 200})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Notum Tank Armor"


async def test_get_by_id_returns_item(client):
    await _seed()

    resp = client.get("/api/items/1")

    assert resp.status_code == 200
    assert resp.json()["name"] == "Notum Tank Armor"


async def test_get_by_id_404s_for_unknown_id(client):
    await _seed()

    resp = client.get("/api/items/999999")

    assert resp.status_code == 404
    assert "999999" in resp.json()["detail"]


async def test_limit_over_200_is_rejected(client):
    await _seed()

    resp = client.get("/api/items", params={"limit": 500})

    assert resp.status_code == 422
