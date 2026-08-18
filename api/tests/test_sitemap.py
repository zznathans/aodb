from app.store import make_item, make_nano, nano_store, store


async def _seed():
    await store.load([make_item(id=i, name=f"Item{i}", ql=100) for i in range(1, 6)])
    await nano_store.load(
        [
            make_nano(id=100 + i, name=f"Nano{i}", ql=50, description=f"Nano{i} description", crystal_id=200 + i)
            for i in range(1, 4)
        ]
    )


async def test_sitemap_index_lists_per_resource_indexes(client):
    resp = client.get("/sitemap.xml")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/xml"
    assert resp.headers["cache-control"] == "public, max-age=3600"
    assert "<sitemapindex" in resp.text
    assert "http://testserver/items/sitemap.xml" in resp.text
    assert "http://testserver/nanos/sitemap.xml" in resp.text


async def test_sitemap_items_index_lists_one_chunk(client):
    await _seed()

    resp = client.get("/items/sitemap.xml")

    assert resp.status_code == 200
    assert "<sitemapindex" in resp.text
    assert "http://testserver/items/sitemap-0.xml" in resp.text
    # only 5 items seeded - well under the 50,000-per-chunk limit
    assert "items/sitemap-1.xml" not in resp.text


async def test_sitemap_items_index_still_returns_one_chunk_when_empty(client):
    resp = client.get("/items/sitemap.xml")

    assert resp.status_code == 200
    assert "http://testserver/items/sitemap-0.xml" in resp.text


async def test_sitemap_nanos_index_lists_one_chunk(client):
    await _seed()

    resp = client.get("/nanos/sitemap.xml")

    assert resp.status_code == 200
    assert "<sitemapindex" in resp.text
    assert "http://testserver/nanos/sitemap-0.xml" in resp.text
    # only 3 nanos seeded - well under the 50,000-per-chunk limit
    assert "nanos/sitemap-1.xml" not in resp.text


async def test_sitemap_nanos_index_still_returns_one_chunk_when_empty(client):
    resp = client.get("/nanos/sitemap.xml")

    assert resp.status_code == 200
    assert "http://testserver/nanos/sitemap-0.xml" in resp.text


async def test_sitemap_items_lists_every_seeded_item(client):
    await _seed()

    resp = client.get("/items/sitemap-0.xml")

    assert resp.status_code == 200
    for i in range(1, 6):
        assert f"http://testserver/items/{i}</loc>" in resp.text


async def test_sitemap_items_empty_chunk_is_a_valid_empty_urlset(client):
    resp = client.get("/items/sitemap-0.xml")

    assert resp.status_code == 200
    assert "<urlset" in resp.text
    assert "<url>" not in resp.text


async def test_sitemap_nanos_lists_every_seeded_nano(client):
    await _seed()

    resp = client.get("/nanos/sitemap-0.xml")

    assert resp.status_code == 200
    for i in range(101, 104):
        assert f"http://testserver/nanos/{i}</loc>" in resp.text


async def test_robots_txt_points_at_sitemap(client):
    resp = client.get("/robots.txt")

    assert resp.status_code == 200
    assert "Sitemap: http://testserver/sitemap.xml" in resp.text
    assert "Allow: /" in resp.text
