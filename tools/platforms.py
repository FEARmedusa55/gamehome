#!/usr/bin/env python3
"""
Platform / category management.

A "category" is one of the platform pages in the left nav (Windows, Linux,
MacOS, Xbox 360, ...). Adding or removing one is a structural change, not a
content edit: the nav is an identical <ul> of <li> items repeated in every page,
and it lives in the boilerplate *before* the first <section>.

So this tool keeps three things in sync, which is the part that is easy to get
wrong by hand:

  1. the nav <li> in every page file
  2. the nav <li> in every staged content/<page>/_head.html
     (otherwise the next `content.py build` silently reverts the nav)
  3. the existence of the platform's page file, and aria-current on its own item

    python tools/platforms.py list
    python tools/platforms.py add sega-switch "Sega Switch"
    python tools/platforms.py remove sega-switch
    python tools/platforms.py rename sega-switch "Sega"
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CONTENT = HERE / "content"
TRASH = HERE / "_trash"

# One nav entry. Level-1 <li>s are matched with balanced tags, not a lazy
# regex: once a parent contains a nested <ul> of children, a non-greedy
# `.*?</li>` stops at the FIRST child's </li> and truncates the parent.
TAG_RE = re.compile(r"<li\b|</li>")
# Match any <li> tag, then check it is a level-1 nav item. Matching the raw
# string `<li jsname="ibnC6b"` instead would miss a parent whose tag we have
# already rewritten to `<li class="mmHasSub" jsname="ibnC6b" ...>` — and would
# wrongly count its level-2 children as level-1 items.
LI_TAG_RE = re.compile(r"<li\b[^>]*>")
URL_RE = re.compile(r'data-url="([^"]*)"')
HREF_RE = re.compile(r'href="([^"]*)"')
LABEL_RE = re.compile(r'<a\b[^>]*>(.*?)</a>', re.S)

# Google Sites prefixes nav labels with two U+2004 spaces and a dash.
LABEL_PREFIX = "\u2004\u2004- "
# Children sit one indent deeper.
LABEL_PREFIX_CHILD = "\u2004\u2004\u2004\u2004- "

SUB_UL = '<ul class="mmSub">'
SUB_STYLE_ID = "mm-subnav-css"
SUB_JS_ID = "mm-subnav-js"
SITE_CSS_ID = "mm-site-css"

# Google Sites hides the top bar — the strip holding the site title and the
# overlay that gives it its colour — above roughly 1300px, preferring a
# permanent sidebar there. Forcing it back on means the bar and title are
# present at every width.
#
# The title itself is then hidden again: the bar is wanted as a plain strip,
# without the site name in it. (The 250px pad that used to push the title past
# the permanent sidebar is gone with it — there is nothing left to offset.)
#
# Note it does NOT bring back the hamburger: above ~600px Google's script
# never creates that button at all (it only builds it for the drawer layout),
# so there is nothing in the DOM to reveal.
SITE_CSS = """<style id="mm-site-css">
.VLoccc{display:block !important; top:0 !important}
.VLoccc a.GAuSPc{display:none !important}
</style>"""

# Remembering which groups are open needs script: the fold state lives in a
# checkbox that is re-created from the page's own markup on every load, so
# without this, moving between pages collapses everything again.
SUB_JS = """<script id="mm-subnav-js">
(function () {
  var KEY = 'mmSubOpen', open = {};
  try { open = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { open = {}; }
  var boxes = document.querySelectorAll('input.mmSubToggle');
  Array.prototype.forEach.call(boxes, function (cb) {
    if (open[cb.id]) { cb.checked = true; }
    cb.addEventListener('change', function () {
      open[cb.id] = cb.checked;
      try { localStorage.setItem(KEY, JSON.stringify(open)); } catch (e) {}
    });
  });
})();
</script>"""

# Collapsible sub-navigation, CSS only.
#
# A static mirror never runs Google Sites' nav script, so nothing would ever
# handle a click on an expand control (verified: aria-expanded stays false).
# State therefore lives in a hidden checkbox, the caret is a <label> for it,
# and the child list is shown with a sibling selector. No JavaScript involved.
SUB_CSS = """<style id="mm-subnav-css">
li.mmHasSub>div{position:relative}
li.mmHasSub input.mmSubToggle{display:none}
ul.mmSub{display:none;margin:0;padding:0;list-style:none}
/* `~` is the rule that fires: the child <ul> sits inside the <li>, as a
   sibling of the hidden checkbox. The :has() form is belt-and-braces for the
   case where something re-parents the <ul> to sit alongside the <li>. */
li.mmHasSub input.mmSubToggle:checked~ul.mmSub{display:block}
li.mmHasSub:has(input.mmSubToggle:checked)+ul.mmSub{display:block}
label.mmSubCaret{position:absolute;right:8px;top:50%;transform:translateY(-50%);
width:20px;height:20px;cursor:pointer;z-index:5;display:flex;align-items:center;
justify-content:center;font-size:10px;line-height:1;opacity:.7;
-webkit-user-select:none;user-select:none}
label.mmSubCaret:before{content:"\\25B8"}
li.mmHasSub input.mmSubToggle:checked~div label.mmSubCaret:before{content:"\\25BE"}
label.mmSubCaret:hover{opacity:1}
</style>"""


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def page_files() -> list[Path]:
    return sorted(
        p for p in HERE.glob("*.html")
        if not p.name.startswith("_") and ".bak" not in p.name
    )


def head_files() -> list[Path]:
    """Staged boilerplate copies — they also contain the nav."""
    return sorted(CONTENT.glob("*/_head.html"))


def tail_files() -> list[Path]:
    """Staged page tails — these hold </body>, so the fold script goes here."""
    return sorted(CONTENT.glob("*/_tail.html"))


def unescape(text: str) -> str:
    return (text.replace("&#8196;", "\u2004")
                .replace("&nbsp;", " ")
                .replace("&amp;", "&"))


def nav_item_spans(html: str):
    """
    Yield (start, end) for each level-1 nav <li>, counting nested <li>s.

    Nested children make a lazy `.*?</li>` unsafe, so walk the tags and match
    the balanced close instead.
    """
    for match in LI_TAG_RE.finditer(html):
        tag = match.group(0)
        if 'jsname="ibnC6b"' not in tag or 'data-nav-level="1"' not in tag:
            continue
        depth = 1
        for t in TAG_RE.finditer(html, match.end()):
            if t.group(0) == "</li>":
                depth -= 1
                if depth == 0:
                    yield match.start(), t.end()
                    break
            else:
                depth += 1


def items(html: str):
    """
    Yield (start, end, slug, label, own_markup, tail) for each level-1 item.

    `own_markup` holds only the item's own <div>/<a> (stopping before any
    nested child list) so it is safe to rewrite; `tail` is everything after it
    up to and including the item's </li>. Rewriting a caller must put
    `own_markup` and `tail` back together, or it silently deletes the
    item's children.
    """
    for start, end in nav_item_spans(html):
        raw = html[start:end]
        own = raw.partition(SUB_UL)[0]
        # `own` must NOT include the closing </li>. With no child list the
        # partition returns the whole item INCLUDING </li>, and appending a
        # child <ul> after that puts the children OUTSIDE the <li> and
        # duplicates the close tag — which is exactly how this corrupted the
        # nav on the first attempt.
        body, sep, _ = own.rpartition("</li>")
        if sep:
            own = body
        tail = raw[len(own):]
        url = URL_RE.search(own)
        label = LABEL_RE.search(own)
        text = (unescape(re.sub(r"<[^>]+>", "", label.group(1))).strip()
                if label else "")
        text = text.lstrip("\u2004 ").lstrip("-").strip()
        yield start, end, (url.group(1) if url else ""), text, own, tail


def cmd_list(args) -> int:
    pages = page_files()
    if not pages:
        print("No page files found.")
        return 1

    html = read(pages[0])
    print(f"Nav categories (from {pages[0].name}):\n")
    for index, (_, _, slug, label, _, _) in enumerate(items(html)):
        marker = "  " if slug.startswith("/") else "? "
        print(f"  [{index:2}] {slug:26} {label}")

    print(f"\n{len(pages)} pages carry this nav, plus "
          f"{len(head_files())} staged _head.html copies.")
    return 0


def _apply_to_all(fn, what: str) -> int:
    """Run fn(html) -> html on every page and staged head file."""
    changed = 0
    targets = page_files() + head_files()
    for target in targets:
        html = read(target)
        new = fn(html)
        if new != html:
            write(target, new)
            changed += 1
    print(f"  {what}: updated {changed}/{len(targets)} files")
    return changed


def _insert_li(html: str, new_li: str, after_slug: str) -> str:
    for start, end, slug, _, _, _ in items(html):
        if slug == after_slug:
            return html[:end] + new_li + html[end:]
    return html


def _remove_li(html: str, slug: str) -> str:
    for start, end, item_slug, _, _, _ in items(html):
        if item_slug == slug:
            return html[:start] + html[end:]
    return html


def _set_current(html: str, slug: str) -> str:
    """
    Move the 'this is the current page' marker onto the given nav item.

    The site marks the current item with the theme_navigation_SelectedItem
    class (lhZOrc) appended to the item's <div class>, and does NOT use
    aria-current — none of the 32 mirrored pages carry aria-current, so match
    what the site actually does rather than inventing attributes.
    """
    out = []
    last = 0
    marked = False
    for start, end, item_slug, _, own, tail in items(html):
        cleaned = re.sub(r"\s*\blhZOrc\b", "", own)
        # Mark only the FIRST match: if a slug somehow appears twice, marking
        # every copy highlighted two nav rows at once.
        if item_slug == slug and not marked:
            tag = re.search(r'<div class="[^"]*"', cleaned)
            if tag:
                cleaned = (cleaned[:tag.end() - 1] + " lhZOrc"
                           + cleaned[tag.end() - 1:])
                marked = True
        out.append(html[last:start])
        out.append(cleaned + tail)      # tail carries any nested children
        last = end
    out.append(html[last:])
    return "".join(out)


NOPAGE_ATTR = 'data-mmnopage="1"'


def child_items(tail: str) -> list:
    """The level-2 rows inside a parent's child list."""
    out = []
    for m in re.finditer(r'<li[^>]*data-nav-level="2"[^>]*>.*?</li>', tail, re.S):
        raw = m.group(0)
        url = URL_RE.search(raw)
        lab = LABEL_RE.search(raw)
        href = HREF_RE.search(raw)
        text = unescape(re.sub(r"<[^>]+>", "", lab.group(1))).strip() if lab else ""
        out.append({
            "slug": url.group(1) if url else "",
            "label": text.lstrip("\u2004 ").lstrip("-").replace("\u200e", "").strip(),
            "href": href.group(1) if href else "",
        })
    return out


def nav_tree(source: str = "") -> list:
    """
    The nav as nested rows, for the editor sidebar.

    A nav-only category (one that groups children but has no page of its own)
    has no href and carries NOPAGE_ATTR, so the sidebar can show it as a
    non-clickable header instead of a page you can open.
    """
    html = read(HERE / (source or page_files()[0].name))
    rows = []
    for _, _, slug, label, own, tail in items(html):
        href = HREF_RE.search(own)
        rows.append({
            "slug": slug,
            "label": label,
            "href": href.group(1) if href else "",
            "nopage": NOPAGE_ATTR in own or not slug,
            "children": child_items(tail),
        })
    return rows


def cmd_add(args) -> int:
    slug_path = "/" + args.slug.lstrip("/")
    filename = f"{args.slug.lstrip('/')}.html"
    dest = HERE / filename

    if dest.exists():
        print(f"! {filename} already exists"
              + (" as a page — use `link` to put it in the nav" if args.no_page
                 else ""))
        return 1

    pages = page_files()
    if not pages:
        print("! no pages to read a nav template from")
        return 1

    # Take the nav <li> template from an existing platform item.
    template = None
    donor_name = None
    for cand in pages:
        html = read(cand)
        for _, _, s, _, own, _ in items(html):
            if s == (args.after or "/windows"):
                template, donor_name = own, cand.name
                break
        if template:
            break

    if template is None:
        print(f"! could not find a nav item to use as a template")
        return 1

    label = args.label if args.label else args.slug.replace("-", " ").title()
    new_li = fresh_li(template, slug_path, filename, label,
                      nopage=args.no_page)

    if args.no_page:
        # A grouping entry with nothing behind it: keep the label and the
        # data-url (tools identify items by it) but drop the link target, so
        # the row is not clickable. The marker lets tools tell it apart from a
        # category that has a real page.
        new_li = re.sub(r"\shref=\"[^\"]*\"", "", new_li, count=1)

    print(f"Adding {'group' if args.no_page else 'category'} {label!r} "
          f"-> {filename if not args.no_page else '(no page)'} "
          f"(nav template: {donor_name})")
    _apply_to_all(lambda h: _insert_li(h, new_li, args.after or "/windows"),
                  "nav insert")

    if args.no_page:
        print("\nDone — nav entry only, no page was created.")
        print("  Name it as a parent to nest pages under it, e.g.")
        print(f"  rename others-{args.slug}-<name>.html and run "
              f"`platforms.py subs --apply`")
        return 0

    # Create the page from a donor so it has a working scaffold.
    source = HERE / (args.from_page or "apple-tv.html")
    if not source.exists():
        print(f"! donor page {source.name} not found")
        return 1
    page_html = _set_current(read(source), slug_path)
    write(dest, page_html)
    print(f"  created {filename} from {source.name}")

    print(f"\nDone. Next:")
    print(f"  python tools/content.py extract {filename}   # make it editable")
    print(f"  python tools/content.py build {filename}")
    return 0


def fresh_li(donor_own: str, slug_path: str, filename: str, label: str,
             child: bool = False, nopage: bool = False) -> str:
    """
    Build a nav entry cloned from a donor item's OWN markup.

    `donor_own` must stop before any child list. Cloning a parent's tail would
    copy its whole <ul> of sub-platforms into the new entry.

    A nav-only group has no href, so the href is ADDED when the donor has none
    — otherwise a page created under a group gets no link and never shows up.
    data-mmnopage is cleared for the same reason: a child of a group is still
    a real page.
    """
    out = re.sub(r'<input[^>]*mmSubToggle[^>]*>', "", donor_own)
    out = re.sub(r'<label[^>]*mmSubCaret[^>]*></label>', "", out)
    out = re.sub(r'\s*class="mmHasSub"', "", out)
    out = re.sub(r"\s*\blhZOrc\b", "", out)
    out = re.sub(r'\s*aria-current="[^"]*"', "", out)
    out = re.sub(r'\s*aria-selected="[^"]*"', "", out)
    out = re.sub(r"\s*" + re.escape(NOPAGE_ATTR), "", out)

    if HREF_RE.search(out):
        out = HREF_RE.sub(lambda m: f'href="{filename}"', out, count=1)
    else:
        out = out.replace("<a ", f'<a href="{filename}" ', 1)
    out = URL_RE.sub(lambda m: f'data-url="{slug_path}"', out, count=1)

    if child:
        out = out.replace('data-nav-level="1"', 'data-nav-level="2"')
        out = out.replace('data-level="1"', 'data-level="2"')
    if nopage:
        out = out.replace("<li ", f"<li {NOPAGE_ATTR} ", 1)

    lab = LABEL_RE.search(out)
    if lab:
        prefix = LABEL_PREFIX_CHILD if child else LABEL_PREFIX
        out = out[:lab.start(1)] + prefix + label + out[lab.end(1):]
    return out + "</li>"


def _donor_item(after_slug: str):
    """The donor item's OWN markup (never its children) and the file it came from."""
    for cand in page_files():
        html = read(cand)
        for _, _, slug, _, own, _ in items(html):
            if slug == after_slug:
                return own, cand.name
    return None, None


def cmd_link(args) -> int:
    """
    Put an EXISTING page into the nav.

    `add` creates a platform (new page + nav entry). `link` only adds the nav
    entry, for pages that already exist but were never in the nav — on this
    site FAQ and Changelog were only ever reachable from links inside other
    pages' content, so they never appeared in the sidebar.
    """
    slug_path = "/" + args.slug.lstrip("/")
    filename = f"{args.slug.lstrip('/')}.html"

    if not (HERE / filename).exists():
        print(f"! {filename} does not exist — use `add` to create a new platform")
        return 1

    for _, _, s, _, _, _ in items(read(page_files()[0])):
        if s == slug_path:
            print(f"! {slug_path} is already in the nav")
            print("  run `list` to see the categories")
            return 1

    template, donor = _donor_item(args.after)
    if template is None:
        print(f"! no nav item with data-url={args.after!r} to copy as a template")
        print("  run `list` to see the categories")
        return 1

    label = args.label if args.label else args.slug.replace("-", " ").title()
    new_li = fresh_li(template, slug_path, filename, label)

    print(f"Linking {label!r} -> {filename} after {args.after} "
          f"(nav template: {donor})")
    _apply_to_all(lambda h: _insert_li(h, new_li, args.after), "nav insert")

    # The page must also mark its own new nav item, or it renders with no
    # highlight while every other page does. Both the page and its staged
    # _head.html hold the nav, so both change — otherwise content.py build
    # would silently revert this.
    page_path = HERE / filename
    staged_head = CONTENT / args.slug.lstrip("/") / "_head.html"
    for target in (page_path, staged_head):
        if not target.exists():
            continue
        html = read(target)
        updated = _set_current(html, slug_path)
        if updated != html:
            write(target, updated)
            print(f"  marked current in {target.relative_to(HERE)}")

    print("\nDone. `python tools/platforms.py list` to confirm.")
    return 0


def current_slug(html: str) -> str:
    """The nav slug this file currently highlights (the lhZOrc item)."""
    for _, _, slug, _, own, _ in items(html):
        if "lhZOrc" in own:
            return slug
    return ""


def label_from_slug(slug: str, parent: str) -> str:
    """others-android-low-storage-players -> 'Low Storage Players'."""
    stem = slug.strip("/")
    prefix = f"others-{parent.strip('/')}-"
    name = stem[len(prefix):] if stem.startswith(prefix) else stem
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", name) if w)


def _nest_parent(own: str, parent_slug: str) -> str:
    """
    Give a nav item the hidden checkbox + caret it needs to fold children.

    The <li> tag is matched loosely on purpose. A nav-only group's tag is
    `<li data-mmnopage="1" jsname="ibnC6b" …>` — different attribute order —
    so matching the literal `<li jsname="ibnC6b"` skipped it, leaving the
    group with a caret pointing at a checkbox that was never inserted and
    therefore unable to unfold.
    """
    if "mmSubToggle" in own:
        return own
    key = "mmsub-" + re.sub(r"[^a-z0-9]+", "-", parent_slug.strip("/")).strip("-")
    toggle = f'<input type="checkbox" class="mmSubToggle" id="{key}">'
    caret = f'<label class="mmSubCaret" for="{key}" title="Show sub-pages"></label>'

    def tag_add_class(m):
        tag = m.group(0)
        if 'class="' in tag:
            return re.sub(r'class="([^"]*)"', r'class="\1 mmHasSub"', tag, count=1)
        return tag[:-1] + ' class="mmHasSub">'

    own = re.sub(r"<li\b[^>]*>", tag_add_class, own, count=1)
    # the checkbox goes first, so `~` can reach the child <ul>
    own = re.sub(r"<li\b[^>]*>", lambda m: m.group(0) + toggle, own, count=1)
    # the caret sits after the link, inside the item's row
    return own.replace("</a>", "</a>" + caret, 1)


def _child_li(template: str, child_slug: str, label: str, active: str) -> str:
    """
    Clone a nav item's own markup as a level-2 child entry.

    Delegates to fresh_li so a child of a nav-only group still gets an href —
    the group has none, and a child with no link never appears as a page.
    """
    new = fresh_li(template, child_slug, f'{child_slug.strip("/")}.html',
                   label, child=True)
    if child_slug == active:
        tag = re.search(r'<div class="[^"]*"', new)
        if tag:
            new = new[:tag.end() - 1] + " lhZOrc" + new[tag.end() - 1:]
    return new


def _apply_subs(html: str, groups: dict, active: str) -> str:
    """
    Nest child items under their parents in one pass.

    groups: {parent_slug: [(child_slug, label), ...]}
    active: the slug to mark lhZOrc on this file (preserves the existing
            highlight, since deriving it from the filename would move it on
            pages like index.html, which highlights /start)
    """
    out, last = [], 0
    for start, end, slug, _, own, tail in items(html):
        clean = re.sub(r"\s*\blhZOrc\b", "", own)
        if slug in groups:
            # Keep the order already in the file, so a manual move sticks;
            # anything new is appended. (Sorted order would undo every move.)
            existing = [c["slug"] for c in child_items(tail)]
            wanted = groups[slug]
            ordered = ([k for s in existing for k in wanted if k[0] == s]
                       + [k for k in wanted if k[0] not in existing])
            kids = "".join(_child_li(clean, cslug, clabel, active)
                           for cslug, clabel in ordered)
            new = _nest_parent(clean, slug) + SUB_UL + kids + "</ul></li>"
        else:
            new = clean + "</li>"
        if slug == active:
            tag = re.search(r'<div class="[^"]*"', new)
            if tag:
                new = new[:tag.end() - 1] + " lhZOrc" + new[tag.end() - 1:]
        out.append(html[last:start])
        out.append(new)
        last = end
    out.append(html[last:])
    return "".join(out)


def _ensure_substyle(html: str) -> str:
    """
    Add the sub-nav CSS once, before </head>.

    An existing block is REPLACED rather than skipped, so edits to SUB_CSS
    reach pages that already carry an older copy.
    """
    if SUB_STYLE_ID in html:
        # lambda: a plain replacement string would interpret the CSS's \25B8
        # caret escapes as regex backreferences
        return re.sub(r'<style id="mm-subnav-css">.*?</style>',
                      lambda m: SUB_CSS, html, count=1, flags=re.S)
    at = html.rfind("</head>")
    return html if at == -1 else html[:at] + SUB_CSS + html[at:]


def _ensure_sitestyle(html: str) -> str:
    """Add/replace the force-visible-top-bar rule, before </head>."""
    if SITE_CSS_ID in html:
        return re.sub(r'<style id="mm-site-css">.*?</style>',
                      lambda m: SITE_CSS, html, count=1, flags=re.S)
    at = html.rfind("</head>")
    return html if at == -1 else html[:at] + SITE_CSS + html[at:]


def _ensure_subjs(html: str) -> str:
    """Add/replace the fold-memory script, just before </body>."""
    if SUB_JS_ID in html:
        return re.sub(r'<script id="mm-subnav-js">.*?</script>',
                      lambda m: SUB_JS, html, count=1, flags=re.S)
    at = html.rfind("</body>")
    if at == -1:
        return html
    return html[:at] + SUB_JS + html[at:]


def derive_groups() -> dict:
    """
    Find sub-platforms from the naming convention, not a config file.

    Sub-pages are already named others-<parent>-<name>, so the parent is the
    nav category matching the leading segment after 'others-'. Longest match
    wins, so a page under a category whose own name contains dashes still
    resolves correctly.
    """
    nav_slugs = [s for _, _, s, _, _, _ in items(read(page_files()[0]))]
    groups: dict = {}
    for child in page_files():
        slug = "/" + child.stem
        if slug in nav_slugs or not child.stem.startswith("others-"):
            continue
        rest = child.stem[len("others-"):]
        parent = None
        for cand in nav_slugs:
            name = cand.strip("/")
            if rest == name or rest.startswith(name + "-"):
                if parent is None or len(name) > len(parent.strip("/")):
                    parent = cand
        if parent:
            groups.setdefault(parent, []).append(
                (slug, label_from_slug(slug, parent)))
    for kids in groups.values():
        kids.sort()
    return groups


def cmd_subs(args) -> int:
    groups = derive_groups()
    if not groups:
        print("No sub-platforms found.")
        print("Sub-pages must be named  others-<parent>-<name>.html")
        return 0

    total = sum(len(v) for v in groups.values())
    print(f"{total} sub-platform(s) under {len(groups)} parent(s):\n")
    for parent in sorted(groups):
        print(f"  {parent}")
        for slug, label in groups[parent]:
            print(f"      {label:26} {slug}")

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to nest them.")
        return 0

    child_slugs = {s for kids in groups.values() for s, _ in kids}
    print("\nApplying to every page and staged head...")
    changed = 0
    targets = page_files() + head_files()
    for target in targets:
        html = read(target)
        # keep whatever highlight the file already had, except for a sub-page
        # that had no nav item before and should now highlight itself
        own_slug = "/" + (target.parent.name if target.name == "_head.html"
                          else target.stem)
        active = own_slug if own_slug in child_slugs else current_slug(html)
        new = _apply_subs(_ensure_sitestyle(_ensure_substyle(html)), groups, active)
        if new != html:
            write(target, new)
            changed += 1
    print(f"  updated {changed}/{len(targets)} files")

    # The fold-memory script belongs where </body> is: the page itself and its
    # staged _tail.html. Putting it in the staged tail is what makes it survive
    # a `content.py build`.
    js_changed = 0
    js_targets = page_files() + tail_files()
    for target in js_targets:
        html = read(target)
        new = _ensure_subjs(html)
        if new != html:
            write(target, new)
            js_changed += 1
    print(f"  fold memory: updated {js_changed}/{len(js_targets)} files")
    print("\nDone. Run `python tools/content.py verify` to confirm the build.")
    return 0


def _mark_current_any(html: str, slug_path: str) -> str:
    """
    Clear the current-page highlight everywhere, then set it on one item.

    `_set_current` only walks level-1 items, so a sub-page's own level-2 row
    would never be marked. This handles either level.
    """
    nav_start = html.find("<nav ")
    nav_end = html.find("</nav>", nav_start)
    if nav_start == -1 or nav_end == -1:
        return html
    nav = re.sub(r"\s*\blhZOrc\b", "", html[nav_start:nav_end])

    at = nav.find(f'data-url="{slug_path}"')
    if at != -1:
        li = nav.rfind("<li", 0, at)
        div = re.search(r'<div class="[^"]*"', nav[li:li + 2000]) if li != -1 else None
        if div:
            s, e = li + div.start(), li + div.end()
            tag = nav[s:e]
            tag = tag[:tag.rfind('"')] + " lhZOrc" + tag[tag.rfind('"'):]
            nav = nav[:s] + tag + nav[e:]

    return html[:nav_start] + nav + html[nav_end:]


def _insert_child(html: str, parent_slug: str, child_li: str) -> str:
    """Insert a level-2 item into its parent's child list, creating that list."""
    for start, end in nav_item_spans(html):
        raw = html[start:end]
        own = raw.partition(SUB_UL)[0]
        # `own` must NOT carry the closing </li>. With no child list the
        # partition yields the whole item including it, so appending a <ul>
        # would put the children OUTSIDE the <li> and leave a stray </li> —
        # which is why new sub-pages never showed up under their group.
        body, sep, _ = own.rpartition("</li>")
        if sep:
            own = body
        slug = URL_RE.search(own)
        if not slug or slug.group(1) != parent_slug:
            continue
        if SUB_UL in raw:
            at = raw.index("</ul>", raw.index(SUB_UL))
            new_raw = raw[:at] + child_li + raw[at:]
        else:
            new_raw = _nest_parent(own.rstrip(), parent_slug) + SUB_UL + \
                child_li + "</ul></li>"
        return html[:start] + new_raw + html[end:]
    return html


def _remove_child(html: str, slug_path: str) -> str:
    """Remove a level-2 child entry. (_remove_li only walks level-1 items.)"""
    for m in re.finditer(r'<li\b[^>]*data-nav-level="2"[^>]*>', html):
        end = html.find("</li>", m.end())
        if end == -1:
            continue
        if f'data-url="{slug_path}"' in html[m.start():end + 5]:
            return html[:m.start()] + html[end + 5:]
    return html


def _tidy_parents(html: str) -> str:
    """
    Drop child lists left empty, and the fold scaffolding of any parent that
    has no children left — a caret that opens nothing is worse than no caret.
    """
    html = re.sub(re.escape(SUB_UL) + r"\s*</ul>", "", html)
    out, last = [], 0
    for start, end in nav_item_spans(html):
        raw = html[start:end]
        own = raw.partition(SUB_UL)[0]
        if SUB_UL not in raw and "mmHasSub" in own:
            own = re.sub(r'<input[^>]*mmSubToggle[^>]*>', "", own)
            own = re.sub(r'<label[^>]*mmSubCaret[^>]*></label>', "", own)
            own = re.sub(r'\s*class="mmHasSub"', "", own)
            raw = own + "</li>" if not own.rstrip().endswith("</li>") else own
        out.append(html[last:start])
        out.append(raw)
        last = end
    out.append(html[last:])
    return "".join(out)


def remove_nav_item(html: str, slug_path: str) -> str:
    """Remove a nav entry at ANY level, then tidy any emptied group."""
    out = _remove_li(html, slug_path)
    if out == html:
        out = _remove_child(html, slug_path)
    if out == html:
        return html
    return _tidy_parents(out)


def _move_child(html: str, parent_slug: str, child_slug: str,
                direction: str) -> str:
    """
    Swap a level-2 child with its neighbour inside its parent's list.

    _apply_subs preserves whatever order it finds, so a move made here
    survives later re-derivations.
    """
    for start, end in nav_item_spans(html):
        raw = html[start:end]
        own = raw.partition(SUB_UL)[0]
        slug = URL_RE.search(own)
        if not slug or slug.group(1) != parent_slug:
            continue
        block = re.search(re.escape(SUB_UL) + r"(.*?)</ul>", raw, re.S)
        if not block:
            return html
        inner = block.group(1)
        found = list(re.finditer(
            r'<li\b[^>]*data-nav-level="2"[^>]*>.*?</li>', inner, re.S))
        blocks = [f.group(0) for f in found]
        idx = next((i for i, b in enumerate(blocks)
                    if f'data-url="{child_slug}"' in b), None)
        if idx is None:
            return html
        j = idx - 1 if direction == "up" else idx + 1
        if j < 0 or j >= len(blocks):
            return html
        blocks[idx], blocks[j] = blocks[j], blocks[idx]
        new_raw = raw[:block.start(1)] + "".join(blocks) + raw[block.end(1):]
        return html[:start] + new_raw + html[end:]
    return html


def _relabel(html: str, slug_path: str, label: str) -> str:
    """
    Set a nav entry's label, at any level.

    Matches the <a ... data-url="slug" ...>…</a> directly, because level-2
    children are not yielded by items() — rewriting by index would miss them.
    """
    pattern = re.compile(r'(<a\b[^>]*\bdata-url="' + re.escape(slug_path)
                         + r'"[^>]*>).*?(</a>)', re.S)

    def swap(m):
        prefix = (LABEL_PREFIX_CHILD if 'data-level="2"' in m.group(1)
                  else LABEL_PREFIX)
        return m.group(1) + prefix + label + m.group(2)

    return pattern.sub(swap, html, count=1)


def cmd_unsub(args) -> int:
    """Remove all nesting and the sub-nav CSS, restoring a flat nav."""
    print("Removing sub-navigation from every page and staged head...")
    changed = 0
    targets = page_files() + head_files() + tail_files()
    for target in targets:
        html = read(target)
        new = html
        # drop each child list, then the parent scaffolding
        new = re.sub(re.escape(SUB_UL) + r".*?</ul>", "", new, flags=re.S)
        new = re.sub(r'<input[^>]*mmSubToggle[^>]*>', "", new)
        new = re.sub(r'<label[^>]*mmSubCaret[^>]*></label>', "", new)
        new = new.replace('<li class="mmHasSub" ', "<li ").replace(
            ' class="mmHasSub"', "")
        new = re.sub(r'<style id="mm-subnav-css">.*?</style>', "", new,
                     flags=re.S)
        new = re.sub(r'<script id="mm-subnav-js">.*?</script>', "", new,
                     flags=re.S)
        if new != html:
            write(target, new)
            changed += 1
    print(f"  updated {changed}/{len(targets)} files")
    return 0


def cmd_remove(args) -> int:
    slug_path = "/" + args.slug.lstrip("/")
    filename = f"{args.slug.lstrip('/')}.html"
    dest = HERE / filename

    found = False
    for page in page_files():
        t = read(page)
        # level-1 items come from items(); a level-2 child is only in the raw
        # markup, so check the whole file too
        if (any(s == slug_path for _, _, s, _, _, _ in items(t))
                or f'data-url="{slug_path}"' in t):
            found = True
            break

    if not found:
        print(f"! no nav item with data-url={slug_path!r}")
        print("  run `list` to see the categories")
        return 1

    print(f"Removing category {args.slug!r}")
    _apply_to_all(lambda h: remove_nav_item(h, slug_path), "nav removal")

    TRASH.mkdir(exist_ok=True)
    if dest.exists():
        shutil.move(str(dest), str(TRASH / f"removed-{filename}"))
        print(f"  page moved to _trash/removed-{filename}")

    staged = CONTENT / args.slug.lstrip("/")
    if staged.exists():
        shutil.move(str(staged), str(TRASH / f"removed-content-{args.slug.lstrip('/')}"))
        print(f"  staged content moved to _trash/removed-content-{args.slug.lstrip('/')}")

    return 0


def cmd_rename(args) -> int:
    slug_path = "/" + args.slug.lstrip("/")
    found = False
    for _, _, s, old_label, _, _ in items(read(page_files()[0])):
        if s == slug_path:
            found = True
            break

    if not found:
        print(f"! no nav item with data-url={slug_path!r}")
        return 1

    def relabel(html: str) -> str:
        for start, end, s, _, own, tail in items(html):
            if s != slug_path:
                continue
            lab = LABEL_RE.search(own)
            new = (own[:lab.start(1)] + LABEL_PREFIX + args.label + own[lab.end(1):])
            return html[:start] + new + tail + html[end:]
        return html

    print(f"Renaming {args.slug!r} -> {args.label!r}")
    _apply_to_all(relabel, "nav relabel")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage nav platform categories.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list"); p.set_defaults(func=cmd_list)

    p = sub.add_parser("add")
    p.add_argument("slug")
    p.add_argument("label", nargs="?")
    p.add_argument("--after", default="/windows",
                   help="insert after this nav item (default: /windows)")
    p.add_argument("--from-page", default="apple-tv.html",
                   help="donor page to copy as the new page's scaffold")
    p.add_argument("--no-page", action="store_true",
                   help="add ONLY a nav entry, with no page behind it "
                        "(useful as a group heading)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove")
    p.add_argument("slug")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("link",
                       help="add an EXISTING page to the nav (no new page)")
    p.add_argument("slug")
    p.add_argument("label", nargs="?")
    p.add_argument("--after", default="/windows",
                   help="insert after this nav item (default: /windows)")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("subs",
                       help="nest others-<parent>-* pages under their parent")
    p.add_argument("--apply", action="store_true",
                   help="actually write (default is a dry run)")
    p.set_defaults(func=cmd_subs)

    p = sub.add_parser("unsub", help="remove all sub-navigation (flat nav)")
    p.set_defaults(func=cmd_unsub)

    p = sub.add_parser("rename")
    p.add_argument("slug")
    p.add_argument("label")
    p.set_defaults(func=cmd_rename)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
