"""Parses the real AO item dump format: a zip archive containing a single
large <aodb><item aoid="..." .../>...</aodb> XML file (e.g.
"171003.xml.zip"). Used by the S3/URL auto-import startup hook in
app/main.py.

Parses with ET.iterparse and clears each <item> element after reading it,
so peak memory stays proportional to one item at a time rather than the
whole ~180MB decompressed document - verified against the real dump:
125,269 items parsed in ~6s at ~65MB peak RSS.

Every <item> (any metatype) becomes an Item, same as before. <item
metatype="n"> elements (nano programs, ~10.5k of the ~125k total) are
additionally parsed into a richer NanoProgram in the same pass - one XML
walk produces both lists, rather than scanning the document twice.
"""

import io
import logging
import re
import zipfile
from typing import IO
from xml.etree import ElementTree as ET

from .store import Effect, Item, NanoProgram, Requirement, make_item, make_nano

logger = logging.getLogger(__name__)


def _int_or_none(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


# <item metatype="i"><type>N</type>...</item> - N's meaning isn't documented
# anywhere, derived by sampling real dump items per (metatype, type) pair:
# type=4 ("Equip Generic Innate Weapon", "Equip Monster Wpn PriDist 3 0", ...)
# is exclusively NPC/monster-only weapon stubs, never a real player item, so
# those are dropped entirely (see the crystal_id/description filtering in
# NanoStore.load() for the nano-side equivalent of this same kind of junk).
_ITEM_TYPE_CATEGORY = {
    "0": "general",
    "1": "utility",
    "2": "armor",
    "3": "implant",
    "5": "spirit",
}


def _item_category(metatype: str | None, type_text: str | None) -> str | None:
    if metatype == "w":
        return "weapon"
    if metatype == "n":
        # Nano program entries are dual-listed in the plain item catalog
        # too (see parse_dump_xml) - bucketed as general there since
        # they're already browsable on their own via /nanos.
        return "general"
    if type_text == "4":
        return None
    return _ITEM_TYPE_CATEGORY.get(type_text or "", "general")


# <slots>...</slots> lists every body location an item can be equipped in
# as free text (e.g. "Right wrist, Left wrist", or all 15 locations at once
# for "social"/full-body armor) - there's no separate left/right slot, an
# item wearable on either wrist just lists both. Normalized down to one
# canonical slot name per item so armor/implants can be browsed by
# equipment slot (see /items/categories/{category}/types in
# app/web.py); ambiguous or missing cases fall back to "" (excluded from
# that breakdown entirely).
_SOCIAL_SLOT_THRESHOLD = 10


def _armor_implant_subcategory(slots_text: str | None) -> str:
    if not slots_text:
        return ""
    parts = [p.strip() for p in slots_text.split(",") if p.strip()]
    if not parts:
        return ""
    if len(parts) >= _SOCIAL_SLOT_THRESHOLD:
        return "Social/Full Body"
    bases = {p.removeprefix("Right ").removeprefix("Left ") for p in parts}
    if len(bases) != 1:
        return "Other"
    return next(iter(bases)).capitalize()


_SLOT_FAMILY_RE = re.compile(r"^(.*?)\s*\d+$")


def _utility_subcategory(slots_text: str | None) -> str:
    """Utility items' <slots> uses AO's utility-slot families instead of
    body locations (e.g. "Utils 1, Utils 2, Utils 3", "Hud 1, Hud 3, Hud 2",
    "Deck 1, Deck 2, ..."), so the numbered-instance suffix is stripped
    instead of a "Right "/"Left " prefix (see _armor_implant_subcategory)."""
    if not slots_text:
        return ""
    parts = [p.strip() for p in slots_text.split(",") if p.strip()]
    if not parts:
        return ""
    families = {_SLOT_FAMILY_RE.sub(r"\1", p) for p in parts}
    if len(families) != 1:
        return "Multi-Slot"
    return next(iter(families))


def _weapon_subcategory(elem: ET.Element) -> str:
    """The dump has no explicit weapon-type field - every weapon's primary
    attack skill (e.g. Pistol, Rifle, 1h Blunt) stands in for one instead,
    same idea as armor/implants using equip slot. "Primary" = the attack
    skillmap entry with the highest percentage weighting."""
    for skillmap_el in elem.findall("skillmap"):
        if skillmap_el.get("type") != "attack":
            continue
        skills = skillmap_el.findall("skill")
        if not skills:
            return ""
        best = max(skills, key=lambda s: float(s.get("percentage") or 0))
        return best.get("name") or ""
    return ""


def _item_subcategory(category: str, elem: ET.Element) -> str:
    if category in ("armor", "implant"):
        slots_el = elem.find("slots")
        return _armor_implant_subcategory(slots_el.text if slots_el is not None else None)
    if category == "weapon":
        return _weapon_subcategory(elem)
    if category == "utility":
        slots_el = elem.find("slots")
        return _utility_subcategory(slots_el.text if slots_el is not None else None)
    return ""


def _parse_item(elem: ET.Element) -> Item | None:
    aoid = elem.get("aoid")
    name_el = elem.find("name")
    name = (name_el.text or "").strip() if name_el is not None else ""
    if not aoid or not name:
        return None

    type_el = elem.find("type")
    category = _item_category(elem.get("metatype"), type_el.text if type_el is not None else None)
    if category is None:
        return None

    ql_el = elem.find("ql")
    icon_el = elem.find("icon")
    desc_el = elem.find("description")

    damage_min, damage_max, damage_critical = _parse_damage(elem)

    return make_item(
        id=int(aoid),
        name=name,
        ql=int(ql_el.text) if ql_el is not None and ql_el.text else 0,
        icon=int(icon_el.text) if icon_el is not None and icon_el.text else 0,
        description=(desc_el.text or None) if desc_el is not None else None,
        category=category,
        subcategory=_item_subcategory(category, elem),
        effects=_parse_effects(elem),
        requirements=_parse_requirements(elem),
        damage_min=damage_min,
        damage_max=damage_max,
        damage_critical=damage_critical,
    )


def _parse_effects(elem: ET.Element) -> tuple[Effect, ...]:
    """Shared by items and nanos - both carry the same <effects><effect
    hook=... target=... action=... attribute=... value=... /></effects>
    shape (on-equip stat bonuses for items, on-use nano effects for nanos)."""
    effects_el = elem.find("effects")
    if effects_el is None:
        return ()
    return tuple(
        Effect(
            hook=eff_el.get("hook") or "",
            target=eff_el.get("target") or "",
            action=eff_el.get("action") or "",
            attribute=eff_el.get("attribute"),
            value=eff_el.get("value"),
        )
        for eff_el in effects_el.findall("effect")
    )


def _parse_requirements(elem: ET.Element) -> tuple[Requirement, ...]:
    """Shared by items and nanos - both carry the same <requirements>
    <requirement hook=... attribute=... operator=... value=.../></requirements>
    shape (stat minimums to equip/use an item, cast a nano, etc.)."""
    requirements_el = elem.find("requirements")
    if requirements_el is None:
        return ()
    return tuple(
        Requirement(
            hook=req_el.get("hook") or "",
            attribute=req_el.get("attribute") or "",
            operator=req_el.get("operator") or "",
            value=req_el.get("value") or "",
        )
        for req_el in requirements_el.findall("requirement")
    )


def _parse_damage(elem: ET.Element) -> tuple[int | None, int | None, int | None]:
    """<damage minimum=.. maximum=.. critical=.. type=../> - weapons only.
    "type" (damage element type) has no known name mapping anywhere in
    this repo (unlike e.g. profession ids), so it's left out entirely
    rather than guessed at."""
    damage_el = elem.find("damage")
    if damage_el is None:
        return None, None, None
    return (
        _int_or_none(damage_el.get("minimum")),
        _int_or_none(damage_el.get("maximum")),
        _int_or_none(damage_el.get("critical")),
    )


def _parse_nano(elem: ET.Element, item: Item) -> NanoProgram:
    nanoclass_el = elem.find("nanoclass")
    school = nanoclass_el.get("school") if nanoclass_el is not None else None
    strain = _int_or_none(nanoclass_el.get("strain")) if nanoclass_el is not None else None

    nanodata_el = elem.find("nanodata")
    nanocost = _int_or_none(nanodata_el.get("nanocost")) if nanodata_el is not None else None
    ncu = _int_or_none(nanodata_el.get("ncu")) if nanodata_el is not None else None
    crystal_id = _int_or_none(nanodata_el.get("crystalid")) if nanodata_el is not None else None
    if crystal_id == -1:
        crystal_id = None

    duration_el = elem.find("duration")
    duration = _int_or_none(duration_el.get("duration")) if duration_el is not None else None

    requirements = _parse_requirements(elem)
    profession = None
    for req in requirements:
        if req.attribute == "Profession" and req.operator == "exactly":
            profession = _int_or_none(req.value)
            break

    effects = _parse_effects(elem)

    return make_nano(
        id=item.id,
        name=item.name,
        ql=item.ql,
        icon=item.icon,
        description=item.description,
        school=school,
        strain=strain,
        nanocost=nanocost,
        ncu=ncu,
        crystal_id=crystal_id,
        duration=duration,
        profession=profession,
        requirements=requirements,
        effects=effects,
    )


def parse_dump_xml(fileobj: IO[bytes]) -> tuple[list[Item], list[NanoProgram]]:
    """Streams <item> elements from an open aodb XML file object. Caller
    owns opening/closing fileobj. Items with no aoid/name are skipped and
    logged."""
    items: list[Item] = []
    nanos: list[NanoProgram] = []
    skipped = 0

    for _event, elem in ET.iterparse(fileobj, events=("end",)):
        if elem.tag != "item":
            continue

        item = _parse_item(elem)
        if item is None:
            skipped += 1
            elem.clear()
            continue

        items.append(item)
        if elem.get("metatype") == "n":
            nanos.append(_parse_nano(elem, item))

        elem.clear()

    if skipped:
        logger.warning("Skipped %d <item> elements with no aoid/name", skipped)

    return items, nanos


def parse_dump_zip(data: bytes) -> tuple[list[Item], list[NanoProgram]]:
    """Opens a zip archive (bytes) and streams the first .xml member found
    into parse_dump_xml, without materializing the decompressed XML as a
    single in-memory string."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_names = [n for n in zf.namelist() if n.endswith(".xml")]
        if not xml_names:
            raise ValueError("No .xml member found in dump zip")
        with zf.open(xml_names[0]) as xml_file:
            return parse_dump_xml(xml_file)


def import_from_url(url: str) -> tuple[list[Item], list[NanoProgram]]:
    """Downloads the dump zip from a plain public HTTPS URL (the bucket is
    public and served directly - no S3 API/credentials needed)."""
    import urllib.request

    logger.info("Downloading item dump from %s", url)
    # Cloudflare blocks the default "Python-urllib/x.y" User-Agent (verified
    # against the real bucket: identical request gets a 403 with that UA,
    # 200 with any other) - set something else.
    req = urllib.request.Request(url, headers={"User-Agent": "aodb/1.0"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - trusted, operator-configured URL
        data = resp.read()
    items, nanos = parse_dump_zip(data)
    logger.info("Loaded %d items (%d nano programs) from %s", len(items), len(nanos), url)
    return items, nanos
