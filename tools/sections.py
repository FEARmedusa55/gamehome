#!/usr/bin/env python3
"""
Section tool for the mirrored pages.

Google Sites builds a page out of a flat list of <section> blocks. Each one is a
self-contained chunk of content, which makes them the natural unit for editing:
remove a section, add a section, reorder sections.

This finds them, reports them, and removes them — with balanced-tag matching, so
it can't cut a section short and leave broken markup behind.

    python tools/sections.py list index.html
    python tools/sections.py list index.html --full
    python tools/sections.py remove index.html h.INITIAL_GRID.dqxorb9439ak
    python tools/sections.py remove index.html <id> --dry-run

Every removal saves a timestamped backup next to the file first.
"""

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

SECTION_OPEN = re.compile(r"<section\b[^>]*>", re.I)
SECTION_TOKEN = re.compile(r"<section\b|</section>", re.I)


def read(path: Path) -> str:
    # read_bytes, not read_text: read_text() applies universal-newline
    # translation and would silently rewrite the file's line endings.
    return path.read_bytes().decode("utf-8")


def write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def iter_sections(html: str):
    """Yield (start, end, open_tag) for each top-level and nested <section>."""
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
                break
            if token.group(0).lower() == "</section>":
                depth -= 1
                if depth == 0:
                    yield match.start(), token.end(), match.group(0)
                    pos = token.end()
                    break
            else:
                depth += 1
            scan = token.end()
        else:
            return


def strip_tags(fragment: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", text).strip()


def section_id(open_tag: str) -> str:
    match = re.search(r'id="([^"]*)"', open_tag)
    return match.group(1) if match else "(no id)"


def cmd_list(args) -> int:
    pages = [Path(p) for p in args.pages]
    for page in pages:
        if not page.is_absolute():
            page = HERE / page
        if not page.exists():
            print(f"! not found: {page}")
            continue

        html = read(page)
        print(f"\n=== {page.name} ({len(html.encode('utf-8')):,} bytes) ===")
        for index, (start, end, open_tag) in enumerate(iter_sections(html)):
            fragment = html[start:end]
            text = strip_tags(fragment)
            preview = text[:78] if text else "(no text — image/decoration)"
            nbytes = len(fragment.encode("utf-8"))
            print(f"  [{index:2}] {section_id(open_tag):38} {nbytes:>8,}b")
            print(f"       {preview}")
            if args.full and text:
                print(f"       FULL: {text[:400]}")
    return 0


def cmd_remove(args) -> int:
    page = Path(args.page)
    if not page.is_absolute():
        page = HERE / page
    if not page.exists():
        print(f"! not found: {page}")
        return 1

    html = read(page)

    target = None
    for start, end, open_tag in iter_sections(html):
        if section_id(open_tag) == args.section_id:
            target = (start, end, open_tag)
            break

    if target is None:
        print(f"! no section with id {args.section_id!r} in {page.name}")
        print("  run `list` to see the available ids")
        return 1

    start, end, open_tag = target
    fragment = html[start:end]
    text = strip_tags(fragment)

    print(f"Removing section {args.section_id!r} from {page.name}")
    print(f"  {len(fragment.encode('utf-8')):,} bytes")
    print(f"  text: {text[:160] if text else '(no text — image/decoration)'}")

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = HERE / "_backups"
    backups.mkdir(exist_ok=True)
    backup = backups / f"{page.name}.{stamp}.bak"
    shutil.copy2(page, backup)

    new_html = html[:start] + html[end:]
    write(page, new_html)

    print(f"\n  removed {(len(html) - len(new_html)):,} bytes")
    print(f"  page now {len(new_html.encode('utf-8')):,} bytes")
    print(f"  backup: {backup.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="List or remove page sections.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list sections in one or more pages")
    p_list.add_argument("pages", nargs="+")
    p_list.add_argument("--full", action="store_true", help="show more text")
    p_list.set_defaults(func=cmd_list)

    p_rm = sub.add_parser("remove", help="remove one section by id")
    p_rm.add_argument("page")
    p_rm.add_argument("section_id")
    p_rm.add_argument("--dry-run", action="store_true")
    p_rm.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
