#!/usr/bin/env python3
"""
Mirror https://www.mcsmmegathread.org into an offline, editable copy.

Fetches every page plus its assets (CSS, JS, images, fonts), saves them under
this folder, and rewrites all URLs so the copy works from the local filesystem.

    python _mirror.py

Only needs the Python standard library.
"""

import gzip
import hashlib
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BASE = "https://www.mcsmmegathread.org"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HERE = Path(__file__).resolve().parent

# Seed pages: (url path, local filename). The crawler also follows any other
# internal links it finds, so pages missing from this list still get picked up.
SEED = [
    ("/start", "index.html"),
    ("/windows", "windows.html"),
    ("/linux", "linux.html"),
    ("/macos", "macos.html"),
    ("/xbox-oneseries-xs", "xbox-oneseries-xs.html"),
    ("/xbox-360", "xbox-360.html"),
    ("/playstation-45", "playstation-45.html"),
    ("/playstation-3", "playstation-3.html"),
    ("/nintendo-switch2", "nintendo-switch2.html"),
    ("/wii-u", "wii-u.html"),
    ("/ios", "ios.html"),
    ("/android", "android.html"),
    ("/apple-tv", "apple-tv.html"),
    ("/youtubenetflix", "youtubenetflix.html"),
    ("/mods-and-extras", "mods-and-extras.html"),
    ("/faq", "faq.html"),
    ("/credits", "credits.html"),
    ("/changelog", "changelog.html"),
]

MAX_PAGES = 60

# Hosts whose files we pull down and localise.
ASSET_HOSTS = (
    "www.gstatic.com",
    "gstatic.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "apis.google.com",
    "lh3.googleusercontent.com",
    "lh4.googleusercontent.com",
    "lh5.googleusercontent.com",
    "lh6.googleusercontent.com",
    "lh7-us.googleusercontent.com",
    "lh7-rt.googleusercontent.com",
    "ssl.gstatic.com",
    "www.mcsmmegathread.org",
    "mcsmmegathread.org",
)

EXT_BY_TYPE = {
    "text/css": ".css",
    "application/javascript": ".js",
    "text/javascript": ".js",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "font/woff2": ".woff2",
    "font/woff": ".woff",
    "font/ttf": ".ttf",
    "application/json": ".json",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico"}
FONT_EXT = {".woff", ".woff2", ".ttf", ".otf"}

stats = {"pages": 0, "assets": 0, "bytes": 0}
failures = []
url_to_local = {}   # absolute remote url -> local relative path


def fetch(url, tries=3):
    """GET a URL, returning (bytes, content_type) or (None, None)."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                },
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip()
                stats["bytes"] += len(raw)
                return raw, ctype
        except Exception as exc:                      # noqa: BLE001
            last = exc
            time.sleep(1.2 * (attempt + 1))
    failures.append((url, repr(last)))
    return None, None


def local_path_for(url, ctype=""):
    """Decide where a remote asset lives locally (deterministic, deduped)."""
    if url in url_to_local:
        return url_to_local[url]

    ext = Path(urlsplit(url).path).suffix.lower()
    if ext not in EXT_BY_TYPE.values():
        ext = EXT_BY_TYPE.get(ctype, "")

    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    if ext in IMAGE_EXT:
        folder = "assets/images"
    elif ext in FONT_EXT:
        folder = "assets/fonts"
    else:
        folder = "assets/external"

    rel = f"{folder}/{digest}{ext or '.bin'}"
    url_to_local[url] = rel
    return rel


def save(rel, data):
    dest = HERE / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def is_asset(url):
    return urlsplit(url).netloc in ASSET_HOSTS


def grab_asset(url, ctype=""):
    """Download one asset; return its local relative path (or None)."""
    rel = local_path_for(url, ctype)
    if (HERE / rel).exists():
        return rel
    data, real_type = fetch(url)
    if data is None:
        return None
    rel = local_path_for(url, real_type)          # refine ext from real type
    save(rel, data)
    stats["assets"] += 1

    # CSS files reference fonts/images of their own — localise those too.
    if rel.endswith(".css"):
        css = data.decode("utf-8", "replace")
        css = replace_css_urls(css, url, HERE / rel)
        (HERE / rel).write_bytes(css.encode("utf-8"))
    return rel


def replace_css_urls(css, base_url, css_local_path):
    """Rewrite url(...) references inside a stylesheet to local paths."""

    def sub(match):
        raw = match.group(1).strip("'\"")
        if raw.startswith("data:"):
            return match.group(0)
        absolute = urljoin(base_url, raw)
        if not is_asset(absolute):
            return match.group(0)
        rel = grab_asset(absolute)
        if rel is None:
            return match.group(0)
        # path relative to the CSS file's own location
        depth = len(css_local_path.relative_to(HERE).parts) - 1
        prefix = "../" * depth
        return f"url({prefix}{rel})"

    return re.sub(r"url\(\s*([^)]+?)\s*\)", sub, css)


def rewrite_internal_links(html):
    """Point /windows style links at the local .html filenames."""
    path_map = {path: filename for path, filename in page_map.items()}

    def sub(match):
        attr, quote_char, url = match.group(1), match.group(2), match.group(3)
        split = urlsplit(url)
        if split.netloc and split.netloc not in ("www.mcsmmegathread.org", "mcsmmegathread.org"):
            return match.group(0)
        path = split.path
        if path in path_map:
            target = path_map[path]
            if split.fragment:
                target += "#" + split.fragment
            return f"{attr}={quote_char}{target}{quote_char}"
        return match.group(0)

    return re.sub(r'(href|src)=(["\'])([^"\']+)\2', sub, html)


def rewrite_assets(html, page_url, page_local):
    """Download every referenced asset and rewrite the URL to a local path."""

    def sub(match):
        attr, quote_char, url = match.group(1), match.group(2), match.group(3)
        if url.startswith(("data:", "mailto:", "javascript:", "#")):
            return match.group(0)
        absolute = urljoin(page_url, url)
        if not is_asset(absolute):
            return match.group(0)
        if urlsplit(absolute).netloc in ("www.mcsmmegathread.org", "mcsmmegathread.org"):
            # internal page link, handled separately
            return match.group(0)
        rel = grab_asset(absolute)
        if rel is None:
            return match.group(0)
        return f"{attr}={quote_char}{rel}{quote_char}"

    return re.sub(r'(href|src)=(["\'])([^"\']+)\2', sub, html)


# ---------------------------------------------------------------------------

page_map = {}          # url path -> local filename
queue = []
crawled = set()

for path, filename in SEED:
    page_map[path] = filename
    queue.append((path, filename))


def discover_internal_links(html):
    """Find internal page links not already queued."""
    found = set()
    for match in re.finditer(r'href=(["\'])([^"\']+)\1', html):
        url = match.group(2)
        if url.startswith(("http://", "https://")):
            split = urlsplit(url)
            if split.netloc not in ("www.mcsmmegathread.org", "mcsmmegathread.org"):
                continue
            path = split.path
        elif url.startswith("/"):
            path = urlsplit(url).path
        else:
            continue
        if not path or path == "/":
            continue
        if Path(path).suffix:
            continue
        if path in page_map:
            continue
        found.add(path)
    return found


print(f"Mirroring {BASE} -> {HERE}\n")

while queue and stats["pages"] < MAX_PAGES:
    path, filename = queue.pop(0)
    if path in crawled:
        continue
    crawled.add(path)

    url = BASE + path
    data, ctype = fetch(url)
    if data is None:
        print(f"  FAIL  {url}")
        continue

    html = data.decode("utf-8", "replace")

    for new_path in discover_internal_links(html):
        slug = new_path.strip("/").replace("/", "-") or "index"
        page_map[new_path] = f"{slug}.html"
        queue.append((new_path, page_map[new_path]))

    html = rewrite_internal_links(html)
    html = rewrite_assets(html, url, filename)

    (HERE / filename).write_bytes(html.encode("utf-8"))
    stats["pages"] += 1
    print(f"  ok    {url:58} -> {filename}")

print(f"\nPages:  {stats['pages']}")
print(f"Assets: {stats['assets']}")
print(f"Total:  {stats['bytes'] / 1_000_000:.1f} MB downloaded")
if failures:
    print(f"\n{len(failures)} failed:")
    for url, err in failures[:20]:
        print(f"  {url}\n    {err}")
else:
    print("No failures.")
