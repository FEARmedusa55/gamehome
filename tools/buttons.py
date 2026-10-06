"""Read and rewrite the filled link buttons inside a page section.

The site's buttons look like this, repeated once per row:

    <div role="presentation" class="U26fgb L7IXhc htnAL QmpIrf M9Bg4d"
         jscontroller="..." aria-label="FAQ" data-tooltip="FAQ" ...>
      <a class="FKF6mc TpQm9d QmpIrf" href="faq.html" aria-label="FAQ">
        <div class="NsaAfc">FAQ</div>
        <div class="wvnY3c" jsname="ksKsZd"></div>
      </a>
    </div>

Editing one means changing three things that must agree: the label div, the
link, and the aria-label / tooltip on both wrappers.

Reordering SWAPS whole widgets rather than rewriting the markup between them.
The buttons may sit in different grid cells, so replacing the span from the
first to the last would swallow the cell markup in between and wreck the row.
"""

from __future__ import annotations

import re

# The filled-button marker. `htnAL` sets the height and `QmpIrf` the colour, so
# a widget carrying QmpIrf is one of these buttons.
WIDGET = re.compile(r'<div\b[^>]*class="[^"]*\bQmpIrf\b[^"]*"[^>]*>', re.I)
LABEL = re.compile(r'<div\b[^>]*class="[^"]*\bNsaAfc\b[^"]*"[^>]*>(.*?)</div>', re.S)
HREF = re.compile(r'(<a\b[^>]*?)\bhref="([^"]*)"', re.I)
ARIA = re.compile(r'\saria-label="[^"]*"', re.I)
TOOLTIP = re.compile(r'\sdata-tooltip="[^"]*"', re.I)
_ANY_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>")


def _widget_spans(html: str) -> list[tuple[int, int]]:
    """Span of each button widget, by balanced tag matching."""
    spans = []
    for m in WIDGET.finditer(html):
        tag_start = m.start()
        depth = 0
        i = tag_start
        while i < len(html):
            t = _ANY_TAG.match(html, i)
            if t:
                closing, name, selfc = t.group(1), t.group(2).lower(), t.group(3)
                if name == "div" and not selfc:
                    depth += 1 if not closing else -1
                    if depth == 0:
                        spans.append((tag_start, t.end()))
                        break
                i = t.end()
                continue
            nxt = html.find("<", i + 1)
            if nxt == -1:
                break
            i = nxt
    return spans


def _prop(css: str, name: str) -> str:
    m = re.search(r"(?:^|;)\s*" + re.escape(name) + r"\s*:\s*([^;\"]+)", css or "")
    return m.group(1).strip() if m else ""


def read_buttons(html: str) -> list[dict]:
    """[{i, label, href, background, colour, size}] for each button, in order.

    The styling comes back too, so a round trip through this list — which is how
    a label edit is saved — does not quietly strip a button's colour or size.
    """
    out = []
    for i, (start, end) in enumerate(_widget_spans(html)):
        widget = html[start:end]
        lab = LABEL.search(widget)
        href = HREF.search(widget)
        style = re.search(r'style="([^"]*)"', widget[:400])
        css = style.group(1) if style else ""
        out.append({
            "i": i,
            "label": re.sub(r"<[^>]+>", "", lab.group(1)).strip() if lab else "",
            "href": href.group(2) if href else "",
            "background": _prop(css, "background-color"),
            "colour": _prop(css, "color"),
            "size": _prop(css, "font-size"),
        })
    return out


def _relabel(widget: str, label: str) -> str:
    """Set the label, and keep both aria-labels in step for screen readers."""
    from html import escape
    safe = escape(label, quote=True)
    if LABEL.search(widget):
        widget = LABEL.sub(lambda m: m.group(0).replace(m.group(1), safe), widget, count=1)
    widget = ARIA.sub(f' aria-label="{safe}"', widget)
    if TOOLTIP.search(widget):
        widget = TOOLTIP.sub(f' data-tooltip="{safe}"', widget, count=1)
    return widget


def _relink(widget: str, href: str) -> str:
    from html import escape
    safe = escape(href or "", quote=True)
    if HREF.search(widget):
        return HREF.sub(lambda m: m.group(1) + f'href="{safe}"', widget, count=1)
    return widget


def _restyle(widget: str, background: str, colour: str, size: str) -> str:
    """Override the QmpIrf class with an inline style, or clear it again."""
    bits = []
    if background:
        bits.append(f"background-color: {background}")
        bits.append(f"border-color: {background}")
    if colour:
        bits.append(f"color: {colour}")
    if size:
        bits.append(f"font-size: {size}")
    style = "; ".join(bits) + ";" if bits else ""

    # drop an existing inline style on the OUTER tag only, then re-add
    first = WIDGET.search(widget)
    if not first:
        return widget
    tag = first.group(0)
    tag_clean = re.sub(r'\s*style="[^"]*"', "", tag)
    if style:
        tag_clean = tag_clean[:-1].rstrip() + f' style="{style}">'
    return tag_clean + widget[first.end():]


def write_buttons(html: str, items: list[dict]) -> str:
    """Rewrite the buttons to match `items` (label / href / per-button style).

    Fewer items than buttons removes the extras; more clones the last one.
    Widgets are replaced in place, so siblings and grid cells are untouched.
    """
    spans = _widget_spans(html)
    if not spans:
        return html

    originals = [html[s:e] for s, e in spans]
    template = originals[0]

    built = []
    for item in items:
        base = template
        base = _relabel(base, str(item.get("label") or ""))
        base = _relink(base, str(item.get("href") or ""))
        base = _restyle(base, str(item.get("background") or ""),
                        str(item.get("colour") or ""), str(item.get("size") or ""))
        built.append(base)

    # Replace back to front so earlier offsets stay valid. Positions past the
    # end of `items` are simply dropped, which is how removal works.
    out = html
    for i in range(len(spans) - 1, -1, -1):
        start, end = spans[i]
        replacement = built[i] if i < len(built) else ""
        out = out[:start] + replacement + out[end:]

    # More items than buttons: append the surplus right after the last one.
    if len(built) > len(spans):
        last_end = None
        for start, end in _widget_spans(out):
            last_end = end
        if last_end is not None:
            extra = "".join(built[len(spans):])
            out = out[:last_end] + extra + out[last_end:]
    return out
