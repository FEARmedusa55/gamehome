# FEAR's Megathread — wiki.fearsbluenova.com

A static site, published with GitHub Pages from this repo's root. It started
life as an offline copy of a Google Sites page, so the design is Google's CSS
and markup; the content is edited with a small local editor.

## Edit the site

```bash
python tools/editor.py          # opens http://127.0.0.1:8765/ in your browser
```

Python 3.10+, standard library only — nothing to install.

- **Left** — the site's pages and nav groups. `+` adds a page under a group,
  `✎` renames, `↑ ↓` reorders (the nav on every page follows), `−` removes.
- **Middle** — the open page's sections, each paragraph as an editable box.
  Edits save on their own a moment after you stop typing.
- **Right** — the real page. Click a paragraph to type in it, a button to edit
  its label and link, an image to swap it. Alt-drag moves a block freely (see
  the note below).
- **Undo** — reverts the last change. 30 steps are kept.

When you are happy, commit and push — GitHub Pages publishes what is on `main`.

> **Free positioning (Alt-drag)** pins a block at a fixed pixel spot. It can
> overlap other text on phones, so check the page at phone width afterwards.

## How a page is put together

Each page is split into small files under `content/<page>/`, and rebuilt from
them on every edit:

```
content/index/
  _head.html        Google Sites boilerplate — the nav lives in here
  00-....html       one file per <section>, in manifest order
  _tail.html        boilerplate after the last section
  manifest.json     section order
```

The build (`tools/content.py build`) also runs `tools/sitekit.py`, which adds
the bits Google's own scripts would have provided and no longer can, because
they crash on a static copy:

- the **phone menu** (the ☰ button),
- **search** (the 🔍 button, or press `/`) — it reads `search-index.js`, which
  the build regenerates,
- each page's `<title>` and link-preview tags, pointed at this domain.

## Command-line tools

```bash
python tools/content.py build            # rebuild every page from content/
python tools/content.py verify           # check each page matches its pieces
python tools/platforms.py list           # show the nav
python tools/textedit.py runs index.html 06-adblockers.html
```

The editor does all of this for you; the commands are there for when you want
to look under the hood.

## Things worth knowing

- **Google Sites splits sentences across `<span>`s** at arbitrary points.
  `textedit.merge_spans` joins neighbouring spans that render identically, which
  is what makes the text editable at all.
- **The nav is copied into every page** and every staged `_head.html`. Always
  change it through the editor or `platforms.py`, which update all of them.
- **Editor working files** — `_undo/`, `_trash/`, `_backups/` — are git-ignored.
  Deleted sections and pages go to `_trash/`, so they can be recovered.

## History

The root-level scripts (`_mirror.py`, `make_editable.py`, `fix_asset_types.py`,
`localise_*.py`) built the original offline copy. They are kept for reference;
**don't run `_mirror.py`** — it re-downloads the old site over your edits.
