"""Renders search results as AOML (Anarchy Online chat markup) text, matching
the raw-body contract the old cidb.bebot.link service returned and that
BeBot's Items.php passes straight through to chat with zero parsing.

Item-row markup mirrors the exact template already used elsewhere in BeBot
for item links (Modules/Ao/Market.php):
    <img src=rdb://ICON> <a href='itemref://ID/ID/QL'>NAME</a> QLQL
"""

from .store import Item


def _color(hex_color: str | None, text: str) -> str:
    if not hex_color:
        return text
    return f"<font color='#{hex_color}'>{text}</font>"


def render_results(
    items: list[Item],
    search: str,
    show_icons: bool,
    color_header: str | None,
    color_highlight: str | None,
    color_normal: str | None,
) -> str:
    if not items:
        return _color(color_header, f"No items found matching '{search}'.")

    header = _color(color_header, f"Item Search Results for '{search}' ({len(items)} found)")
    lines = [header]
    for item in items:
        icon_part = f"<img src=rdb://{item.icon}> " if show_icons else ""
        name_link = _color(
            color_highlight,
            f"<a href='itemref://{item.id}/{item.id}/{item.ql}'>{item.name}</a>",
        )
        ql_part = _color(color_normal, f" QL{item.ql}")
        lines.append(f"{icon_part}{name_link}{ql_part}")

    return "\n".join(lines)
