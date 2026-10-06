#!/usr/bin/env python3
"""
Edit the text of a section, in place, without changing how it looks.

The problem this solves: Google Sites splits a single sentence across several
<span>s that all carry identical styling, so the text isn't contiguous in the
source and a naive find-replace misses it.

Two steps make it editable:

  1. MERGE — adjacent <span>s with identical attributes collapse into one. This
     is visually a no-op (adjacent inline elements with the same styling render
     as one run of text), but it makes the text contiguous.
  2. REPLACE — the text of a merged run is swapped for new text. The element and
     all its inline styling stay exactly as they were, so only the words change.

Operates on the staged content files so the change survives a rebuild:

    python tools/textedit.py runs index.html 05-important-read-below-if-nothing.html
    python tools/textedit.py set  index.html 05-important-read-below-if-nothing.html 0 "New text"
    python tools/content.py build index.html
"""

import argparse
import re
import sys
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CONTENT = HERE / "content"

# Adjacent spans holding plain text. Used for locating spans, not for pairing.
SPAN_PAIR = re.compile(
    r"(<span\b[^>]*>)([^<>]*?)</span>(<span\b[^>]*>)([^<>]*?)</span>"
)

# A soft line break, as Google Sites writes one when text is entered on two
# lines. The site's own content contains none — but we create them, because a
# newline typed into the editor has nowhere else to go. HTML collapses a raw
# "\n" to a space, which is why typing one used to do nothing at all.
BR = r"<br\b[^>]*>"

# A single span whose entire content is text (no nested tags) apart from soft
# breaks. Allowing `br` inside matters: without it, adding a line break drops
# the run out of the editor's reach entirely.
SPAN_TOKEN = re.compile(rf"(<span\b[^>]*>)((?:[^<>]|{BR})*)</span>")


def to_text(inner: str) -> str:
    """Markup -> the plain text the editor should show.

    Entities are decoded and breaks become newlines, so the box shows the words
    as a reader sees them. escape_text() is the way back.
    """
    return unescape(re.sub(BR, "\n", inner))


def escape_text(text: str) -> str:
    """Plain text -> markup-safe text.

    Text typed into the editor arrives decoded, so it must be escaped before it
    goes back into the page — otherwise an ampersand or a stray angle bracket
    from the keyboard would corrupt the markup.
    """
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def to_html(text: str) -> str:
    """Editor box -> markup: escapes, then turns newlines into real breaks."""
    return re.sub(r"\r\n?|\n", "<br>", escape_text(text))


# Block-level elements. The editor shows one editing box per block rather than
# one per span: styling part of a line splits a span in two, and those halves
# belong in the box they came from, not in new ones of their own.
BLOCK_TAGS = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div", "td")

_ANY_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>")


def blocks(html: str) -> list[tuple[int, int, int, str, str]]:
    """(content_start, content_end, id, tag, open_tag) for every block element.

    `content_start`/`content_end` bracket what is INSIDE the element, so a block
    can be rewritten without touching its own tag. ids count every block seen,
    so they stay stable for a given piece of markup.
    """
    found = []
    stack: list[tuple[str, int, int, str]] = []
    counter = 0
    for m in _ANY_TAG.finditer(html):
        closing, tag, self_closing = m.group(1), m.group(2).lower(), m.group(3)
        if tag not in BLOCK_TAGS or self_closing:
            continue
        if closing:
            if stack and stack[-1][0] == tag:
                _, bid, content_start, open_tag = stack.pop()
                found.append((content_start, m.start(), bid, tag, open_tag))
            continue
        counter += 1
        stack.append((tag, counter, m.end(), m.group(0)))
    return found


def block_of(spans, pos: int) -> int | None:
    """The innermost block id containing `pos`, from blocks() output."""
    best = None
    for start, end, bid, _tag, _open in spans:
        if start <= pos < end:
            # innermost = the shortest block that still contains the position
            if best is None or (end - start) < best[0]:
                best = (end - start, bid)
    return best[1] if best else None


def group_runs(html: str):
    """Group the text runs by the block they sit in.

    Returns [(block_id, tag, [run indexes])] in document order. This is what
    lets a paragraph stay ONE editing box however many spans styling splits it
    into.
    """
    spans = blocks(html)
    runs = list(text_runs(html))
    tags = {s[2]: s[3] for s in spans}
    groups: dict[int, list[int]] = {}
    order: list[int] = []
    for i, item in enumerate(runs):
        bid = block_of(spans, item[0].start())
        if bid is None:
            continue
        if bid not in groups:
            groups[bid] = []
            order.append(bid)
        groups[bid].append(i)
    return [(bid, tags.get(bid, ""), groups[bid]) for bid in order]


def render_parts(parts, span_class: str = "C9DxTc") -> str:
    """Inner markup for a block, from [{text, style, href}] parts.

    Rebuilds spans rather than trusting any HTML the browser hands back, so a
    round trip through the editor cannot smuggle in new tags or attributes.
    A part carrying `href` becomes a real link; the link colour and underline
    travel in its style, set by the editor's Link button.
    """
    out = []
    for part in parts:
        text = to_html(str(part.get("text") or ""))
        style = str(part.get("style") or "").strip()
        if style and not style.endswith(";"):
            style += ";"
        cls = str(part.get("class") or span_class)
        if style:
            inner = f'<span class="{cls}" style="{style}">{text}</span>'
        else:
            inner = text
        href = str(part.get("href") or "").strip()
        if href:
            # Escape the URL for an attribute, and stop the new tab handing the
            # opener window over to the target page.
            safe = (href.replace("&", "&amp;").replace('"', "&quot;")
                        .replace("<", "&lt;").replace(">", "&gt;"))
            out.append(f'<a href="{safe}" target="_blank" rel="noopener noreferrer">{inner}</a>')
        else:
            out.append(inner)
    return "".join(out)


def _markup_offsets(raw: str, start: int, end: int) -> tuple[int, int]:
    """Map plain-text offsets onto the same positions inside `raw`.

    The editor's selection is measured against the text it displays, which has
    breaks as newlines; the markup has them as <br>. Splitting on the wrong
    offsets would slice into the middle of a tag.
    """
    out = []
    plain_at = 0
    i = 0
    starts = {start, end}
    while i <= len(raw):
        if plain_at in starts and len(out) < 2:
            out.append(i)
        if i >= len(raw):
            break
        m = re.compile(BR).match(raw, i)
        if m:
            if plain_at in starts and len(out) < 2:
                out.append(i)
            i = m.end()
            plain_at += 1        # a break is one character in the text form
            continue
        i += 1
        plain_at += 1
    while len(out) < 2:
        out.append(len(raw))
    return out[0], out[1]

# Properties whose given value is the CSS initial and therefore cannot change
# how anything renders. Google Sites scatters these in and out of style strings,
# which is why two visually identical spans can carry different-looking styles.
NOOP_PROPS = {
    "font-variant": "normal",
    "font-variant-caps": "normal",
    "font-style": "normal",
    "font-weight": "normal",
    "font-stretch": "normal",
    "vertical-align": "baseline",
    "text-decoration": "none",
    "text-decoration-line": "none",
    "text-transform": "none",
    "white-space": "normal",
    "letter-spacing": "normal",
    "word-spacing": "normal",
    "font-size-adjust": "none",
    "text-indent": "0",
}


def style_key(open_tag: str) -> str:
    """
    A normalised fingerprint of a tag's styling, for comparing two spans.

    Parses the style attribute, drops no-op declarations, and sorts what's left.
    Two spans with the same key render identically, so merging them is safe.
    """
    match = re.search(r'style="([^"]*)"', open_tag)
    if not match:
        return ""
    props = []
    for decl in match.group(1).split(";"):
        if ":" not in decl:
            continue
        name, value = decl.split(":", 1)
        name = name.strip().lower()
        value = value.strip().lower().rstrip()
        if NOOP_PROPS.get(name) == value:
            continue
        props.append((name, value))
    props.sort()
    return ";".join(f"{k}:{v}" for k, v in props)


def span_key(open_tag: str) -> tuple:
    """Class plus normalised style — everything that can affect rendering."""
    match = re.search(r'class="([^"]*)"', open_tag)
    return (match.group(1) if match else "", style_key(open_tag))

# A leaf element whose entire content is text (no child tags) apart from soft
# breaks — see SPAN_TOKEN above for why `br` is permitted.
LEAF = re.compile(rf"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>((?:[^<>]|{BR})*)</\1>")


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def merge_spans(html: str) -> str:
    """
    Collapse runs of adjacent spans that render identically. Visually a no-op.

    Deliberately NOT a pairwise regex: matching pairs left-to-right consumes each
    span into one pair, so the boundary *between* two pairs never gets compared.
    With spans C,D,E,F where D and E match but C/D and E/F don't, both pairs fail
    and D+E silently stay split. Instead, collect every plain-text span and merge
    maximal runs of genuinely adjacent spans sharing one style key.
    """
    matches = list(SPAN_TOKEN.finditer(html))
    if not matches:
        return html

    out = []
    last = 0
    i = 0
    while i < len(matches):
        first = matches[i]
        key = span_key(first.group(1))
        j = i
        while (j + 1 < len(matches)
               and matches[j + 1].start() == matches[j].end()   # truly adjacent
               and span_key(matches[j + 1].group(1)) == key):
            j += 1

        if j > i:
            text = "".join(matches[k].group(2) for k in range(i, j + 1))
            out.append(html[last:first.start()])
            out.append(first.group(1) + text + "</span>")
            last = matches[j].end()
        i = j + 1

    out.append(html[last:])
    return "".join(out)


def set_style_props(attrs: str, color: str | None = None,
                    bold: bool | None = None, size: str | None = None) -> str:
    """
    Add, update or remove inline style properties on a tag's attribute string.

    Colour here is genuinely local: the site's content classes (C9DxTc, puwcIf,
    Qnc8Te) do not appear in any stylesheet, so the inline style is the only
    thing setting the colour. Changing it cannot affect anything else.

    color="" removes the colour so the run inherits the page's text colour.
    """
    match = re.search(r'style="([^"]*)"', attrs)
    props: list[list[str]] = []
    if match:
        for decl in match.group(1).split(";"):
            if ":" not in decl:
                continue
            name, value = decl.split(":", 1)
            name, value = name.strip(), value.strip()
            if name and not any(p[0].lower() == name.lower() for p in props):
                props.append([name, value])

    def put(name: str, value: str) -> None:
        for prop in props:
            if prop[0].lower() == name.lower():
                prop[1] = value
                return
        props.append([name, value])

    def drop(name: str) -> None:
        props[:] = [p for p in props if p[0].lower() != name.lower()]

    if color is not None:
        if color:
            put("color", color)
        else:
            drop("color")
    if bold is not None:
        put("font-weight", "700" if bold else "400")
    # Size works exactly like colour: the site sets it per run as an inline
    # font-size (body text is 13.999pt), so a size change is just another
    # declaration in the same style string.
    if size is not None:
        if size:
            put("font-size", size)
        else:
            drop("font-size")

    style = "; ".join(f"{name}: {value}" for name, value in props)
    if style:
        style += ";"
    if match:
        return attrs[:match.start()] + f'style="{style}"' + attrs[match.end():]
    return f'{attrs} style="{style}"' if style else attrs


def text_runs(html: str):
    """Yield (match, tag, attrs, text) for each leaf element holding text."""
    for match in LEAF.finditer(html):
        text = match.group(3)
        if text.strip():
            yield match, match.group(1), match.group(2), text


def visible(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def resolve(page: str, section: str) -> Path:
    """Find the staged section file for a page."""
    folder = CONTENT / Path(page).stem
    if not folder.exists():
        sys.exit(f"! no staged content for {page} — run: "
                 f"python tools/content.py extract {page}")
    path = folder / section
    if not path.exists():
        matches = [p for p in folder.glob("*.html") if section in p.name]
        if len(matches) == 1:
            return matches[0]
        print(f"! {section!r} not found in {folder.name}/")
        print("  available:")
        for p in sorted(folder.glob("[0-9]*.html")):
            print(f"    {p.name}")
        sys.exit(1)
    return path


def cmd_runs(args) -> int:
    path = resolve(args.page, args.section)
    html = merge_spans(read(path))
    print(f"{path.name}\n")
    found = 0
    for index, (match, tag, attrs, text) in enumerate(text_runs(html)):
        found += 1
        shape = " ".join(
            f"{k}={v[:22]}" for k, v in re.findall(r'(\w[\w-]*)="([^"]*)"', attrs)
        )
        print(f"  [{index:2}] <{tag}>  {visible(text)[:96]}")
        if shape:
            print(f"        {shape[:110]}")
    print(f"\n{found} text runs")
    return 0


def cmd_set(args) -> int:
    path = resolve(args.page, args.section)
    html = read(path)
    merged = merge_spans(html)

    runs = list(text_runs(merged))
    if not runs:
        print("! no text runs found in this section")
        return 1
    if args.index < 0 or args.index >= len(runs):
        print(f"! index {args.index} out of range (0-{len(runs) - 1})")
        return 1

    match, tag, attrs, old = runs[args.index]
    print(f"{path.name}  run [{args.index}] <{tag}>")
    print(f"  before: {visible(old)[:100]}")
    print(f"  after : {visible(args.text)[:100]}")

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    # Replace only the text between the tags; element, attrs and styling untouched.
    new_html = (merged[:match.start(3)] + args.text + merged[match.end(3):])

    # Keep the span merge, since it is what made the run contiguous.
    write(path, new_html)
    print(f"\n  written ({len(merged) - len(html):+,} bytes from span merge)")
    print(f"  now run: python tools/content.py build {args.page}")
    return 0


def set_block_props(html: str, block_id, props: dict) -> str:
    """Set inline style properties on the block whose id is block_id.

    Used for positioning a block freely and for restyling a button. Anchored by
    id from the same merged scan set_block uses, so the open tag edited here is
    the one that scan saw — no counting between two different scans.

    Properties named in `props` are replaced if already present; an empty value
    removes them. Everything else on the tag is left alone.
    """
    merged = merge_spans(html)
    target = next((b for b in blocks(merged) if str(b[2]) == str(block_id)), None)
    if target is None:
        return html

    start = target[0]
    lt = merged.rfind("<", 0, start)
    if lt == -1:
        return html
    gt = merged.find(">", lt)
    if gt == -1 or gt > start:
        return html
    tag = merged[lt:gt + 1]
    if tag.endswith("/>"):
        return html

    style = re.search(r'style="([^"]*)"', tag)
    current = style.group(1).strip() if style else ""
    drop = {k.strip().lower() for k in props}
    kept = [p.strip() for p in current.split(";")
            if p.strip() and p.split(":")[0].strip().lower() not in drop]
    for key, value in props.items():
        if value:
            kept.append(f"{key}: {value}")
    new_style = "; ".join(kept) + (";" if kept else "")

    if style:
        new_tag = tag[:style.start()] + f'style="{new_style}"' + tag[style.end():]
    else:
        new_tag = tag[:-1].rstrip() + f' style="{new_style}">'
    return merged[:lt] + new_tag + merged[gt + 1:]


def main() -> int:
    parser = argparse.ArgumentParser(description="Edit section text in place.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("runs", help="list editable text runs in a section")
    p.add_argument("page")
    p.add_argument("section")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("set", help="replace one text run")
    p.add_argument("page")
    p.add_argument("section")
    p.add_argument("index", type=int)
    p.add_argument("text")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_set)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
