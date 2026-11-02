"""HTML -> PNG rendering through a headless Chromium-class browser.

Chromium is discovered in a fixed precedence, most specific first:

  1. ``CHROME_BIN`` env var (explicit override, highest priority)
  2. macOS app bundles — stable first, then Beta/Dev/Canary, then Chromium,
     Brave (incl. Beta/Nightly) and Edge
  3. ``chromium`` / ``google-chrome`` / ``chrome`` / ``microsoft-edge`` on
     PATH (Linux and dev shells)
  4. a Playwright-managed Chromium under the platform cache (only present if
     someone previously ran ``playwright install chromium``)

The package does not depend on Playwright; it shells out to whichever binary
it finds. ``render_html_to_png`` screenshots a local ``file://`` URL at the
requested viewport, so the HTML can reference neighbouring CSS/images through
relative paths.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

_MACOS_BUNDLES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    "/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Brave Browser Beta.app/Contents/MacOS/Brave Browser Beta",
    "/Applications/Brave Browser Nightly.app/Contents/MacOS/Brave Browser Nightly",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

_PATH_NAMES = (
    "chromium", "google-chrome", "google-chrome-stable", "chromium-browser",
    "brave-browser", "microsoft-edge", "chrome",
)

_PLAYWRIGHT_ROOTS = (
    Path.home() / "Library/Caches/ms-playwright",
    Path.home() / ".cache/ms-playwright",
)


def _find_chrome_binary() -> Optional[str]:
    """Locate a Chromium-class binary, or return None when none exists."""
    explicit = os.environ.get("CHROME_BIN")
    if explicit and Path(explicit).exists():
        return explicit

    for bundle in _MACOS_BUNDLES:
        if Path(bundle).exists():
            return bundle

    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found

    # Playwright cache fallback. Prefer the full chromium build over
    # headless_shell, which has historically lacked the --screenshot flag.
    for root in _PLAYWRIGHT_ROOTS:
        if not root.exists():
            continue
        for prefix in ("chromium-", "chromium_headless_shell-"):
            builds = sorted(root.glob(f"{prefix}*"))
            if not builds:
                continue
            latest = builds[-1]
            for rel in (
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
                "chrome-linux/chrome",
            ):
                candidate = latest / rel
                if candidate.exists():
                    return str(candidate)
    return None


def render_html_to_png(html_path: Path, png_path: Path, width: int,
                       height: int) -> Path:
    """Screenshot ``html_path`` to ``png_path`` at the given viewport."""
    chrome = _find_chrome_binary()
    if chrome is None:
        raise RuntimeError(
            "Couldn't find a Chrome/Chromium binary to render HTML.\n"
            "On macOS, install Google Chrome from https://www.google.com/chrome/.\n"
            "On Linux, install `chromium` or `google-chrome` from your package manager.\n"
            "Or point at an existing binary: CHROME_BIN=/path/to/chrome rendermill render"
        )

    html_path = html_path.resolve()
    png_path = png_path.resolve()
    png_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--hide-scrollbars",
        "--disable-gpu",
        "--force-device-scale-factor=1",
        f"--window-size={width},{height}",
        f"--screenshot={png_path}",
        # Give web fonts and images a beat to load before the snapshot.
        "--virtual-time-budget=2000",
        "--default-background-color=00000000",
        f"file://{html_path}",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    if not png_path.exists():
        raise RuntimeError(
            f"Chrome screenshot failed for {html_path.name}.\n"
            f"stderr: {result.stderr.decode(errors='ignore')[-600:]}"
        )
    return png_path


def render_all(slide_dir: Path, png_dir: Path, slides: list, width: int,
               height: int) -> list[Optional[Path]]:
    """Render every HTML-typed slide to a PNG; non-HTML slides map to None.

    Returns one entry per slide, in input order.
    """
    png_dir.mkdir(parents=True, exist_ok=True)
    outputs: List[Optional[Path]] = []
    for idx, slide in enumerate(slides):
        if slide.get("type", "image") != "html":
            outputs.append(None)
            continue
        source = slide.get("html")
        if not source:
            raise ValueError(f"slide {idx}: type=html requires `html` field")
        html_path = (slide_dir / source).resolve()
        if not html_path.exists():
            raise FileNotFoundError(f"slide {idx}: HTML file not found: {html_path}")
        png_path = png_dir / f"slide_{idx:02d}.png"
        print(f"  [html->png] slide {idx:02d}: {source} -> {png_path.name}")
        render_html_to_png(html_path, png_path, width, height)
        outputs.append(png_path)
    return outputs
