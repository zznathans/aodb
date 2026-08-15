"""Single shared Jinja2Templates instance, split out from web.py so main.py
can import web.py's router without a circular import."""

from fastapi.templating import Jinja2Templates

from .professions import PROFESSION_NAMES

templates = Jinja2Templates(directory="app/templates")


def profession_name(profession_id: int | None) -> str:
    """id -> display name, e.g. `{{ nano.profession | profession_name }}`.
    None (no profession assigned - see NanoProgram.profession) renders the
    same "General" label used everywhere else for that case (the profession
    tile grid, the profession=0 sentinel in NanoStore._filtered_matches)."""
    if profession_id is None:
        return "General"
    return PROFESSION_NAMES.get(profession_id, f"Unknown ({profession_id})")


def profession_slug(profession_id: int | None) -> str:
    """id -> URL slug for /nanos/professions/{slug}, e.g.
    `{{ nano.profession | profession_slug }}`."""
    if profession_id is None:
        return "general"
    name = PROFESSION_NAMES.get(profession_id)
    return name.lower().replace(" ", "-") if name else str(profession_id)


templates.env.filters["profession_name"] = profession_name
templates.env.filters["profession_slug"] = profession_slug
