"""
Publish the site to GitHub from the editor — the git steps, run for you.

    status()        what has changed, which branch, anything waiting to upload
    publish(note)   save every change as one commit, take in anything new on
                    GitHub, and upload. On main, GitHub Pages puts it live.
    update()        take in what changed on GitHub (a merged pull request, say)
    to_main()       move to main, the branch the live site is built from

Everything goes through the `git` already on this computer, with its sign-in.
Git is never allowed to stop and ask a question in a terminal nobody is
watching (GIT_TERMINAL_PROMPT=0); a sign-in window from Git Credential
Manager still appears as normal.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
LIVE_BRANCH = "main"

# Used only when git has no name/email set up on this computer. The upload is
# still made with your own GitHub sign-in; this is just the commit's label.
FALLBACK_NAME = "Site editor"
FALLBACK_EMAIL = "site-editor@users.noreply.github.com"


class GitError(Exception):
    pass


def _git(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", GIT_MERGE_AUTOEDIT="no")
    try:
        r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise GitError("GitHub took too long to answer. Check your internet connection "
                       "and try again.")
    if check and r.returncode != 0:
        raise GitError(_explain(r.stderr or r.stdout))
    return r


def _explain(err: str) -> str:
    """Git's error, said plainly where we recognise it."""
    low = err.lower()
    if "could not read username" in low or "authentication failed" in low \
            or "permission denied" in low or "403" in low:
        return ("GitHub would not let this computer upload. Sign in to GitHub in "
                "Git (or GitHub Desktop) on this computer, then try again.")
    if "could not resolve host" in low or "unable to access" in low:
        return "Could not reach GitHub. Check your internet connection and try again."
    if "conflict" in low:
        return ("The same text was changed here and on GitHub, and git cannot "
                "combine the two by itself. Nothing was lost — your changes are "
                "still here, saved.")
    first = next((l.strip() for l in err.splitlines() if l.strip()), "git failed")
    return re.sub(r"^(fatal|error):\s*", "", first)


def _available() -> str | None:
    if not shutil.which("git"):
        return ("Git is not installed on this computer, so the editor cannot "
                "upload. Install GitHub Desktop (desktop.github.com) — it "
                "includes Git — and sign in.")
    r = _git("rev-parse", "--is-inside-work-tree", check=False)
    if r.returncode != 0 or r.stdout.strip() != "true":
        return ("This folder is not connected to GitHub. Open it from a clone of "
                "the repository (GitHub Desktop: File → Clone repository).")
    return None


def _branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _changes() -> list[str]:
    out = _git("status", "--porcelain", "-uall").stdout
    return [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]


def _ahead() -> int:
    r = _git("rev-list", "--count", "@{u}..HEAD", check=False)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else 0


def _repo_url() -> str:
    r = _git("remote", "get-url", "origin", check=False)
    url = r.stdout.strip()
    m = re.match(r"(?:https://github\.com/|git@github\.com:)(.+?)(?:\.git)?$", url)
    return f"https://github.com/{m.group(1)}" if m else ""


def _pages(paths: list[str]) -> list[str]:
    """The site pages among changed files, by name — what a person changed."""
    pages = set()
    for p in paths:
        parts = p.split("/")
        if len(parts) == 1 and p.endswith(".html"):
            pages.add(p)
        elif parts[0] == "content" and len(parts) > 2:
            pages.add(parts[1] + ".html")
        elif parts[0] == "assets" and len(parts) > 1 and parts[1] == "uploads":
            pages.add("(uploaded images)")
    return sorted(pages)


def status() -> dict:
    problem = _available()
    if problem:
        return {"ok": True, "ready": False, "problem": problem}
    paths = _changes()
    branch = _branch()
    return {
        "ok": True, "ready": True,
        "branch": branch, "live": branch == LIVE_BRANCH,
        "changed": len(paths), "pages": _pages(paths),
        "ahead": _ahead(), "repo": _repo_url(),
    }


def _identity() -> list[str]:
    """-c name/email overrides, only when this computer has none set."""
    out = []
    if not _git("config", "user.name", check=False).stdout.strip():
        out += ["-c", f"user.name={FALLBACK_NAME}"]
    if not _git("config", "user.email", check=False).stdout.strip():
        out += ["-c", f"user.email={FALLBACK_EMAIL}"]
    return out


def _pull(branch: str) -> bool:
    """Merge in what is new on GitHub. True if anything came in.

    A merge, never a rebase: it cannot rewrite anything already uploaded. If
    the two sides clash, the merge is undone so the folder is exactly as it
    was, and the error says so.
    """
    if _git("ls-remote", "--exit-code", "--heads", "origin", branch, check=False).returncode != 0:
        return False                      # a branch GitHub does not have yet
    before = _git("rev-parse", "HEAD").stdout.strip()
    r = _git(*_identity(), "pull", "--no-rebase", "--no-edit", "origin", branch, check=False)
    if r.returncode != 0:
        _git("merge", "--abort", check=False)
        raise GitError(_explain(r.stderr or r.stdout))
    return _git("rev-parse", "HEAD").stdout.strip() != before


def publish(note: str = "") -> dict:
    problem = _available()
    if problem:
        return {"ok": False, "error": problem}
    branch = _branch()
    paths = _changes()
    committed = False
    if paths:
        pages = _pages(paths)
        title = (note or "").strip().splitlines()[0][:72] if (note or "").strip() else \
            "Update " + (", ".join(p.removesuffix(".html") for p in pages[:3]) or "site") + \
            (" and more" if len(pages) > 3 else "")
        body = f"Published from the site editor, {datetime.now():%Y-%m-%d %H:%M}."
        if pages:
            body += "\n\nPages: " + ", ".join(pages)
        _git("add", "-A")
        _git(*_identity(), "commit", "-q", "-m", title, "-m", body)
        committed = True
    pulled = _pull(branch)
    if not committed and not _ahead() and not pulled:
        return {"ok": True, "did": "nothing", "branch": branch,
                "message": "Nothing to publish — the site already has everything."}
    _git("push", "-u", "origin", branch, timeout=300)
    if branch == LIVE_BRANCH:
        message = "Published. The site updates in a minute or two."
    else:
        message = (f"Uploaded to the '{branch}' branch. It goes live once that is "
                   f"merged into {LIVE_BRANCH} on GitHub.")
    return {"ok": True, "did": "published", "branch": branch, "pulled": pulled,
            "message": message}


def update() -> dict:
    problem = _available()
    if problem:
        return {"ok": False, "error": problem}
    branch = _branch()
    stashed = False
    if _changes():
        # Unpublished edits are put aside for the update and put back after.
        _git(*_identity(), "stash", "push", "-u", "-q", "-m", "editor: before getting latest")
        stashed = True
    try:
        pulled = _pull(branch)
    finally:
        if stashed:
            r = _git("stash", "pop", "-q", check=False)
            if r.returncode != 0:
                raise GitError("Got the latest from GitHub, but your unpublished edits "
                               "clash with it. They are kept safe in git's stash.")
    return {"ok": True, "pulled": pulled, "branch": branch,
            "message": "Up to date with GitHub." if not pulled
            else "Got the latest from GitHub."}


def to_main() -> dict:
    problem = _available()
    if problem:
        return {"ok": False, "error": problem}
    if _changes():
        return {"ok": False, "error": "Publish your changes on this branch first, then switch."}
    _git("fetch", "-q", "origin", LIVE_BRANCH)
    _git("checkout", "-q", LIVE_BRANCH)
    pulled = _pull(LIVE_BRANCH)
    return {"ok": True, "branch": LIVE_BRANCH, "pulled": pulled, "reload": True,
            "message": f"Now on {LIVE_BRANCH}: Publish goes straight to the live site."}
