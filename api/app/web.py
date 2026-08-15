"""Server-rendered HTML browse/search UI, independent of the JSON API
(app/api.py) and the legacy AOML contract (app/legacy.py) - mounted
under /browse specifically so it can never collide with the JSON API's
own /items and /nanos paths. Reads from the exact same store/nano_store
singletons app/api.py uses, so results are always consistent between the
two. Renders full pages with no JS required; app/static/search.js is a
progressive-enhancement layer on top that calls the JSON API directly for
snappier search/pagination once loaded.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .store import nano_store, store
from .templating import templates

router = APIRouter(prefix="/browse")

PAGE_SIZE = 50


def _clamp_page(page: int) -> int:
    return page if page >= 1 else 1


@router.get("/", response_class=HTMLResponse)
async def browse_home(request: Request):
    item_total = await store.count("", 0)
    nano_total = await nano_store.count("", 0, "", None)
    school_counts = await nano_store.school_counts()
    schools = sorted(school_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return templates.TemplateResponse(
        request,
        "browse_home.html",
        {"item_total": item_total, "nano_total": nano_total, "schools": schools},
    )


@router.get("/items", response_class=HTMLResponse)
async def browse_items(request: Request, q: str = "", ql: int = 0, page: int = 1):
    page = _clamp_page(page)
    offset = (page - 1) * PAGE_SIZE
    total = await store.count(q, ql)
    items = await store.search(q, ql, PAGE_SIZE, offset)
    return templates.TemplateResponse(
        request,
        "items.html",
        {
            "q": q,
            "ql": ql,
            "page": page,
            "page_size": PAGE_SIZE,
            "total": total,
            "items": items,
            "has_next": offset + PAGE_SIZE < total,
        },
    )


@router.get("/items/{aoid}", response_class=HTMLResponse)
async def item_detail(request: Request, aoid: int):
    item = await store.get(aoid)
    return templates.TemplateResponse(
        request,
        "item_detail.html",
        {"item": item, "aoid": aoid},
        status_code=200 if item is not None else 404,
    )


@router.get("/nanos", response_class=HTMLResponse)
async def browse_nanos(
    request: Request, q: str = "", ql: int = 0, school: str = "", profession: int | None = None, page: int = 1
):
    page = _clamp_page(page)
    offset = (page - 1) * PAGE_SIZE
    total = await nano_store.count(q, ql, school, profession)
    nanos = await nano_store.search(q, ql, school, profession, PAGE_SIZE, offset)
    return templates.TemplateResponse(
        request,
        "nanos.html",
        {
            "q": q,
            "ql": ql,
            "school": school,
            "profession": profession,
            "page": page,
            "page_size": PAGE_SIZE,
            "total": total,
            "nanos": nanos,
            "has_next": offset + PAGE_SIZE < total,
        },
    )


@router.get("/nanos/{aoid}", response_class=HTMLResponse)
async def nano_detail(request: Request, aoid: int):
    nano = await nano_store.get(aoid)
    return templates.TemplateResponse(
        request,
        "nano_detail.html",
        {"nano": nano, "aoid": aoid},
        status_code=200 if nano is not None else 404,
    )
