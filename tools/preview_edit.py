"""Mark the editable blocks of a served page so the preview can be edited.

The preview shows the real page. To edit it in place, the editor has to know
which staged section and block any given element came from.

The first attempt worked this out arithmetically — scanning the section, listing
its blocks, and walking the two in step. That was wrong: one section's blocks()
ids came back as 1, 11, 13, 12, 14, 15, 16, 10, which is not document order,
while the editor numbers its cards by group_runs order. The two disagree, so
every index shifted and an edit would have saved onto a neighbouring paragraph.

So nothing here counts anything. The server writes down, for each block it
offers, the id it wants ("section:block") and the text it will show. The script
in the page walks the real DOM, finds the element whose text matches, and tags
it with that id. Matching on text survives markup differences, span merging and
reordering; arithmetic does not.

Nothing is written to disk: this runs at serve time only, for requests carrying
the edit flag.
"""

from __future__ import annotations

import json
import re


def targets(section_info, page_folder, names) -> list[dict]:
    """The blocks the editor offers, as [{t: "si:bi", text: "..."}].

    `t` is the id the editor will see; `text` is what the page script matches
    against. The order is the order the editor lists its cards in, which is
    section_info's block order — the same order group_runs produced.
    """
    out = []
    for si, name in enumerate(names):
        info = section_info(page_folder, name)
        runs = info.get("runs") or []
        bi = 0
        for block in info.get("blocks") or []:
            idxs = block.get("runs") or []
            # Read-only text (a button label, say) is not offered as a card.
            if not any((runs[i] or {}).get("editable") is not False for i in idxs):
                continue
            # Concatenate, never join: highlighting splits a block mid-word, and
            # joining would insert a space ("uB lock" instead of "uBlock").
            # Newlines come from <br>, which contribute nothing to the DOM's
            # textContent, so they are dropped rather than turned into spaces —
            # that keeps this equal to what the page script sees.
            text = "".join((runs[i] or {}).get("raw", "") for i in idxs)
            text = re.sub(r"\s+", " ", text.replace("\r", "").replace("\n", "")).strip()
            out.append({"t": f"{si}:{bi}", "text": text})
            bi += 1
    return out


INJECT = """
<style id="mm-edit-css">
  [data-mmb]{outline:1px dashed transparent;outline-offset:2px;transition:outline-color .12s}
  [data-mmb]:hover{outline-color:rgba(77,141,255,.55)}
  [data-mmb]:focus,[data-mmb]:focus-within{outline:2px solid rgba(77,141,255,.9);outline-offset:2px}
  [contenteditable="true"]{cursor:text}
</style>
<script id="mm-edit-js">
(function () {
  var TARGETS = __TARGETS__;
  var TAGS = 'p,h1,h2,h3,h4,h5,h6,li,blockquote,td,div';

  function norm(s) { return (s || '').replace(/[\\r\\n]+/g, '').replace(/\\s+/g, ' ').trim(); }
  function send(msg) { try { parent.postMessage(msg, '*'); } catch (e) {} }

  // Walk the real DOM and tag whatever matches a target by text. Deepest first:
  // a wrapper div and the paragraph inside it carry the same text, and the
  // paragraph is the one worth editing.
  var candidates = [];
  document.querySelectorAll(TAGS).forEach(function (el) {
    // Text inside a filled button is a widget, not a paragraph; rewriting it
    // would flatten the button.
    if (el.closest('.QmpIrf, .U26fgb, a.FKF6mc')) return;
    var txt = norm(el.textContent);
    if (!txt) return;
    var depth = 0, p = el;
    while ((p = p.parentElement)) depth++;
    candidates.push({ el: el, txt: txt, depth: depth });
  });
  candidates.sort(function (a, b) { return b.depth - a.depth; });

  var used = {};
  var matched = 0;
  candidates.forEach(function (c) {
    for (var i = 0; i < TARGETS.length; i++) {
      var t = TARGETS[i];
      if (used[t.t]) continue;
      if (norm(t.text) === c.txt) {
        used[t.t] = 1;
        matched++;
        c.el.setAttribute('data-mmb', t.t);
        c.el.setAttribute('contenteditable', 'true');
        return;
      }
    }
  });

  var box = null;
  var dirty = false;

  function tag(el) { return el && el.getAttribute ? el.getAttribute('data-mmb') : null; }

  // Same notion of position as the editor's nodeText: walk text nodes and count.
  function offsetOf(root, node, off) {
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), at = 0, n;
    while ((n = w.nextNode())) {
      if (n === node) return at + off;
      at += n.nodeValue.length;
    }
    return at;
  }

  // The block's own markup, with our attributes stripped, so the editor reads
  // back exactly what it wrote.
  function currentHtml(el) {
    var clone = el.cloneNode(true);
    clone.querySelectorAll('[data-mmb]').forEach(function (n) {
      n.removeAttribute('data-mmb');
      n.removeAttribute('contenteditable');
    });
    return clone.innerHTML;
  }

  document.addEventListener('focusin', function (e) {
    var el = e.target.closest && e.target.closest('[data-mmb]');
    if (!el) return;
    box = el;
    send({ mm: 'focus', target: tag(el) });
  });

  document.addEventListener('click', function (e) {
    var el = e.target.closest && e.target.closest('[data-mmb]');
    if (!el) return;
    // Let links inside a paragraph keep working when you hold a modifier.
    if (e.target.closest('a[href]') && (e.metaKey || e.ctrlKey)) return;
    send({ mm: 'focus', target: tag(el) });
  });

  document.addEventListener('input', function (e) {
    var el = e.target.closest && e.target.closest('[data-mmb]');
    if (el) dirty = true;
  });

  // Report the words you have selected, so the bar styles those and not the
  // whole paragraph.
  var selTimer = null;
  document.addEventListener('selectionchange', function () {
    if (!box) return;
    clearTimeout(selTimer);
    selTimer = setTimeout(function () {
      var s = document.getSelection();
      if (!s || !s.rangeCount) return;
      var r = s.getRangeAt(0);
      if (r.collapsed) return;
      var start = offsetOf(box, r.startContainer, r.startOffset);
      var end = offsetOf(box, r.endContainer, r.endOffset);
      if (end <= start) return;
      send({ mm: 'sel', target: tag(box), start: start, end: end });
    }, 120);
  });

  document.addEventListener('focusout', function (e) {
    var el = e.target.closest && e.target.closest('[data-mmb]');
    if (!el || el !== box) return;
    if (!dirty) return;          // nothing typed, nothing to save
    dirty = false;
    send({ mm: 'change', target: tag(el), html: currentHtml(el) });
  });

  // Report how many of the expected blocks we actually found, so the editor can
  // tell a complete map from a partial one.
  send({ mm: 'ready', matched: matched, expected: TARGETS.length });
})();
</script>
"""


def inject(page_html: str, targets_list: list[dict]) -> str:
    """Append the marker script, carrying the target list as JSON."""
    payload = json.dumps(targets_list, separators=(",", ":"))
    block = INJECT.replace("__TARGETS__", payload)
    if "</body>" in page_html:
        return page_html.replace("</body>", block + "</body>", 1)
    return page_html + block
