#!/usr/bin/env python3
"""
Site-wide finishing touches, applied every time a page is built.

The mirrored pages were made by Google Sites, whose own scripts crash on a
static copy (`_._DumpException is not a function`). Two things died with them:

  * the phone menu — the hamburger never opened the nav drawer, so on a phone
    there was no way to reach any other page;
  * the search icon — it did nothing at all.

This module replaces both with a small script and stylesheet of our own, and
keeps each page's <title> and link-preview tags pointing at this site rather
than the one it was copied from. It also writes search-index.js, the list of
every page's text that the search box reads.

content.build_one() calls finalize() on every page it assembles, so nothing
here needs running by hand. `python tools/sitekit.py` rebuilds the search
index on its own, if you ever edit a page outside the editor.
"""

from __future__ import annotations

import html as htmllib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sections import iter_sections, section_id, strip_tags  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
CONTENT = HERE / "content"
SEARCH_INDEX = HERE / "search-index.js"


def site_host() -> str:
    """The published domain, read from the CNAME GitHub Pages uses."""
    try:
        host = (HERE / "CNAME").read_text(encoding="utf-8").strip()
    except OSError:
        host = ""
    return host or "wiki.fearsbluenova.com"


def page_url(page_file: str) -> str:
    """GitHub Pages serves foo.html at /foo, and index.html at /."""
    stem = Path(page_file).stem
    return f"https://{site_host()}/" + ("" if stem == "index" else stem)


# ---------------------------------------------------------------------------
# Phone menu + search: the stylesheet and script every page carries
# ---------------------------------------------------------------------------

CSS_ID = "mm-drawer-css"
JS_ID = "mm-site-js"

SITE_CSS = """<style id="mm-drawer-css">
/* Phone menu. Below ~1280px Google parks the nav off-screen and its own
   script slides it back by adding `jsnVQ` — the class its stylesheet animates
   the drawer and its rows with. That script never runs here, so ours adds the
   same class (see SITE_JS). */
html.mmNavOpen #yuynLe{overflow-y:auto;box-shadow:4px 0 24px rgba(0,0,0,.5)}
#mm-nav-back{display:none}
html.mmNavOpen #mm-nav-back{display:block;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:69}
/* Search */
#mm-search{position:fixed;inset:0;z-index:2147483000;background:rgba(5,6,20,.72);display:flex;
justify-content:center;align-items:flex-start;padding:10vh 16px 16px;box-sizing:border-box}
#mm-search[hidden]{display:none}
#mm-search .mmBox{width:100%;max-width:640px;max-height:78vh;display:flex;flex-direction:column;
background:#131540;color:#f9f9f9;border:1px solid rgba(255,255,255,.15);border-radius:10px;
box-shadow:0 12px 40px rgba(0,0,0,.5);font-family:Quicksand,Arial,sans-serif;overflow:hidden}
#mm-search input{width:100%;box-sizing:border-box;font:inherit;font-size:18px;padding:14px 16px;
background:transparent;color:inherit;border:0;border-bottom:1px solid rgba(255,255,255,.15);outline:none}
#mm-search ul{list-style:none;margin:0;padding:6px;overflow-y:auto}
#mm-search li a{display:block;padding:10px 12px;border-radius:6px;color:inherit;text-decoration:none}
#mm-search li a:hover,#mm-search li a:focus{background:rgba(60,68,204,.45);outline:none}
#mm-search .mmWhere{font-size:12px;opacity:.7;margin-bottom:2px}
#mm-search .mmSnip{font-size:14px;line-height:1.45}
#mm-search mark{background:#3c44cc;color:#fff;border-radius:2px;padding:0 1px}
#mm-search .mmEmpty{padding:16px;opacity:.7;font-size:14px}
</style>"""

SITE_JS = """<script id="mm-site-js">
(function () {
  if (window.__mmSite) return;
  window.__mmSite = true;
  var doc = document, root = doc.documentElement;

  // --- phone menu -----------------------------------------------------------
  var burger = doc.getElementById('s9iPrd');
  var nav = doc.getElementById('yuynLe');
  var back = null;
  function setNav(open) {
    root.classList.toggle('mmNavOpen', open);
    if (nav) nav.classList.toggle('jsnVQ', open);
    if (burger) burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open && !back) {
      back = doc.createElement('div');
      back.id = 'mm-nav-back';
      back.addEventListener('click', function () { setNav(false); });
      doc.body.appendChild(back);
    }
  }
  if (burger && nav) {
    var toggle = function (e) {
      e.preventDefault();
      e.stopPropagation();
      setNav(!root.classList.contains('mmNavOpen'));
    };
    burger.addEventListener('click', toggle, true);
    burger.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') toggle(e);
    }, true);
    nav.addEventListener('click', function (e) {
      if (e.target.closest && e.target.closest('a[href]')) setNav(false);
    });
  }

  // --- search ---------------------------------------------------------------
  // The index is a script, not JSON, so it also loads from a file:// copy.
  var INDEX = null, box = null, input = null, list = null;

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function loadIndex(done) {
    if (INDEX) return done();
    var s = doc.createElement('script');
    s.src = 'search-index.js';
    s.onload = function () { INDEX = window.MM_SEARCH || []; done(); };
    s.onerror = function () { INDEX = []; done('The search index could not be loaded.'); };
    doc.head.appendChild(s);
  }
  function snippet(text, words) {
    var low = text.toLowerCase(), at = -1;
    for (var i = 0; i < words.length && at < 0; i++) at = low.indexOf(words[i]);
    var from = Math.max(0, at - 60), to = Math.min(text.length, Math.max(at, 0) + 140);
    var out = esc(text.slice(from, to));
    words.forEach(function (w) {
      out = out.replace(new RegExp('(' + esc(w).replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&') + ')', 'gi'),
                        '<mark>$1</mark>');
    });
    return (from > 0 ? '\\u2026' : '') + out + (to < text.length ? '\\u2026' : '');
  }
  function run() {
    var q = input.value.trim().toLowerCase();
    if (!q) { list.innerHTML = ''; return; }
    var words = q.split(/\\s+/), hits = [];
    (INDEX || []).forEach(function (page) {
      (page.s || []).forEach(function (sec) {
        var hay = (page.t + ' ' + (sec.h || '') + ' ' + sec.x).toLowerCase();
        for (var i = 0; i < words.length; i++) if (hay.indexOf(words[i]) < 0) return;
        var score = 0;
        words.forEach(function (w) {
          score += hay.split(w).length - 1;
          if ((sec.h || '').toLowerCase().indexOf(w) >= 0) score += 5;
          if (page.t.toLowerCase().indexOf(w) >= 0) score += 3;
        });
        hits.push({ page: page, sec: sec, score: score });
      });
    });
    hits.sort(function (a, b) { return b.score - a.score; });
    if (!hits.length) {
      list.innerHTML = '<li class="mmEmpty">Nothing found for \\u201c' + esc(input.value.trim()) + '\\u201d.</li>';
      return;
    }
    list.innerHTML = hits.slice(0, 40).map(function (h) {
      var where = esc(h.page.t) + (h.sec.h && h.sec.h !== h.page.t ? ' \\u203a ' + esc(h.sec.h) : '');
      return '<li><a href="' + esc(h.page.p + (h.sec.id ? '#' + h.sec.id : '')) + '">' +
             '<div class="mmWhere">' + where + '</div>' +
             '<div class="mmSnip">' + snippet(h.sec.x, words) + '</div></a></li>';
    }).join('');
  }
  function closeSearch() { if (box) box.hidden = true; }
  function openSearch() {
    if (!box) {
      box = doc.createElement('div');
      box.id = 'mm-search';
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      box.setAttribute('aria-label', 'Search this site');
      box.innerHTML = '<div class="mmBox"><input type="search" placeholder="Search this site\\u2026" ' +
                      'aria-label="Search this site" autocomplete="off"><ul></ul></div>';
      doc.body.appendChild(box);
      input = box.querySelector('input');
      list = box.querySelector('ul');
      input.addEventListener('input', run);
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          var first = list.querySelector('a');
          if (first) first.click();
        }
      });
      box.addEventListener('click', function (e) {
        if (e.target === box) closeSearch();
        else if (e.target.closest && e.target.closest('a[href]')) closeSearch();
      });
    }
    box.hidden = false;
    setNav(false);
    input.focus();
    input.select();
    loadIndex(function (err) {
      if (err) list.innerHTML = '<li class="mmEmpty">' + esc(err) + '</li>';
      else run();
    });
  }
  var opener = function (e) { e.preventDefault(); e.stopPropagation(); openSearch(); };
  doc.querySelectorAll('[jsname="R9oOZd"], [aria-label="Open search bar"]').forEach(function (b) {
    b.addEventListener('click', opener, true);
    b.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') opener(e);
    }, true);
  });

  doc.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeSearch(); setNav(false); }
    // "/" opens search, as on most sites — unless you are typing somewhere.
    if (e.key === '/' && !(e.target.closest && e.target.closest('input, textarea, [contenteditable="true"]'))) {
      e.preventDefault();
      openSearch();
    }
  });
})();
</script>"""


def _ensure_block(html: str, block_id: str, block: str, before: str) -> str:
    """Put `block` in the page once, replacing an older copy if there is one."""
    tag = block.split(">", 1)[0].split()[0].lstrip("<")        # style / script
    pattern = re.compile(rf'<{tag} id="{block_id}">.*?</{tag}>', re.S)
    if pattern.search(html):
        return pattern.sub(lambda m: block, html, count=1)
    at = html.rfind(before)
    return html if at == -1 else html[:at] + block + html[at:]


def ensure_assets(html: str) -> str:
    html = _ensure_block(html, CSS_ID, SITE_CSS, "</head>")
    return _ensure_block(html, JS_ID, SITE_JS, "</body>")


# ---------------------------------------------------------------------------
# Title and link-preview tags
# ---------------------------------------------------------------------------

# Images hosted by Google Sites. They belong to the old site and can stop
# working at any time, so link previews simply go without a picture.
_IMAGE_META = re.compile(
    r'<meta (?:itemprop="(?:thumbnailUrl|image|imageUrl)"|property="og:image")[^>]*>')
_DESC_META = re.compile(
    r'<meta (?:property="og:description"|itemprop="description"|name="description")[^>]*>')


def current_label(html: str) -> str:
    """The label of the nav row this page highlights as its own, at any level."""
    nav_at = html.find("<nav ")
    nav_end = html.find("</nav>", nav_at)
    if nav_at == -1 or nav_end == -1:
        return ""
    nav = html[nav_at:nav_end]
    mark = re.search(r'<div class="[^"]*\blhZOrc\b[^"]*"', nav)
    if not mark:
        return ""
    link = re.search(r"<a\b[^>]*>(.*?)</a>", nav[mark.end():], re.S)
    if not link:
        return ""
    text = htmllib.unescape(re.sub(r"<[^>]+>", "", link.group(1)))
    return text.replace("\u200e", "").lstrip("\u2004 ").lstrip("-").strip()


def page_text(html: str) -> str:
    """All the readable text in the page's sections."""
    text = " ".join(strip_tags(html[s:e]) for s, e, _ in iter_sections(html))
    return re.sub(r"\s+", " ", text).strip()


def _set_meta(html: str, attr: str, name: str, value: str) -> str:
    pattern = re.compile(rf'(<meta {attr}="{re.escape(name)}" content=")[^"]*(")')
    return pattern.sub(lambda m: m.group(1) + htmllib.escape(value, quote=True) + m.group(2),
                       html, count=1)


def fix_meta(html: str, page_file: str) -> str:
    head_end = html.find("</head>")
    if head_end == -1:
        return html
    head, rest = html[:head_end], html[head_end:]

    title = current_label(html)
    if not title:
        m = re.search(r"<title>(.*?)</title>", head, re.S)
        title = htmllib.unescape(m.group(1)).strip() if m else Path(page_file).stem
    safe_title = htmllib.escape(title, quote=False)
    head = re.sub(r"<title>.*?</title>", lambda m: f"<title>{safe_title}</title>", head,
                  count=1, flags=re.S)
    head = _set_meta(head, "property", "og:title", title)
    head = _set_meta(head, "itemprop", "name", title)

    url = page_url(page_file)
    head = _set_meta(head, "property", "og:url", url)
    head = _set_meta(head, "itemprop", "url", url)

    head = _IMAGE_META.sub("", head)

    # The description is the opening words of the page itself, so it can never
    # go stale the way the copied Google Sites one did.
    text = page_text(html)
    desc = text[:200].rsplit(" ", 1)[0] + "\u2026" if len(text) > 200 else text
    head = _DESC_META.sub("", head)
    if desc:
        safe = htmllib.escape(desc, quote=True)
        tags = (f'<meta name="description" content="{safe}">'
                f'<meta property="og:description" content="{safe}">')
        og = re.search(r'<meta property="og:title"[^>]*>', head)
        head = head[:og.end()] + tags + head[og.end():] if og else head + tags
    return head + rest


def finalize(html: str, page_file: str) -> str:
    """Everything a built page needs on top of its staged pieces."""
    return fix_meta(ensure_assets(html), page_file)


# ---------------------------------------------------------------------------
# Search index
# ---------------------------------------------------------------------------

def _heading(fragment: str) -> str:
    m = re.search(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", fragment, re.S)
    return strip_tags(m.group(1)) if m else ""


def build_search_index(pages: list[str] | None = None) -> int:
    """Write search-index.js from the built pages. Returns the section count."""
    if pages is None:
        pages = sorted(p.name for p in HERE.glob("*.html")
                       if not p.name.startswith("_") and ".bak" not in p.name)
    entries = []
    count = 0
    for name in pages:
        path = HERE / name
        if not path.exists():
            continue
        html = path.read_bytes().decode("utf-8")
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        title = htmllib.unescape(m.group(1)).strip() if m else Path(name).stem
        secs = []
        for start, end, open_tag in iter_sections(html):
            fragment = html[start:end]
            text = strip_tags(fragment)
            if not text:
                continue
            sid = section_id(open_tag)
            secs.append({"id": "" if sid == "(no id)" else sid,
                         "h": _heading(fragment), "x": text})
        if secs:
            entries.append({"p": name, "t": title, "s": secs})
            count += len(secs)
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    # "</" would end the <script> early if this were ever inlined; harmless to
    # escape either way.
    payload = payload.replace("</", "<\\/")
    text = "window.MM_SEARCH=" + payload + ";\n"
    if not SEARCH_INDEX.exists() or SEARCH_INDEX.read_bytes().decode("utf-8") != text:
        SEARCH_INDEX.write_bytes(text.encode("utf-8"))
    return count


if __name__ == "__main__":
    print(f"search index: {build_search_index()} sections")
