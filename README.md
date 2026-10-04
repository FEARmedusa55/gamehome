# mcsmmegathread.org — offline mirror

A complete local copy of `https://www.mcsmmegathread.org`, downloaded so it can
be edited by hand. **31 pages, 221 assets, 58 MB.** Verified rendering offline in
a browser — the copy matches the live site exactly on font (`Quicksand`), size
(`45.3333px`), colour and body font, with 10,763 CSS rules applied.

## View it

```bash
python -m http.server 8899     # → http://127.0.0.1:8899/
```

Or just open `index.html` directly. Both work.

## What's in here

```
index.html                        /start  — the main page
windows.html, linux.html, ...     one file per platform page
faq.html, credits.html, ...       the rest of the nav
others-*.html                     17 pages from an /others/ subtree that ISN'T
                                  linked in the nav (see below)

assets/images/                    every image, sprite and icon on the site
assets/external/                  Google's CSS + JS bundles
assets/fonts/                     webfonts (Roboto, etc.)

_pristine-html/                   untouched copies, saved before the span merge
_mirror.py                        the downloader — re-run to refresh the mirror
make_editable.py                  merges fragmented <span>s (see "Editing")
fix_asset_types.py                renames mis-typed files (see "Gotchas")
localise_css_assets.py            downloads assets referenced from inside CSS
```

## Scripts, in the order they were run

```bash
python _mirror.py               # download 31 pages + assets, rewrite links
python make_editable.py         # merge fragmented spans (do this before editing)
python fix_asset_types.py       # fix extensions — IMPORTANT, see below
python localise_css_assets.py   # pull webfonts/sprites referenced by the CSS
```

**Run `fix_asset_types.py` before `localise_css_assets.py`** — the second one only
processes files ending in `.css`, which don't exist until the first has run.

### Gotchas hit while building this

Google's servers return a generic `Content-Type` for many assets, so the
downloader saved **142 files as `.bin`**. Images still worked offline — browsers
sniff content inside an `<img>` — but **CSS is not given that leniency**: a
stylesheet served as `application/octet-stream` is silently ignored. The copy
looked largely fine while being completely unstyled, and `body` fell back to
`"Times New Roman"` instead of `"sans-serif"`.

Because every stylesheet was named `.bin` at download time, the
font-localising step inside `_mirror.py` never matched, leaving 240 `url(...)`
references pointing at live Google servers and **zero fonts downloaded**.
`localise_css_assets.py` recovered them: 77 unique assets, all fetched, 0
failures.

Both problems are invisible unless you actually compare computed styles against
the live page, which is why the check above compares them directly.

## Editing

This is a faithful Google Sites capture, so it is **editable but dense**. Each
page is 150–240 KB, of which the vast majority is Google Sites boilerplate; the
prose you actually care about is a small fraction.

**Verified workflow.** Find a distinctive phrase and edit it in place. I tested
this end to end: changing the `<h1>` text in `index.html` and reloading showed
the new text immediately, and the old text was gone.

### The one gotcha

Google Sites **splits a single line of text across multiple `<span>`s at
arbitrary points**:

```html
<span class="Rn3Z1b C9DxTc" style="color:#ffffff; font-family:Lexend, Arial; font-weight:700">Welcome to t</span>
<span class="Rn3Z1b C9DxTc" style="color:#ffffff; font-family:Lexend, Arial; font-weight:700">he Minecraft: Story Mode Megathread!</span>
```

So searching for a sentence will often fail, because the sentence isn't
contiguous in the source. `make_editable.py` merges adjacent spans that carry
identical attributes — a visual no-op that collapses a lot of this:

```bash
python make_editable.py     # already run once; 87 spans merged across 31 pages
```

It saves pristine copies to `_pristine-html/` first, so you can always
`diff -u _pristine-html/index.html index.html` to see what it did, or restore.

If you edit a heading and it still looks split, merge the remaining two spans by
hand — the attributes are identical, so you can delete the `</span><span ...>`
boundary between them.

### Practical tips

- **Changing text:** easy. Find it, edit it, reload.
- **Removing a whole section:** works, but delete the *entire* enclosing
  `<div>`/`<section>` block, not just the text — otherwise you leave an empty
  container that can collapse your spacing.
- **Adding new content:** copy an existing block that looks like what you want
  and rewrite its text. Keep the `<div class="...">` wrappers and inline styles,
  or it won't match the layout.
- **Removing a page:** delete the `.html` file, then remove its `<a href=...>`
  from the nav — the nav is duplicated into **every** one of the 31 files.
- **Don't run `_mirror.py` again** unless you want to overwrite your edits; it
  re-downloads from the live site and clobbers local files.

### What won't work offline

- YouTube embeds (7 of them) and one Google Docs preview still point at
  `youtube.com` / `docs.google.com` — they need a live connection.
- Google Sites' own JS is localised, but anything that calls back to Google's
  servers (search, the "Embedded Files" viewer) will fail.

## Editing workflow (staged)

Rather than hand-editing 126KB of Google Sites boilerplate per page, each page
is split into small files under `content/`:

```
content/index/
  _head.html                                 126KB — boilerplate, don't touch
  00-welcome-to-the-minecraft-story.html
  01-h-2d7941ccbfa53edd-0.html
  ...
  15-how-each-version-plays-pcmac.html
  _tail.html                                 ~4.5KB — boilerplate
  manifest.json                              order + source hash
```

```bash
# split a page into editable pieces
python tools/content.py extract index.html

# confirm the split is lossless (rebuild must match the page byte-for-byte)
python tools/content.py verify index.html

# rebuild after editing
python tools/content.py build index.html

# remove a section: file goes to _trash/, entry leaves the manifest
python tools/content.py drop index.html 08-vpns
```

**Editing is then file operations:**

| I want to… | Do this |
|---|---|
| Change text | Edit the numbered `.html` file, then `build` |
| Remove a section | `drop <page> <file>` — or just delete the file and rebuild |
| Reorder sections | Rename the files so the numeric prefixes sort differently (`02-` ↔ `05-`) and reorder the manifest, or edit `manifest.json` |
| Duplicate a section | Copy the file to a new number and add it to the manifest |
| Add a brand-new section | Copy `_template.html` from the static build, or clone an existing section and rewrite it |

`verify` is the safety net: if it reports OK, `head + sections + tail` reproduces
the page byte-for-byte, so nothing was lost in the split.

**Do not edit `_head.html` or `_tail.html`** unless you know what you're doing —
that's the Google Sites scaffold (per-page CSS, the nav, the header). Content
lives in the numbered files.

### Verified

- Plain rebuild is a byte-exact no-op: sha1 `6a84220f041c` before and after.
- Dropping a section shrank the page 222,061 → 219,023 bytes and removed it from
  the rendered DOM.
- Restoring it returned the page to the *identical* sha1.
- All tooling reads/writes raw bytes, so line endings are never rewritten.

### A note on line endings

`index.html` was normalised from CRLF to LF by an early version of the tooling
(read_text applies universal-newline translation). It renders identically and
HTML is line-ending agnostic, but it's why that one file differs from the
`_pristine-html/` copy in newline terms. The tools now use `read_bytes()`
specifically to avoid this.



## The editor

```bash
python tools/editor.py
```

Opens a local page in your browser. Nothing to install (Python standard library
only). Three panes:

- **Left** — categories (platforms) and pages. Remove a category with `−`, add
  one with the box at the bottom.
- **Middle** — a page's sections, each with its text as editable fields, plus
  move up/down, Duplicate and Delete.
- **Right** — live preview of the real page.

Edit text, then **Save section**. That writes only the text; the styling is
untouched. `Open site` opens the site itself in a new tab.

### Why it can't change the design

The design lives in files the editor never writes to: the four Google Sites CSS
bundles (`assets/external/*.css`) and each page's boilerplate (`_head.html`,
`_tail.html`). The editor only rewrites the numbered content files under
`content/`. So the worst a text edit can do is change the words.

### What makes text editable at all

Google Sites splits one sentence across several spans, so a find-and-replace
misses it. Two steps fix that:

1. **Merge** — adjacent spans that render identically collapse into one. Only
   the text becomes contiguous; nothing changes on screen.
2. **Replace** — the merged run's text is swapped, keeping the element and its
   inline styling.

Two subtle bugs this had to get right, both found by testing rather than
inspection:

- **No-op style properties.** Two spans that look identical can carry different
  style strings — one has `font-variant: normal`, the other doesn't. Comparing
  style strings literally leaves them split, so the merge compares a *normalised*
  key (`style_key`) with no-op declarations removed.
- **Pairwise matching skips boundaries.** A regex matching adjacent span pairs
  consumes each span into one pair, so the boundary between two pairs is never
  compared. With spans `C,D,E,F` where `D` and `E` match but `C/D` and `E/F`
  don't, both pairs fail and `D+E` silently stay split. The merge now collects
  every span and merges maximal runs of genuinely adjacent spans.

### Adding and reordering

| I want to… | How |
|---|---|
| Add a page | Type a name in **New page name** at the bottom of the sidebar. It gets a nav entry, a blank section, and appears in the nav everywhere. |
| Add a platform category | Same, via **New platform name** under Categories. |
| Add a blank section | **+ New blank section** at the top of a page. Uses `tools/templates/heading-and-text.html` — a pre-styled heading + paragraph you fill in. |
| Add a section like an existing one | **Duplicate** on any section. The copy lands directly beneath its source. |
| Reorder pages | The **↑ ↓** arrows beside each page. This reorders the site's nav too — rewritten in every page and every staged `_head.html`. |
| Undo | The **Undo** button. Covers text edits, section add/remove/reorder, page and category changes. Eight steps kept. |

Regenerate the blank-section template with `python tools/make_templates.py` if you
break it.

### Bugs worth knowing about (all caught by testing, not by reading)

**Duplicate silently did nothing.** A local `const clone` shadowed the global
`clone()` function inside its own click handler, so every click threw
`clone is not a function`.

**Undo deleted 30 pages.** Undo removes any page absent from its snapshot, so
undoing "add page" doesn't leave an orphan. But snapshots taken before
`scope.txt` existed defaulted to site-wide — and a *page-scoped* snapshot's
`recorded` set holds one file, so every other page looked like an orphan. Scope
is now never guessed. All pages were recovered from `_trash/`.

**Stale servers served old code.** `ThreadingHTTPServer` sets `SO_REUSEADDR`,
which on Windows lets a second server bind a port already in use — so a stale
instance kept answering while the new one idled. The editor now refuses to start
on a busy port and says so.

**A rejected action burned an undo slot** (it snapshotted before validating).
Failed actions now discard their snapshot.

### Verified

- Merging across all 31 pages: **text identical on every page**, 195 spans merged.
- Per-span style fingerprints (colour, size, weight, family, decoration) are
  unchanged by the merge — the only difference is fragments joining up.
- A text edit renders with identical computed styling, and reverting returns the
  page to its exact previous sha1.


## Heads-up on the `/others/` pages

The downloader followed internal links and found a subtree the nav never shows —
17 extra pages under `/others/`, including:

```
others-windows-steamrip.html
others-windows-fitgirl-repacks.html
others-windows-s1-install-instructions.html
others-windows-s2-install-instructions.html
others-windows-megathread-installers.html
others-android-legacy-downloads.html
...plus 11 more
```

Those are the pirated-download and installation pages — SteamRip and FitGirl are
known repack distributors. They came along because a mirror follows links; they're
yours to keep or delete. If you're repurposing this as a general template you'll
probably want to drop them.

## Deploying

Any static host takes it as-is: GitHub Pages, Netlify, Cloudflare Pages, Vercel.
No build step. Publish directory `.`, no build command.

## How it was fetched

`_mirror.py` uses only the Python standard library:

1. Seeds the 18 nav pages, then crawls internal links (that's how the `/others/`
   pages were discovered).
2. Rewrites every internal link and asset URL to a local relative path.
3. Downloads assets from `gstatic.com`, `googleusercontent.com`,
   `fonts.googleapis.com` etc. — including `url(...)` references *inside* the
   downloaded CSS, so fonts and background images come along.
4. Retries failed requests 3× with backoff. **Zero failures on this run.**

Result: 0 leftover absolute internal links, 0 root-relative links. The only
remaining remote references are the 7 YouTube/Docs embeds noted above.
