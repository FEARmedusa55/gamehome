#!/usr/bin/env python3
"""
Content staging for the mirrored pages.

Splits each mirrored page into small, editable pieces so you can work on the
content without touching 126KB of Google Sites boilerplate:

    content/<page>/_head.html     everything before the first section
    content/<page>/00-<slug>.html one file per <section>
    content/<page>/01-<slug>.html
    ...
    content/<page>/_tail.html     everything after the last section
    content/<page>/manifest.json  order + source hash

Then `build` reassembles them into the page. Editing content becomes: edit or
delete a numbered file, rename to reorder, copy one to duplicate a section.

Every command is byte-exact: it reads and writes raw bytes, so it never rewrites
line endings or mangles encoding.

    python tools/content.py extract index.html
    python tools/content.py verify index.html     # round-trip must match source
    python tools/content.py build index.html
    python tools/content.py status
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sections import iter_sections, section_id, strip_tags  # noqa: E402
from textedit import merge_spans                             # noqa: E402
import sitekit                                               # noqa: E402

HERE = Path(__file__).resolve().parent.parent
CONTENT = HERE / "content"

SECTION_OPEN = re.compile(r"<section\b", re.I)


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def page_files() -> list[Path]:
    return sorted(
        p for p in HERE.glob("*.html")
        if not p.name.startswith("_") and ".bak" not in p.name
    )


def resolve(name: str) -> Path:
    path = Path(name)
    if not path.is_absolute():
        path = HERE / path
    return path


def split_page(html: str):
    """
    Return (head, [chunks], tail) such that head + "".join(chunks) + tail == html.

    Each chunk is a <section> plus any whitespace that preceded it — the gaps
    between sections have to live somewhere or the round-trip loses bytes.
    """
    first = SECTION_OPEN.search(html)
    if not first:
        return html, [], ""

    last = html.rfind("</section>") + len("</section>")
    head = html[:first.start()]
    tail = html[last:]
    body = html[first.start():last]

    chunks = []
    prev = 0
    for start, end, _ in iter_sections(body):
        chunks.append(body[prev:end])      # gap before + the section itself
        prev = end
    if prev < len(body):
        tail = body[prev:] + tail          # any trailing gap joins the tail

    return head, chunks, tail


def slug_for(index: int, fragment: str, open_tag: str) -> str:
    text = strip_tags(fragment)
    if text:
        words = re.sub(r"[^a-z0-9 ]+", "", text.lower()).split()[:5]
        slug = "-".join(words)
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", section_id(open_tag).lower()).strip("-")[:40]
    slug = slug[:44].strip("-") or "section"
    return f"{index:02}-{slug}.html"


def cmd_extract(args) -> int:
    pages = [resolve(p) for p in args.pages] if args.pages else page_files()
    for page in pages:
        if not page.exists():
            print(f"! not found: {page}")
            continue

        html = read(page)
        head, sections, tail = split_page(html)
        if not sections:
            print(f"! {page.name}: no sections found, skipping")
            continue

        out = CONTENT / page.stem
        if out.exists() and not args.force:
            print(f"! {page.name}: {out.relative_to(HERE)} already exists "
                  f"(use --force to overwrite)")
            continue

        if out.exists():
            shutil.rmtree(out)

        write(out / "_head.html", head)
        write(out / "_tail.html", tail)

        names = []
        for i, fragment in enumerate(sections):
            # Merge fragmented spans once, here, so text runs are contiguous and
            # their indices stay stable across edits. Visually a no-op.
            fragment = merge_spans(fragment)
            # the chunk may begin with whitespace, so locate the tag itself
            tag_at = fragment.index("<section")
            open_tag = fragment[tag_at:fragment.index(">", tag_at) + 1]
            name = slug_for(i, fragment, open_tag)
            write(out / name, fragment)
            names.append(name)

        manifest = {
            "page": page.name,
            "source_sha1": sha1(html),
            "source_bytes": len(html.encode("utf-8")),
            "head": "_head.html",
            "tail": "_tail.html",
            "sections": names,
        }
        write(out / "manifest.json", json.dumps(manifest, indent=2) + "\n")

        print(f"  {page.name:28} -> content/{page.stem}/  "
              f"{len(sections)} sections, {len(html):,} bytes")

    return 0


def build_one(page: Path, quiet: bool = False) -> str | None:
    """Reassemble a page from its content/ folder. Returns the HTML."""
    folder = CONTENT / page.stem
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        if not quiet:
            print(f"! no content folder for {page.name} "
                  f"(run extract first)")
        return None

    manifest = json.loads(read(manifest_path))
    parts = [read(folder / manifest["head"])]
    for name in manifest["sections"]:
        path = folder / name
        if not path.exists():
            # Deleting a section file is a legitimate way to remove a section;
            # build skips it rather than failing.
            if not quiet:
                print(f"    ! {name} missing — skipped")
            continue
        parts.append(read(path))
    parts.append(read(folder / manifest["tail"]))
    # The phone menu, search and link-preview tags are added here, on every
    # build, so no page can be published without them.
    return sitekit.finalize("".join(parts), page.name)


def cmd_drop(args) -> int:
    """Remove a section: delete its file and unlist it from the manifest."""
    page = resolve(args.page)
    folder = CONTENT / page.stem
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        print(f"! no content folder for {page.name}")
        return 1

    manifest = json.loads(read(manifest_path))
    name = args.section_file

    if name not in manifest["sections"]:
        matches = [s for s in manifest["sections"] if name in s]
        if len(matches) == 1:
            name = matches[0]
        else:
            print(f"! {args.section_file!r} is not a staged section of {page.name}")
            print("  available:")
            for s in manifest["sections"]:
                print(f"    {s}")
            return 1

    manifest["sections"].remove(name)
    write(manifest_path, json.dumps(manifest, indent=2) + "\n")

    target = folder / name
    trash = HERE / "_trash"
    trash.mkdir(exist_ok=True)
    if target.exists():
        shutil.move(str(target), str(trash / f"{page.stem}.{name}"))

    print(f"dropped {name} from {page.name}")
    print(f"  file moved to _trash/{page.stem}.{name}")
    print(f"  {len(manifest['sections'])} sections remain")
    print(f"  run: python tools/content.py build {page.name}")
    return 0


def cmd_build(args) -> int:
    pages = [resolve(p) for p in args.pages] if args.pages else page_files()
    built = 0
    for page in pages:
        if not (CONTENT / page.stem / "manifest.json").exists():
            continue
        html = build_one(page)
        if html is None:
            continue
        if args.dry_run:
            print(f"  {page.name:28} would be {len(html):,} bytes")
        else:
            write(page, html)
            print(f"  {page.name:28} rebuilt ({len(html):,} bytes)")
        built += 1
    if not built:
        print("Nothing to build.")
    elif not args.dry_run:
        print(f"  search index: {sitekit.build_search_index()} sections")
    return 0


def cmd_verify(args) -> int:
    pages = [resolve(p) for p in args.pages] if args.pages else page_files()
    ok = bad = 0
    for page in pages:
        manifest_path = CONTENT / page.stem / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(read(manifest_path))
        rebuilt = build_one(page, quiet=True)
        current = read(page)

        source_ok = sha1(current) == manifest["source_sha1"]
        rebuild_ok = rebuilt == current

        if rebuild_ok:
            ok += 1
            print(f"  OK    {page.name:28} rebuild matches current file")
        else:
            bad += 1
            print(f"  FAIL  {page.name:28} rebuild differs from current file")
            if source_ok:
                print("        (source unchanged since extract — bug in split/join)")

        if not source_ok:
            print(f"        note: {page.name} has been edited since extract "
                  f"(expected if you removed/added sections)")

    print(f"\n{ok} ok, {bad} failing")
    return 1 if bad else 0


def cmd_status(args) -> int:
    folders = sorted(p for p in CONTENT.glob("*") if p.is_dir())
    if not folders:
        print("Nothing extracted yet. Run: python tools/content.py extract")
        return 0
    print(f"{len(folders)} pages staged in content/\n")
    print(f'{"page":28} {"sections":>8} {"bytes":>10}')
    for folder in folders:
        manifest_path = folder / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(read(manifest_path))
        print(f'{manifest["page"]:28} {len(manifest["sections"]):>8} '
              f'{manifest["source_bytes"]:>10,}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage page content for editing.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ex = sub.add_parser("extract", help="split pages into content/ files")
    p_ex.add_argument("pages", nargs="*")
    p_ex.add_argument("--force", action="store_true")
    p_ex.set_defaults(func=cmd_extract)

    p_bd = sub.add_parser("build", help="reassemble pages from content/")
    p_bd.add_argument("pages", nargs="*")
    p_bd.add_argument("--dry-run", action="store_true")
    p_bd.set_defaults(func=cmd_build)

    p_vf = sub.add_parser("verify", help="check rebuild matches the page")
    p_vf.add_argument("pages", nargs="*")
    p_vf.set_defaults(func=cmd_verify)

    p_dr = sub.add_parser("drop", help="remove a section by file name")
    p_dr.add_argument("page")
    p_dr.add_argument("section_file")
    p_dr.set_defaults(func=cmd_drop)

    p_st = sub.add_parser("status", help="show staged pages")
    p_st.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
