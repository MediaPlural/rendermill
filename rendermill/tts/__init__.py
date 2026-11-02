"""Text-to-speech providers: ElevenLabs (primary), Deepgram, local (free).

Local = the OS's built-in offline voice — macOS `say`, Windows SAPI, or
Linux espeak-ng — dispatched by `rendermill.tts.local`.

Each `make_audio_<provider>` takes `(text, idx, *, workdir, cache, tts_cfg)`
and returns `(m4a_path, duration_seconds)`. ElevenLabs additionally writes
an alignment JSON sidecar (`narr_NN.alignment.json` in workdir) for
caption timing. Deepgram and the local providers produce no word-level
alignment; caption flow degrades to estimated timing.

Caching is wired through `cache.run`:
  - kind="tts" caches raw provider output (mp3 / wav / optional alignment sidecar)
  - kind="m4a" caches the silenceremove+atempo m4a encode

Voice defaults (canon = the Viiy voice stack; single source of truth is the
`rendermill_voice_bridge` module — imported at runtime, never copied here):
  ElevenLabs: Viiy Signature (eleven_v3, use_speaker_boost=False)
  Deepgram:   aura-2-thalia-en
  local:      auto  (best installed voice per-OS: say / SAPI / espeak-ng)
"""
from __future__ import annotations
import base64
import importlib.util
import json
import os
import platform
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Tuple

from .. import keys
from . import local as _local
from . import macos as _macos  # kept for backward-compat imports/tests


# The Viiy voice bridge is the single source of truth for the locked
# ElevenLabs voice set (IDs, model, settings). We import it at runtime rather
# than copying the IDs into this file, so the defaults cannot drift from canon.
# `rendermill/tts/__init__.py` -> parents[2] is the repo root; the bridge sits
# in the sibling skills tree under the viiy-hq checkout root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VOICE_BRIDGE_CANDIDATES = (
    os.environ.get("VIIY_VOICE_BRIDGE"),
    _REPO_ROOT.parents[2] / "skills" / "media" / "viiy-voice-design"
    / "scripts" / "rendermill_voice_bridge.py",
    Path.home() / "viiy-hq" / "skills" / "media" / "viiy-voice-design"
    / "scripts" / "rendermill_voice_bridge.py",
)

_voice_bridge_mod = None


def voice_bridge():
    """Import the Viiy voice bridge (canon for the locked ElevenLabs voice set).

    Override the bridge path with ``VIIY_VOICE_BRIDGE``. Raises a clear error
    when the bridge cannot be found, since the defaults below depend on it.
    """
    global _voice_bridge_mod
    if _voice_bridge_mod is not None:
        return _voice_bridge_mod
    for cand in _VOICE_BRIDGE_CANDIDATES:
        if not cand:
            continue
        p = Path(cand)
        if not p.exists():
            continue
        spec = importlib.util.spec_from_file_location("rendermill_voice_bridge", str(p))
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _voice_bridge_mod = mod
        return mod
    raise RuntimeError(
        "Viiy voice bridge not found. Set VIIY_VOICE_BRIDGE to the path of "
        "rendermill_voice_bridge.py (the canonical voice registry)."
    )


def rendermill_tts(voice: str = "viiy-signature") -> Dict[str, Any]:
    """Return the canonical rendermill `tts` block for a locked Viiy voice."""
    return voice_bridge().rendermill_tts(voice)


def drift_report(tts_cfg: Dict[str, Any] | None) -> list[str]:
    """Flag an off-canon rendermill `tts` block. Empty list == on-canon.

    Wired straight to the bridge's drift gate (the generic "Jessica" voice,
    turbo models, and speaker-boost are all flagged here).
    """
    return voice_bridge().drift_report(tts_cfg)


_bridge = voice_bridge()
VIIY_VOICES = dict(_bridge.VIIY_VOICES)
GENERIC_VOICE_IDS = dict(_bridge.GENERIC_VOICE_IDS)
_VIIY_CANON = _bridge.rendermill_tts("viiy-signature")

# provider stays "auto" so free mode / Deepgram / local still cascade; the
# voice identity, model and settings are the Viiy canon from the bridge.
DEFAULT_TTS = {
    "provider": "auto",
    "voice_id": _VIIY_CANON["voice_id"],
    "model": _VIIY_CANON["model"],
    "speaking_rate": _VIIY_CANON["speaking_rate"],
    "voice_settings": dict(_VIIY_CANON["voice_settings"]),
    "deepgram_voice": "aura-2-thalia-en",
    "macos_voice": "auto",
    "local_voice": "auto",
}


def _ffprobe_duration(path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(res.stdout.strip())


def _encode_to_m4a(src: Path, dst: Path, rate: float) -> Path:
    """AAC m4a with silenceremove (trim leading + collapse internal >0.25s
    pauses) and optional atempo speedup. Mirrors growth-machine make_video.py.
    """
    filters = [
        "silenceremove=start_periods=1:start_duration=0:start_threshold=-40dB"
        ":stop_periods=-1:stop_duration=0.25:stop_threshold=-38dB",
    ]
    if abs(rate - 1.0) > 1e-3:
        filters.append(f"atempo={rate}")
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-filter:a", ",".join(filters),
        "-c:a", "aac", "-b:a", "192k", str(dst),
    ]
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return dst


def make_audio_elevenlabs(text: str, idx: int, *, workdir: Path,
                          cache, tts_cfg: Dict[str, Any]) -> Tuple[Path, float]:
    """ElevenLabs /text-to-speech/{voice}/with-timestamps → m4a + alignment JSON.
    Cached by (text, voice, model). m4a encode separately cached by (mp3, rate)."""
    api_key = keys.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ElevenLabs requested but no ELEVENLABS_API_KEY found.\n"
            "Run: rendermill keys set EL=<your-key>"
        )

    voice_id = tts_cfg.get("voice_id", DEFAULT_TTS["voice_id"])
    model = tts_cfg.get("model", DEFAULT_TTS["model"])
    rate = float(tts_cfg.get("speaking_rate", DEFAULT_TTS["speaking_rate"]))

    def _generate(out_path: Path) -> None:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
        # Project-config voice_settings override (per voice-design canon);
        # falls back to the canon settings (speaker boost OFF) when not set.
        vs = dict(tts_cfg.get("voice_settings") or {
            "stability": 0.55, "similarity_boost": 0.85,
            "style": 0.20, "use_speaker_boost": False,
        })
        body = {
            "text": text,
            "model_id": model,
            "voice_settings": vs,
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.load(r)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="ignore")[:300]
            raise RuntimeError(f"ElevenLabs error {e.code}: {err}")
        out_path.write_bytes(base64.b64decode(res["audio_base64"]))
        align_path = out_path.with_suffix(".alignment.json")
        align_path.write_text(json.dumps(res.get("alignment") or {}))

    tts_key = [text, "voice", voice_id, "model", model]
    cached_mp3 = cache.run("tts", tts_key, "mp3", _generate)

    # Copy cached output to canonical per-slide paths used downstream.
    target_mp3 = workdir / f"narr_{idx:02d}.mp3"
    target_align = workdir / f"narr_{idx:02d}.alignment.json"
    target_mp3.write_bytes(cached_mp3.read_bytes())
    align_src = cached_mp3.with_suffix(".alignment.json")
    if align_src.exists():
        target_align.write_text(align_src.read_text())

    m4a_key = [cached_mp3, "rate", round(rate, 4), "silenceremove", "0.25", "0.0"]

    def _m4a_gen(out_path: Path) -> None:
        _encode_to_m4a(target_mp3, out_path, rate=rate)

    cached_m4a = cache.run("m4a", m4a_key, "m4a", _m4a_gen)
    target_m4a = workdir / f"narr_{idx:02d}.m4a"
    target_m4a.write_bytes(cached_m4a.read_bytes())
    return target_m4a, _ffprobe_duration(target_m4a)


def make_audio_deepgram(text: str, idx: int, *, workdir: Path,
                        cache, tts_cfg: Dict[str, Any]) -> Tuple[Path, float]:
    """Deepgram Aura-2 TTS → m4a. No timestamp alignment available; captions
    that depend on word-level timings will be skipped for Deepgram slides.
    """
    api_key = keys.get("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Deepgram requested but no DEEPGRAM_API_KEY found.\n"
            "Run: rendermill keys set DG=<your-key>"
        )
    voice = tts_cfg.get("deepgram_voice", DEFAULT_TTS["deepgram_voice"])
    rate = float(tts_cfg.get("speaking_rate", DEFAULT_TTS["speaking_rate"]))
    url = f"https://api.deepgram.com/v1/speak?model={voice}&encoding=mp3"

    def _generate(out_path: Path) -> None:
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": text}).encode(),
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                audio = r.read()
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="ignore")[:300]
            raise RuntimeError(f"Deepgram error {e.code}: {err}")
        out_path.write_bytes(audio)

    tts_key = [text, "deepgram", "voice", voice]
    cached_mp3 = cache.run("tts", tts_key, "mp3", _generate)
    target_mp3 = workdir / f"narr_{idx:02d}.mp3"
    target_mp3.write_bytes(cached_mp3.read_bytes())

    m4a_key = [cached_mp3, "rate", round(rate, 4), "silenceremove", "0.25", "0.0", "dg"]

    def _m4a_gen(out_path: Path) -> None:
        _encode_to_m4a(target_mp3, out_path, rate=rate)

    cached_m4a = cache.run("m4a", m4a_key, "m4a", _m4a_gen)
    target_m4a = workdir / f"narr_{idx:02d}.m4a"
    target_m4a.write_bytes(cached_m4a.read_bytes())
    return target_m4a, _ffprobe_duration(target_m4a)


def make_audio_local(text: str, idx: int, *, workdir: Path,
                     cache, tts_cfg: Dict[str, Any]) -> Tuple[Path, float]:
    """Local (free, offline) TTS -> wav -> m4a. Dispatches per-OS to macOS
    `say`, Windows SAPI, or Linux espeak-ng. No word-level alignment.

    The heavy lifting lives in `rendermill.tts.local.synthesize` so it can be
    imported directly from tests / external code.
    """
    voice = (tts_cfg.get("local_voice") or tts_cfg.get("macos_voice")
             or DEFAULT_TTS["local_voice"])
    rate = float(tts_cfg.get("speaking_rate", DEFAULT_TTS["speaking_rate"]))

    def _generate(out_path: Path) -> None:
        # synthesize writes a wav directly to out_path.
        _local.synthesize(text, out_path, voice=voice)

    tts_key = [text, "local", platform.system(), "voice", voice]
    cached_wav = cache.run("tts", tts_key, "wav", _generate)
    target_wav = workdir / f"narr_{idx:02d}.wav"
    target_wav.write_bytes(cached_wav.read_bytes())

    m4a_key = [cached_wav, "rate", round(rate, 4), "silenceremove", "0.25", "0.0", "local"]

    def _m4a_gen(out_path: Path) -> None:
        _encode_to_m4a(target_wav, out_path, rate=rate)

    cached_m4a = cache.run("m4a", m4a_key, "m4a", _m4a_gen)
    target_m4a = workdir / f"narr_{idx:02d}.m4a"
    target_m4a.write_bytes(cached_m4a.read_bytes())
    return target_m4a, _ffprobe_duration(target_m4a)


def make_audio_macos(text: str, idx: int, **kwargs) -> Tuple[Path, float]:
    """Backward-compat alias: `macos` now resolves to the cross-platform
    `local` provider (which is `say` on macOS)."""
    return make_audio_local(text, idx, **kwargs)


def resolve_provider(tts_cfg: Dict[str, Any]) -> str:
    """Resolve a (possibly "auto") provider to a concrete one from keys + platform.

    Explicit providers pass through unchanged (the per-provider function still
    raises its own clear error if that provider's key is missing). "auto" (the
    default) picks the best available: ElevenLabs -> Deepgram -> macOS local;
    if none is possible (Linux, no keys) it raises a single clear error.
    Pure: no network, no synthesis. Safe to call repeatedly (idempotent).
    """
    provider = (tts_cfg or {}).get("provider") or "auto"
    provider = provider.lower()
    if provider in ("elevenlabs", "el", "eleven"):
        return "elevenlabs"
    if provider == "deepgram":
        return "deepgram"
    if provider in ("macos", "say", "macos-say", "local", "free"):
        return "local"
    if provider != "auto":
        return provider  # unknown explicit value; dispatch will raise a clear error

    if keys.get("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    if keys.get("DEEPGRAM_API_KEY"):
        return "deepgram"
    if _local.is_available():
        return "local"
    raise RuntimeError(
        "No TTS available: set ELEVENLABS_API_KEY or DEEPGRAM_API_KEY, or "
        "install a local voice (macOS `say` / Windows SAPI / Linux espeak-ng)."
    )


def make_audio(text: str, idx: int, *, workdir: Path, cache,
               tts_cfg: Dict[str, Any]) -> Tuple[Path, float]:
    """Dispatch to the resolved provider. `provider:"auto"` (the default) picks
    the best available from the keys present (see `resolve_provider`)."""
    provider = resolve_provider(tts_cfg)
    tts_cfg = {**tts_cfg, "provider": provider}
    if provider == "deepgram":
        return make_audio_deepgram(text, idx, workdir=workdir, cache=cache, tts_cfg=tts_cfg)
    if provider == "local":
        return make_audio_local(text, idx, workdir=workdir, cache=cache, tts_cfg=tts_cfg)
    if provider == "elevenlabs":
        return make_audio_elevenlabs(text, idx, workdir=workdir, cache=cache, tts_cfg=tts_cfg)
    raise ValueError(
        f"Unknown TTS provider: {provider!r}. "
        "Use 'auto', 'elevenlabs', 'deepgram', or 'local'."
    )
