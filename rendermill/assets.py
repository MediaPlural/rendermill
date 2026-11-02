"""Programmatic asset fetchers.

Most of the imagery a slide deck needs is already public — a repo's social
preview card, its README hero, a YouTube thumbnail, a headless screenshot of a
live URL. This module turns a short ``kind:value`` spec into a downloaded file
under a project's ``assets/`` directory, so slide HTML can reference it via a
stable ``../assets/<name>`` path.

Kinds (see ``dispatch``):
    gh:owner/repo      GitHub OpenGraph card (1200x600 PNG)
    readme:owner/repo  first real image from the repo README (often the hero)
    yt:VIDEO_ID        YouTube thumbnail (maxresdefault, with fallbacks)
    shot:https://...   headless Chromium screenshot of a public URL
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# macOS app bundles tried in order (first that exists wins). PATH lookup runs
# first so Linux/nix users resolve before these paths are even consulted.
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

_PNG_MAGIC = b"\x89PNG"
_USER_AGENT = "rendermill/asset-fetch"
_RETRIES = 3
_RETRY_WAIT_S = 5.0


def _slugify(text: str) -> str:
    """Collapse anything that isn't filename-safe into underscores."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", text).strip("_")[:80]


def _fetch(url: str, *, retries: int = _RETRIES, wait: float = _RETRY_WAIT_S) -> bytes:
    """GET a URL with retries on transient failures; raise on persistent ones."""
    last: Optional[Exception] = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # GitHub's OG endpoint throttles with 429/503 + a short HTML body.
            if e.code in (429, 503) and attempt + 1 < retries:
                time.sleep(wait)
                last = e
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt + 1 < retries:
                time.sleep(wait)
                last = e
                continue
            raise
    raise last if last else RuntimeError("fetch failed without a recorded error")


def _find_chrome() -> Optional[str]:
    """First Chromium-class binary on PATH or in a known macOS app bundle."""
    for name in ("google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _require_owner_repo(spec: str) -> tuple[str, str]:
    if "/" not in spec or spec.count("/") != 1:
        raise ValueError(f"expected 'owner/repo', got {spec!r}")
    owner, repo = spec.split("/", 1)
    return owner, repo


def fetch_gh_og(spec: str, assets_dir: Path) -> Path:
    """Fetch a GitHub OpenGraph card for ``owner/repo``."""
    owner, repo = _require_owner_repo(spec)
    url = f"https://opengraph.githubassets.com/{int(time.time())}/{owner}/{repo}"
    body = _fetch(url)
    if not body.startswith(_PNG_MAGIC) or len(body) < 1000:
        snippet = body[:80].decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"GitHub OG returned non-PNG content: {snippet!r}")
    out = assets_dir / f"gh_{_slugify(repo)}.png"
    out.write_bytes(body)
    return out


def fetch_yt_thumb(video_id: str, assets_dir: Path) -> Path:
    """Fetch a YouTube thumbnail, accepting a bare id or a full URL."""
    match = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", video_id)
    if match:
        video_id = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError(f"not a YouTube video id: {video_id!r}")

    for quality in ("maxresdefault", "hqdefault", "mqdefault"):
        url = f"https://img.youtube.com/vi/{video_id}/{quality}.jpg"
        try:
            body = _fetch(url, retries=1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        if len(body) > 500:
            out = assets_dir / f"yt_{video_id}.jpg"
            out.write_bytes(body)
            return out
    raise RuntimeError(f"no thumbnail found for video {video_id}")


def fetch_readme_hero(spec: str, assets_dir: Path) -> Path:
    """Fetch the first non-badge image referenced by a repo's README."""
    owner, repo = _require_owner_repo(spec)
    readme, base = None, None
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        try:
            readme = _fetch(url, retries=1).decode("utf-8", errors="replace")
            base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
            break
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    if readme is None or base is None:
        raise RuntimeError(f"no README found for {spec} on main or master")

    # Find the first <img src> or ![alt](url), skipping CI/shield badges.
    target: Optional[str] = None
    pattern = (r'(?:<img\s+[^>]*src=["\']([^"\']+)["\'])'
               r'|(?:!\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\))')
    for match in re.finditer(pattern, readme):
        candidate = match.group(1) or match.group(2)
        if not candidate:
            continue
        low = candidate.lower()
        if any(s in low for s in ("shields.io", "img.shields.io", "/badge", "workflows")):
            continue
        target = candidate
        break
    if target is None:
        raise RuntimeError(f"no usable image found in README for {spec}")

    if target.startswith(("http://", "https://")):
        full = target
    elif target.startswith("./"):
        full = base + target[2:]
    elif target.startswith("/"):
        full = f"https://raw.githubusercontent.com/{owner}/{repo}/main{target}"
    else:
        full = base + target

    body = _fetch(full)
    if full.lower().endswith(".png"):
        ext = ".png"
    elif full.lower().endswith((".jpg", ".jpeg")):
        ext = ".jpg"
    else:
        ext = ".png"
    out = assets_dir / f"readme_{_slugify(repo)}{ext}"
    out.write_bytes(body)
    return out


def fetch_shot(url: str, assets_dir: Path, *, width: int = 1920,
               height: int = 1080) -> Path:
    """Take a headless Chromium screenshot of a public URL."""
    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError(
            "no Chrome-class binary found. Install Google Chrome, Chrome "
            "Beta, Brave, or Chromium, or point CHROME at an existing binary."
        )
    slug = _slugify(re.sub(r"^https?://", "", url))
    out = assets_dir / f"shot_{slug}.png"
    cmd = [
        chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
        f"--screenshot={out}", f"--window-size={width},{height}",
        "--virtual-time-budget=5000", url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(
            f"chrome screenshot failed (rc={result.returncode}): "
            f"{result.stderr.strip()[:200]}"
        )
    return out


def dispatch(spec: str, assets_dir: Path) -> Path:
    """Route a ``kind:value`` spec to the matching fetcher."""
    kind, _, value = spec.partition(":")
    if not value:
        raise ValueError(f"spec missing a colon-separated value: {spec!r}")
    handlers = {
        "gh": fetch_gh_og,
        "yt": fetch_yt_thumb,
        "readme": fetch_readme_hero,
        "shot": fetch_shot,
    }
    handler = handlers.get(kind)
    if handler is None:
        raise ValueError(f"unknown kind {kind!r}, expected one of: {', '.join(handlers)}")
    return handler(value, assets_dir)
