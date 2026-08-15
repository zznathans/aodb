async def test_list_professions_returns_sorted_id_name_pairs(client):
    resp = client.get("/professions")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0] == {"id": 1, "name": "Soldier"}
    assert {"id": 12, "name": "Meta-Physicist"} in body
    assert [p["id"] for p in body] == sorted(p["id"] for p in body)


async def test_list_professions_skips_unassigned_id_13(client):
    resp = client.get("/professions")

    ids = [p["id"] for p in resp.json()]
    assert 13 not in ids


async def test_get_profession_by_id(client):
    resp = client.get("/professions/11")

    assert resp.status_code == 200
    assert resp.json() == {"id": 11, "name": "Nano-Technician"}


async def test_get_profession_404s_for_unknown_id(client):
    resp = client.get("/professions/13")

    assert resp.status_code == 404
