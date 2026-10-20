"""Cross-platform local (free, offline) TTS provider.

Dispatches to the OS's built-in voice:
  - macOS   → `say`       (tts.macos)
  - Windows → SAPI        (tts.windows)
  - Linux   → espeak-ng   (tts.linux)

All three backends share one `synthesize(text, out_path, *, voice="auto")`
signature and write a 16-bit PCM mono WAV, so the caller never cares which
backend is active.

Public API:
    synthesize(text, out_path, *, voice="auto") -> Path
    is_available() -> bool
    describe() -> str
"""
from __future__ import annotations
import platform
from pathlib import Path

from . import linux as _linux
from . import macos as _macos
from . import windows as _windows


def _backend():
    system = platform.system()
    if system == "Darwin":
        return _macos
    if system == "Windows":
        return _windows
    return _linux


def synthesize(text: str, out_path: Path, *, voice: str = "auto") -> Path:
    return _backend().synthesize(text, out_path, voice=voice)


def is_available() -> bool:
    return _backend().is_available()


def describe() -> str:
    """Human label for the active local backend (setup-wizard friendly)."""
    system = platform.system()
    if system == "Darwin":
        return "macOS say"
    if system == "Windows":
        return "Windows SAPI"
    return "espeak-ng"
