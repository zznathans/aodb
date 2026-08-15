# aodb api

A self-hosted Anarchy Online item/nano database API: parses the official
item dump and serves it back out as a JSON API, plus a legacy
AOML-compatible endpoint returning raw chat-markup text for game-chat
clients that expect that format.

See the [repo README](https://github.com/zznathans/aodb#readme) for how
this fits together with the [Helm chart](https://github.com/zznathans/aodb/tree/main/chart)
that deploys it.

## Browsing

`GET /` redirects to `/browse/` - a server-rendered (Jinja2, no build
step, no JS framework) landing page with overall item/nano counts and a
nanos-by-school breakdown, linking into `/browse/items` and
`/browse/nanos` for searching by hand. Each has a search form and
paginated results linking to a `/browse/items/{aoid}` /
`/browse/nanos/{aoid}` detail page. Works with JavaScript disabled (plain
GET forms and links); `app/static/search.js` progressively enhances
search/pagination to fetch from the JSON API below and re-render in
place instead of a full page reload, when JS is available. Icons aren't
rendered (the `icon` field is a numeric AO client icon id with no public
image mapping this app takes a dependency on).

Crawlers are pointed at the catalog via `/robots.txt` (`Sitemap:` directive)
and `/sitemap.xml` - a sitemap index (per
[sitemaps.org](https://www.sitemaps.org/protocol.html)) referencing
`/sitemap-pages.xml` (the two `/browse` landing pages) plus chunked
`/sitemap-items-{n}.xml` / `/sitemap-nanos-{n}.xml` sub-sitemaps (50,000
URLs each, since the full catalog exceeds a single sitemap's limit).
Responses are cached (`Cache-Control: public, max-age=3600`) since the
underlying data only changes on a dump reload.

## API

Swagger UI is at `/docs`; machine-readable spec is at `/openapi.json`
(`/healthz` is intentionally excluded from both). `/.well-known/api-catalog`
advertises both per [RFC 9727](https://www.rfc-editor.org/rfc/rfc9727) -
a standard well-known location for API discovery tooling that doesn't
already know to look for `/openapi.json` specifically.

### Primary API (plain JSON)

A normal JSON API for item/nano data - real HTTP status codes, no
legacy-endpoint quirks.

- `GET /items?q=<name>&ql=<ql>&limit=50&offset=0` or the equivalent
  `POST /items` with the same fields as a JSON body - both return a bare
  JSON array of `{"id", "name", "ql", "icon", "description"}`, with the
  total match count (pre-pagination) in the `X-Total-Count` response header.
- `GET /items/{aoid}` - direct lookup by item id, 404 (JSON body) if not
  found.
- `GET`/`POST /nanos` - same shape as `/items`, plus `school` (exact
  match, e.g. `Combat`/`Healing`/`Psionic`/`Space`/`Protection`) and
  `profession` (raw numeric profession id as it appears in the dump - see
  `/professions` below to resolve it to a name). Response objects
  additionally include `strain`, `nanocost`, `ncu`, `crystal_id`,
  `duration`, `profession`, and `requirements` (the full list of casting
  requirements from the dump, as `{"attribute", "operator", "value"}`).
- `GET /nanos/{aoid}` - direct lookup by nano id.
- `GET /professions` - the numeric profession id -> name mapping used by
  `/nanos`' `profession` field (e.g. `{"id": 11, "name": "Nano-Technician"}`),
  since the dump itself carries no such mapping. `GET /professions/{id}`
  looks up a single id, 404 if unassigned (e.g. id `13`). Sourced from
  [Nadybot](https://github.com/Nadybot/Nadybot)'s
  `OnlineController::getProfessionId()`, itself derived from Anarchy
  Online's own client data.

### Legacy (AOML)

`GET /legacy?output=aoml&max=50&search=<name>&ql=<ql>&icons=true&color_header=<hex>&color_highlight=<hex>&color_normal=<hex>`

Returns raw AOML (Anarchy Online chat markup) text instead of JSON, for
game-chat clients that expect that format and pass it straight through to
chat with zero parsing. Only `output=aoml` is implemented; any other value
returns HTTP 400. Otherwise always returns HTTP 200 with a body (including
"no results"), since these clients typically don't check the status code
and show the raw response verbatim in chat.

## Data

The item dump (a zipped `<aodb><item aoid="..." .../></aodb>` XML file, e.g.
`171003.xml.zip`) is parsed once and stored in Redis (`app/dump_loader.py`,
`app/store.py`), shared by every pod rather than each pod holding its own
in-memory copy. On startup, whichever pod acquires a short-lived Redis lock
does the parse-and-load; the rest just wait for it to finish and then read
straight from Redis. Readiness is gated on this completing.

Name search (`q=`) is a prefix match against a Redis sorted-set index, not a
substring match - `q=smg` will not match "Combat SMG".

Set `REDIS_URL` to point at Redis (default `redis://localhost:6379/0`). The
Redis instance is assumed dedicated to this app - a fresh load flushes it.

## Analytics

Both mechanisms below ship disabled by default - deploying this repo
as-is sends no analytics anywhere.

`/docs` and every `/browse/*` page will include the raw contents of
`app/templates/_analytics.html` if that file exists (e.g. a Cloudflare
Web Analytics beacon, a Google Analytics tag - see
`app/templates/_analytics.html.example` for the expected shape). That
file is `.gitignore`d and never committed; supplying one (a custom image
layer, a mounted secret, etc.) is entirely up to whoever deploys this.

Separately, request-level analytics (method, path, status, response
time) can optionally be forwarded to [api-analytics](https://github.com/tom-draper/api-analytics)
by setting `API_ANALYTICS_KEY`; without it, that middleware simply isn't
registered (see `app/main.py`). Client IPs are never sent
(`privacy_level=2`).

## Local development

```
cd api
pip install -r requirements-dev.txt
redis-server &
DUMP_PATH=/path/to/171003.xml.zip uvicorn app.main:app --reload
pytest
```

Tests use `fakeredis` and don't need a real Redis instance running.

`DUMP_URL` (used in production) works too; `DUMP_PATH` is for a local file
without needing network access.
