from app.store import make_item, make_nano, nano_store, store


async def _seed():
    await store.load([make_item(id=i, name=f"Item{i}", ql=100) for i in range(1, 6)])
    await nano_store.load([make_nano(id=100 + i, name=f"Nano{i}", ql=50) for i in range(1, 4)])


async def test_sitemap_index_lists_pages_and_one_chunk_each(client):
    await _seed()

    resp = client.get("/sitemap.xml")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/xml"
    assert resp.headers["cache-control"] == "public, max-age=3600"
    assert "<sitemapindex" in resp.text
    assert "http://testserver/sitemap-pages.xml" in resp.text
    assert "http://testserver/sitemap-items-0.xml" in resp.text
    assert "http://testserver/sitemap-nanos-0.xml" in resp.text
    # only 5 items / 3 nanos seeded - well under the 50,000-per-chunk limit
    assert "sitemap-items-1.xml" not in resp.text
    assert "sitemap-nanos-1.xml" not in resp.text


async def test_sitemap_index_still_returns_one_chunk_when_empty(client):
    resp = client.get("/sitemap.xml")

    assert resp.status_code == 200
    assert "http://testserver/sitemap-items-0.xml" in resp.text
    assert "http://testserver/sitemap-nanos-0.xml" in resp.text


async def test_sitemap_pages_lists_browse_landing_pages(client):
    resp = client.get("/sitemap-pages.xml")

    assert resp.status_code == 200
    assert "<urlset" in resp.text
    assert "http://testserver/browse/</loc>" in resp.text
    assert "http://testserver/browse/items</loc>" in resp.text
    assert "http://testserver/browse/nanos</loc>" in resp.text


async def test_sitemap_items_lists_every_seeded_item(client):
    await _seed()

    resp = client.get("/sitemap-items-0.xml")

    assert resp.status_code == 200
    for i in range(1, 6):
        assert f"http://testserver/browse/items/{i}</loc>" in resp.text


async def test_sitemap_items_empty_chunk_is_a_valid_empty_urlset(client):
    resp = client.get("/sitemap-items-0.xml")

    assert resp.status_code == 200
    assert "<urlset" in resp.text
    assert "<url>" not in resp.text


async def test_sitemap_nanos_lists_every_seeded_nano(client):
    await _seed()

    resp = client.get("/sitemap-nanos-0.xml")

    assert resp.status_code == 200
    for i in range(101, 104):
        assert f"http://testserver/browse/nanos/{i}</loc>" in resp.text


async def test_robots_txt_points_at_sitemap(client):
    resp = client.get("/robots.txt")

    assert resp.status_code == 200
    assert "Sitemap: http://testserver/sitemap.xml" in resp.text
    assert "Allow: /" in resp.text
