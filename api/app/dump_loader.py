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
import zipfile
from typing import IO
from xml.etree import ElementTree as ET

from .store import Item, NanoProgram, Requirement, make_item, make_nano

logger = logging.getLogger(__name__)


def _int_or_none(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_item(elem: ET.Element) -> Item | None:
    aoid = elem.get("aoid")
    name_el = elem.find("name")
    name = (name_el.text or "").strip() if name_el is not None else ""
    if not aoid or not name:
        return None

    ql_el = elem.find("ql")
    icon_el = elem.find("icon")
    desc_el = elem.find("description")

    return make_item(
        id=int(aoid),
        name=name,
        ql=int(ql_el.text) if ql_el is not None and ql_el.text else 0,
        icon=int(icon_el.text) if icon_el is not None and icon_el.text else 0,
        description=(desc_el.text or None) if desc_el is not None else None,
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

    requirements = []
    profession = None
    requirements_el = elem.find("requirements")
    if requirements_el is not None:
        for req_el in requirements_el.findall("requirement"):
            attribute = req_el.get("attribute") or ""
            operator = req_el.get("operator") or ""
            value = req_el.get("value") or ""
            requirements.append(Requirement(attribute=attribute, operator=operator, value=value))
            if attribute == "Profession" and operator == "exactly":
                profession = _int_or_none(value)

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
        requirements=tuple(requirements),
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
    req = urllib.request.Request(url, headers={"User-Agent": "aodb-api/1.0"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - trusted, operator-configured URL
        data = resp.read()
    items, nanos = parse_dump_zip(data)
    logger.info("Loaded %d items (%d nano programs) from %s", len(items), len(nanos), url)
    return items, nanos
