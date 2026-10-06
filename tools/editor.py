#!/usr/bin/env python3
"""
Local editor for the site.

    python tools/editor.py

Opens a page in your browser where you can edit text, add/remove/reorder
sections, and add/remove platform categories — without touching HTML, and
without changing how the site looks.

How it stays faithful: the design lives in the Google Sites CSS bundles and the
per-page boilerplate (_head.html / _tail.html). This editor never writes to
those. It only rewrites the numbered content files, so a text edit can change
the words and nothing else.

Nothing is installed; this is the Python standard library plus the other tools
in this folder.
"""

import base64
import hashlib
import json
import mimetypes
import random
import re
import shutil
import string
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content as content_mod     # noqa: E402
import platforms as platforms_mod  # noqa: E402
import sections as sections_mod    # noqa: E402
import buttons as buttons_mod      # noqa: E402
import preview_edit               # noqa: E402
import textedit as textedit_mod    # noqa: E402

HERE = Path(__file__).resolve().parent.parent
CONTENT = HERE / "content"
TRASH = HERE / "_trash"
UI = Path(__file__).resolve().parent / "editor_ui.html"
PORT = 8765


def arg_value(flag: str, default):
    """Read --flag value or --flag=value from argv."""
    argv = sys.argv[1:]
    for i, item in enumerate(argv):
        if item == flag and i + 1 < len(argv):
            return argv[i + 1]
        if item.startswith(flag + "="):
            return item.split("=", 1)[1]
    return default

PAGE_SKIP = ("_", "demo-")

# Nicer titles where title-casing mangles the name, or the page isn't in the nav.
TITLE_FIX = {
    "index": "Start", "faq": "FAQ", "changelog": "Changelog",
    "macos": "MacOS", "ios": "iOS", "wii-u": "Wii U", "apple-tv": "Apple TV",
    "xbox-oneseries-xs": "Xbox One / Series X|S",
    "nintendo-switch2": "Nintendo Switch / Switch 2",
    "playstation-45": "PlayStation 4 / 5",
    "youtubenetflix": "YouTube / Netflix",
    "mods-and-extras": "Mods and Extras",
}


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def site_pages() -> list[str]:
    return sorted(
        p.name for p in HERE.glob("*.html")
        if not p.name.startswith(PAGE_SKIP) and ".bak" not in p.name
    )


def nav_labels() -> dict:
    """{page filename: nav label} — the nicest source of a page's name."""
    pages = site_pages()
    if not pages:
        return {}
    labels = {}
    html = read(HERE / pages[0])
    for _, _, slug, label, _, tail in platforms_mod.items(html):
        if not slug.startswith("/"):
            continue
        href = slug.lstrip("/")
        if href == "start":
            href = "index"
        clean = label.lstrip("\u2004 ").lstrip("-").replace("\u200e", "").strip()
        labels[href + ".html"] = clean
        # Sub-pages too: items() only yields level-1 rows, so without this a
        # child page's title fell back to its filename ("Others Android …").
        for kid in platforms_mod.child_items(tail):
            kid_slug = kid["slug"].lstrip("/")
            if kid_slug:
                labels[kid_slug + ".html"] = kid["label"]
    return labels


def title_for(page_file: str, labels: dict | None = None) -> str:
    labels = labels if labels is not None else nav_labels()
    if page_file in labels:
        return labels[page_file]
    stem = Path(page_file).stem
    return TITLE_FIX.get(stem, stem.replace("-", " ").title())


def ensure_staged(page_file: str) -> Path:
    """Make sure the page has an extracted content/ folder."""
    folder = CONTENT / Path(page_file).stem
    if not (folder / "manifest.json").exists():
        page = HERE / page_file
        import argparse
        content_mod.cmd_extract(
            argparse.Namespace(pages=[str(page)], force=True)
        )
    return folder


def manifest_of(page_file: str) -> dict:
    folder = ensure_staged(page_file)
    return json.loads(read(folder / "manifest.json"))


def save_manifest(page_file: str, manifest: dict) -> None:
    folder = CONTENT / Path(page_file).stem
    write(folder / "manifest.json", json.dumps(manifest, indent=2) + "\n")


def section_info(folder: Path, name: str) -> dict:
    path = folder / name
    if not path.exists():
        return {"file": name, "missing": True, "preview": "(file missing)",
                "id": "", "runs": []}

    html = read(path)
    # Scan the SAME markup set_block writes against. merge_spans collapses
    # identical neighbouring spans, which renumbers blocks — if the editor is
    # shown ids from the unmerged file, set_block cannot find them.
    html = textedit_mod.merge_spans(html)
    open_tag = ""
    at = html.find("<section")
    if at >= 0:
        open_tag = html[at:html.index(">", at) + 1]

    # A run inside <a href> is a link. The editor has to know, or the next save
    # would rebuild the box without the href and quietly delete the link.
    #
    # Only links INSIDE the run's own block count. The site's filled buttons are
    # <div><a href><div>label</div></a></div>, so the <a> WRAPS the block — that
    # is a button label, not inline text, and treating it as a link (or letting
    # it be edited as a paragraph) would rewrite the button and break it.
    link_spans = [(m.start(), m.end(), m.group(1))
                  for m in re.finditer(r'<a\b[^>]*href="([^"]*)"[^>]*>.*?</a>', html, re.S)]
    block_spans = textedit_mod.blocks(html)

    def inline_href(pos: int) -> str:
        inner = textedit_mod.block_of(block_spans, pos)
        for start, end, url in link_spans:
            if start <= pos < end:
                # inside the block => a real inline link on its text
                if any(b[2] == inner and b[0] <= start for b in block_spans):
                    return url
        return ""

    def inside_button(pos: int) -> bool:
        """Is this run text INSIDE a link container, rather than a paragraph?"""
        for start, end, _url in link_spans:
            if start <= pos < end and not inline_href(pos):
                return True
        return False

    runs = []
    for i, (match, tag, attrs, text) in enumerate(textedit_mod.text_runs(html)):
        colour = re.search(r'color:\s*([^;"]+)', attrs)
        weight = re.search(r'font-weight:\s*([^;"]+)', attrs)
        size = re.search(r'font-size:\s*([^;"]+)', attrs)
        css = re.search(r'style="([^"]*)"', attrs)
        cls = re.search(r'class="([^"]*)"', attrs)
        runs.append({
            "i": i,
            "tag": tag,
            # `css` and `class` are the run's own styling verbatim, which is what
            # the editor needs to render it and to send it back unchanged.
            "css": css.group(1).strip() if css else "",
            "class": cls.group(1).strip() if cls else "",
            "text": re.sub(r"\s+", " ", text).strip(),
            # `raw` is what the editor's box shows, so breaks must read as
            # newlines — the markup has them as <br>.
            "raw": textedit_mod.to_text(text),
            "style": attrs[:120],
            "color": colour.group(1).strip() if colour else "",
            "bold": weight.group(1).strip() in ("700", "bold") if weight else False,
            "size": size.group(1).strip() if size else "",
            "href": inline_href(match.start()),
            # False for text that belongs to a button or other link container.
            # The editor shows those read-only: rewriting them through set_block
            # would flatten the button's own structure.
            "editable": not inside_button(match.start()),
        })

    # One editing box per block, not per run: styling part of a line splits a
    # span, and those halves belong in the box they came from. The UI reads
    # this to group the runs back together.
    blocks = [
        {"id": bid, "tag": tag, "runs": idxs}
        for bid, tag, idxs in textedit_mod.group_runs(html)
    ]

    images = []
    for i, img in enumerate(re.finditer(r"<img\b[^>]*>", html)):
        tag = img.group(0)
        src = re.search(r'src="([^"]*)"', tag)
        alt = re.search(r'alt="([^"]*)"', tag)
        images.append({
            "i": i,
            "src": src.group(1) if src else "",
            "alt": alt.group(1) if alt else "",
        })

    return {
        "file": name,
        "id": sections_mod.section_id(open_tag) if open_tag else "",
        "preview": sections_mod.strip_tags(html)[:110] or "(no text — image/decoration)",
        "runs": runs,
        "blocks": blocks,
        # The span class this page's text actually uses, so newly split spans
        # look like every other span around them.
        "spanClass": next((r["class"] for r in runs if r["class"]), "C9DxTc"),
        # The filled link buttons in this section, if any. They are edited as a
        # list rather than as text: a button is a widget, and rewriting its
        # label as a paragraph would flatten it.
        "buttons": buttons_mod.read_buttons(html),
        "images": images,
    }


def upload_image(payload: dict) -> dict:
    """
    Save an uploaded image into assets/uploads/ and return its site-relative path.

    The name is content-addressed with a short hash of the bytes, so uploading
    the same picture twice reuses one file instead of piling up copies.
    """
    raw_b64 = (payload.get("data") or "").split(",")[-1]
    if not raw_b64:
        return {"ok": False, "error": "no image data"}

    try:
        blob = base64.b64decode(raw_b64, validate=True)
    except Exception:
        return {"ok": False, "error": "image data was not valid base64"}

    if not blob:
        return {"ok": False, "error": "the image file was empty"}

    name = payload.get("name") or "image"
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_EXT:
        ext = ".png"

    stem = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")[:40]
    digest = hashlib.sha1(blob).hexdigest()[:12]
    UPLOADS.mkdir(parents=True, exist_ok=True)

    # Same bytes already uploaded (under any name)? Reuse that file rather than
    # writing a second copy of an identical picture.
    existing = next(iter(UPLOADS.glob(f"*-{digest}.*")), None)
    if existing is not None:
        return {"ok": True, "src": f"assets/uploads/{existing.name}",
                "name": existing.name, "bytes": len(blob), "reused": True}

    dest = UPLOADS / f"{stem or 'image'}-{digest}{ext}"
    dest.write_bytes(blob)

    return {"ok": True, "src": f"assets/uploads/{dest.name}",
            "name": dest.name, "bytes": len(blob), "reused": False}


def default_image() -> str:
    """The picture a new Image element starts with.

    Read from the template itself rather than a separate settings file, so the
    two cannot fall out of step.
    """
    path = TEMPLATES / "image.html"
    if not path.exists():
        return ""
    m = re.search(r'<img\b[^>]*\bsrc="([^"]*)"', read(path))
    return m.group(1) if m else ""


def set_default_image(src: str) -> int:
    """Point both image element templates at a new default picture.

    Both, so an Image element and an Image + text element start from the same
    place. Returns how many templates changed.
    """
    changed = 0
    for name in ("image", "image-text"):
        path = TEMPLATES / f"{name}.html"
        if not path.exists():
            continue
        html = read(path)
        new = re.sub(r'(<img\b[^>]*?\bsrc=")[^"]*(")',
                     lambda m: m.group(1) + src + m.group(2), html, count=1)
        if new != html:
            write(path, new)
            changed += 1
    return changed


def rebuild(page_file: str) -> int:
    page = HERE / page_file
    html = content_mod.build_one(page, quiet=True)
    if html is None:
        return 0
    write(page, html)
    return len(html)


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

UNDO = HERE / "_undo"
UNDO_LIMIT = 8            # snapshots kept
UNDO_MAX_BYTES = 400_000_000

TEMPLATES = Path(__file__).resolve().parent / "templates"
ORDER_FILE = CONTENT / "_order.json"
UPLOADS = HERE / "assets" / "uploads"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}


def fresh_id() -> str:
    """A unique section id — new sections must not collide with their source."""
    return "h.new_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _prune_undo() -> None:
    entries = sorted(p for p in UNDO.glob("e*") if p.is_dir())
    while len(entries) > UNDO_LIMIT:
        shutil.rmtree(entries.pop(0), ignore_errors=True)
    while entries and sum(_dir_size(e) for e in entries) > UNDO_MAX_BYTES:
        shutil.rmtree(entries.pop(0), ignore_errors=True)


def snapshot(label: str, page_file: str | None = None) -> str:
    """
    Record the state an action is about to change.

    page_file=None snapshots every page plus all staged content — needed for
    anything that touches the nav, since the nav is duplicated into every page.
    Passing a page name snapshots just that page's file and staged folder, which
    keeps a text edit cheap.
    """
    UNDO.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    entry = UNDO / f"e{stamp}"
    (entry / "pages").mkdir(parents=True)

    names = [page_file] if page_file else site_pages()
    for name in names:
        src = HERE / name
        if src.exists():
            shutil.copy2(src, entry / "pages" / name)

    scope = "page" if page_file else "site"
    if page_file:
        folder = CONTENT / Path(page_file).stem
        if folder.exists():
            shutil.copytree(folder, entry / "content" / Path(page_file).stem)
    elif CONTENT.exists():
        shutil.copytree(CONTENT, entry / "content")

    write(entry / "label.txt", label + "\n")
    write(entry / "scope.txt", scope + "\n")
    _prune_undo()
    return entry.name


def undo_last() -> dict:
    entries = sorted(p for p in UNDO.glob("e*") if p.is_dir())
    if not entries:
        return {"ok": False, "error": "nothing to undo"}

    entry = entries[-1]
    label_file = entry / "label.txt"
    label = label_file.read_text(encoding="utf-8").strip() if label_file.exists() else "change"
    # Never GUESS the scope. An old snapshot with no scope.txt defaulted to
    # "site", which made a page-scoped snapshot look site-wide — its `recorded`
    # set held one file, so orphan removal deleted the other 30 pages.
    scope_file = entry / "scope.txt"
    scope = scope_file.read_text(encoding="utf-8").strip() if scope_file.exists() else None

    for f in (entry / "pages").glob("*.html"):
        shutil.copy2(f, HERE / f.name)

    snap_content = entry / "content"
    if snap_content.exists():
        for item in snap_content.iterdir():
            target = CONTENT / item.name
            if item.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(item, target)
            else:
                # e.g. content/_order.json — a file, not a folder
                shutil.copy2(item, target)

        # A site-wide snapshot taken before _order.json existed must remove it
        # again, or the pre-snapshot ordering silently survives the undo.
        if scope == "site" and not (snap_content / "_order.json").exists():
            if ORDER_FILE.exists():
                ORDER_FILE.unlink()

    # Pages created after the snapshot (undoing "add page") aren't in the
    # snapshot to restore, so remove them explicitly or they linger as orphans.
    if scope == "site":
        recorded = {f.name for f in (entry / "pages").glob("*.html")}
        for name in site_pages():
            if name in recorded:
                continue
            TRASH.mkdir(exist_ok=True)
            src = HERE / name
            if src.exists():
                shutil.move(str(src), str(TRASH / f"undone-{name}"))
            folder = CONTENT / Path(name).stem
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)

    shutil.rmtree(entry, ignore_errors=True)
    return {"ok": True, "undid": label}


# ---------------------------------------------------------------------------
# Ordering (drives both the sidebar and the site's nav)
# ---------------------------------------------------------------------------

def load_order() -> list:
    if ORDER_FILE.exists():
        try:
            saved = json.loads(read(ORDER_FILE))
            known = site_pages()
            merged = [p for p in saved if p in known]
            merged += [p for p in known if p not in merged]
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return site_pages()


def save_order(order: list) -> None:
    write(ORDER_FILE, json.dumps(order, indent=2) + "\n")


def sync_nav_order(order: list) -> int:
    """
    Reorder the nav <li> entries in every page (and every staged _head.html) to
    match `order`. Entries are matched by their href, not by slug, because the
    two differ (xbox-one-series-xs.html <-> /xbox-oneseries-xs).
    """
    rank = {name: i for i, name in enumerate(order)}
    changed = 0
    targets = site_pages() + [str(p) for p in CONTENT.glob("*/_head.html")]

    for target in targets:
        path = Path(target)
        if not path.is_absolute():
            path = HERE / path
        if not path.exists():
            continue
        html = read(path)
        rows = list(platforms_mod.items(html))
        if not rows:
            continue

        # Reorder each item's own markup TOGETHER with its nested child list,
        # or a parent moves and leaves its sub-platforms behind. (`items` now
        # yields own_markup + tail as separate pieces.)
        blocks = [own + tail for _, _, _, _, own, tail in rows]

        keys = []
        for block in blocks:
            m = re.search(r'href="([^"]+)"', block)
            keys.append(m.group(1) if m else None)

        positions = [i for i, k in enumerate(keys) if k in rank]
        if len(positions) < 2:
            continue

        wanted = sorted(positions, key=lambda i: rank[keys[i]])
        if [blocks[i] for i in positions] == [blocks[i] for i in wanted]:
            continue

        rebuilt = blocks[:]
        for pos, src in zip(positions, wanted):
            rebuilt[pos] = blocks[src]

        new_html = html[:rows[0][0]] + "".join(rebuilt) + html[rows[-1][1]:]
        if new_html != html:
            write(path, new_html)
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def undo_depth() -> int:
    return len([p for p in UNDO.glob("e*") if p.is_dir()]) if UNDO.exists() else 0


TEMPLATE_LABELS = {
    "heading-and-text": "Heading + text",
    "note": "Note / callout",
    "text": "Text",
    "list": "Bullet list",
    "links": "Link buttons",
    "spacer": "Spacer",
    "image": "Image",
    "image-text": "Image + text",
    "free": "Free position",
}


def template_list() -> list:
    """The element types offered in the Add element menu."""
    if not TEMPLATES.exists():
        return []
    return [
        {"file": f.name,
         "label": TEMPLATE_LABELS.get(f.stem, f.stem.replace("-", " ").title())}
        for f in sorted(TEMPLATES.glob("*.html"))
        # nav-item.html is the donor for new nav entries, not an element type
        if f.stem not in ("nav-item",)
    ]


def api_state() -> dict:
    labels = nav_labels()
    pages = [{"file": name, "title": title_for(name, labels)} for name in load_order()]

    cats = []
    html = read(HERE / site_pages()[0])
    for _, _, slug, label, _, _ in platforms_mod.items(html):
        if not slug.startswith("/"):
            continue
        cats.append({"slug": slug.lstrip("/"),
                     "label": label.lstrip("\u2004 ").lstrip("-").replace("\u200e", "").strip()})
    return {"pages": pages, "categories": cats, "undoDepth": undo_depth(),
            "templates": template_list(), "tree": platforms_mod.nav_tree(),
            "defaultImage": default_image()}


def api_page(page_file: str) -> dict:
    manifest = manifest_of(page_file)
    folder = CONTENT / Path(page_file).stem
    sections = [section_info(folder, name) for name in manifest["sections"]]
    return {
        "file": page_file,
        "title": title_for(page_file),
        "sections": sections,
    }


ACTION_LABELS = {
    "set_text": "edit text",
    "set_block": "edit paragraph",
    "set_buttons": "edit buttons",
    "set_free": "move block",
    "set_default_image": "set default image",
    "set_style": "restyle text",
    "set_image": "swap image",
    "drop_section": "delete section",
    "move_section": "move section",
    "add_section": "add section",
    "add_page": "add page",
    "add_group": "add group",
    "move_page": "reorder",
    "add_category": "add category",
    "rename_category": "rename",
    "move_child": "move sub-page",
    "remove_category": "remove category",
}


def act(payload: dict) -> dict:
    """Validate, snapshot, dispatch, and discard the snapshot if it failed."""
    action = payload.get("action")

    if action == "undo":
        return undo_last()

    # Uploads write a new asset, never a page — no snapshot to take or discard.
    if action == "upload_image":
        return upload_image(payload)

    if action == "rebuild":
        return {"ok": True, "rebuilt": rebuild(payload.get("file"))}

    if action not in ACTION_LABELS:
        return {"ok": False, "error": f"unknown action {action!r}"}

    page = payload.get("file")
    page_scoped = action in ("set_text", "set_style", "set_image", "drop_section",
                             "move_section", "add_section")
    if page_scoped and (not page or page not in site_pages()):
        return {"ok": False, "error": "unknown page"}

    label = ACTION_LABELS[action]
    extra = payload.get("label") or payload.get("slug") or ""
    if extra:
        label = f"{label} {extra}"

    entry = snapshot(label, page if page_scoped else None)
    try:
        result = _dispatch(payload, action, page, page_scoped)
    except Exception:
        shutil.rmtree(UNDO / entry, ignore_errors=True)
        raise

    # A rejected action must not burn an undo slot.
    if not result.get("ok"):
        shutil.rmtree(UNDO / entry, ignore_errors=True)
    return result


def _dispatch(payload: dict, action: str, page, page_scoped: bool) -> dict:
    # ------------------------------------------------------------------ text
    if action == "set_block":
        # Rewrite a whole block's insides as one go, from [{text, style}] parts.
        # The editor sends this after styling or editing a paragraph, so the
        # paragraph stays one block however many spans it now contains.
        folder = ensure_staged(page)
        path = folder / payload["section"]
        html = textedit_mod.merge_spans(read(path))
        want = payload.get("block")
        # Compare as strings: the id reaches us from the browser, where it has
        # been through dataset and JSON, so it may be "17" rather than 17. An
        # int/str comparison here silently fails to find the block, and the save
        # is dropped.
        target = next((s for s in textedit_mod.blocks(html) if str(s[2]) == str(want)), None)
        if target is None:
            return {"ok": False, "error": f"block {want!r} not found"}
        c_start, c_end, _bid, _tag, _open = target

        parts = payload.get("parts") or []
        if not parts:
            return {"ok": False, "error": "nothing to write"}
        # Keep whatever span class the block already uses, so the new spans look
        # like every other span in the page.
        span_class = payload.get("spanClass") or "C9DxTc"
        inner = textedit_mod.render_parts(parts, span_class=span_class)
        write(path, html[:c_start] + inner + html[c_end:])
        return {"ok": True, "rebuilt": rebuild(page)}

    if action == "set_buttons":
        # Rewrite a section's buttons from a list: label, href, and optional
        # per-button colour/size. Fewer entries removes the extras, more adds.
        folder = ensure_staged(page)
        path = folder / payload["section"]
        html = read(path)
        items = payload.get("buttons")
        if not isinstance(items, list):
            return {"ok": False, "error": "need a buttons list"}
        new = buttons_mod.write_buttons(html, items)
        if new == html:
            return {"ok": False, "error": "no buttons found in that section"}
        write(path, new)
        return {"ok": True, "rebuilt": rebuild(page)}

    if action == "set_free":
        # Position a block freely, like dropping it on a page rather than
        # leaving it in the flow. The tradeoff the site's grid imposes: an
        # absolutely positioned block no longer pushes its neighbours around,
        # so the section keeps a min-height and can look emptier than it did.
        folder = ensure_staged(page)
        path = folder / payload["section"]
        html = read(path)
        left, top = payload.get("left"), payload.get("top")
        if left is None or top is None:
            return {"ok": False, "error": "need left and top"}
        props = {"position": "absolute",
                 "left": f"{int(round(float(left)))}px",
                 "top": f"{int(round(float(top)))}px",
                 "z-index": "3"}
        new = textedit_mod.set_block_props(html, payload.get("block"), props)
        if new == html:
            return {"ok": False, "error": "block not found, or nothing changed"}
        write(path, new)
        return {"ok": True, "rebuilt": rebuild(page)}

    if action == "set_default_image":
        # The picture a new Image element starts with. Not a per-page setting:
        # it changes the template, so every new element from now on uses it.
        src = (payload.get("src") or "").strip()
        if not src:
            return {"ok": False, "error": "need an image src"}
        changed = set_default_image(src)
        if not changed:
            return {"ok": False, "error": "no image templates to update"}
        return {"ok": True, "changed": changed, "src": src}

    if action == "set_text":
        folder = ensure_staged(page)
        path = folder / payload["section"]
        html = textedit_mod.merge_spans(read(path))
        runs = list(textedit_mod.text_runs(html))
        idx = payload["index"]
        if idx < 0 or idx >= len(runs):
            return {"ok": False, "error": f"run {idx} out of range"}
        match = runs[idx][0]
        # Newlines in the box become real breaks; a raw "\n" would collapse to
        # a space and the line break would silently vanish.
        write(path, html[:match.start(3)] + textedit_mod.to_html(payload["text"])
              + html[match.end(3):])
        return {"ok": True, "rebuilt": rebuild(page)}

    # -------------------------------------------------------------- styling
    if action == "set_style":
        folder = ensure_staged(page)
        path = folder / payload["section"]
        html = read(path)
        runs = list(textedit_mod.text_runs(html))
        idx = payload["index"]
        if idx < 0 or idx >= len(runs):
            return {"ok": False, "error": f"run {idx} out of range"}

        match, tag, attrs, text = runs[idx]
        color = payload.get("color")
        bold = payload.get("bold")
        size = payload.get("size")
        start, end = payload.get("start"), payload.get("end")

        # Selection offsets are measured in the box's text, where a break is one
        # newline; the markup holds it as a <br>. Compare and slice in the text
        # form, then map back onto the markup so a slice never lands inside a tag.
        plain = textedit_mod.to_text(text)

        # Whole run only when the selection really covers all of it. A selection
        # that merely STARTS at 0 is still partial (start <= 0 alone was wrong).
        whole = (start is None or end is None or start >= end
                 or (start <= 0 and end >= len(plain)))

        if whole:
            new_attrs = textedit_mod.set_style_props(attrs, color=color, bold=bold, size=size)
            write(path, html[:match.start(2)] + new_attrs + html[match.end(2):])
        else:
            m_start, m_end = textedit_mod._markup_offsets(text, int(start or 0), int(end or 0))
            head, mid, tail = text[:m_start], text[m_start:m_end], text[m_end:]
            mid_attrs = textedit_mod.set_style_props(attrs, color=color, bold=bold, size=size)

            if tag.lower() == "span":
                # split into sibling spans, so only the slice is restyled
                parts = []
                for chunk, use in ((head, attrs), (mid, mid_attrs), (tail, attrs)):
                    if chunk:
                        parts.append(f"<{tag}{use}>{chunk}</{tag}>")
                new_html = "".join(parts)
            else:
                # A block (p/h2/li) must stay ONE block — splitting it would
                # create a second heading. Style the slice with an inner span.
                bits = []
                if color:
                    bits.append(f"color: {color}")
                if bold is not None:
                    bits.append(f"font-weight: {'700' if bold else '400'}")
                if size:
                    bits.append(f"font-size: {size}")
                inner_style = f' style="{"; ".join(bits)};"' if bits else ""
                inner = f"<span>{head}</span>" if head else ""
                inner += f"<span{inner_style}>{mid}</span>"
                inner += f"<span>{tail}</span>" if tail else ""
                new_html = f"<{tag}{attrs}>{inner}</{tag}>"

            write(path, html[:match.start()] + new_html + html[match.end():])
        return {"ok": True, "rebuilt": rebuild(page)}

    # ---------------------------------------------------------------- images
    if action == "set_image":
        folder = ensure_staged(page)
        path = folder / payload["section"]
        html = read(path)
        found = list(re.finditer(r"<img\b[^>]*>", html))
        idx = payload["index"]
        if idx < 0 or idx >= len(found):
            return {"ok": False, "error": f"image {idx} out of range"}

        tag = found[idx].group(0)
        src = payload.get("src") or ""
        if not src:
            return {"ok": False, "error": "no image chosen"}

        if re.search(r'src="[^"]*"', tag):
            new_tag = re.sub(r'src="[^"]*"', f'src="{src}"', tag, count=1)
        else:
            new_tag = tag[:-1].rstrip() + f' src="{src}">'

        alt = payload.get("alt")
        if alt is not None:
            if re.search(r'alt="[^"]*"', new_tag):
                new_tag = re.sub(r'alt="[^"]*"', f'alt="{alt}"', new_tag, count=1)
            else:
                new_tag = new_tag[:-1].rstrip() + f' alt="{alt}">'

        write(path, html[:found[idx].start()] + new_tag + html[found[idx].end():])
        return {"ok": True, "rebuilt": rebuild(page), "src": src}

    # -------------------------------------------------------------- sections
    if action == "drop_section":
        manifest = manifest_of(page)
        name = payload["section"]
        if name in manifest["sections"]:
            manifest["sections"].remove(name)
            save_manifest(page, manifest)
            TRASH.mkdir(exist_ok=True)
            src = CONTENT / Path(page).stem / name
            if src.exists():
                shutil.move(str(src), str(TRASH / f"{Path(page).stem}.{name}"))
        return {"ok": True, "rebuilt": rebuild(page)}

    if action == "move_section":
        manifest = manifest_of(page)
        order = manifest["sections"]
        name = payload["section"]
        if name not in order:
            return {"ok": False, "error": "section not found"}
        i = order.index(name)
        j = i - 1 if payload["dir"] == "up" else i + 1
        if j < 0 or j >= len(order):
            return {"ok": False, "error": "already at the edge"}
        order[i], order[j] = order[j], order[i]
        save_manifest(page, manifest)
        return {"ok": True, "rebuilt": rebuild(page)}

    if action == "add_section":
        manifest = manifest_of(page)
        folder = CONTENT / Path(page).stem
        order = manifest["sections"]
        source = payload.get("source") or "blank"

        if source == "blank":
            tpl = TEMPLATES / (payload.get("template") or "heading-and-text.html")
            if not tpl.exists():
                return {"ok": False,
                        "error": "template missing — run: python tools/make_templates.py"}
            html = read(tpl).replace("SECTION_ID", fresh_id())
            base = "new-section"
        else:
            src = folder / source
            if not src.exists():
                return {"ok": False, "error": f"section {source} not found"}
            html = read(src)
            # Fresh id: a copy that reused its source's id would put duplicate
            # ids in the page.
            html = re.sub(r'\bid="h\.[^"]*"', f'id="{fresh_id()}"', html)
            base = re.sub(r"^\d+-", "", source).removesuffix(".html")

        name = f"{len(order):02d}-{base}.html"
        n = 2
        while (folder / name).exists():
            name = f"{len(order):02d}-{base}-{n}.html"
            n += 1

        write(folder / name, html)
        after = payload.get("after")
        if after and after in order:
            order.insert(order.index(after) + 1, name)
        else:
            order.append(name)
        save_manifest(page, manifest)
        return {"ok": True, "rebuilt": rebuild(page), "section": name}

    # ----------------------------------------------------------------- pages
    # Adding an entry normally clones one that already exists, so the new row
    # carries the site's own classes. `nav_donor_own` returns None when there is
    # no such entry, and callers fall back to the built-in pristine item in
    # tools/templates/nav-item.html — an emptied nav is exactly when you most
    # want to add to it, and there is then nothing left to clone.
    # `anchor=None` means append to the end of the nav list.
    def nav_donor_own(want: str | None) -> str | None:
        if want:
            for cand in site_pages():
                p = HERE / cand
                if not p.exists():
                    continue
                for _, _, s, _, own, _ in platforms_mod.items(read(p)):
                    if s == want:
                        return own
        return None

    def any_nav_anchor() -> str | None:
        for cand in site_pages():
            p = HERE / cand
            if not p.exists():
                continue
            its = list(platforms_mod.items(read(p)))
            if its:
                return its[-1][2]
        return None

    def any_page_stem() -> str:
        for cand in site_pages():
            if (HERE / cand).exists():
                return cand
        return "index.html"

    if action == "add_group":
        # A nav entry with nothing behind it — a group heading you can nest
        # pages under. No page file is created.
        slug = re.sub(r"[^a-z0-9]+", "-", (payload.get("slug") or "").lower()).strip("-")
        if not slug:
            return {"ok": False, "error": "give the group a name"}
        if (HERE / f"{slug}.html").exists():
            return {"ok": False,
                    "error": f"{slug}.html already exists as a page — use + page"}
        for _, _, s, _, _, _ in platforms_mod.items(read(HERE / site_pages()[0])):
            if s == "/" + slug:
                return {"ok": False,
                        "error": f'a nav entry "/{slug}" already exists'}

        label_text = payload.get("label") or slug.replace("-", " ").title()
        anchor = payload.get("after") or any_nav_anchor()
        template = nav_donor_own(anchor) or platforms_mod.default_nav_item()

        new_li = platforms_mod.fresh_li(template, "/" + slug, f"{slug}.html",
                                        label_text, nopage=True)
        # Drop the link target so the row is not clickable, but KEEP data-url:
        # tools identify nav entries by it. The marker says "no page here".
        new_li = re.sub(r'\shref="[^"]*"', "", new_li, count=1)

        platforms_mod._apply_to_all(
            lambda h: platforms_mod._insert_li(h, new_li, anchor), "nav insert")
        return {"ok": True, "group": slug}

    if action == "add_page":
        slug = re.sub(r"[^a-z0-9]+", "-", (payload.get("slug") or "").lower()).strip("-")
        if not slug:
            return {"ok": False, "error": "give the page a name"}

        parent = (payload.get("parent") or "").strip()
        if parent and not parent.startswith("/"):
            parent = "/" + parent
        # Under a category the file is named "<group>-<page>" (others-<parent>-<name>),
        # which is what derive_groups reads — so the page nests under its
        # category with no extra step.
        stem = f"others-{parent.strip('/')}-{slug}" if parent else slug
        filename = f"{stem}.html"
        if (HERE / filename).exists():
            return {"ok": False, "error": f"{filename} already exists"}

        label_text = payload.get("label") or slug.replace("-", " ").title()
        in_nav = payload.get("in_nav", True)
        anchor = payload.get("after") or any_nav_anchor()

        if in_nav and parent:
            own = nav_donor_own(parent)
            if own is None:
                return {"ok": False, "error": f"no nav entry {parent!r} to nest under"}
            child_li = platforms_mod.fresh_li(own, "/" + stem, filename,
                                              label_text, child=True)
            platforms_mod._apply_to_all(
                lambda h: platforms_mod._insert_child(h, parent, child_li),
                "nav insert")
        elif in_nav:
            own = nav_donor_own(anchor) or platforms_mod.default_nav_item()
            new_li = platforms_mod.fresh_li(own, "/" + stem, filename, label_text)
            platforms_mod._apply_to_all(
                lambda h: platforms_mod._insert_li(h, new_li, anchor), "nav insert")

        # Build the page from a donor's boilerplate (read AFTER the nav insert,
        # so the new page carries the nav entry too) plus one blank section.
        # A child page borrows its category's page, so it looks like a sibling.
        from_page = payload.get("from_page") or (
            f"{parent.strip('/')}.html" if parent and (HERE / f"{parent.strip('/')}.html").exists()
            else any_page_stem())
        donor = read(HERE / from_page)
        first = donor.index("<section")
        last = donor.rindex("</section>") + len("</section>")
        blank = read(TEMPLATES / "heading-and-text.html").replace("SECTION_ID", fresh_id())
        new_html = donor[:first] + blank + donor[last:]
        # mark the new page's OWN nav row (level 2 for a child, which
        # _set_current would miss), and clear the donor's highlight
        new_html = platforms_mod._mark_current_any(new_html, "/" + stem)
        write(HERE / filename, new_html)

        order = load_order()
        if filename not in order:
            order.append(filename)
        save_order(order)

        ensure_staged(filename)
        rebuild(filename)
        return {"ok": True, "page": filename}

    if action == "move_child":
        parent = (payload.get("parent") or "").strip()
        slug = (payload.get("slug") or "").strip()
        direction = payload.get("dir") or "up"
        if not parent or not slug:
            return {"ok": False, "error": "need a parent and a sub-page"}
        if not parent.startswith("/"):
            parent = "/" + parent
        if not slug.startswith("/"):
            slug = "/" + slug
        platforms_mod._apply_to_all(
            lambda h: platforms_mod._move_child(h, parent, slug, direction),
            "sub-page reorder")
        return {"ok": True}

    if action == "rename_category":
        slug = (payload.get("slug") or "").strip().lstrip("/")
        label = (payload.get("label") or "").strip()
        if not slug or not label:
            return {"ok": False, "error": "need a slug and a new name"}
        found = False
        for _, _, s, _, _, _ in platforms_mod.items(read(HERE / site_pages()[0])):
            if s == "/" + slug:
                found = True
                break
        if not found:
            return {"ok": False, "error": f"no nav entry /{slug}"}
        platforms_mod._apply_to_all(
            lambda h: platforms_mod._relabel(h, "/" + slug, label), "nav relabel")
        return {"ok": True, "label": label}

    if action == "move_page":
        if not page or page not in site_pages():
            return {"ok": False, "error": "unknown page"}
        order = load_order()
        i = order.index(page)
        j = i - 1 if payload.get("dir") == "up" else i + 1
        if j < 0 or j >= len(order):
            return {"ok": False, "error": "already at the edge"}
        order[i], order[j] = order[j], order[i]
        save_order(order)
        return {"ok": True, "navFiles": sync_nav_order(order)}

    # ------------------------------------------------------------ categories
    if action == "add_category":
        import argparse
        rc = platforms_mod.cmd_add(argparse.Namespace(
            slug=payload["slug"],
            label=payload.get("label") or payload["slug"],
            after=payload.get("after", "/windows"),
            from_page="apple-tv.html"))
        return {"ok": rc == 0}

    if action == "remove_category":
        import argparse
        rc = platforms_mod.cmd_remove(argparse.Namespace(slug=payload["slug"]))
        return {"ok": rc == 0}

    return {"ok": False, "error": f"unhandled action {action!r}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the console clean

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"))

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        route = parsed.path

        if route in ("/", "/editor"):
            self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            return

        if route == "/api/state":
            self._json(api_state())
            return

        if route == "/api/page":
            q = parse_qs(parsed.query)
            page = (q.get("file") or [""])[0]
            if page not in site_pages():
                self._json({"error": "unknown page"}, 404)
                return
            self._json(api_page(page))
            return

        # static files from the project root, so the preview iframe works
        rel = route.lstrip("/") or "index.html"
        target = (HERE / rel).resolve()
        try:
            target.relative_to(HERE.resolve())
        except ValueError:
            self._json({"error": "forbidden"}, 403)
            return
        if target.is_file():
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()

            # The preview asks for edit mode. Mark which staged section and block
            # each editable element came from, and inject the small script that
            # reports clicks and edits back. Done at serve time: the .html files
            # on disk keep their original markup.
            if ctype.startswith("text/html") and parse_qs(parsed.query).get("mm_edit"):
                try:
                    page = rel.replace("\\", "/")
                    if page in site_pages():
                        html = data.decode("utf-8")
                        folder = CONTENT / Path(page).stem
                        names = manifest_of(page)["sections"]
                        # The blocks the editor offers, with the text it shows
                        # for each. The page script finds the matching elements
                        # by that text — no position arithmetic on either side.
                        targets = preview_edit.targets(section_info, folder, names)
                        html = preview_edit.inject(html, targets)
                        data = html.encode("utf-8")
                        print(f"  preview edit: {page} — {len(targets)} blocks to match")
                    else:
                        print(f"  preview edit: {page} is not a site page, serving plain")
                except Exception as exc:                      # noqa: BLE001
                    # Never break the preview over the marking: fall back to the
                    # plain page and say why in the console.
                    print(f"  preview edit failed for {rel}: {exc!r} — serving plain")

            self._send(200, data, ctype)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        try:
            self._json(act(payload))
        except Exception as exc:                       # noqa: BLE001
            self._json({"ok": False, "error": repr(exc)}, 500)


class EditorServer(ThreadingHTTPServer):
    # ThreadingHTTPServer defaults allow_reuse_address to True, which on Windows
    # lets a SECOND server bind a port that is already in use — so a stale
    # instance keeps serving old code and your new one silently does nothing.
    # Refuse that instead.
    allow_reuse_address = False


def main() -> int:
    if not UI.exists():
        print(f"! missing {UI}")
        return 1
    no_open = "--no-open" in sys.argv
    port = int(arg_value("--port", PORT))
    try:
        server = EditorServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"! cannot start: port {port} is already in use.")
        print(f"  An editor is probably already running — open "
              f"http://127.0.0.1:{port}/ , or stop that one first.")
        print(f"  (Or start a second one elsewhere: --port {port + 1})")
        print(f"  ({exc})")
        return 1
    url = f"http://127.0.0.1:{port}/"
    print(f"Editor running at {url}")
    print("Ctrl-C to stop.\n")
    if not no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
