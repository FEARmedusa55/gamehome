#!/usr/bin/env python3
"""
Convert Google Sites sections into clean, semantic HTML.

The original markup is a pyramid of <div>s and <span>s carrying inline styles and
obfuscated class names. Almost none of it is meaningful: the content is really
just headings, paragraphs, lists, images and links buried inside it.

So the conversion is a *strip*, not a rewrite:

  * keep semantic tags (h1-h6, p, ul/ol/li, blockquote, table, a, img, strong/em)
  * unwrap div/span/etc. — their children are kept, the wrapper disappears
  * drop script/style/svg/iframe entirely
  * keep only a few attributes (href, src, alt, title, colspan, rowspan)
  * collapse leftover whitespace and drop empty elements

A useful side effect: Google Sites splits one sentence across several spans at
arbitrary points ("Welcome to t" + "he Minecraft!"). Unwrapping the spans
concatenates them, so the fragmented text fixes itself.

    python tools/clean.py demo apple-tv.html      # write a single converted page
    python tools/clean.py dump apple-tv.html      # print the converted content
"""

import argparse
import re
import struct
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from platforms import items as nav_items  # noqa: E402

HERE = Path(__file__).resolve().parent.parent

# Tags worth keeping in the output.
KEEP = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "td", "th",
    "figure", "figcaption", "hr", "br",
    "strong", "em", "b", "i", "u", "small", "sup", "sub",
    "a", "img",
}

# Tags whose entire subtree is discarded.
DROP = {
    "script", "style", "noscript", "svg", "path", "iframe",
    "object", "embed", "form", "button", "input", "select",
    "textarea", "option", "video", "audio", "canvas", "g",
    "defs", "symbol", "use", "meta", "link",
}

# Attributes worth keeping, per tag (plus a global allow-list).
ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}

VOID = {"br", "img", "hr"}


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.children = []


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        # stray close tag — ignore

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def build_tree(html: str) -> Node:
    parser = TreeBuilder()
    parser.feed(html)
    parser.close()
    return parser.root


def keep_attrs(tag: str, attrs: dict) -> dict:
    allowed = ATTRS.get(tag)
    if not allowed:
        return {}
    return {k: v for k, v in attrs.items() if k in allowed and v}


def render(node: Node, out: list) -> None:
    """Render a node into `out`, unwrapping containers and dropping noise."""
    if node.tag in DROP:
        return

    if node.tag not in KEEP:
        # div, span, section, tbody, ... — keep the children, lose the wrapper
        for child in node.children:
            if isinstance(child, str):
                out.append(child)
            else:
                render(child, out)
        return

    attrs = keep_attrs(node.tag, node.attrs)
    attr_text = "".join(f' {k}="{v}"' for k, v in attrs.items())
    if node.tag in VOID:
        out.append(f"<{node.tag}{attr_text}>")
        return

    out.append(f"<{node.tag}{attr_text}>")
    for child in node.children:
        if isinstance(child, str):
            out.append(child)
        else:
            render(child, out)
    out.append(f"</{node.tag}>")


def split_br_paragraphs(html: str) -> str:
    """
    Google Sites separates blocks with runs of <br> rather than real paragraphs.
    Inside a <p>, turn 2+ consecutive <br> into actual paragraph breaks so the
    output reads properly instead of being one long run of line breaks.
    """
    def repl(match: re.Match) -> str:
        inner = match.group(1)
        parts = re.split(r"(?:<br\s*/?>\s*){2,}", inner)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) <= 1:
            return match.group(0)
        return "".join(f"<p>{p}</p>" for p in parts)

    return re.sub(r"<p[^>]*>(.*?)</p>", repl, html, flags=re.S)


def fix_nesting(html: str) -> str:
    """
    Google Sites wraps whole paragraphs in an <a>, giving <a><p>text</p></a> —
    invalid HTML (block inside inline). Flip it to <p><a>text</a></p>.
    """
    return re.sub(
        r"<a\b([^>]*)>\s*<p>(.*?)</p>\s*</a>",
        r"<p><a\1>\2</a></p>",
        html,
        flags=re.S,
    )


def clean_fragment(fragment: str) -> str:
    """Convert one section fragment to clean HTML, or '' if nothing remains."""
    out: list[str] = []
    render(build_tree(fragment), out)
    text = "".join(out)

    # collapse whitespace runs, keeping single spaces
    text = re.sub(r"[ \t\r\n]+", " ", text)
    text = re.sub(r">\s+<", "><", text)

    text = split_br_paragraphs(text)
    text = fix_nesting(text)

    # drop elements that ended up empty
    for tag in ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                "strong", "em", "b", "i", "a", "span"):
        text = re.sub(rf"<{tag}\b[^>]*>\s*</{tag}>", "", text)

    text = text.strip()

    # nothing meaningful left (e.g. a pure decoration section)
    if not re.sub(r"<[^>]+>", "", text).strip() and "<img" not in text:
        return ""
    return text


SECTION_OPEN = re.compile(r"<section\b", re.I)
SECTION_TOKEN = re.compile(r"<section\b|</section>", re.I)


def split_sections(html: str):
    """Yield each <section> block from a page."""
    pos = 0
    while True:
        match = SECTION_OPEN.search(html, pos)
        if not match:
            return
        depth = 0
        scan = match.start()
        while True:
            token = SECTION_TOKEN.search(html, scan)
            if not token:
                return
            if token.group(0).lower() == "</section>":
                depth -= 1
                if depth == 0:
                    yield html[match.start():token.end()]
                    pos = token.end()
                    break
            else:
                depth += 1
            scan = token.end()


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def image_size(path: Path):
    """Read (width, height) from a PNG or JPEG without external libraries."""
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])

    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return (w, h)
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def strip_leading_banner(blocks: list[str]) -> list[str]:
    """
    Each platform page opens with a full-width decorative title graphic (the
    Apple TV logo, a page header banner, ...). Now that the page has a real
    text <h1> from the nav label, those are redundant — and at 1280px wide they
    swallow the entire viewport. Drop the leading image if it's banner-sized.

    Content screenshots are also 1280px wide, so only the *first* image on the
    page is considered.
    """
    if not blocks:
        return blocks

    match = re.match(r'\s*<img src="([^"]+)"[^>]*>', blocks[0])
    if not match:
        return blocks

    size = image_size(HERE / match.group(1))
    if not size or size[0] < 1000:
        return blocks

    remainder = blocks[0][match.end():]
    remainder = re.sub(r"^(?:\s|<br\s*/?>)+", "", remainder).strip()
    if remainder and (re.sub(r"<[^>]+>", "", remainder).strip() or "<img" in remainder):
        blocks[0] = remainder
    else:
        blocks.pop(0)
    return blocks


def demote_h1(blocks: list[str]) -> list[str]:
    """
    The page title is now a real <h1> generated from the nav label, so any <h1>
    left inside the content would give the page two competing top-level headings.
    Demote content <h1>s to <h2> for a clean outline.
    """
    return [
        b.replace("<h1>", "<h2>").replace("</h1>", "</h2>")
         .replace("<h1 ", "<h2 ")
        for b in blocks
    ]


def convert_page(page: Path) -> list[str]:
    """Return a list of clean HTML blocks for the page."""
    html = read(page)
    blocks = []
    for section in split_sections(html):
        cleaned = clean_fragment(section)
        if cleaned:
            blocks.append(cleaned)
    return demote_h1(strip_leading_banner(blocks))


def get_nav() -> list[tuple[str, str]]:
    """[(href, label)] from the mirrored nav, so converted pages keep the nav."""
    pages = sorted(p for p in HERE.glob("*.html")
                   if not p.name.startswith("_") and ".bak" not in p.name)
    html = read(pages[0])
    nav = []
    for _, _, slug, label, _ in nav_items(html):
        if not slug.startswith("/"):
            continue
        href = slug.lstrip("/")
        if href == "start":
            href = "index"
        label = label.lstrip("\u2004 ").lstrip("-").strip()
        label = label.replace("\u200e", "")
        nav.append((href + ".html", label))
    return nav


def cmd_dump(args) -> int:
    page = Path(args.page)
    if not page.is_absolute():
        page = HERE / page
    blocks = convert_page(page)
    print(f"# {page.name}: {len(blocks)} content blocks\n")
    for i, block in enumerate(blocks):
        print(f"--- block {i} ({len(block)} chars) ---")
        print(block[:600])
        print()
    return 0


# Nicer titles for pages the nav doesn't cover (faq, changelog) or where
# title-casing mangles the name ("Faq", "Ios", "Macos").
TITLE_FIX = {
    "index": "Start", "faq": "FAQ", "changelog": "Changelog",
    "macos": "MacOS", "ios": "iOS", "wii-u": "Wii U", "apple-tv": "Apple TV",
    "xbox-oneseries-xs": "Xbox One / Series X|S",
    "nintendo-switch2": "Nintendo Switch / Switch 2",
    "playstation-45": "PlayStation 4 / 5",
    "youtubenetflix": "YouTube / Netflix",
    "mods-and-extras": "Mods and Extras",
}


def page_title(page: Path) -> str:
    for href, label in get_nav():
        if href == page.name:
            return label
    return TITLE_FIX.get(page.stem, page.stem.replace("-", " ").title())


def cmd_demo(args) -> int:
    page = Path(args.page)
    if not page.is_absolute():
        page = HERE / page
    blocks = convert_page(page)

    nav_html = "\n".join(
        f'      <a href="{href}">{label}</a>' for href, label in get_nav()
    )
    body = "\n\n".join(f"  {b}" for b in blocks)

    # Google Sites renders page titles as images, so the converted page needs
    # a real <h1>. Take it from the nav label for this page.
    title = page_title(page)

    out = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} &middot; MCSM Megathread</title>
<link rel="stylesheet" href="assets/site.css">
</head>
<body>
<header class="topbar">
  <div class="wrap">
    <a class="brand" href="index.html">MCSM Megathread</a>
  </div>
  <nav class="nav">
    <div class="wrap">
{nav_html}
    </div>
  </nav>
</header>

<main>
  <article class="wrap">
    <h1>{title}</h1>

{body}
  </article>
</main>

<footer class="foot">
  <div class="wrap">
    <p>A fanmade project — not affiliated with Mojang or Telltale Games in any way.</p>
  </div>
</footer>
</body>
</html>
"""
    dest = HERE / f"demo-{page.stem}.html"
    dest.write_bytes(out.encode("utf-8"))
    print(f"wrote {dest.name} ({len(out):,} chars, {len(blocks)} blocks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert to clean HTML.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dump"); p.add_argument("page"); p.set_defaults(func=cmd_dump)
    p = sub.add_parser("demo"); p.add_argument("page"); p.set_defaults(func=cmd_demo)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
