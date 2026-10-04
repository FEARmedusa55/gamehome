#!/usr/bin/env python3
"""
Make the mirrored Google Sites pages easier to hand-edit.

Google Sites splits a single run of text across many <span> elements at
arbitrary points, e.g.

    <span class="X">Welcome to t</span><span class="X">he Minecraft!</span>

so one line you want to rewrite can be chopped into five pieces that all carry
identical styling. This merges adjacent <span> pairs with identical attributes.

The merge is visually a no-op (adjacent inline spans with identical attributes
render as one run of text), but it makes the source far easier to read and edit.

A pristine copy of every .html file is written to `_pristine-html/` before the
first run, so you can always diff against the untouched original.

    python make_editable.py
"""

import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKUP = HERE / "_pristine-html"

# Two adjacent spans holding plain text, with no nested markup between them.
PAIR = re.compile(
    r"(<span\b[^>]*>)([^<>]*?)</span>(<span\b[^>]*>)([^<>]*?)</span>"
)


def merge_once(html: str) -> str:
    def sub(match: re.Match) -> str:
        if match.group(1) == match.group(3):          # identical attributes
            return match.group(1) + match.group(2) + match.group(4) + "</span>"
        return match.group(0)

    return PAIR.sub(sub, html)


def merge(html: str) -> str:
    """Repeat until stable, so runs of 3+ spans collapse too."""
    while True:
        merged = merge_once(html)
        if merged == html:
            return html
        html = merged


def main() -> None:
    pages = [p for p in sorted(HERE.glob("*.html")) if not p.name.startswith("_")]
    if not pages:
        sys.exit("No .html files found next to this script.")

    BACKUP.mkdir(exist_ok=True)

    total_before = total_after = 0
    for page in pages:
        backup = BACKUP / page.name
        if not backup.exists():
            shutil.copy2(page, backup)

        html = page.read_text(encoding="utf-8")
        before = html.count("<span")
        merged = merge(html)
        after = merged.count("<span")

        if merged != html:
            page.write_text(merged, encoding="utf-8")

        total_before += before
        total_after += after
        if before != after:
            print(f"  {page.name:48} {before:5} -> {after:5} spans")

    print(
        f"\nTotal spans across {len(pages)} pages: "
        f"{total_before} -> {total_after} "
        f"({total_before - total_after} merged away)"
    )
    print(f"Pristine originals: {BACKUP}")


if __name__ == "__main__":
    main()
