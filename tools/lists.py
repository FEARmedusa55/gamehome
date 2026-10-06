"""
Rewrite a bulleted list from the shape the editor sends back.

Typing into one bullet is an ordinary paragraph edit (set_block). Changing the
list's SHAPE is not: a new bullet (Shift+Enter), indenting (Tab), outdenting
(Shift+Tab) or joining two bullets (Backspace) adds, moves or removes <li>
elements, which a paragraph edit cannot do. For those the editor sends the
whole list as a tree,

    [{"parts": [...], "children": [...]}, ...]

and the list is rebuilt from it. The markup for each level — the <ul>, <li>
and <p> tags with all their classes and styles — is copied from the list being
replaced, so a rebuilt list looks exactly like the original. Text goes through
textedit.render_parts, like every other edit, so no HTML from the browser ever
reaches the page.
"""

from __future__ import annotations

import re

import textedit

_LIST_TAG = re.compile(r"<(/?)(ul|ol)\b[^>]*>", re.I)
_TAG = re.compile(r"<(/?)(ul|ol|li|p)\b[^>]*>", re.I)

MAX_DEPTH = 8


def list_spans(html: str) -> list[tuple[int, int]]:
    """(start, end) of every outermost <ul>/<ol>, nested lists included in it."""
    spans, depth, start = [], 0, 0
    for m in _LIST_TAG.finditer(html):
        if not m.group(1):
            if depth == 0:
                start = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                spans.append((start, m.end()))
    return spans


def level_tags(list_html: str) -> list[dict]:
    """The opening tags used at each nesting level of a list.

    [{"list": '<ul class=...>', "li": '<li ...>', "p": '<p ...>' or None}, ...]
    A level the list does not reach reuses the deepest one it does.
    """
    levels: list[dict] = []
    stack: list[str] = []          # open ul/ol/li/p names
    list_depth = -1
    for m in _TAG.finditer(list_html):
        closing, name = m.group(1), m.group(2).lower()
        if not closing:
            if name in ("ul", "ol"):
                list_depth += 1
                while len(levels) <= list_depth:
                    levels.append({"list": None, "li": None, "p": None})
                levels[list_depth]["list"] = levels[list_depth]["list"] or m.group(0)
            elif name == "li" and list_depth >= 0:
                levels[list_depth]["li"] = levels[list_depth]["li"] or m.group(0)
            elif name == "p" and stack and stack[-1] == "li" and list_depth >= 0:
                levels[list_depth]["p"] = levels[list_depth]["p"] or m.group(0)
            stack.append(name)
        else:
            while stack:
                top = stack.pop()
                if top == name:
                    break
            if name in ("ul", "ol"):
                list_depth -= 1
    for lvl in levels:
        lvl["li"] = lvl["li"] or "<li>"
    return levels


def _close(open_tag: str) -> str:
    return "</" + re.match(r"<\s*([a-zA-Z]+)", open_tag).group(1).lower() + ">"


def render(items: list, levels: list[dict], span_class: str, depth: int = 0) -> str:
    """Markup for a list of items (and their children) at one nesting level."""
    lvl = levels[min(depth, len(levels) - 1)]
    list_open = lvl["list"] or "<ul>"
    out = [list_open]
    for item in items:
        text = textedit.render_parts(item.get("parts") or [], span_class=span_class)
        kids = item.get("children") or []
        body = (lvl["p"] + text + "</p>") if lvl["p"] else text
        sub = render(kids, levels, span_class, depth + 1) if kids else ""
        out.append(lvl["li"] + body + sub + "</li>")
    out.append(_close(list_open))
    return "".join(out)


def clean_items(items, depth: int = 0) -> list:
    """Validate the tree from the browser, dropping bullets left empty."""
    if not isinstance(items, list) or depth > MAX_DEPTH:
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parts = [p for p in (item.get("parts") or []) if isinstance(p, dict)]
        kids = clean_items(item.get("children"), depth + 1)
        has_text = any(str(p.get("text") or "").strip() for p in parts)
        if has_text:
            out.append({"parts": parts, "children": kids})
        else:
            # An empty bullet goes, but anything nested under it moves up a
            # level rather than vanishing with it.
            out.extend(kids)
    return out


def rewrite(html: str, block_id, items: list, span_class: str) -> str | None:
    """Replace the list holding block `block_id` with one built from `items`.

    Returns the new section markup, or None when that block is not in a list.
    An empty `items` removes the list.
    """
    target = next((b for b in textedit.blocks(html) if str(b[2]) == str(block_id)), None)
    if target is None:
        return None
    pos = target[0]
    span = next(((s, e) for s, e in list_spans(html) if s <= pos < e), None)
    if span is None:
        return None
    start, end = span
    items = clean_items(items)
    new = render(items, level_tags(html[start:end]), span_class) if items else ""
    return html[:start] + new + html[end:]
