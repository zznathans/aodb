import io
import zipfile
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import pytest

from app.dump_loader import (
    _armor_implant_subcategory,
    _int_or_none,
    _utility_subcategory,
    _weapon_subcategory,
    import_from_url,
    parse_dump_xml,
    parse_dump_zip,
)

XML_TEXT = """<?xml version="1.0"?>
<aodb>
  <item aoid="21601" patch="110000" metatype="i">
    <name>Flamethrower Ammunition</name>
    <description>This is Ammunition for the flamethrowers.</description>
    <ql>1</ql>
    <icon>32168</icon>
  </item>
  <item aoid="21793" patch="110000" metatype="i">
    <name>Augmented Nano Armor Sleeves</name>
    <description>Nano Armor, plugged into the user&#146;s nervous system.</description>
    <ql>200</ql>
    <icon>13231</icon>
  </item>
  <item aoid="99999" patch="110000" metatype="i">
    <ql>1</ql>
    <icon>1</icon>
  </item>
  <item aoid="25980" patch="110000" metatype="n">
    <name>Death's Gaze</name>
    <description>Attempts to hold the target in place.</description>
    <ql>142</ql>
    <icon>16248</icon>
    <nanodata crystalid="26017" nanocost="265" ncu="44" />
    <nanoclass school="Combat" strain="147" />
    <duration duration="453" />
    <requirements>
      <requirement hook="To Use" attribute="Psychological modifications" operator="at least" value="662" />
      <requirement hook="To Use" attribute="Profession" operator="exactly" value="5" />
    </requirements>
  </item>
  <item aoid="25982" patch="110000" metatype="n">
    <name>Change Form: Opifex</name>
    <ql>-1</ql>
    <icon>39274</icon>
    <nanodata crystalid="-1" nanocost="115" ncu="14" />
    <nanoclass school="Healing" strain="0" />
  </item>
</aodb>
"""


def _zip_bytes(xml_text: str, filename: str = "dump.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, xml_text)
    return buf.getvalue()


def test_parse_dump_xml_parses_expected_item_fields():
    items, _nanos = parse_dump_xml(io.BytesIO(XML_TEXT.encode("utf-8")))

    # The third <item> has no <name> and is skipped; nano items count as
    # regular items too (4 total: 2 plain + 2 nanos).
    assert len(items) == 4
    assert [i.id for i in items[:2]] == [21601, 21793]
    assert items[0].name == "Flamethrower Ammunition"
    assert items[0].name_lower == "flamethrower ammunition"
    assert items[0].ql == 1
    assert items[0].icon == 32168
    assert "nervous system" in items[1].description


def test_parse_dump_xml_parses_nano_specific_fields():
    _items, nanos = parse_dump_xml(io.BytesIO(XML_TEXT.encode("utf-8")))

    assert len(nanos) == 2
    gaze = next(n for n in nanos if n.id == 25980)
    assert gaze.name == "Death's Gaze"
    assert gaze.school == "Combat"
    assert gaze.strain == 147
    assert gaze.nanocost == 265
    assert gaze.ncu == 44
    assert gaze.crystal_id == 26017
    assert gaze.duration == 453
    assert gaze.profession == 5
    assert len(gaze.requirements) == 2
    assert gaze.requirements[1].attribute == "Profession"
    assert gaze.requirements[1].value == "5"


def test_parse_dump_xml_nano_missing_optional_fields():
    _items, nanos = parse_dump_xml(io.BytesIO(XML_TEXT.encode("utf-8")))

    change_form = next(n for n in nanos if n.id == 25982)
    assert change_form.duration is None
    assert change_form.profession is None
    assert change_form.crystal_id is None  # -1 in the dump means "no crystal"
    assert change_form.requirements == ()


def test_parse_dump_zip_extracts_and_parses():
    items, nanos = parse_dump_zip(_zip_bytes(XML_TEXT, "171003.xml"))
    assert len(items) == 4
    assert len(nanos) == 2


def test_parse_dump_zip_raises_when_no_xml_member():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "not an xml file")

    with pytest.raises(ValueError, match="No .xml member found"):
        parse_dump_zip(buf.getvalue())


def test_int_or_none_handles_missing_and_invalid_values():
    assert _int_or_none(None) is None
    assert _int_or_none("not-a-number") is None
    assert _int_or_none("42") == 42


def test_armor_implant_subcategory_handles_missing_and_empty_slots():
    assert _armor_implant_subcategory(None) == ""
    assert _armor_implant_subcategory("") == ""
    assert _armor_implant_subcategory("  ,  ") == ""


def test_armor_implant_subcategory_strips_left_right_prefix_and_capitalizes():
    assert _armor_implant_subcategory("Right Wrist, Left Wrist") == "Wrist"


def test_armor_implant_subcategory_ambiguous_bases_fall_back_to_other():
    assert _armor_implant_subcategory("Right Wrist, Head") == "Other"


def test_armor_implant_subcategory_many_slots_is_social_full_body():
    ten_slots = ", ".join(f"Slot{i}" for i in range(10))
    assert _armor_implant_subcategory(ten_slots) == "Social/Full Body"


def test_utility_subcategory_handles_missing_and_empty_slots():
    assert _utility_subcategory(None) == ""
    assert _utility_subcategory("") == ""


def test_utility_subcategory_strips_numbered_instance_suffix():
    assert _utility_subcategory("Utils 1, Utils 2, Utils 3") == "Utils"


def test_utility_subcategory_mixed_families_is_multi_slot():
    assert _utility_subcategory("Utils 1, Hud 2") == "Multi-Slot"


def test_utility_subcategory_only_separators_is_empty():
    assert _utility_subcategory("  ,  ") == ""


def test_weapon_subcategory_attack_skillmap_with_no_skills_returns_empty():
    root = ET.Element("item")
    ET.SubElement(root, "skillmap", type="attack")
    assert _weapon_subcategory(root) == ""


def _skillmap_elem(kind: str, skills: dict[str, float]) -> ET.Element:
    root = ET.Element("item")
    skillmap_el = ET.SubElement(root, "skillmap", type=kind)
    for name, percentage in skills.items():
        ET.SubElement(skillmap_el, "skill", name=name, percentage=str(percentage))
    return root


def test_weapon_subcategory_picks_highest_percentage_attack_skill():
    elem = _skillmap_elem("attack", {"Pistol": 40.0, "Rifle": 60.0})
    assert _weapon_subcategory(elem) == "Rifle"


def test_weapon_subcategory_ignores_non_attack_skillmaps():
    root = ET.Element("item")
    ET.SubElement(root, "skillmap", type="defense")
    assert _weapon_subcategory(root) == ""


def test_weapon_subcategory_no_skillmap_returns_empty():
    assert _weapon_subcategory(ET.Element("item")) == ""


_SUBCATEGORY_XML = """<?xml version="1.0"?>
<aodb>
  <item aoid="1" patch="1" metatype="w">
    <name>Test Rifle</name>
    <ql>100</ql>
    <icon>1</icon>
    <damage minimum="10" maximum="20" critical="30" type="1" />
    <skillmap type="attack">
      <skill name="Pistol" percentage="30" />
      <skill name="Rifle" percentage="70" />
    </skillmap>
    <effects>
      <effect hook="Wear" target="Self" action="Modify" attribute="Strength" value="5" />
    </effects>
  </item>
  <item aoid="2" patch="1" metatype="i">
    <type>2</type>
    <name>Test Armor</name>
    <ql>100</ql>
    <icon>1</icon>
    <slots>Right Wrist, Left Wrist</slots>
  </item>
  <item aoid="3" patch="1" metatype="i">
    <type>1</type>
    <name>Test Utility</name>
    <ql>100</ql>
    <icon>1</icon>
    <slots>Utils 1, Utils 2</slots>
  </item>
  <item aoid="4" patch="1" metatype="i">
    <type>4</type>
    <name>NPC-only Junk Weapon</name>
    <ql>1</ql>
    <icon>1</icon>
  </item>
</aodb>
"""


def test_parse_dump_xml_derives_weapon_category_and_subcategory_and_damage():
    items, _nanos = parse_dump_xml(io.BytesIO(_SUBCATEGORY_XML.encode("utf-8")))

    rifle = next(i for i in items if i.id == 1)
    assert rifle.category == "weapon"
    assert rifle.subcategory == "Rifle"
    assert (rifle.damage_min, rifle.damage_max, rifle.damage_critical) == (10, 20, 30)
    assert rifle.effects[0].attribute == "Strength"


def test_parse_dump_xml_derives_armor_and_utility_subcategory():
    items, _nanos = parse_dump_xml(io.BytesIO(_SUBCATEGORY_XML.encode("utf-8")))

    armor = next(i for i in items if i.id == 2)
    assert armor.category == "armor"
    assert armor.subcategory == "Wrist"

    utility = next(i for i in items if i.id == 3)
    assert utility.category == "utility"
    assert utility.subcategory == "Utils"


def test_parse_dump_xml_drops_type_4_npc_only_items():
    items, _nanos = parse_dump_xml(io.BytesIO(_SUBCATEGORY_XML.encode("utf-8")))

    assert all(i.id != 4 for i in items)
    assert len(items) == 3


def test_import_from_url_downloads_and_parses():
    zip_data = _zip_bytes(XML_TEXT)
    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_data
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        items, nanos = import_from_url("https://example.invalid/171003.xml.zip")

    assert len(items) == 4
    assert len(nanos) == 2
    (request,), _ = mock_urlopen.call_args
    assert request.full_url == "https://example.invalid/171003.xml.zip"
    # Cloudflare blocks the default "Python-urllib/..." UA on the real bucket.
    assert request.get_header("User-agent") == "aodb/1.0"
