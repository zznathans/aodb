"""Server-rendered HTML browse/search UI, independent of the JSON API
(app/api.py, mounted under /api) and the legacy AOML contract (app/legacy.py,
/api/legacy). Lives at the site root so it can never collide with the JSON
API's own /api/items and /api/nanos paths. Reads from the exact same
store/nano_store singletons app/api.py uses, so results are always
consistent between the two. Renders full pages with no JS required;
app/static/search.js is a progressive-enhancement layer on top that calls
the JSON API directly for snappier search/pagination once loaded.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .item_categories import CATEGORY_NAMES
from .professions import PROFESSION_NAMES, PROFESSION_SLUGS
from .store import Item, nano_store, store
from .templating import templates

router = APIRouter()

PAGE_SIZE = 50

# profession=0 is the "no profession assigned" sentinel (see
# NanoStore._filtered_matches) - "general" is its slug for
# /nanos/professions/{slug}, same idea as an item's "general" category.
_NANO_PROFESSION_SLUGS: dict[str, int] = {**PROFESSION_SLUGS, "general": 0}

# Categories with real subcategory data - see _render_items(). Equip slot
# for armor/implant/utility, primary attack skill for weapon (see
# app/dump_loader.py's _item_subcategory()).
_SUBCATEGORY_BROWSABLE_CATEGORIES = {"armor", "implant", "weapon", "utility"}


def _clamp_page(page: int) -> int:
    return page if page >= 1 else 1


def _slugify(name: str) -> str:
    return name.lower().replace(" ", "-").replace("/", "-")


def _merge_ql_variants(items: list[Item]) -> list[dict]:
    """Collapses consecutive items that differ only by ql (same name and
    description - the dump carries every quality-level of an item as a
    fully separate entry, e.g. dozens of "Notum Tank Armor" rows) into one
    display row showing a ql range instead of a wall of near-duplicates.
    Only merges *consecutive* items, which is enough given results are
    already name-sorted (same-name items land next to each other) - a
    same-name group that happens to straddle a page boundary won't merge
    across pages, which is an acceptable rough edge for a display-only
    grouping like this."""
    merged: list[dict] = []
    for item in items:
        if merged and merged[-1]["name"] == item.name and merged[-1]["description"] == item.description:
            group = merged[-1]
            new_min = min(group["ql_min"], item.ql)
            if item.ql == new_min:
                group["id"] = item.id
            group["ql_min"] = new_min
            group["ql_max"] = max(group["ql_max"], item.ql)
        else:
            merged.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                    "ql_min": item.ql,
                    "ql_max": item.ql,
                }
            )
    for group in merged:
        group["ql_display"] = (
            str(group["ql_min"]) if group["ql_min"] == group["ql_max"] else f"{group['ql_min']}–{group['ql_max']}"
        )
    return merged


def _interpolate_value(lo: int, hi: int, lo_ql: int, hi_ql: int, target_ql: int) -> int:
    if hi_ql == lo_ql:
        return lo
    return round(lo + (hi - lo) * (target_ql - lo_ql) / (hi_ql - lo_ql))


def _bracket_variants(variants: list[Item], target_ql: int) -> tuple[Item, Item]:
    """The two ql "printings" of an item that target_ql falls between -
    same pair the game client interpolates between for a ql the dump
    doesn't have its own row for. target_ql is clamped to the variant
    list's own range first."""
    target_ql = max(variants[0].ql, min(target_ql, variants[-1].ql))
    lo, hi = variants[0], variants[-1]
    for variant in variants:
        if variant.ql <= target_ql:
            lo = variant
        if variant.ql >= target_ql:
            hi = variant
            break
    return lo, hi


def _interpolate_requirements(lo: Item, hi: Item, target_ql: int) -> list[dict]:
    """"To Equip" requirements scale the same way damage/stats do between
    an item's two known ql printings (verified against real dump data -
    e.g. "Useless Triple-Blade" needs 1h Edged 5 at ql 1 vs 89 at ql 23).
    Fixed-identity requirements (exactly-type, e.g. Profession/Breed/Gender)
    aren't scaled - shown as-is from the higher-ql variant, which
    consistently carries them for interpolation pairs in this dump."""
    lo_reqs = {req.attribute: req for req in lo.requirements if req.hook == "To Equip"}
    result = []
    for req in hi.requirements:
        if req.hook != "To Equip":
            continue
        lo_req = lo_reqs.get(req.attribute)
        value = req.value
        if (
            lo_req is not None
            and req.operator in ("at least", "at most")
            and lo_req.value.lstrip("-").isdigit()
            and req.value.lstrip("-").isdigit()
        ):
            value = str(_interpolate_value(int(lo_req.value), int(req.value), lo.ql, hi.ql, target_ql))
        result.append({"hook": req.hook, "attribute": req.attribute, "operator": req.operator, "value": value})
    return result


def _interpolated_item_stats(variants: list[Item], target_ql: int) -> dict:
    """Linearly interpolates damage, "Modify" stat effects, and "To Equip"
    requirements between the two ql variants bracketing target_ql - this is
    the same interpolation the AO client itself does for items that only
    carry a low-ql and high-ql dump entry (see _bracket_variants)."""
    lo, hi = _bracket_variants(variants, target_ql)
    target_ql = max(variants[0].ql, min(target_ql, variants[-1].ql))

    damage_min = damage_max = damage_critical = None
    if lo.damage_min is not None and hi.damage_min is not None:
        damage_min = _interpolate_value(lo.damage_min, hi.damage_min, lo.ql, hi.ql, target_ql)
        damage_max = _interpolate_value(lo.damage_max or 0, hi.damage_max or 0, lo.ql, hi.ql, target_ql)
        damage_critical = _interpolate_value(
            lo.damage_critical or 0, hi.damage_critical or 0, lo.ql, hi.ql, target_ql
        )

    def _modify_stats(item: Item) -> dict[str, int]:
        return {
            e.attribute: int(e.value)
            for e in item.effects
            if e.action == "Modify" and e.attribute and e.value and e.value.lstrip("-").isdigit()
        }

    lo_stats = _modify_stats(lo)
    hi_stats = _modify_stats(hi)
    stats = [
        {"attribute": attribute, "value": _interpolate_value(lo_stats[attribute], value, lo.ql, hi.ql, target_ql)}
        for attribute, value in hi_stats.items()
        if attribute in lo_stats
    ]
    stats.sort(key=lambda s: s["attribute"])

    return {
        "ql": target_ql,
        "damage_min": damage_min,
        "damage_max": damage_max,
        "damage_critical": damage_critical,
        "stats": stats,
        "requirements": _interpolate_requirements(lo, hi, target_ql),
    }


def _qs(**params: str | int | None) -> str:
    """Builds a query string for the "view this in the API" links shown
    next to each page's title, dropping params that mean "unset" for that
    param (None, empty string) - passed explicitly per call since e.g.
    ql=0 means "any" but profession=0 is a real filter (the "no profession
    assigned" sentinel - see NanoStore._filtered_matches)."""
    return urlencode({k: v for k, v in params.items() if v not in (None, "")})


@router.get("/", response_class=HTMLResponse)
async def browse_home(request: Request):
    item_total = await store.count("", 0)
    nano_total = await nano_store.count("", 0, "", None)
    return templates.TemplateResponse(
        request,
        "browse_home.html",
        {"item_total": item_total, "nano_total": nano_total},
    )


async def _render_items(request: Request, q: str, category: str, page: int, subcategory: str = "") -> HTMLResponse:
    page = _clamp_page(page)
    offset = (page - 1) * PAGE_SIZE

    # "armor", "implant", and "weapon" all carry real subcategory data (see
    # app/dump_loader.py's _item_subcategory()) and get a breakdown
    # (/categories/{category}/types/{type}) in place of the usual "pick a
    # category, see everything" behavior - selecting one of these
    # categories alone isn't enough to show results, only an actual search
    # or picking a specific subcategory is.
    show_subcategory_grid = category in _SUBCATEGORY_BROWSABLE_CATEGORIES and not subcategory
    has_query = bool(q or subcategory or (category and not show_subcategory_grid))

    total = await store.count(q, 0, category, subcategory) if has_query else 0
    items = await store.search(q, 0, PAGE_SIZE, offset, category, subcategory) if has_query else []
    items = _merge_ql_variants(items)

    category_counts = await store.category_counts()
    categories = [
        {"id": id_, "name": name, "item_count": category_counts.get(id_, 0)}
        for id_, name in sorted(CATEGORY_NAMES.items(), key=lambda kv: kv[1])
    ]

    subcategories = None
    if show_subcategory_grid:
        subcategory_counts = await store.subcategory_counts(category)
        subcategories = [
            {"slug": _slugify(name), "name": name, "item_count": count}
            for name, count in sorted(subcategory_counts.items(), key=lambda kv: kv[0])
        ]

    json_api_url = (
        "/api/items?" + _qs(q=q, category=category, subcategory=subcategory, limit=PAGE_SIZE, offset=offset)
        if has_query
        else None
    )
    aoml_api_url = (
        "/api/legacy?" + _qs(output="aoml", search=q, max=PAGE_SIZE) if has_query and not category else None
    )
    return templates.TemplateResponse(
        request,
        "items.html",
        {
            "q": q,
            "category": category,
            "category_name": CATEGORY_NAMES.get(category),
            "subcategory": subcategory,
            "page": page,
            "page_size": PAGE_SIZE,
            "total": total,
            "items": items,
            "has_next": offset + PAGE_SIZE < total,
            "categories": categories,
            "subcategories": subcategories,
            "has_query": has_query,
            "json_api_url": json_api_url,
            "aoml_api_url": aoml_api_url,
        },
    )


@router.get("/items", response_class=HTMLResponse)
async def browse_items(request: Request, q: str = "", page: int = 1):
    return await _render_items(request, q, "", page)


@router.get("/items/categories/{slug}", response_class=HTMLResponse)
async def browse_items_by_category(request: Request, slug: str, q: str = "", page: int = 1):
    if slug not in CATEGORY_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown category '{slug}'")
    return await _render_items(request, q, slug, page)


@router.get("/items/categories/{slug}/types/{type_slug}", response_class=HTMLResponse)
async def browse_items_by_category_subcategory(request: Request, slug: str, type_slug: str, q: str = "", page: int = 1):
    if slug not in CATEGORY_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown category '{slug}'")
    subcategory_counts = await store.subcategory_counts(slug)
    subcategory = next((name for name in subcategory_counts if _slugify(name) == type_slug), None)
    if subcategory is None:
        raise HTTPException(status_code=404, detail=f"Unknown type '{type_slug}' for category '{slug}'")
    return await _render_items(request, q, slug, page, subcategory)


@router.get("/items/{aoid}", response_class=HTMLResponse)
async def item_detail(request: Request, aoid: int, ql: int | None = None):
    item = await store.get(aoid)
    if item is None:
        return templates.TemplateResponse(
            request,
            "item_detail.html",
            {"item": None, "aoid": aoid, "json_api_url": f"/api/items/{aoid}", "category_name": None},
            status_code=404,
        )

    category_name = CATEGORY_NAMES.get(item.category, item.category)
    variants = await store.get_ql_variants(item.name_lower, item.description)
    ql_min = variants[0].ql if variants else item.ql
    ql_max = variants[-1].ql if variants else item.ql
    has_ql_range = ql_min != ql_max

    display = None
    if has_ql_range:
        display = _interpolated_item_stats(variants, ql if ql is not None else item.ql)

    return templates.TemplateResponse(
        request,
        "item_detail.html",
        {
            "item": item,
            "aoid": aoid,
            "json_api_url": f"/api/items/{aoid}",
            "category_name": category_name,
            "has_ql_range": has_ql_range,
            "ql_min": ql_min,
            "ql_max": ql_max,
            "display": display,
        },
        status_code=200,
    )


async def _render_nanos(request: Request, q: str, profession: int | None, page: int) -> HTMLResponse:
    page = _clamp_page(page)
    offset = (page - 1) * PAGE_SIZE
    has_query = bool(q or profession is not None)
    total = await nano_store.count(q, 0, "", profession) if has_query else 0
    nanos = await nano_store.search(q, 0, "", profession, PAGE_SIZE, offset) if has_query else []

    profession_counts = await nano_store.profession_counts()
    professions = [
        {
            "id": id_,
            "slug": slug,
            "name": PROFESSION_NAMES[id_],
            "nano_count": profession_counts.get(id_, 0),
        }
        for slug, id_ in sorted(PROFESSION_SLUGS.items(), key=lambda kv: PROFESSION_NAMES[kv[1]])
    ]
    # profession=0 is the "no profession assigned" sentinel (see
    # NanoStore._filtered_matches) - the generic nanos every profession gets.
    # Reuses count()'s own profession=0 handling rather than
    # nano_total-minus-sum, since that undercounted nanos with a
    # profession-shaped requirement that isn't an "exactly" operator.
    general_count = await nano_store.count("", 0, "", 0)
    professions.append({"id": 0, "slug": "general", "name": "General", "nano_count": general_count})
    json_api_url = (
        "/api/nanos?" + _qs(q=q, profession=profession, limit=PAGE_SIZE, offset=offset) if has_query else None
    )
    return templates.TemplateResponse(
        request,
        "nanos.html",
        {
            "q": q,
            "profession": profession,
            "page": page,
            "page_size": PAGE_SIZE,
            "total": total,
            "nanos": nanos,
            "has_next": offset + PAGE_SIZE < total,
            "professions": professions,
            "has_query": has_query,
            "json_api_url": json_api_url,
        },
    )


@router.get("/nanos", response_class=HTMLResponse)
async def browse_nanos(request: Request, q: str = "", page: int = 1):
    return await _render_nanos(request, q, None, page)


@router.get("/nanos/professions/{slug}", response_class=HTMLResponse)
async def browse_nanos_by_profession(request: Request, slug: str, q: str = "", page: int = 1):
    profession = _NANO_PROFESSION_SLUGS.get(slug)
    if profession is None:
        raise HTTPException(status_code=404, detail=f"Unknown profession '{slug}'")
    return await _render_nanos(request, q, profession, page)


@router.get("/nanos/{aoid}", response_class=HTMLResponse)
async def nano_detail(request: Request, aoid: int):
    nano = await nano_store.get(aoid)
    return templates.TemplateResponse(
        request,
        "nano_detail.html",
        {"nano": nano, "aoid": aoid, "json_api_url": f"/api/nanos/{aoid}"},
        status_code=200 if nano is not None else 404,
    )
