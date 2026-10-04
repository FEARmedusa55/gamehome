#!/usr/bin/env python3
"""
Localise remote images used in inline style attributes.

Google Sites writes background images as inline styles in the HTML, e.g.

    <div class="IFuOkc" style="background-image: url(https://lh7-us...=w1280);">

`localise_css_assets.py` only handled url() refs inside .css files, so 188 of
these were still pointing at Google's servers — background images would simply
not appear offline.

    python localise_inline_styles.py
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

EXT_BY_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}


def download(url: str) -> tuple[bytes | None, str]:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip()
                return raw, ctype
        except Exception as exc:                       # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    failures.append((url, repr(last)))
    return None, ""


def local_target(url: str, ctype: str) -> str:
    ext = Path(urlsplit(url).path).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}:
        ext = EXT_BY_TYPE.get(ctype, ".bin")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"assets/images/{digest}{ext}"


def main() -> None:
    pages = sorted(HERE.glob("*.html"))
    if not pages:
        sys.exit("No .html files found.")

    occurrences = 0
    downloaded = 0

    for page in pages:
        text = page.read_text(encoding="utf-8")

        def sub(match: re.Match) -> str:
            nonlocal occurrences, downloaded
            quote, url = match.group(1), match.group(2)
            occurrences += 1

            if url not in cache:
                rel = local_target(url, "")
                dest = HERE / rel
                if not dest.exists():
                    data, ctype = download(url)
                    if data is None:
                        return match.group(0)
                    rel = local_target(url, ctype)
                    dest = HERE / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    downloaded += 1
                cache[url] = rel

            return f"url({quote}{cache[url]}{quote})"

        rewritten = URL_RE.sub(sub, text)
        if rewritten != text:
            page.write_text(rewritten, encoding="utf-8")

    print(f"Inline url() occurrences: {occurrences}")
    print(f"Unique URLs:              {len(cache)}")
    print(f"Newly downloaded:         {downloaded}")

    remaining = sum(
        len(URL_RE.findall(p.read_text(encoding="utf-8"))) for p in pages
    )
    print(f"Remote refs remaining:    {remaining}")

    if failures:
        print(f"\n{len(failures)} failed:")
        for url, err in failures[:10]:
            print(f"  {url[:90]}\n    {err}")
    else:
        print("\nNo failures.")


if __name__ == "__main__":
    main()
