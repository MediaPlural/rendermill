"""Windows SAPI (System.Speech) TTS provider — free, offline, built-in.

Uses Windows PowerShell 5.1's `System.Speech.Synthesis.SpeechSynthesizer` to
write a 16-bit PCM mono 22050 Hz WAV (the same shape the macOS `say` path
emits), so the downstream m4a encode is identical across platforms.

Caveats:
  * Windows-only. `System.Speech` ships with the .NET Framework bundled in
    every modern Windows, and is reachable from Windows PowerShell 5.1
    (`powershell.exe`) — NOT PowerShell 7 (`pwsh`), which dropped System.Speech.
  * No word-level timing; captions fall back to estimated timing.
  * Voice quality "decent" — fine for long technical walkthroughs, not
    flashy for "Hi, I'm Becky" intros (use ElevenLabs for those).

Public API:
    synthesize(text, out_path, *, voice="auto") -> Path
    is_available() -> bool
"""
from __future__ import annotations
import platform
import shutil
import subprocess
from pathlib import Path


# Default voice: "auto" lets the system default speak (usually Microsoft
# David / Zira / Mark depending on locale + installed voices).
DEFAULT_VOICE = "auto"

_PS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ('{voice}' -ne 'auto') {{ try {{ $synth.SelectVoice('{voice}') }} catch {{}} }}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo -ArgumentList 22050, ([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen), ([System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile('{out_path}', $fmt)
$synth.Speak('{text}')
$synth.Dispose()
'''


def _require_windows() -> None:
    if platform.system() != "Windows":
        raise RuntimeError(
            "the windows (SAPI) TTS provider only runs on Windows — "
            "use the local provider on the current OS instead."
        )


def _ps_escape(s: str) -> str:
    """Escape for a PowerShell single-quoted string: the only special char
    is `'`, escaped by doubling it (`''`)."""
    return s.replace("'", "''")


def synthesize(text: str, out_path: Path, *, voice: str = "auto") -> Path:
    _require_windows()
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    voice = voice or "auto"
    script = _PS_SCRIPT.format(
        voice=_ps_escape(voice),
        out_path=_ps_escape(str(out_path)),
        text=_ps_escape(text),
    )
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-Command", script,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "powershell not found on PATH; Windows SAPI TTS needs Windows "
            "PowerShell 5.1 (bundled with Windows)."
        ) from e
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "")[-400:]
        raise RuntimeError(
            f"Windows SAPI synthesis failed (exit {e.returncode}): {err}"
        ) from e

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"Windows SAPI produced no output at {out_path}")
    return out_path


def is_available() -> bool:
    return platform.system() == "Windows" and shutil.which("powershell") is not None
