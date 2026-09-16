import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from api_analytics.fastapi import Analytics, Config
from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from pymongo.errors import DuplicateKeyError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .analytics import analytics_snippet
from .api import router as api_router
from .dump_loader import import_from_url, parse_dump_zip
from .legacy import router as legacy_router
from .mongo_client import get_mongo
from .professions import router as professions_router
from .sitemap import router as sitemap_router
from .store import Item, NanoProgram, nano_store, store
from .web import router as web_router

# uvicorn/fastapi only configure their own named loggers (uvicorn,
# uvicorn.error, uvicorn.access) - the root logger is otherwise untouched
# and defaults to WARNING, which was silently dropping every INFO-level
# log below (including ones that already existed here). basicConfig() is a
# no-op if the root logger already has handlers, so this is safe however
# the app is actually run (uvicorn, fastapi run, tests, etc).
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

# api_analytics (see the Analytics middleware below) hardcodes its own
# logger to DEBUG internally (api_analytics/core.py), ignoring the root
# level set above entirely - it logs every single request's full payload
# at DEBUG, which is noise in production. Only takes effect when
# API_ANALYTICS_KEY is actually set; harmless no-op otherwise.
logging.getLogger("api_analytics").setLevel(logging.WARNING)

# Mongo is shared by every pod, so only one pod needs to actually parse the
# dump and write it - the rest just wait for that to finish and then read
# the same data straight out of Mongo. "Version" is just the source
# identifier: if it's unchanged since the last successful load, the data
# already in Mongo is assumed current and reloading is skipped entirely.
# The lock is a single singleton document (_id=_LOAD_DOC_ID) in the
# load_state collection: find_one_and_update's atomic compare-and-swap
# acquires it (only matching when no other pod's lock is still within its
# TTL) the same way SET NX EX used to against Redis, and a plain field on
# the same doc tracks the ready version (mirrors GET on a separate key).
_LOAD_DOC_ID = "load"
_LOAD_LOCK_TTL = timedelta(seconds=300)
_LOAD_WAIT_TIMEOUT_SECONDS = 120

# The actual write work (index creation, bulk inserts, count aggregation)
# used to happen as one all-or-nothing call each to store.load()/
# nano_store.load() - a crash/OOMKill partway through meant the next pod
# to pick up the lock redid all of it from scratch (cheap for the mostly-
# idempotent Mongo writes themselves, but still real time re-parsing +
# re-running everything before the app could ever become ready). Named,
# ordered migrations give each step independent visibility (logged, and
# recorded in the "migrations" collection below) and let a resumed load
# skip whatever already finished, rather than redoing the whole thing.
# Every step takes the full parsed dump - re-parsing per pod restart is
# cheap (~5-10s for the whole dump, see app/dump_loader.py) compared to
# persisting/re-hydrating a partial parse result, so that's not itself
# tracked as a migration.
_MIGRATIONS: list[tuple[str, Callable[[list[Item], list[NanoProgram]], Awaitable[None]]]] = [
    ("ensure_indexes_items", lambda items, nanos: store.ensure_indexes()),
    ("ensure_indexes_nanos", lambda items, nanos: nano_store.ensure_indexes()),
    ("load_items", lambda items, nanos: store.insert_items(items)),
    ("load_nanos", lambda items, nanos: nano_store.insert_nanos(nanos)),
    ("compute_item_counts", lambda items, nanos: store.compute_counts(items)),
    ("compute_nano_counts", lambda items, nanos: nano_store.compute_counts(nanos)),
]


async def _run_pending_migrations(version: str, items: list[Item], nanos: list[NanoProgram]) -> None:
    """Runs every migration in _MIGRATIONS not yet recorded complete for
    this dump version, in order, recording each as it finishes. Migration
    docs are permanent (never cleaned up) and keyed by "{version}:{name}",
    so a later dump version starts every migration fresh while a retried
    load of the *same* version picks up wherever it left off."""
    migrations = get_mongo()["migrations"]
    for name, step in _MIGRATIONS:
        migration_id = f"{version}:{name}"
        if await migrations.find_one({"_id": migration_id}) is not None:
            logger.info("Migration %s already completed (version=%s) - skipping", name, version)
            continue
        logger.info("Running migration %s (version=%s)", name, version)
        await step(items, nanos)
        await migrations.update_one(
            {"_id": migration_id},
            {"$set": {"version": version, "name": name, "completed_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        logger.info("Migration %s complete (version=%s)", name, version)


async def _load_items() -> None:
    """Loads the item dump into Mongo before the app starts accepting
    traffic, unless another pod has already loaded the same dump version.
    DUMP_URL pulls the dump zip from its public HTTPS URL - the normal
    deployed path. DUMP_PATH loads a local dump zip instead, for local dev.
    With neither set, Mongo stays empty and the API serves "no results" for
    everything rather than failing to start.

    The dump is parsed once, then written via _run_pending_migrations - see
    _MIGRATIONS above for why that's a series of small, individually
    tracked steps rather than one call each to store.load()/
    nano_store.load()."""
    version = os.environ.get("DUMP_URL") or os.environ.get("DUMP_PATH")
    if not version:
        logger.warning("Neither DUMP_URL nor DUMP_PATH is set - serving with empty item/nano stores")
        return

    logger.info("Connecting to Mongo")
    load_state = get_mongo()["load_state"]

    doc = await load_state.find_one({"_id": _LOAD_DOC_ID})
    if doc is not None and doc.get("ready_version") == version:
        logger.info("Dump already loaded in Mongo (version=%s) - skipping load", version)
        return

    now = datetime.now(timezone.utc)
    locked_until = now + _LOAD_LOCK_TTL
    try:
        # First-ever load: no document exists yet, so this is the Mongo
        # analogue of Redis's SET NX - whichever pod's insert_one wins the
        # race gets the lock, the loser's raises a duplicate-key error and
        # falls through to the CAS below. Deliberately not upsert=True on
        # the find_one_and_update below instead: an upsert there would try
        # to insert a second doc with this same _id once one already
        # exists (just not matching the lock-available filter), which
        # raises the same duplicate-key error rather than the "no match"
        # this code needs to tell "someone else holds the lock" apart from
        # a real failure.
        await load_state.insert_one({"_id": _LOAD_DOC_ID, "locked_until": locked_until})
        acquired = {"_id": _LOAD_DOC_ID, "locked_until": locked_until}
    except DuplicateKeyError:
        acquired = await load_state.find_one_and_update(
            {"_id": _LOAD_DOC_ID, "$or": [{"locked_until": {"$lt": now}}, {"locked_until": {"$exists": False}}]},
            {"$set": {"locked_until": locked_until}},
            return_document=True,
        )

    if acquired is None:
        logger.info("Another pod is loading the dump - waiting for it to finish")
        waited = 0.0
        while waited < _LOAD_WAIT_TIMEOUT_SECONDS:
            await asyncio.sleep(0.5)
            waited += 0.5
            doc = await load_state.find_one({"_id": _LOAD_DOC_ID})
            if doc is not None and doc.get("ready_version") == version:
                logger.info("Dump load finished on another pod after %.1fs - ready", waited)
                return
        logger.warning("Timed out waiting for another pod to finish loading the dump - serving what's in Mongo")
        return

    logger.info("Acquired load lock (version=%s) - this pod will load the dump", version)
    try:
        if acquired.get("ready_version") == version:
            return  # someone else finished between our find_one check and our lock acquire

        dump_url = os.environ.get("DUMP_URL")
        if dump_url:
            items, nanos = import_from_url(dump_url)
        else:
            logger.info("Reading local item dump from %s", os.environ["DUMP_PATH"])
            with open(os.environ["DUMP_PATH"], "rb") as f:
                items, nanos = parse_dump_zip(f.read())
            logger.info("Parsed %d items (%d nano programs) from %s", len(items), len(nanos), os.environ["DUMP_PATH"])

        await _run_pending_migrations(version, items, nanos)

        await load_state.update_one({"_id": _LOAD_DOC_ID}, {"$set": {"ready_version": version}})
        logger.info("Dump load complete (version=%s) - ready to serve", version)
    finally:
        await load_state.update_one({"_id": _LOAD_DOC_ID}, {"$unset": {"locked_until": ""}})


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _load_items()
    yield


app = FastAPI(title="aodb", lifespan=lifespan, docs_url=None, openapi_url="/api/openapi.json")


class _FilteredAnalytics(Analytics):
    """api-analytics' Config (api_analytics/fastapi.py) has no option to
    exclude paths from logging - it records every request that reaches the
    middleware, unconditionally. Left as-is, that means k8s hitting
    /healthz every few seconds dominates the analytics dashboard with
    noise that isn't real API usage, along with crawler/infra requests
    for /robots.txt, /.well-known/api-catalog, and static assets. This
    subclass skips straight to call_next (bypassing Analytics.dispatch,
    and therefore log_request) for exactly those paths."""

    _EXCLUDED_PATHS = frozenset({"/healthz", "/robots.txt", "/.well-known/api-catalog", "/metrics"})
    _EXCLUDED_PREFIXES = ("/static/",)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in self._EXCLUDED_PATHS or path.startswith(self._EXCLUDED_PREFIXES):
            return await call_next(request)
        return await super().dispatch(request, call_next)


class HeadMethodMiddleware:
    """FastAPI 0.141.1 registers @app.get/@router.get routes with
    methods={"GET"} only - unlike plain Starlette Routes, it no longer adds
    HEAD automatically, so every GET endpoint site-wide 405s on HEAD
    (verified against this exact fastapi/starlette pin; see requirements.txt).
    That trips uptime/link checkers and crawlers that probe with HEAD before
    GET, which is what surfaces as a "soft 404". Works around it by routing
    HEAD requests through the matching GET handler and dropping the body,
    per normal HTTP HEAD semantics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        scope = {**scope, "method": "GET"}
        body_sent = False

        async def send_wrapper(message: dict) -> None:
            nonlocal body_sent
            if message["type"] == "http.response.body":
                if body_sent:
                    return
                body_sent = True
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(message)

        await self.app(scope, receive, send_wrapper)


# api-analytics (https://github.com/tom-draper/api-analytics) - optional,
# opt-in request analytics forwarded to a third-party dashboard. Only
# enabled when an API key is provisioned; privacy_level=2 means client IPs
# are never sent, since this is a public, unauthenticated API.
_analytics_key = os.environ.get("API_ANALYTICS_KEY")
if _analytics_key:
    app.add_middleware(_FilteredAnalytics, api_key=_analytics_key, config=Config(privacy_level=2))
else:
    logger.warning("API_ANALYTICS_KEY not set - request analytics middleware disabled")

app.add_middleware(HeadMethodMiddleware)

# TLS is always terminated in front of this app (Cloudflare/whatever ingress
# the cluster uses - see chart/values.yaml's ingress comments), so uvicorn
# itself only ever sees plain HTTP. Without this, request.base_url (used to
# build every sitemap <loc> and the /robots.txt Sitemap: line) reports
# "http://" even in production. trusted_hosts="*" is fine here since the
# service is never reachable except through that fronting proxy.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api_router, prefix="/api")
app.include_router(legacy_router, prefix="/api")
app.include_router(professions_router, prefix="/api")
app.include_router(sitemap_router)
app.include_router(web_router)


@app.get("/api", include_in_schema=False)
def docs() -> HTMLResponse:
    html = get_swagger_ui_html(openapi_url=app.openapi_url, title=f"{app.title} - Swagger UI").body.decode()
    return HTMLResponse(html.replace("</head>", f"{analytics_snippet()}</head>"))


@app.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
def healthz():
    return "ok"


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots(request: Request):
    base = str(request.base_url).rstrip("/")
    return f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"


# RFC 9727 (https://www.rfc-editor.org/rfc/rfc9727) - a standard well-known
# location advertising this API's machine-readable description, so a
# generic API-discovery client doesn't need to already know about
# /api/openapi.json specifically. This URI's own location is fixed by the
# RFC (always at the site root, regardless of where the API itself lives)
# - only the hrefs inside point into /api. One linkset entry describing
# this single API; "anchor" is this API's own canonical base URL, derived
# from the request rather than hardcoded so it's correct regardless of
# what host/scheme it's actually reached through (custom domain,
# port-forward, etc.).
@app.get("/.well-known/api-catalog", include_in_schema=False)
def api_catalog(request: Request) -> JSONResponse:
    base = str(request.base_url).rstrip("/")
    linkset = {
        "linkset": [
            {
                "anchor": base,
                "service-desc": [{"href": f"{base}/api/openapi.json", "type": "application/vnd.oas.openapi+json"}],
                "service-doc": [{"href": f"{base}/api", "type": "text/html"}],
            }
        ]
    }
    return JSONResponse(
        content=linkset,
        media_type='application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"',
    )


# prometheus-fastapi-instrumentator (https://github.com/trallnag/prometheus-fastapi-instrumentator)
# - per-request latency/count/size metrics grouped by route template (e.g.
# "/items/{aoid}", not one series per aoid). instrument() is called last,
# after every route above is registered, so it can see the full route table
# when building its handler-grouping. Scraped by the ServiceMonitor in
# chart/templates/deployment.yaml, the same pattern already used for the
# bundled Redis exporter in chart/templates/redis.yaml.
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
