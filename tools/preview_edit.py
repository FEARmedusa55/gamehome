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

  // Buttons are widgets, not paragraphs, so they get their own marker rather
  // than being offered as text. Numbering is plain document order inside each
  // section, which is the order the editor's button list uses too — no text
  // matching needed here, unlike blocks.
  //
  // Selected by tag, not by ".yaqOzd": the class on these sections carries an
  // invisible character after it, so the class selector matches nothing even
  // though the element is right there. The server's regex tolerates it; CSS
  // does not.
  [...document.querySelectorAll('section')].forEach(function (s, si) {
    [...s.querySelectorAll('div.QmpIrf')].forEach(function (w, k) {
      w.setAttribute('data-mmb-btn', si + ':' + k);
      w.style.cursor = 'pointer';
    });
    // Images get a marker too, so clicking one can offer a swap. Numbered in
    // document order, which is the order the editor lists them in.
    [...s.querySelectorAll('img')].forEach(function (im, k) {
      im.setAttribute('data-mmb-img', si + ':' + k);
      im.style.cursor = 'pointer';
    });
  });
  var buttonsTagged = document.querySelectorAll('[data-mmb-btn]').length;
  var imagesTagged = document.querySelectorAll('[data-mmb-img]').length;

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

  // What is really under the cursor at this point, top to bottom. The site's own
  // markup layers decorative elements over the original content — backgrounds,
  // hover layers — so a click can land on one of those even though it looks like
  // it is on the paragraph. That is why elements added later, which have no such
  // layers, were editable while the original ones were not.
  function targetAt(e) {
    var direct = e.target.closest && e.target.closest('[data-mmb-btn], [data-mmb-img], [data-mmb]');
    if (direct) return direct;
    var list = [];
    try {
      list = document.elementsFromPoint(e.clientX, e.clientY) || [];
    } catch (err) {
      list = [document.elementFromPoint(e.clientX, e.clientY)];
    }
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (!el || !el.closest) continue;
      var hit = el.closest('[data-mmb-btn], [data-mmb-img], [data-mmb]');
      if (hit) return hit;
    }
    return null;
  }

  function pickAt(e) {
    var el = targetAt(e);
    if (!el) return false;
    if (el.hasAttribute('data-mmb-btn')) {
      send({ mm: 'button', target: el.getAttribute('data-mmb-btn') });
      return true;
    }
    if (el.hasAttribute('data-mmb-img')) {
      send({ mm: 'image', target: el.getAttribute('data-mmb-img') });
      return true;
    }
    box = el;
    send({ mm: 'focus', target: tag(el) });
    return true;
  }

  document.addEventListener('focusin', function (e) {
    var el = e.target.closest && e.target.closest('[data-mmb]');
    if (!el) return;
    box = el;
    send({ mm: 'focus', target: tag(el) });
  }, true);

  // mousedown as well as click, so the paragraph takes the caret straight away.
  document.addEventListener('mousedown', function (e) {
    if (e.button !== 0) return;
    var el = targetAt(e);
    if (el && el.hasAttribute('data-mmb')) { box = el; }
  }, true);

  document.addEventListener('click', function (e) {
    // A button or an image first: both are widgets edited as fields, not text.
    var btn = e.target.closest && e.target.closest('[data-mmb-btn]');
    if (btn) {
      e.preventDefault();
      send({ mm: 'button', target: btn.getAttribute('data-mmb-btn') });
      return;
    }
    var im = e.target.closest && e.target.closest('[data-mmb-img]');
    if (im) {
      e.preventDefault();
      send({ mm: 'image', target: im.getAttribute('data-mmb-img') });
      return;
    }
    var el = e.target.closest && e.target.closest('[data-mmb]');
    // Never let a link navigate the preview. A navigation leaves the flagged
    // page behind — markers and this script gone — and the preview silently
    // stops being editable mid-session, which is confusing precisely because
    // nothing looks broken. Hold a modifier to open one in a new tab instead.
    // Heading anchors (#…) are let through: they only scroll.
    var a = e.target.closest && e.target.closest('a[href]');
    if (a && !(a.getAttribute('href') || '').startsWith('#')) {
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) send({ mm: 'open', href: a.getAttribute('href') });
    }
    // Fall back to what is under the point: the target may be an overlay.
    if (!el) {
      pickAt(e);
      return;
    }
    // Let links inside a paragraph keep working when you hold a modifier.
    if (e.target.closest('a[href]') && (e.metaKey || e.ctrlKey)) return;
    send({ mm: 'focus', target: tag(el) });
  }, true);

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
  }, true);

  // --- moving ---------------------------------------------------------------
  // Drag a block to put it where you want it, rather than where the grid
  // put it. The drag is previewed locally with a transform, so nothing is
  // written until you let go; then the offset from where it started is sent,
  // and the editor turns that into a position.
  var drag = null;

  document.addEventListener('mousedown', function (e) {
    var el = e.target.closest && e.target.closest('[data-mmb]');
    if (!el) return;
    if (e.button !== 0) return;
    // Alt-drag moves; a plain drag inside a paragraph still selects text.
    if (!e.altKey) return;
    e.preventDefault();
    var r = el.getBoundingClientRect();
    drag = { el: el, x0: e.clientX, y0: e.clientY, left: r.left, top: r.top };
    el.style.outline = '2px solid rgba(77,141,255,.9)';
  });

  document.addEventListener('mousemove', function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
    drag.el.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
    drag.dx = dx; drag.dy = dy;
  });

  document.addEventListener('mouseup', function () {
    if (!drag) return;
    var d = drag;
    drag = null;
    d.el.style.outline = '';
    d.el.style.transform = '';
    if (!d.dx && !d.dy) return;
    // Measure from where the drag STARTED plus how far it travelled. The
    // transform is cleared just above, so measuring the element now would
    // report its original place and throw the move away.
    var pr = d.el.offsetParent ? d.el.offsetParent.getBoundingClientRect() : { left: 0, top: 0 };
    send({ mm: 'move', target: tag(d.el),
           left: Math.round(d.left - pr.left + d.dx),
           top: Math.round(d.top - pr.top + d.dy) });
  });

  // Tell the editor how many of the expected blocks we found, so it can tell
  // a complete map from a partial one.
  send({ mm: 'ready', matched: matched, expected: TARGETS.length,
         buttons: buttonsTagged, images: imagesTagged });
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
