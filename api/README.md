# aodb api

A self-hosted Anarchy Online item/nano database API: parses the official
item dump and serves it back out as a JSON API, plus a legacy
AOML-compatible endpoint returning raw chat-markup text for game-chat
clients that expect that format.

See the [repo README](https://github.com/zznathans/aodb#readme) for how
this fits together with the [Helm chart](https://github.com/zznathans/aodb/tree/main/chart)
that deploys it.

## Browsing

`GET /` - a server-rendered (Jinja2, no build step, no JS framework)
landing page with overall item/nano counts, linking into `/items` and
`/nanos` for searching by hand. Each has a search form and paginated
results linking to a `/items/{aoid}` / `/nanos/{aoid}` detail page. Works
with JavaScript disabled (plain GET forms and links); `app/static/search.js`
progressively enhances search/pagination to fetch from the JSON API below
and re-render in place instead of a full page reload, when JS is
available. Icons aren't rendered (the `icon` field is a numeric AO client
icon id with no public image mapping this app takes a dependency on).

Crawlers are pointed at the catalog via `/robots.txt` (`Sitemap:` directive)
and `/sitemap.xml` - a three-level sitemap index (per
[sitemaps.org](https://www.sitemaps.org/protocol.html)): the root
references a per-resource index nested under each resource's own path
(`/items/sitemap.xml`, `/nanos/sitemap.xml`), which in turn references
that resource's chunked `/items/sitemap-{n}.xml` / `/nanos/sitemap-{n}.xml`
sub-sitemaps (50,000 URLs each, since the full catalog exceeds a single
sitemap's limit). Responses are cached (`Cache-Control: public,
max-age=3600`) since the underlying data only changes on a dump reload.

## API

Everything under `/api` - a normal JSON API for item/nano data, separate
from the browse UI above so the two never collide over the same paths
(e.g. `/items` is the browse UI, `/api/items` is the JSON endpoint).
Swagger UI is at `/api`; machine-readable spec is at
`/api/openapi.json` (`/healthz` is intentionally excluded from both).
`/.well-known/api-catalog` advertises both per
[RFC 9727](https://www.rfc-editor.org/rfc/rfc9727) - a standard
well-known location for API discovery tooling that doesn't already know
to look for `/api/openapi.json` specifically (this one URI stays at the
site root per the RFC, regardless of where the API itself lives).

### Primary API (plain JSON)

A normal JSON API for item/nano data - real HTTP status codes, no
legacy-endpoint quirks.

- `GET /api/items?q=<name>&ql=<ql>&limit=50&offset=0` or the equivalent
  `POST /api/items` with the same fields as a JSON body - both return a bare
  JSON array of `{"id", "name", "ql", "icon", "description"}`, with the
  total match count (pre-pagination) in the `X-Total-Count` response header.
- `GET /api/items/{aoid}` - direct lookup by item id, 404 (JSON body) if not
  found.
- `GET`/`POST /api/nanos` - same shape as `/api/items`, plus `school` (exact
  match, e.g. `Combat`/`Healing`/`Psionic`/`Space`/`Protection`) and
  `profession` (raw numeric profession id as it appears in the dump - see
  `/api/professions` below to resolve it to a name). Response objects
  additionally include `strain`, `nanocost`, `ncu`, `crystal_id`,
  `duration`, `profession`, and `requirements` (the full list of casting
  requirements from the dump, as `{"attribute", "operator", "value"}`).
- `GET /api/nanos/{aoid}` - direct lookup by nano id.
- `GET /api/professions` - the numeric profession id -> name mapping used by
  `/api/nanos`' `profession` field (e.g. `{"id": 11, "name": "Nano-Technician"}`),
  since the dump itself carries no such mapping. `GET /api/professions/{id}`
  looks up a single id, 404 if unassigned (e.g. id `13`). Sourced from
  [Nadybot](https://github.com/Nadybot/Nadybot)'s
  `OnlineController::getProfessionId()`, itself derived from Anarchy
  Online's own client data.

### Legacy (AOML)

`GET /api/legacy?output=aoml&max=50&search=<name>&ql=<ql>&icons=true&color_header=<hex>&color_highlight=<hex>&color_normal=<hex>`

Returns raw AOML (Anarchy Online chat markup) text instead of JSON, for
game-chat clients that expect that format and pass it straight through to
chat with zero parsing. Only `output=aoml` is implemented; any other value
returns HTTP 400. Otherwise always returns HTTP 200 with a body (including
"no results"), since these clients typically don't check the status code
and show the raw response verbatim in chat.

## Data

The item dump (a zipped `<aodb><item aoid="..." .../></aodb>` XML file, e.g.
`171003.xml.zip`) is parsed once and stored in MongoDB (`app/dump_loader.py`,
`app/store.py`), shared by every pod rather than each pod holding its own
in-memory copy. On startup, whichever pod acquires a short-lived Mongo-backed
lock parses the dump, then writes it via a series of small, named migrations
(`_MIGRATIONS` in `app/main.py`: create each store's indexes, insert items,
insert nanos, compute category/profession/school counts) instead of one
all-or-nothing call - each migration is recorded in the `migrations`
collection as it finishes, so a load that crashes partway through resumes
from the next unfinished migration on retry rather than redoing everything.
Every other pod just waits for the load to finish and then reads straight
from Mongo. Readiness is gated on every migration completing.

Name search (`q=`) is a substring match, not just a prefix - `q=smg` matches
"Combat SMG". Backed by a trigram index (3-char sliding windows of each name,
stored as a `trigrams` array field and queried with a multikey index, also
built in `app/store.py`) that narrows the search down before a final
in-Python check confirms the actual match and sorts the results; query
strings under 3 characters can't use that index and fall back to a full scan
instead (see the module docstring in `app/store.py` for the full design).

Set `MONGO_URL` to point at MongoDB (default `mongodb://localhost:27017/aodb`).
The Mongo instance is assumed dedicated to this app. A load never deletes or
replaces existing documents first (so an in-progress or crashed load can't
leave other pods reading an empty store) - it writes whatever ids aren't
already present and skips the rest (`insert_many(ordered=False)`, relying on
the unique `_id` index to reject already-loaded ids), which also makes
reloading the same or overlapping dump cheap. Ids removed from a newer dump
version aren't cleaned up automatically.

Redis is optional: set `REDIS_URL` to point at a Redis instance and it's used
as a cache-aside layer in front of `search()`/`count()` (the two queries
expensive enough to be worth caching - see `app/store.py`'s `_cached_json`),
with a short TTL rather than active invalidation on reload. Mongo is the
actual source of truth, so a missing or unreachable Redis only costs some
query latency, never correctness or availability - every Redis call here is
wrapped to log and fall back to querying Mongo directly on any error.

## Analytics

Both mechanisms below ship disabled by default - deploying this repo
as-is sends no analytics anywhere.

`/api` and every browse UI page will include the raw contents of
`app/templates/_analytics.html` if that file exists (e.g. a Cloudflare
Web Analytics beacon, a Google Analytics tag - see
`app/templates/_analytics.html.example` for the expected shape). That
file is `.gitignore`d and never committed, so it's never part of the
published image either - deploying via the chart, set
`aodbApi.analyticsHtml` to the same raw HTML and it gets mounted over
that path for you (see `chart/README.md.gotmpl`); running from source
some other way, supplying the file (a custom image layer, a mounted
secret, etc.) is up to you.

Separately, request-level analytics (method, path, status, response
time) can optionally be forwarded to [api-analytics](https://github.com/tom-draper/api-analytics)
by setting `API_ANALYTICS_KEY`; without it, that middleware simply isn't
registered (see `app/main.py`). Client IPs are never sent
(`privacy_level=2`). `/healthz`, `/robots.txt`,
`/.well-known/api-catalog`, and `/static/*` are excluded from logging
(api-analytics has no built-in path filter, so this is handled by
`_FilteredAnalytics` in `app/main.py`) since they're infra/crawler noise,
not real API usage.

## Metrics

`/metrics` exposes Prometheus-format request metrics (counts, latencies,
sizes, grouped by route template - e.g. `/items/{aoid}`, not one series
per aoid) via [prometheus-fastapi-instrumentator](https://github.com/trallnag/prometheus-fastapi-instrumentator).
It's always on and unauthenticated, same as the rest of this API. Deploying
via the chart, set `aodbApi.serviceMonitor.enabled=true` to have the
Prometheus Operator scrape it automatically (requires the
prometheus-operator CRDs already installed in the cluster - same
opt-in pattern as `aodbApi.redis.serviceMonitor` for the bundled Redis
exporter).

## Local development

```
cd api
pip install -r requirements-dev.txt
mongod &
DUMP_PATH=/path/to/171003.xml.zip uvicorn app.main:app --reload
pytest
```

Redis is optional locally too - only set `REDIS_URL`/run `redis-server` if
you want to exercise the cache layer; the app runs fine without it.

Tests use `mongomock-motor` and `fakeredis` and don't need a real Mongo or
Redis instance running.

`DUMP_URL` (used in production) works too; `DUMP_PATH` is for a local file
without needing network access.
