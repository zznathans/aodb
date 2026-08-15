"""Display names for the item categories derived in app/dump_loader.py's
_item_category() (from the dump's metatype attribute and <type> element -
see that function's docstring for how each category was identified)."""

CATEGORY_NAMES: dict[str, str] = {
    "weapon": "Weapons",
    "armor": "Armor",
    "implant": "Implants & Symbiants",
    "utility": "Utilities",
    "general": "General",
    "spirit": "Spirits",
}
