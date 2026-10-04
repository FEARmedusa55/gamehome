#!/usr/bin/env python3
"""
Localise the assets referenced from *inside* the downloaded stylesheets.

_mirror.py had a step for this, but at download time every stylesheet was saved
as `.bin`, so that step never matched and was skipped. After
`fix_asset_types.py` renamed them to `.css`, 240 url(...) references remained
pointing at live Google servers — mostly Roboto webfonts (fonts.gstatic.com) and
UI icon sprites (ssl.gstatic.com).

This downloads them, saves them under assets/fonts or assets/images, and
rewrites the CSS to point at the local copies, so the page is fully
self-contained with no network access.

    python localise_css_assets.py
"""

import gzip
import hashlib
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

URL_RE = re.compile(r"url\(\s*(['\"]?)(https?://[^)'\"]+)\1\s*\)")

cache: dict[str, str] = {}
failures: list[tuple[str, str]] = []

FONT_EXT = {".woff2", ".woff", ".ttf", ".otf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}


def download(url: str) -> bytes | None:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except Exception as exc:                       # noqa: BLE001
            last = exc
            time.sleep(1.2 * (attempt + 1))
    failures.append((url, repr(last)))
    return None


def local_target(url: str) -> str:
    """Where this URL should live, as a path relative to the repo root."""
    ext = Path(urlsplit(url).path).suffix.lower()
    if ext in FONT_EXT:
        folder = "assets/fonts"
    elif ext in IMAGE_EXT:
        folder = "assets/images"
    else:
        folder, ext = "assets/external", ext or ".bin"

    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"{folder}/{digest}{ext}"


def main() -> None:
    css_files = sorted(ASSETS.rglob("*.css"))
    if not css_files:
        sys.exit("No CSS files found — run fix_asset_types.py first.")

    total_refs = 0
    downloaded = 0

    for css in css_files:
        text = css.read_text(encoding="utf-8")
        depth = len(css.relative_to(HERE).parts) - 1     # assets/external/x.css -> 2
        prefix = "../" * depth

        def sub(match: re.Match, css=css, prefix=prefix) -> str:
            nonlocal total_refs, downloaded
            quote, url = match.group(1), match.group(2)
            total_refs += 1

            if url not in cache:
                rel = local_target(url)
                dest = HERE / rel
                if not dest.exists():
                    data = download(url)
                    if data is None:
                        return match.group(0)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    downloaded += 1
                cache[url] = prefix + rel

            return f"url({quote}{cache[url]}{quote})"

        rewritten = URL_RE.sub(sub, text)
        if rewritten != text:
            css.write_text(rewritten, encoding="utf-8")

    print(f"Referenced URL occurrences: {total_refs}")
    print(f"Unique URLs:               {len(cache)}")
    print(f"Newly downloaded:          {downloaded}")

    remaining = 0
    for css in css_files:
        remaining += len(URL_RE.findall(css.read_text(encoding="utf-8")))
    print(f"Remote refs remaining:     {remaining}")

    if failures:
        print(f"\n{len(failures)} failed:")
        for url, err in failures[:10]:
            print(f"  {url}\n    {err}")
    else:
        print("\nNo failures.")


if __name__ == "__main__":
    main()
