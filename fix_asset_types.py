#!/usr/bin/env python3
"""
Fix asset file extensions in the mirror.

Google's servers return some assets with a generic or unusual Content-Type, so
_mirror.py saved 142 files as `.bin`. That breaks the copy: a browser will
happily sniff an <img>, but it flatly refuses to apply a stylesheet served as
application/octet-stream — so all of Google Sites' CSS was silently ignored and
the pages rendered unstyled.

This walks assets/, identifies each `.bin` by its magic bytes, renames it to the
correct extension, and rewrites every reference in the HTML and CSS.

    python fix_asset_types.py
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"


def sniff(data: bytes) -> str | None:
    """Identify a file's real type from its leading bytes."""
    head = data[:64]

    # --- binary formats -------------------------------------------------
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"wOFF"):
        return ".woff"
    if head.startswith(b"wOF2"):
        return ".woff2"
    if head.startswith(b"\x00\x01\x00\x00"):
        return ".ico"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return ".webm"
    if head.startswith(b"%PDF"):
        return ".pdf"

    # --- text formats ---------------------------------------------------
    text = data[:800].decode("utf-8", "ignore").lstrip()
    low = text.lower()

    if low.startswith(("<?xml", "<svg")) or "<svg" in low[:300]:
        return ".svg"
    if low.startswith(("<!doctype html", "<html")):
        return ".html"
    if low.startswith(
        ("!function", "function", "(function", "var ", "const ", "let ",
         '"use strict', "'use strict", "import ", "window.", "/*!")
    ):
        return ".js"
    # a CSS rule: selector-ish text followed by a brace
    if "{" in text and re.search(r"(^|[\s,}])[.#@a-zA-Z*\[:][^{};]{0,120}\{", text):
        return ".css"

    return None


def main() -> None:
    if not ASSETS.exists():
        sys.exit(f"No assets folder at {ASSETS}")

    bins = sorted(ASSETS.rglob("*.bin"))
    if not bins:
        print("No .bin files left — nothing to do.")
        return

    mapping: dict[str, str] = {}
    unknown = []

    for path in bins:
        data = path.open("rb").read(2048)
        ext = sniff(data)
        if not ext:
            unknown.append(path.name)
            continue

        target = path.with_suffix(ext)
        if target.exists():
            target = path.with_name(f"{path.stem}_{ext.lstrip('.')}{ext}")

        path.rename(target)
        mapping[path.name] = target.name

    if not mapping:
        print("Nothing identified.")
        return

    # --- rewrite references ---------------------------------------------
    targets = list(HERE.rglob("*.html")) + list(ASSETS.rglob("*.css"))
    touched = 0
    for doc in targets:
        try:
            text = doc.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        original = text
        for old, new in mapping.items():
            if old in text:
                text = text.replace(old, new)
        if text != original:
            doc.write_text(text, encoding="utf-8")
            touched += 1

    kinds: dict[str, int] = {}
    for old, new in mapping.items():
        kinds[Path(new).suffix] = kinds.get(Path(new).suffix, 0) + 1

    print(f"Renamed {len(mapping)} files by real content type:")
    for ext, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {ext:7} {count}")
    print(f"\nUpdated references in {touched} files.")
    if unknown:
        print(f"\n{len(unknown)} left as .bin (unrecognised):")
        for name in unknown[:10]:
            print(f"  {name}")


if __name__ == "__main__":
    main()
