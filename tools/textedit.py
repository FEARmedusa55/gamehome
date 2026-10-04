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
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CONTENT = HERE / "content"

# Adjacent spans holding plain text. Used for locating spans, not for pairing.
SPAN_PAIR = re.compile(
    r"(<span\b[^>]*>)([^<>]*?)</span>(<span\b[^>]*>)([^<>]*?)</span>"
)

# A single span whose entire content is text (no nested tags).
SPAN_TOKEN = re.compile(r"(<span\b[^>]*>)([^<>]*)</span>")

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

# A leaf element whose entire content is text (no child tags).
LEAF = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>([^<>]*)</\1>")


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
                    bold: bool | None = None) -> str:
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
