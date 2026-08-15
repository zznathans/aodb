"""sitemaps.org-protocol sitemap generation, so search engines can discover
the item/nano catalog rather than relying on crawling links alone. The full
catalog (~125k items + ~10.5k nanos per app/store.py's module docstring)
exceeds a single sitemap's 50,000-URL limit, so this is a sitemap index
(/sitemap.xml) referencing per-resource, per-chunk sub-sitemaps rather than
one flat file - see https://www.sitemaps.org/protocol.html.

Every response here only depends on data that changes at most once per
dump reload (see main.py's _load_items), so a generous Cache-Control lets
whatever's in front of this app (Cloudflare, per the analytics beacon)
absorb most repeat crawler hits instead of regenerating on every request.
"""

from fastapi import APIRouter, Request, Response

from .store import nano_store, store

router = APIRouter()

_MAX_URLS_PER_SITEMAP = 50_000
_CACHE_CONTROL = "public, max-age=3600"
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
_XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _urlset_xml(locs: list[str]) -> str:
    body = "".join(f"<url><loc>{_xml_escape(loc)}</loc></url>" for loc in locs)
    return f'{_XML_DECLARATION}<urlset xmlns="{_SITEMAP_NS}">{body}</urlset>'


def _sitemap_index_xml(locs: list[str]) -> str:
    body = "".join(f"<sitemap><loc>{_xml_escape(loc)}</loc></sitemap>" for loc in locs)
    return f'{_XML_DECLARATION}<sitemapindex xmlns="{_SITEMAP_NS}">{body}</sitemapindex>'


def _xml_response(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml", headers={"Cache-Control": _CACHE_CONTROL})


def _chunk_count(total: int) -> int:
    """At least 1 chunk even when total is 0, so e.g. sitemap-items-0.xml
    always exists (as an empty urlset) rather than 404ing."""
    return max(1, -(-total // _MAX_URLS_PER_SITEMAP))


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap_index(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    item_total = await store.count("", 0)
    nano_total = await nano_store.count("", 0, "", None)

    locs = [f"{base}/sitemap-pages.xml"]
    locs += [f"{base}/sitemap-items-{n}.xml" for n in range(_chunk_count(item_total))]
    locs += [f"{base}/sitemap-nanos-{n}.xml" for n in range(_chunk_count(nano_total))]
    return _xml_response(_sitemap_index_xml(locs))


@router.get("/sitemap-pages.xml", include_in_schema=False)
async def sitemap_pages(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    return _xml_response(_urlset_xml([f"{base}/browse/", f"{base}/browse/items", f"{base}/browse/nanos"]))


@router.get("/sitemap-items-{n}.xml", include_in_schema=False)
async def sitemap_items(request: Request, n: int) -> Response:
    base = str(request.base_url).rstrip("/")
    ids = await store.list_ids(_MAX_URLS_PER_SITEMAP, n * _MAX_URLS_PER_SITEMAP)
    return _xml_response(_urlset_xml([f"{base}/browse/items/{id_}" for id_ in ids]))


@router.get("/sitemap-nanos-{n}.xml", include_in_schema=False)
async def sitemap_nanos(request: Request, n: int) -> Response:
    base = str(request.base_url).rstrip("/")
    ids = await nano_store.list_ids(_MAX_URLS_PER_SITEMAP, n * _MAX_URLS_PER_SITEMAP)
    return _xml_response(_urlset_xml([f"{base}/browse/nanos/{id_}" for id_ in ids]))
