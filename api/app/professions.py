"""Numeric profession id -> name mapping for the `profession` field on nano
programs (see app/store.py's NanoProgram, app/api.py's NanoOut). The dump
only carries the raw numeric id with no accompanying name, and there's no
such mapping anywhere else in this repo - these ids and names come from
Nadybot's OnlineController::getProfessionId()
(https://github.com/Nadybot/Nadybot/blob/main/src/Modules/ONLINE_MODULE/OnlineController.php),
itself derived from Anarchy Online's own client data (cross-checked against
the client's GFX_GUI_ICON_PROFESSION_<id> icon naming in Nadybot's
DiscordController). Id 13 has no assigned profession in either source.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

PROFESSION_NAMES: dict[int, str] = {
    1: "Soldier",
    2: "Martial Artist",
    3: "Engineer",
    4: "Fixer",
    5: "Agent",
    6: "Adventurer",
    7: "Trader",
    8: "Bureaucrat",
    9: "Enforcer",
    10: "Doctor",
    11: "Nano-Technician",
    12: "Meta-Physicist",
    14: "Keeper",
    15: "Shade",
}

# URL-friendly slug -> id, for /nanos/professions/{slug} (see
# app/web.py) - lets that URL read as a name ("nano-technician") rather
# than a raw numeric id a visitor has no way to know.
PROFESSION_SLUGS: dict[str, int] = {name.lower().replace(" ", "-"): id_ for id_, name in PROFESSION_NAMES.items()}


class ProfessionOut(BaseModel):
    id: int
    name: str


@router.get("/professions")
def list_professions() -> list[ProfessionOut]:
    return [ProfessionOut(id=id_, name=name) for id_, name in sorted(PROFESSION_NAMES.items())]


@router.get("/professions/{id}")
def get_profession(id: int) -> ProfessionOut:
    name = PROFESSION_NAMES.get(id)
    if name is None:
        raise HTTPException(status_code=404, detail=f"No profession with id {id}")
    return ProfessionOut(id=id, name=name)
