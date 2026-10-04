"""Remove whole elements from the staged page heads, then rebuild.

The mirrored pages carry a few pieces of Google Sites chrome that are pure
furniture for our purposes. They live in the staged `content/<page>/_head.html`
rather than in the built pages, so removing them there is what makes the change
survive a `content.py build` — editing the built pages alone would be undone by
the next rebuild.

Usage:
    python tools/strip.py list
    python tools/strip.py banner      # remove the announcement banner
    python tools/strip.py banner --dry
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


# ---------------------------------------------------------------- known chrome

# The announcement banner ("Megathread v4.1 | Respawned coming soon!" plus its
# Discord button). `jscontroller="eFZtfd"` occurs exactly once per page and
# wraps the whole thing, so it is a safe anchor. The <header> it sits inside is
# left alone: it also carries the top bar, which is wanted.
TARGETS = {
    "banner": {
        "anchor": r'jscontroller="eFZtfd"',
        "label": "announcement banner",
        "note": "the strip at the very top with the Discord button",
    },
}


# ------------------------------------------------------------- tag balancing

def _tag_span(html: str, anchor_re: str) -> tuple[int, int] | None:
    """Span of the whole balanced element whose opening tag matches anchor_re.

    Walks forward counting opens and closes of that one tag name, so nested
    copies of the same tag do not end it early.
    """
    m = re.search(anchor_re, html)
    if not m:
        return None

    tag_start = html.rfind("<", 0, m.start())
    if tag_start == -1:
        return None
    name = re.match(r"<([a-zA-Z0-9]+)", html[tag_start:])
    if not name:
        return None
    tag = name.group(1)

    # Opening tags of this name, and self-closing ones which do not nest.
    opens = re.compile(rf"<{tag}\b[^>]*?(?<!/)>", re.I)
    closes = re.compile(rf"</{tag}\s*>", re.I)

    depth = 0
    i = tag_start
    while i < len(html):
        o = opens.search(html, i)
        c = closes.search(html, i)
        if not c:
            return None
        if o and o.start() < c.start():
            depth += 1
            i = o.end()
        else:
            depth -= 1
            i = c.end()
            if depth == 0:
                return tag_start, i
    return None


# ------------------------------------------------------------------ operation

def staged_heads() -> list[Path]:
    return sorted(ROOT.glob("content/*/_head.html"))


def cmd_list(_args) -> None:
    print("Removable chrome:\n")
    for key, spec in TARGETS.items():
        n = 0
        for p in staged_heads():
            if re.search(spec["anchor"], p.read_bytes().decode("utf-8", "ignore")):
                n += 1
        print(f"  {key:8} {spec['label']:24} present in {n}/{len(staged_heads())} staged heads")
        print(f"           {spec['note']}")
    print("\nRun `python tools/strip.py <name>` to remove one, then the pages are rebuilt.")


def cmd_strip(args) -> None:
    spec = TARGETS.get(args.what)
    if not spec:
        print(f"unknown target '{args.what}'. Known: {', '.join(TARGETS)}")
        raise SystemExit(2)

    print(f"Removing the {spec['label']} from the staged heads...\n")
    removed = already = 0
    for p in staged_heads():
        text = p.read_bytes().decode("utf-8", "ignore")
        span = _tag_span(text, spec["anchor"])
        if span is None:
            already += 1
            continue
        start, end = span
        # eat one trailing newline so removal does not leave a blank line
        while end < len(text) and text[end] in "\r\n":
            end += 1
        new = text[:start] + text[end:]
        if args.dry:
            print(f"  would remove {end - start} chars from {p.parent.name}/_head.html")
        else:
            p.write_bytes(new.encode("utf-8"))
        removed += 1

    verb = "would remove" if args.dry else "removed"
    print(f"\n  {verb} from {removed} file(s); {already} already without it")

    if args.dry or not removed:
        print("\nDry run / nothing changed — no rebuild.")
        return

    print("\nRebuilding pages from the staged content...")
    import subprocess
    r = subprocess.run([sys.executable, str(HERE / "content.py"), "build"],
                       cwd=str(ROOT), capture_output=True, text=True)
    for line in (r.stdout or "").strip().splitlines()[-6:]:
        print("  " + line)
    if r.returncode != 0:
        print((r.stderr or "").strip()[-600:])

    print("\nVerifying...")
    r = subprocess.run([sys.executable, str(HERE / "content.py"), "verify"],
                       cwd=str(ROOT), capture_output=True, text=True)
    print("  " + (r.stdout or "").strip().splitlines()[-1] if r.stdout else "  verify failed")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show removable chrome").set_defaults(fn=cmd_list)

    p = sub.add_parser("strip", help="remove an element from every staged head")
    p.add_argument("what", choices=sorted(TARGETS))
    p.add_argument("--dry", action="store_true", help="report what would change, write nothing")
    p.set_defaults(fn=cmd_strip)
    # allow `python tools/strip.py banner` as shorthand
    ap.epilog = "shorthand: python tools/strip.py banner [--dry]"

    argv = sys.argv[1:]
    if argv and argv[0] in TARGETS:
        argv = ["strip"] + argv
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()