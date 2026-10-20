#!/usr/bin/env python3
"""Cross-platform LOCAL TTS smoke probe (armory tool).

Live-tests the rendermill local TTS backend on whatever OS this runs on:
  - macOS   -> `say`        (tts/macos.py)
  - Windows -> SAPI         (tts/windows.py)
  - Linux   -> espeak-ng    (tts/linux.py)

Loads the real backend module from a path (no package import, so there's no
rendermill dependency chain), synthesizes a short phrase to a WAV, and
verifies the output is a valid, non-empty WAVE file.

Exit codes: 0 = PASS, 1 = FAIL (backend ran but produced no valid WAV),
2 = SETUP (missing binary / wrong OS / import error).
Stdlib only — runs anywhere Python 3.9+ is present.
"""
from __future__ import annotations

import argparse
import importlib.util
import platform
import sys
import tempfile
from pathlib import Path

EXIT_PASS, EXIT_FAIL, EXIT_SETUP = 0, 1, 2

BACKENDS = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}
PHRASE = "Rendermill local voice smoke test. One two three."


def load_backend(tts_dir: Path, name: str):
    path = tts_dir / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"backend not found: {path}")
    spec = importlib.util.spec_from_file_location(f"rm_tts_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def is_wav(path: Path) -> bool:
    data = path.read_bytes()
    return len(data) > 44 and data[0:4] == b"RIFF" and data[8:12] == b"WAVE"


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-platform local TTS smoke probe")
    ap.add_argument("--tts-dir", type=Path, required=True,
                    help="path to rendermill/tts/ (contains macos.py, windows.py, linux.py)")
    ap.add_argument("--voice", default="auto")
    args = ap.parse_args()

    system = platform.system()
    backend = BACKENDS.get(system)
    if backend is None:
        print(f"SETUP: no local TTS backend for OS {system!r}", file=sys.stderr)
        return EXIT_SETUP

    try:
        mod = load_backend(args.tts_dir, backend)
    except (FileNotFoundError, ImportError) as e:
        print(f"SETUP: {e}", file=sys.stderr)
        return EXIT_SETUP

    if not getattr(mod, "is_available", lambda: True)():
        need = {"Darwin": "say", "Windows": "powershell", "Linux": "espeak-ng"}[system]
        print(f"SETUP: {backend} backend unavailable (missing {need})", file=sys.stderr)
        return EXIT_SETUP

    with tempfile.TemporaryDirectory(prefix="rm-tts-smoke-") as td:
        out = Path(td) / "out.wav"
        try:
            mod.synthesize(PHRASE, out, voice=args.voice)
        except Exception as e:  # noqa: BLE001 - any backend error is a FAIL
            print(f"FAIL: {backend}.synthesize raised {type(e).__name__}: {e}",
                  file=sys.stderr)
            return EXIT_FAIL

        if not out.exists() or not is_wav(out):
            size = out.stat().st_size if out.exists() else 0
            print(f"FAIL: {backend} produced no valid WAV at {out} ({size} bytes)",
                  file=sys.stderr)
            return EXIT_FAIL

        print(f"PASS: {backend} synthesized a valid WAV "
              f"({out.stat().st_size} bytes) on {system}")

    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
