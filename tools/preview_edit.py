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

SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.S | re.I)


def strip_scripts(page_html: str) -> str:
    """Remove the page's own scripts from an edit-mode preview.

    Google Sites' bundle re-renders the sections it knows about from an internal
    model, replacing those DOM nodes — which wipes the markers and the
    contenteditable we set on them. That is why elements that were already on the
    page could not be edited while newly added ones could: the added ones are not
    in that model and are left alone. In Firefox the bundle does not even get
    that far, throwing "_._DumpException is not a function", and the page
    re-renders anyway.

    The mirror's pages are static HTML and every bit of the styling is CSS, so
    removing these scripts changes nothing you can see. Scripts we add ourselves
    (ids beginning "mm-") are kept.
    """
    def keep(match: "re.Match") -> str:
        head = match.group(0)[:300]
        return match.group(0) if 'id="mm-' in head else ""

    return SCRIPT_RE.sub(keep, page_html)


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
  /* Faintly outlined always, stronger on hover and focus. Invisible-until-hover
     made a marked page and an unmarked one look identical, which is exactly the
     wrong property for something that keeps needing to be diagnosed. */
  [data-mmb]{
    outline: 1px dashed rgba(77,141,255,.35) !important;
    outline-offset: 2px;
    transition: outline-color .12s;
  }
  [data-mmb]:hover{outline-color: rgba(77,141,255,.95) !important}
  [data-mmb]:focus,[data-mmb]:focus-within{
    outline: 2px solid rgba(77,141,255,.95) !important;
    outline-offset: 2px;
  }
  [contenteditable="true"]{cursor:text}
  /* Google's stylesheet turns pointer events off on some headings, so the
     mouse went straight through them: the hover helper said "nothing marked"
     and the heading could not be clicked into. Edit mode only. */
  [data-mmb], [data-mmb] *{pointer-events:auto !important}
  /* An empty section — a spacer — has nothing in it to aim at, and can be only
     a few pixels tall. Outline it in a different colour from the blocks, so it
     is visible and obviously clickable rather than invisible furniture. */
  [data-mmb-sec]{
    outline: 1px dashed rgba(255,193,102,.5) !important;
    outline-offset: -1px;
    cursor: pointer;
  }
  [data-mmb-sec]:hover{outline-color: rgba(255,193,102,.95) !important}
</style>
<script id="mm-edit-js">
(function () {
  var TARGETS = __TARGETS__;
  var TAGS = 'p,h1,h2,h3,h4,h5,h6,li,blockquote,td,div';

  function norm(s) { return (s || '').replace(/[\\r\\n]+/g, '').replace(/\\s+/g, ' ').trim(); }
  // Whitespace removed entirely, for the fallback pass below.
  function squash(s) { return norm(s).replace(/\\s+/g, ''); }
  function send(msg) { try { parent.postMessage(msg, '*'); } catch (e) {} }

  // Tagging, as a function so it can be run again later. Google Sites re-renders
  // its own sections from its internal model, which replaces those DOM nodes and
  // takes the markers and contenteditable with them — while sections added later
  // are not in that model and survive. That is exactly why made elements stayed
  // editable and original ones did not.
  //
  // Idempotent: tagging an element that is already tagged changes nothing, so
  // the editor can call this freely whenever it sees markers go missing.
  function retag() {
    // Walk the real DOM and tag whatever matches a target by text, in
    // document order — the order the targets are listed in — so that two
    // blocks with the same words ("Homebrew", twice) each get their own
    // marker rather than swapping places.
    var all = [];
    var byText = {};
    document.querySelectorAll(TAGS).forEach(function (el) {
      // Text inside a filled button is a widget, not a paragraph; rewriting it
      // would flatten the button.
      if (el.closest('.QmpIrf, .U26fgb, a.FKF6mc')) return;
      var txt = norm(el.textContent);
      if (!txt) return;
      var c = { el: el, txt: txt };
      all.push(c);
      (byText[txt] = byText[txt] || []).push(c);
    });
    // A wrapper div and the paragraph inside it carry the same text; the
    // paragraph is the one worth editing, so the wrapper is dropped.
    var candidates = all.filter(function (c) {
      return !byText[c.txt].some(function (d) { return d !== c && c.el.contains(d.el); });
    });

    var used = {};
    var matched = 0;
    // Two passes: an exact normalized match first, then — only if that found
    // nothing — a comparison with all whitespace removed.
    //
    // The server reads a block's text out of the markup, and that can drop a
    // space the rendered text keeps: "(only Save the light was virus checked by
    // hand)" against " (only Save the light…". A one-character difference is
    // all it took for a block never to be tagged, and the original content is
    // full of these while cleanly authored blocks have none — which is exactly
    // why blocks that were already on the page could not be edited while newly
    // added ones could. Exact matches are tried first so a looser comparison
    // can never take an element an exact one would have claimed.
    var passes = [
      function (a, b) { return norm(a) === norm(b); },
      function (a, b) { return squash(a) === squash(b); }
    ];
    var taken = [];
    for (var pi = 0; pi < passes.length; pi++) {
      for (var i = 0; i < TARGETS.length; i++) {
        var t = TARGETS[i];
        if (used[t.t]) continue;
        for (var ci = 0; ci < candidates.length; ci++) {
          var c = candidates[ci];
          if (taken[ci] || !passes[pi](t.text, c.txt)) continue;
          taken[ci] = true;
          used[t.t] = 1;
          matched++;
          c.el.setAttribute('data-mmb', t.t);
          c.el.setAttribute('contenteditable', 'true');
          break;
        }
      }
    }

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

    // Sections with nothing marked in them — a spacer, an empty divider — have
    // no text to click, so they could not be selected at all and there was no
    // way to move or delete them. Mark the section itself so a click anywhere
    // in it selects it.
    var secs = document.querySelectorAll('section');
    for (var si2 = 0; si2 < secs.length; si2++) {
      var s = secs[si2];
      if (s.querySelector('[data-mmb], [data-mmb-btn], [data-mmb-img]')) {
        s.removeAttribute('data-mmb-sec');
      } else {
        s.setAttribute('data-mmb-sec', String(si2));
        s.style.cursor = 'pointer';
      }
    }

    return {
      matched: document.querySelectorAll('[data-mmb]').length,
      buttons: document.querySelectorAll('[data-mmb-btn]').length,
      images: document.querySelectorAll('[data-mmb-img]').length,
      sections: document.querySelectorAll('[data-mmb-sec]').length,
    };
  }

  // The editor calls this when it notices the markers have gone.
  window.__mmRetag = retag;

  var counts = retag();
  var matched = counts.matched;
  var buttonsTagged = counts.buttons;
  var imagesTagged = counts.images;

  var box = null;
  var dirty = false;

  function tag(el) { return el && el.getAttribute ? el.getAttribute('data-mmb') : null; }

  // Same notion of position as the editor's nodeText: text counts its
  // characters and a <br> counts as one. Counting text nodes alone put every
  // selection after a line break one letter early per break.
  function textLen(node) {
    var n = 0;
    node.childNodes.forEach(function (c) {
      if (c.nodeType === 3) n += c.nodeValue.length;
      else if (c.nodeName === 'BR') n += 1;
      else n += textLen(c);
    });
    return n;
  }
  function offsetOf(root, node, off) {
    var r = document.createRange();
    r.selectNodeContents(root);
    try { r.setEnd(node, off); } catch (e) { return textLen(root); }
    return textLen(r.cloneContents());
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
    if (!el) { diag('click at ' + e.clientX + ',' + e.clientY + ' \u2192 nothing marked'); return false; }
    if (el.hasAttribute('data-mmb-btn')) {
      diag('click \u2192 button [' + el.getAttribute('data-mmb-btn') + ']');
      send({ mm: 'button', target: el.getAttribute('data-mmb-btn') });
      return true;
    }
    if (el.hasAttribute('data-mmb-img')) {
      diag('click \u2192 image [' + el.getAttribute('data-mmb-img') + ']');
      send({ mm: 'image', target: el.getAttribute('data-mmb-img') });
      return true;
    }
    box = el;
    diag('click \u2192 ' + el.tagName + ' [' + tag(el) + ']');
    send({ mm: 'focus', target: tag(el) });
    return true;
  }

  // A readout inside the page itself. Diagnosing this from the outside has
  // failed repeatedly: the browser console cannot be seen from here, and any
  // status line in the editor depends on messages getting out of the frame.
  // This proves the script ran at all, and says what it is doing as you move
  // and click, in the one place the person is already looking.
  var dbg = document.createElement('div');
  dbg.id = 'mm-dbg';
  dbg.setAttribute('aria-hidden', 'true');
  dbg.style.cssText =
    'position:fixed;left:6px;bottom:6px;z-index:2147483647;' +
    'background:rgba(12,15,20,.92);color:#9fd0ff;' +
    'border:1px solid rgba(77,141,255,.55);border-radius:4px;' +
    'font:11px/1.45 ui-monospace,Consolas,monospace;padding:3px 7px;' +
    'pointer-events:none;max-width:70vw;white-space:pre-wrap';
  (document.body || document.documentElement).appendChild(dbg);

  function diag(line) { dbg.textContent = 'mm: ' + line; }
  diag('script alive');

  // A highlight that cannot be covered. The CSS outline on [data-mmb] is not
  // enough on its own — Google Sites stacks its sections, and a later one paints
  // over an earlier block's outline, so a marked paragraph can look unmarked.
  // This box is fixed-position in the page's own coordinate space with the
  // largest z-index there is, so it always paints.
  var hi = document.createElement('div');
  hi.id = 'mm-hi';
  hi.setAttribute('aria-hidden', 'true');
  hi.style.cssText =
    'position:fixed;pointer-events:none;z-index:2147483647;display:none;' +
    'border:2px solid rgba(77,141,255,.95);background:rgba(77,141,255,.10);' +
    'border-radius:2px;box-sizing:border-box';
  (document.body || document.documentElement).appendChild(hi);

  function highlight(el) {
    if (!el) { hi.style.display = 'none'; return; }
    var r = el.getBoundingClientRect();
    hi.style.display = 'block';
    hi.style.left = (r.left - 2) + 'px';
    hi.style.top = (r.top - 2) + 'px';
    hi.style.width = Math.max(0, r.width) + 'px';
    hi.style.height = Math.max(0, r.height) + 'px';
  }

  var hiTimer = null;
  document.addEventListener('mousemove', function (e) {
    clearTimeout(hiTimer);
    var x = e.clientX, y = e.clientY, t = e.target;
    hiTimer = setTimeout(function () {
      var hit = targetAt({ target: t, clientX: x, clientY: y });
      highlight(hit);
      diag('move ' + x + ',' + y + ' \u2192 ' +
           (hit ? hit.tagName + ' [' + (hit.getAttribute('data-mmb') || 'widget') + ']' : 'nothing marked'));
    }, 40);
  }, true);
  document.addEventListener('mouseleave', function () { highlight(null); }, true);
  document.addEventListener('scroll', function () { highlight(box); }, true);

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
    // An empty section — a spacer — has no blocks to click, so selecting the
    // section itself is the only way to move or delete it.
    var emptySec = e.target.closest && e.target.closest('[data-mmb-sec]');
    if (emptySec && !(e.target.closest && e.target.closest('[data-mmb]'))) {
      e.preventDefault();
      diag('click \u2192 empty section [' + emptySec.getAttribute('data-mmb-sec') + ']');
      send({ mm: 'section', target: emptySec.getAttribute('data-mmb-sec') });
      return;
    }
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
  diag('marked ' + matched + ' of ' + TARGETS.length + ' blocks \u00b7 buttons ' +
       buttonsTagged + ' \u00b7 images ' + imagesTagged);
})();
</script>
"""


def _payload(targets_list: list[dict]) -> str:
    """The target list as JSON that is safe inside a <script>.

    A paragraph containing "</script>" would otherwise end the script early
    and leave the preview uneditable.
    """
    return json.dumps(targets_list, separators=(",", ":")).replace("</", "<\\/")


def inject(page_html: str, targets_list: list[dict]) -> str:
    """Append the marker script, carrying the target list as JSON."""
    payload = _payload(targets_list)
    block = INJECT.replace("__TARGETS__", payload)
    if "</body>" in page_html:
        return page_html.replace("</body>", block + "</body>", 1)
    return page_html + block


def parts(targets_list: list[dict]) -> dict:
    """The injected style and script separately, for the editor to inject itself.

    Serving the script inside the page is not enough on its own: the page's own
    scripts can replace the document after it loads, which wipes anything that
    was in that markup. That is what happens here in Firefox, where the site's
    own bundle throws (_._DumpException is not a function) and the page
    re-renders — the markers show for a frame and then are gone.

    The editor frame's own JavaScript does run, so it can put these in the frame
    after the page settles, and put them back if the page replaces itself again.
    """
    payload = _payload(targets_list)
    cut = INJECT.find("<script")
    style, script = INJECT[:cut], INJECT[cut:]
    # Strip the wrapper tags: the editor sets these as textContent on elements it
    # creates, so leaving "<script …>" in front would make the JavaScript
    # invalid. The tags are only needed when serving them inside a page.
    style = style[style.find(">") + 1:style.rfind("</style>")]
    script = script[script.find(">") + 1:script.rfind("</script>")]
    return {
        "style": style.replace("__TARGETS__", payload),
        "script": script.replace("__TARGETS__", payload),
    }
