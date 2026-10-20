"""Linux local TTS provider — espeak-ng (free, offline, near-universal).

espeak-ng writes a mono WAV directly (`-w`), so we mirror the macOS `say`
shape (16-bit PCM, ~22050 Hz) and feed the same downstream m4a encode.

Caveats:
  * Robotic voice (formant synthesis) — fine for drafts / walkthroughs.
  * Not bundled on most distros: `apt install espeak-ng`, `dnf install
    espeak-ng`, or `brew install espeak-ng`.
  * No word-level timing; captions fall back to estimated timing.

Public API:
    synthesize(text, out_path, *, voice="auto") -> Path
    is_available() -> bool
"""
from __future__ import annotations
import platform
import shutil
import subprocess
from pathlib import Path


DEFAULT_VOICE = "en"


def _require_linux() -> None:
    system = platform.system()
    if system == "Windows":
        raise RuntimeError("the linux TTS provider does not run on Windows.")
    if system == "Darwin":
        raise RuntimeError("the linux TTS provider does not run on macOS.")


def _find_espeak() -> str:
    for name in ("espeak-ng", "espeak"):
        p = shutil.which(name)
        if p:
            return p
    raise RuntimeError(
        "no local TTS on Linux: install espeak-ng "
        "(e.g. `apt install espeak-ng`, `dnf install espeak-ng`, or `brew install espeak-ng`)."
    )


def synthesize(text: str, out_path: Path, *, voice: str = "auto") -> Path:
    _require_linux()
    espeak = _find_espeak()
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    voice = voice if (voice and voice != "auto") else DEFAULT_VOICE
    cmd = [espeak, "-w", str(out_path), "-v", voice, text]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="ignore")[-300:]
        raise RuntimeError(f"espeak-ng failed (exit {e.returncode}): {err}") from e

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"espeak-ng produced no output at {out_path}")
    return out_path


def is_available() -> bool:
    system = platform.system()
    if system in ("Windows", "Darwin"):
        return False
    return (shutil.which("espeak-ng") or shutil.which("espeak")) is not None
