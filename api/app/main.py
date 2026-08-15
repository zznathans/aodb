import asyncio
import logging
import os
from contextlib import asynccontextmanager

from api_analytics.fastapi import Analytics, Config
from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .analytics import CLOUDFLARE_BEACON, GOOGLE_ANALYTICS_TAG
from .api import router as api_router
from .dump_loader import import_from_url, parse_dump_zip
from .legacy import router as legacy_router
from .professions import router as professions_router
from .redis_client import get_redis
from .sitemap import router as sitemap_router
from .store import nano_store, reset_all, store
from .web import router as web_router

# uvicorn/fastapi only configure their own named loggers (uvicorn,
# uvicorn.error, uvicorn.access) - the root logger is otherwise untouched
# and defaults to WARNING, which was silently dropping every INFO-level
# log below (including ones that already existed here). basicConfig() is a
# no-op if the root logger already has handlers, so this is safe however
# the app is actually run (uvicorn, fastapi run, tests, etc).
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

# Redis is shared by every pod, so only one pod needs to actually parse the
# dump and write it - the rest just wait for that to finish and then read
# the same data straight out of Redis. "Version" is just the source
# identifier: if it's unchanged since the last successful load, the data
# already in Redis is assumed current and reloading is skipped entirely.
_LOAD_LOCK_KEY = "aodb:load:lock"
_LOAD_READY_KEY = "aodb:load:ready"
_LOAD_LOCK_TTL_SECONDS = 300
_LOAD_WAIT_TIMEOUT_SECONDS = 120


async def _load_items() -> None:
    """Loads the item dump into Redis before the app starts accepting
    traffic, unless another pod has already loaded the same dump version.
    DUMP_URL pulls the dump zip from its public HTTPS URL - the normal
    deployed path. DUMP_PATH loads a local dump zip instead, for local dev.
    With neither set, Redis stays empty and the API serves "no results" for
    everything rather than failing to start."""
    version = os.environ.get("DUMP_URL") or os.environ.get("DUMP_PATH")
    if not version:
        logger.warning("Neither DUMP_URL nor DUMP_PATH is set - serving with empty item/nano stores")
        return

    logger.info("Connecting to Redis")
    client = get_redis()

    if await client.get(_LOAD_READY_KEY) == version:
        logger.info("Dump already loaded in Redis (version=%s) - skipping load", version)
        return

    if not await client.set(_LOAD_LOCK_KEY, "1", nx=True, ex=_LOAD_LOCK_TTL_SECONDS):
        logger.info("Another pod is loading the dump - waiting for it to finish")
        waited = 0.0
        while waited < _LOAD_WAIT_TIMEOUT_SECONDS:
            await asyncio.sleep(0.5)
            waited += 0.5
            if await client.get(_LOAD_READY_KEY) == version:
                logger.info("Dump load finished on another pod after %.1fs - ready", waited)
                return
        logger.warning("Timed out waiting for another pod to finish loading the dump - serving what's in Redis")
        return

    logger.info("Acquired load lock (version=%s) - this pod will load the dump", version)
    try:
        if await client.get(_LOAD_READY_KEY) == version:
            return  # someone else finished between our GET and our SET NX

        dump_url = os.environ.get("DUMP_URL")
        if dump_url:
            items, nanos = import_from_url(dump_url)
        else:
            logger.info("Reading local item dump from %s", os.environ["DUMP_PATH"])
            with open(os.environ["DUMP_PATH"], "rb") as f:
                items, nanos = parse_dump_zip(f.read())
            logger.info("Parsed %d items (%d nano programs) from %s", len(items), len(nanos), os.environ["DUMP_PATH"])

        logger.info("Flushing Redis before load")
        await reset_all()

        logger.info("Writing %d items to Redis", len(items))
        await store.load(items)

        logger.info("Writing %d nano programs to Redis", len(nanos))
        await nano_store.load(nanos)

        await client.set(_LOAD_READY_KEY, version)
        logger.info("Dump load complete (version=%s) - ready to serve", version)
    finally:
        await client.delete(_LOAD_LOCK_KEY)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _load_items()
    yield


app = FastAPI(title="aodb-api", lifespan=lifespan, docs_url=None)

# api-analytics (https://github.com/tom-draper/api-analytics) - optional,
# opt-in request analytics forwarded to a third-party dashboard. Only
# enabled when an API key is provisioned; privacy_level=2 means client IPs
# are never sent, since this is a public, unauthenticated API.
_analytics_key = os.environ.get("API_ANALYTICS_KEY")
if _analytics_key:
    app.add_middleware(Analytics, api_key=_analytics_key, config=Config(privacy_level=2))
else:
    logger.warning("API_ANALYTICS_KEY not set - request analytics middleware disabled")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api_router)
app.include_router(legacy_router)
app.include_router(professions_router)
app.include_router(web_router)
app.include_router(sitemap_router)


@app.get("/docs", include_in_schema=False)
def docs() -> HTMLResponse:
    html = get_swagger_ui_html(openapi_url=app.openapi_url, title=f"{app.title} - Swagger UI").body.decode()
    return HTMLResponse(html.replace("</head>", f"{CLOUDFLARE_BEACON}{GOOGLE_ANALYTICS_TAG}</head>"))


@app.get("/", include_in_schema=False)
def homepage():
    return RedirectResponse(url="/browse/")


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
# /openapi.json specifically. One linkset entry describing this single API;
# "anchor" is this API's own canonical base URL, derived from the request
# rather than hardcoded so it's correct regardless of what host/scheme it's
# actually reached through (custom domain, port-forward, etc.).
@app.get("/.well-known/api-catalog", include_in_schema=False)
def api_catalog(request: Request) -> JSONResponse:
    base = str(request.base_url).rstrip("/")
    linkset = {
        "linkset": [
            {
                "anchor": base,
                "service-desc": [{"href": f"{base}/openapi.json", "type": "application/vnd.oas.openapi+json"}],
                "service-doc": [{"href": f"{base}/docs", "type": "text/html"}],
            }
        ]
    }
    return JSONResponse(
        content=linkset,
        media_type='application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"',
    )
