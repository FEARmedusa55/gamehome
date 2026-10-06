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


# ---------------------------------------------------------------------------
# Bullets on / off for one paragraph (the editor's "• list" button)
# ---------------------------------------------------------------------------

# The site's own list markup, for a paragraph that becomes a bullet with no
# list beside it to copy from.
DEFAULT_LIST = ('<ul class="n8H08c UVNKR " style="list-style-type: square; '
                'margin-left: 0; margin-right: 0; padding: 0;">')
DEFAULT_LI = '<li  dir="ltr" class="zfr3Q TYR86d eD0Rn " style="margin-left: 15.0pt;">'
DEFAULT_P = ('<p  dir="ltr" role="presentation" class="zfr3Q CDt4Ke " '
             'style="margin-left: 0.0pt; padding-left: 0.0pt; text-indent: 0.0pt;">')


def entries(list_html: str, offset: int = 0) -> list[dict]:
    """A list as its bullets in page order, each with its depth and markup.

    [{"depth", "li", "p", "body", "start", "end"}] — `body` is the bullet's
    text markup exactly as it is in the file, `start`/`end` where that body
    sits (plus `offset`), so a position can be traced to its bullet.
    """
    out: list[dict] = []
    depth = -1
    stack: list[str] = []
    cur = None          # the <li> being read, until its body is found
    for m in _TAG.finditer(list_html):
        closing, name = m.group(1), m.group(2).lower()
        if not closing:
            if name in ("ul", "ol"):
                if cur and cur["body"] is None:      # a bullet with no <p>
                    cur["body"] = list_html[cur["start"]:m.start()]
                    cur["end"] = m.start()
                depth += 1
            elif name == "li":
                cur = {"depth": depth, "li": m.group(0), "p": None, "body": None,
                       "start": m.end(), "end": m.end()}
                out.append(cur)
            elif name == "p" and cur and cur["body"] is None and stack and stack[-1] == "li":
                cur["p"] = m.group(0)
                cur["start"] = m.end()
            stack.append(name)
        else:
            if name == "p" and cur and cur["p"] and cur["body"] is None:
                cur["body"] = list_html[cur["start"]:m.start()]
                cur["end"] = m.start()
            elif name == "li" and cur and cur["body"] is None:
                cur["body"] = list_html[cur["start"]:m.start()]
                cur["end"] = m.start()
            while stack:
                if stack.pop() == name:
                    break
            if name in ("ul", "ol"):
                depth -= 1
    for e in out:
        e["start"] += offset
        e["end"] += offset
        if e["body"] is None:
            e["body"] = ""
    return out


def _nest(flat: list[dict]) -> list[dict]:
    """Bullets in page order -> a tree, with depths made consistent.

    The first bullet is level 0 and none sits more than one level below the
    one before it, so a list cut from the middle of another still nests.
    """
    if not flat:
        return []
    base = flat[0]["depth"]
    root: list[dict] = []
    stack: list[tuple[int, list]] = [(-1, root)]
    prev = -1
    for e in flat:
        d = max(0, min(e["depth"] - base, prev + 1))
        prev = d
        while stack[-1][0] >= d:
            stack.pop()
        node = {"e": e, "children": []}
        stack[-1][1].append(node)
        stack.append((d, node["children"]))
    return root


def _render_raw(nodes: list[dict], levels: list[dict], depth: int = 0) -> str:
    """Like render(), but each bullet keeps its own markup and tags."""
    lvl = levels[min(depth, len(levels) - 1)] if levels else {}
    list_open = lvl.get("list") or DEFAULT_LIST
    out = [list_open]
    for node in nodes:
        e = node["e"]
        body = (e["p"] + e["body"] + "</p>") if e["p"] else e["body"]
        sub = _render_raw(node["children"], levels, depth + 1) if node["children"] else ""
        out.append(e["li"] + body + sub + "</li>")
    out.append(_close(list_open))
    return "".join(out)


def _as_paragraph(e: dict) -> str:
    """A bullet's text as a plain paragraph, in the bullet's own <p> styling."""
    p = e["p"] or DEFAULT_P
    p = re.sub(r'\s+role="presentation"', "", p)
    return p + e["body"] + "</p>"


def toggle(html: str, block_id) -> tuple[str | None, str]:
    """Turn the paragraph `block_id` into a bullet, or the bullet back into a
    paragraph. Returns (new_html or None, what happened / why not)."""
    target = next((b for b in textedit.blocks(html) if str(b[2]) == str(block_id)), None)
    if target is None:
        return None, "paragraph not found"
    c_start, c_end, _bid, tag, _open = target

    # A bullet: the list splits around it, and it becomes a paragraph there.
    for s, e in list_spans(html):
        if s <= c_start < e:
            flat = entries(html[s:e], offset=s)
            k = next((i for i, x in enumerate(flat) if x["start"] <= c_start <= x["end"]), None)
            if k is None:
                return None, "could not find that bullet"
            levels = level_tags(html[s:e])
            before, item, after = flat[:k], flat[k], flat[k + 1:]
            new = ((_render_raw(_nest(before), levels) if before else "")
                   + _as_paragraph(item)
                   + (_render_raw(_nest(after), levels) if after else ""))
            return html[:s] + new + html[e:], "bullet off"

    if tag != "p":
        return None, "bullets work on paragraphs — this is a heading or a box"

    # A paragraph: it joins the list right before it (or after it) if there
    # is one, the way Word continues a list; otherwise it starts a new one.
    p_start = html.rfind("<", 0, c_start)
    p_end = c_end + len("</p>")
    body = html[c_start:c_end]
    spans = list_spans(html)
    # "Right beside" allows blank space and line breaks in between — Google
    # often leaves a <br> after a list — which go when the two are joined.
    gap = re.compile(r"(?:\s|<br\b[^>]*>)*\Z", re.I)
    prev = next(((s, e) for s, e in spans if e <= p_start and gap.match(html[e:p_start])), None)
    nxt = next(((s, e) for s, e in spans if s >= p_end and gap.match(html[p_end:s])), None)

    def bullet(levels: list[dict]) -> dict:
        lvl = levels[0] if levels else {}
        return {"depth": 0, "li": lvl.get("li") or DEFAULT_LI,
                "p": lvl.get("p") or DEFAULT_P, "body": body}

    if prev:
        s, e = prev
        flat = entries(html[s:e])
        levels = level_tags(html[s:e])
        merged = _render_raw(_nest(flat + [bullet(levels)]), levels)
        return html[:s] + merged + html[p_end:], "bullet on"
    if nxt:
        s, e = nxt
        flat = entries(html[s:e])
        levels = level_tags(html[s:e])
        merged = _render_raw(_nest([bullet(levels)] + flat), levels)
        return html[:p_start] + merged + html[e:], "bullet on"
    return (html[:p_start] + _render_raw([{"e": bullet([]), "children": []}], [])
            + html[p_end:]), "bullet on"
