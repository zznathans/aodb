from app.store import make_item, store


async def _seed():
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200, icon=12345),
            make_item(id=2, name="Notum Splitter", ql=150, icon=54321),
        ]
    )


async def test_search_returns_matching_items(client):
    await _seed()

    resp = client.get(
        "/legacy",
        params={"bot": "BeBot", "output": "aoml", "max": 50, "search": "Notum", "ql": 0, "icons": "true"},
    )

    assert resp.status_code == 200
    body = resp.text
    assert "Notum Tank Armor" in body
    assert "Notum Splitter" in body
    assert "<img src=rdb://12345>" in body
    assert "itemref://1/1/200" in body


async def test_search_with_no_matches_returns_200_not_error(client):
    await _seed()

    resp = client.get("/legacy", params={"output": "aoml", "search": "Nonexistent Item"})

    assert resp.status_code == 200
    assert "No items found" in resp.text


async def test_ql_filter(client):
    await _seed()

    resp = client.get("/legacy", params={"output": "aoml", "search": "Notum", "ql": 200})

    assert "Notum Tank Armor" in resp.text
    assert "Notum Splitter" not in resp.text


async def test_icons_false_omits_icon_tag(client):
    await _seed()

    resp = client.get("/legacy", params={"output": "aoml", "search": "Notum", "icons": "false"})

    assert "<img src=rdb://" not in resp.text


async def test_unsupported_output_format_still_returns_200_body(client):
    resp = client.get("/legacy", params={"output": "json", "search": "x"})

    # Not a hard requirement of the old service, but a deliberate choice here:
    # fail loudly with a 400 rather than silently misrendering, since no real
    # client ever sends anything but output=aoml.
    assert resp.status_code == 400


async def test_empty_store_still_returns_200(client):
    resp = client.get("/legacy", params={"output": "aoml", "search": "anything"})

    assert resp.status_code == 200
    assert "No items found" in resp.text


async def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


async def test_docs_includes_cloudflare_analytics_beacon(client):
    resp = client.get("/docs")

    assert resp.status_code == 200
    assert "static.cloudflareinsights.com/beacon.min.js" in resp.text


async def test_docs_includes_google_analytics_tag(client):
    resp = client.get("/docs")

    assert resp.status_code == 200
    assert "googletagmanager.com/gtag/js?id=G-6SJKPHMGR3" in resp.text


async def test_api_catalog_describes_this_api(client):
    resp = client.get("/.well-known/api-catalog")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == (
        'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"'
    )
    body = resp.json()
    entry = body["linkset"][0]
    assert entry["anchor"] == "http://testserver"
    assert entry["service-desc"][0]["href"] == "http://testserver/openapi.json"
    assert entry["service-doc"][0]["href"] == "http://testserver/docs"
