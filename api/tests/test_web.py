from pathlib import Path

from app.store import Effect, Requirement, make_item, make_nano, nano_store, store
from app.web import _merge_ql_variants

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


async def test_browse_home_renders_stats_and_links(client):
    await _seed_items()
    await _seed_nanos()

    resp = client.get("/")

    assert resp.status_code == 200
    assert '<span class="stat-number">2</span>' in resp.text
    assert '<span class="stat-number">1</span>' in resp.text
    assert 'href="/items"' in resp.text
    assert 'href="/nanos"' in resp.text


async def test_browse_home_renders_with_no_data_loaded(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert '<span class="stat-number">0</span>' in resp.text


async def test_browse_items_renders_seeded_names(client):
    await _seed_items()

    resp = client.get("/items", params={"q": "notum"})

    assert resp.status_code == 200
    assert "Notum Tank Armor" in resp.text
    assert "Notum Splitter" in resp.text


async def test_browse_items_filters_by_query(client):
    await _seed_items()

    resp = client.get("/items", params={"q": "notum tank"})

    assert resp.status_code == 200
    assert "Notum Tank Armor" in resp.text
    assert "Notum Splitter" not in resp.text


async def test_browse_items_no_matches_renders_empty_state(client):
    await _seed_items()

    resp = client.get("/items", params={"q": "nonexistent"})

    assert resp.status_code == 200
    assert "No items found" in resp.text


async def test_browse_item_detail_renders_item(client):
    await _seed_items()

    resp = client.get("/items/1")

    assert resp.status_code == 200
    assert "Notum Tank Armor" in resp.text
    assert "Sturdy." in resp.text


async def test_browse_item_detail_404s_for_unknown_id(client):
    await _seed_items()

    resp = client.get("/items/999999")

    assert resp.status_code == 404
    assert "999999" in resp.text
    assert resp.headers["content-type"].startswith("text/html")


async def _seed_ql_variants():
    await store.load(
        [
            make_item(
                id=10,
                name="Test Blade",
                ql=10,
                icon=1,
                description="A test blade.",
                category="weapon",
                damage_min=10,
                damage_max=20,
                damage_critical=5,
                effects=(Effect(hook="Wear", target="Self", action="Modify", attribute="Strength", value="100"),),
                requirements=(Requirement(hook="To Equip", attribute="1h Edged", operator="at least", value="50"),),
            ),
            make_item(
                id=11,
                name="Test Blade",
                ql=110,
                icon=1,
                description="A test blade.",
                category="weapon",
                damage_min=110,
                damage_max=220,
                damage_critical=105,
                effects=(Effect(hook="Wear", target="Self", action="Modify", attribute="Strength", value="300"),),
                requirements=(
                    Requirement(hook="To Equip", attribute="1h Edged", operator="at least", value="550"),
                    Requirement(hook="To Use", attribute="Level", operator="at least", value="10"),
                ),
            ),
        ]
    )


async def test_item_detail_interpolates_stats_between_ql_variants(client):
    await _seed_ql_variants()

    resp = client.get("/items/10", params={"ql": 60})

    assert resp.status_code == 200
    assert "range 10&ndash;110" in resp.text
    assert "60&ndash;120" in resp.text  # interpolated damage_min-damage_max
    assert "crit 55" in resp.text  # interpolated damage_critical
    assert "Strength" in resp.text and "200" in resp.text  # interpolated Modify stat
    assert "300" in resp.text  # interpolated "at least" requirement value


async def test_item_detail_clamps_ql_outside_variant_range(client):
    await _seed_ql_variants()

    resp = client.get("/items/10", params={"ql": 99999})

    assert resp.status_code == 200
    # Clamped to the high variant's own values - no interpolation beyond it.
    assert "110&ndash;220" in resp.text
    assert "crit 105" in resp.text


async def test_item_detail_defaults_to_own_ql_when_ql_param_omitted(client):
    await _seed_ql_variants()

    resp = client.get("/items/10")

    assert resp.status_code == 200
    assert "10&ndash;20" in resp.text
    assert "crit 5" in resp.text


async def test_browse_items_merges_consecutive_same_name_ql_variants(client):
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=100, description="Sturdy.", category="armor"),
            make_item(id=2, name="Notum Tank Armor", ql=150, description="Sturdy.", category="armor"),
            make_item(id=3, name="Notum Tank Armor", ql=200, description="Sturdy.", category="armor"),
        ]
    )

    resp = client.get("/items", params={"q": "notum"})

    assert resp.status_code == 200
    assert "100–200" in resp.text
    assert 'href="/items/1"' in resp.text
    assert resp.text.count("Notum Tank Armor") == 1


async def test_browse_items_by_category_filters_by_query(client):
    await store.load(
        [
            make_item(id=1, name="Notum Tank Armor", ql=100, category="general"),
            make_item(id=2, name="Notum Splitter", ql=100, category="general"),
        ]
    )

    resp = client.get("/items/categories/general", params={"q": "notum tank"})

    assert resp.status_code == 200
    assert "Notum Tank Armor" in resp.text
    assert "Notum Splitter" not in resp.text


def test_merge_ql_variants_uses_the_lowest_ql_variants_id():
    # Same-name group encountered in descending-ish ql order (search()
    # doesn't guarantee any particular order among substring-matched
    # candidates before its own final name sort) - the merged row's link
    # should follow whichever variant actually has the lowest ql, not just
    # the first one seen.
    items = [
        make_item(id=5, name="Notum Tank Armor", ql=200, description="Sturdy."),
        make_item(id=6, name="Notum Tank Armor", ql=100, description="Sturdy."),
    ]

    merged = _merge_ql_variants(items)

    assert len(merged) == 1
    assert merged[0]["id"] == 6
    assert merged[0]["ql_display"] == "100–200"


async def test_browse_items_by_category_404s_for_unknown_category(client):
    resp = client.get("/items/categories/nonexistent")

    assert resp.status_code == 404


async def test_browse_items_by_category_subcategory_404s_for_unknown_category(client):
    resp = client.get("/items/categories/nonexistent/types/wrist")

    assert resp.status_code == 404


async def test_browse_items_by_category_subcategory_404s_for_unknown_type(client):
    resp = client.get("/items/categories/armor/types/nonexistent-type")

    assert resp.status_code == 404


async def test_browse_items_shows_subcategory_grid_for_browsable_category(client):
    await store.load(
        [
            make_item(id=1, name="Wrist Guard", ql=10, category="armor", subcategory="Wrist"),
            make_item(id=2, name="Head Guard", ql=10, category="armor", subcategory="Head"),
        ]
    )

    resp = client.get("/items/categories/armor")

    assert resp.status_code == 200
    assert "Wrist" in resp.text
    assert "Head" in resp.text
    # No query and no subcategory picked yet - shows the breakdown grid, not results.
    assert "Wrist Guard" not in resp.text


async def test_browse_items_by_category_subcategory_renders_matching_items(client):
    await store.load(
        [
            make_item(id=1, name="Wrist Guard", ql=10, category="armor", subcategory="Wrist"),
            make_item(id=2, name="Head Guard", ql=10, category="armor", subcategory="Head"),
        ]
    )

    resp = client.get("/items/categories/armor/types/wrist")

    assert resp.status_code == 200
    assert "Wrist Guard" in resp.text
    assert "Head Guard" not in resp.text


async def test_browse_nanos_renders_seeded_names(client):
    await _seed_nanos()

    resp = client.get("/nanos", params={"q": "death"})

    assert resp.status_code == 200
    assert "Death&#39;s Gaze" in resp.text or "Death's Gaze" in resp.text


async def test_browse_nanos_filters_by_profession(client):
    await _seed_nanos()

    resp = client.get("/nanos/professions/agent")
    assert "Death" in resp.text

    resp = client.get("/nanos/professions/doctor")
    assert "No nanos found" in resp.text


async def test_browse_nanos_by_unknown_profession_404s(client):
    resp = client.get("/nanos/professions/nonexistent")

    assert resp.status_code == 404


async def test_browse_nano_detail_renders_nano(client):
    await _seed_nanos()

    resp = client.get("/nanos/25980")

    assert resp.status_code == 200
    assert "Combat" in resp.text
    assert "265" in resp.text  # nanocost


async def test_browse_nano_detail_404s_for_unknown_id(client):
    await _seed_nanos()

    resp = client.get("/nanos/999999")

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
    for path in ("/", "/items", "/nanos"):
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
        resp = client.get("/")
        assert "window.__test_analytics = true;" in resp.text
    finally:
        _ANALYTICS_PARTIAL.unlink()
