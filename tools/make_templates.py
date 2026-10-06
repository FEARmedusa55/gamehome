#!/usr/bin/env python3
"""
Build the "blank section" templates used by the editor's New section button.

A brand-new section has to look native, which means it must reuse the site's own
markup — hand-writing Google Sites markup would not match. So each template is
lifted from a real section and then neutralised:

  * the text is replaced with placeholders
  * explicit colours are stripped so the text inherits the page's colour
    (otherwise a template taken from a red heading stays red)
  * the section id is cleared — a fresh one is generated per insert, so new
    sections never collide with the original they were copied from

    python tools/make_templates.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import textedit as textedit_mod  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
CONTENT = HERE / "content"
OUT = Path(__file__).resolve().parent / "templates"

# (name, menu label, source page, source section, placeholders, strip colours?)
SOURCES = [
    ("heading-and-text", "Heading + text", "index.html",
     "05-important-read-below-if-nothing.html",
     ["New section heading", "Write your text here."], True),

    ("note", "Note / callout", "index.html",
     "05-important-read-below-if-nothing.html",
     ["NOTE", "Write your note here."], False),

    ("text", "Text", "android.html",
     "02-there-is-no-way-to.html",
     ["Write your text here."], True),

    # No "Bullet list" element: any paragraph becomes a bullet with the
    # editor's "• list" button, which also works on the site's own text.

    ("links", "Link buttons", "android.html",
     "13-zarchiver-by-zdevs-split-apks.html",
     ["Link one", "Link two", "Link three", "Link four"], True),

    ("spacer", "Spacer", "android.html",
     "03-h-4908d146757e1f3d-15.html",
     [], True),

    ("image", "Image", "android.html",
     "01-h-30f65f4b4b51cf72-34.html", [], False),

    ("image-text", "Image + text", "android.html",
     "18-your-season-one-obb-folder.html",
     ["Your image caption goes here."], False),
]

# Fallbacks if a page has been re-staged and the source filename changed.
ALTERNATES = {
    "text": [("android.html", "32-important-note-as-android-phones.html"),
             ("apple-tv.html", "03-and-thats-it-thats-quite.html")],
    "links": [("android.html", "11-recommended-installer-installerx-revived-by.html"),
              ("android.html", "05-adreno-mirrors-coming-soon-mali.html")],
    "spacer": [("android.html", "09-h-4908d146757e1f3d-30.html")],
    "image": [("changelog.html", "00-h-704885ebc648004b-5.html")],
    "image-text": [("android.html", "19-your-season-one-data-folder.html")],
    "heading-and-text": [("apple-tv.html", "02-if-you-have-an-apple.html")],
    "note": [("android.html", "29-notes-note-it-seems-that.html")],
}


def find_source(page: str, section: str):
    path = CONTENT / Path(page).stem / section
    if path.exists():
        return path
    folder = CONTENT / Path(page).stem
    if folder.exists():
        stem = section.split("-", 1)[-1].removesuffix(".html")[:16]
        for cand in sorted(folder.glob("[0-9]*.html")):
            if stem and stem in cand.name:
                return cand
    return None


def build(page: str, section: str, placeholders: list, strip_colors: bool):
    src = find_source(page, section)
    if src is None:
        return None

    html = textedit_mod.merge_spans(src.read_bytes().decode("utf-8"))

    # 1. strip explicit colour so the template inherits the page's text colour
    if strip_colors:
        html = re.sub(r'style="([^"]*?)color:\s*[^;"]+;?\s*', r'style="\1', html)
        html = re.sub(r'style="\s*;?\s*"', 'style=""', html)

    # 2. blank the section id so each insert can take a fresh one
    html = re.sub(r'\bid="h\.[^"]*"', 'id="SECTION_ID"', html)

    # 3. replace each text run with a placeholder
    out, last, i = [], 0, 0
    for match, tag, attrs, text in textedit_mod.text_runs(html):
        if not text.strip():
            continue
        out.append(html[last:match.start(3)])
        out.append(placeholders[i] if i < len(placeholders) else "")
        last = match.end(3)
        i += 1
    out.append(html[last:])
    return "".join(out), i


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, label, page, section, placeholders, strip in SOURCES:
        result, used = None, section
        for pg, sec in [(page, section)] + ALTERNATES.get(name, []):
            result = build(pg, sec, placeholders, strip)
            if result:
                used = f"{pg} / {sec}"
                break
        if not result:
            print(f"  ! {name:18} no usable source section — skipped")
            continue
        html, runs = result
        (OUT / f"{name}.html").write_bytes(html.encode("utf-8"))
        print(f"  {name:18} {len(html):>7,} chars  {runs:>2} runs  <- {used}")
        ok += 1
    print(f"\n{ok} element templates in tools/templates/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
