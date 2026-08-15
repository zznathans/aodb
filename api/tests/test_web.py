from pathlib import Path

from app.store import make_item, make_nano, nano_store, store

_ANALYTICS_PARTIAL = Path(__file__).parent.parent / "app" / "templates" / "_analytics.html"


async def _seed_items():
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=200, icon=12345, description="Sturdy."),
            make_item(id=2, name="Notum Splitter", ql=150, icon=54321),
        ]
    )


async def _seed_nanos():
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
        ]
    )


async def test_homepage_redirects_to_browse_landing_page(client):
    resp = client.get("/", follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/browse/"


async def test_browse_home_renders_stats_and_links(client):
    await _seed_items()
    await _seed_nanos()

    resp = client.get("/browse/")

    assert resp.status_code == 200
    assert '<span class="stat-number">2</span>' in resp.text
    assert '<span class="stat-number">1</span>' in resp.text
    assert 'href="/browse/items"' in resp.text
    assert 'href="/browse/nanos"' in resp.text
    assert "Combat" in resp.text


async def test_browse_home_renders_with_no_data_loaded(client):
    resp = client.get("/browse/")

    assert resp.status_code == 200
    assert "No nano data loaded." in resp.text


async def test_browse_items_renders_seeded_names(client):
    await _seed_items()

    resp = client.get("/browse/items")

    assert resp.status_code == 200
    assert "Notum Tank Armor" in resp.text
    assert "Notum Splitter" in resp.text


async def test_browse_items_filters_by_query(client):
    await _seed_items()

    resp = client.get("/browse/items", params={"q": "notum", "ql": 200})

    assert resp.status_code == 200
    assert "Notum Tank Armor" in resp.text
    assert "Notum Splitter" not in resp.text


async def test_browse_items_no_matches_renders_empty_state(client):
    await _seed_items()

    resp = client.get("/browse/items", params={"q": "nonexistent"})

    assert resp.status_code == 200
    assert "No items found" in resp.text


async def test_browse_item_detail_renders_item(client):
    await _seed_items()

    resp = client.get("/browse/items/1")

    assert resp.status_code == 200
    assert "Notum Tank Armor" in resp.text
    assert "Sturdy." in resp.text


async def test_browse_item_detail_404s_for_unknown_id(client):
    await _seed_items()

    resp = client.get("/browse/items/999999")

    assert resp.status_code == 404
    assert "999999" in resp.text
    assert resp.headers["content-type"].startswith("text/html")


async def test_browse_nanos_renders_seeded_names(client):
    await _seed_nanos()

    resp = client.get("/browse/nanos")

    assert resp.status_code == 200
    assert "Death&#39;s Gaze" in resp.text or "Death's Gaze" in resp.text


async def test_browse_nanos_filters_by_school(client):
    await _seed_nanos()

    resp = client.get("/browse/nanos", params={"school": "Combat"})
    assert "Death" in resp.text

    resp = client.get("/browse/nanos", params={"school": "Healing"})
    assert "No nanos found" in resp.text


async def test_browse_nano_detail_renders_nano(client):
    await _seed_nanos()

    resp = client.get("/browse/nanos/25980")

    assert resp.status_code == 200
    assert "Combat" in resp.text
    assert "265" in resp.text  # nanocost


async def test_browse_nano_detail_404s_for_unknown_id(client):
    await _seed_nanos()

    resp = client.get("/browse/nanos/999999")

    assert resp.status_code == 404
    assert "999999" in resp.text


async def test_static_files_are_served(client):
    css = client.get("/static/style.css")
    js = client.get("/static/search.js")

    assert css.status_code == 200
    assert js.status_code == 200
    assert "site-header" in css.text
    assert "search-form" in js.text


async def test_browse_pages_include_no_analytics_by_default(client):
    # This repo used to hardcode real Cloudflare/GA tracking IDs here - a
    # stock deployment (no app/templates/_analytics.html supplied) must
    # ship with none at all.
    for path in ("/browse/", "/browse/items", "/browse/nanos"):
        resp = client.get(path)
        assert "cloudflareinsights.com" not in resp.text
        assert "googletagmanager.com" not in resp.text


async def test_browse_pages_include_analytics_partial_when_present(client):
    # Proves the {% include "_analytics.html" ignore missing %} mechanism
    # itself actually works, using a throwaway file at the real path
    # base.html includes from - cleaned up even if the test fails.
    assert not _ANALYTICS_PARTIAL.exists()
    _ANALYTICS_PARTIAL.write_text("<script>window.__test_analytics = true;</script>")
    try:
        resp = client.get("/browse/")
        assert "window.__test_analytics = true;" in resp.text
    finally:
        _ANALYTICS_PARTIAL.unlink()
