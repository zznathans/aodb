from pathlib import Path

from app.store import make_item, store

_ANALYTICS_PARTIAL = Path(__file__).parent.parent / "app" / "templates" / "_analytics.html"


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
        "/api/legacy",
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

    resp = client.get("/api/legacy", params={"output": "aoml", "search": "Nonexistent Item"})

    assert resp.status_code == 200
    assert "No items found" in resp.text


async def test_ql_filter(client):
    await _seed()

    resp = client.get("/api/legacy", params={"output": "aoml", "search": "Notum", "ql": 200})

    assert "Notum Tank Armor" in resp.text
    assert "Notum Splitter" not in resp.text


async def test_icons_false_omits_icon_tag(client):
    await _seed()

    resp = client.get("/api/legacy", params={"output": "aoml", "search": "Notum", "icons": "false"})

    assert "<img src=rdb://" not in resp.text


async def test_color_params_wrap_output_in_font_tags(client):
    await _seed()

    resp = client.get(
        "/api/legacy",
        params={
            "output": "aoml",
            "search": "Notum",
            "color_header": "FFFFFF",
            "color_highlight": "FF0000",
            "color_normal": "00FF00",
        },
    )

    assert "<font color='#FFFFFF'>Item Search Results" in resp.text
    assert "<font color='#FF0000'>" in resp.text
    assert "<font color='#00FF00'> QL200</font>" in resp.text


async def test_unsupported_output_format_still_returns_200_body(client):
    resp = client.get("/api/legacy", params={"output": "json", "search": "x"})

    # Not a hard requirement of the old service, but a deliberate choice here:
    # fail loudly with a 400 rather than silently misrendering, since no real
    # client ever sends anything but output=aoml.
    assert resp.status_code == 400


async def test_empty_store_still_returns_200(client):
    resp = client.get("/api/legacy", params={"output": "aoml", "search": "anything"})

    assert resp.status_code == 200
    assert "No items found" in resp.text


async def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


async def test_docs_includes_no_analytics_by_default(client):
    # This repo used to hardcode real tracking IDs here - a stock
    # deployment (no app/templates/_analytics.html supplied) must ship
    # with none at all. See test_web.py for the "file present" case.
    resp = client.get("/api")

    assert resp.status_code == 200
    assert "cloudflareinsights.com" not in resp.text
    assert "googletagmanager.com" not in resp.text


async def test_docs_includes_analytics_partial_when_present(client):
    assert not _ANALYTICS_PARTIAL.exists()
    _ANALYTICS_PARTIAL.write_text("<script>window.__test_analytics = true;</script>")
    try:
        resp = client.get("/api")
        assert "window.__test_analytics = true;" in resp.text
    finally:
        _ANALYTICS_PARTIAL.unlink()


async def test_api_catalog_describes_this_api(client):
    resp = client.get("/.well-known/api-catalog")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == (
        'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"'
    )
    body = resp.json()
    entry = body["linkset"][0]
    assert entry["anchor"] == "http://testserver"
    assert entry["service-desc"][0]["href"] == "http://testserver/api/openapi.json"
    assert entry["service-doc"][0]["href"] == "http://testserver/api"
