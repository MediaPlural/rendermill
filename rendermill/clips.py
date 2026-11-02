"""Clip-first cuts output (spec §5.4) — cuts become a render output.

From the master timeline (per-slide durations + word-level captions) emit
pre-timestamped clip segments alongside the master MP4:

  hook     : 0 .. end of slide 0 (plus optional head room)
  slide N  : each slide boundary (full slide, timestamps prebaked into the
             filename so an editor/agent never re-watches to find them)
  cta      : the slide declaring role=cta (or the last slide)

Output shape: one MP4 per segment + a clips.json manifest carrying
segment role, start/end on the master timeline, and the narration text —
the raw material for §6 Testing / §7 reply-layer re-openers.

Segments are cut with ffmpeg `-ss/-to` stream-copy when the cut lands on
the slide boundary (safe: each slide is its own encoded chunk in the
concat), falling back to re-encode for word-boundary trims.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .timeline import PAD_S, SlideSpan


@dataclass
class Clip:
    role: str          # "hook" | "slide_00" ... | "cta"
    label: str         # human name for the manifest
    start_s: float     # master-timeline in point
    end_s: float       # master-timeline out point
    narration: str     # what is spoken in this segment


def _fmt_ts(seconds: float) -> str:
    s = max(0.0, seconds)
    m = int(s // 60)
    rem = s - m * 60
    return f"{m:02d}{rem:04.1f}"


def build_clips(slides: List[Dict[str, Any]], durations: List[float],
                *, hook_extra_s: float = 0.0,
                pad_s: Optional[float] = None,
                spans: Optional[Sequence[SlideSpan]] = None) -> List[Clip]:
    """Assemble the cut list from slides, their measured narration durations,
    and slide roles.

    Prefers `spans` — the MEASURED positions of each encoded slide on the
    master timeline. A clip cut on the WRONG boundary is an off-by-one that
    only shows up when an editor pastes it into a timeline, so the cuts are
    derived from what was actually encoded, not from `sum(audio + pad)`.

    `pad_s` defaults to the single PAD_S definition in timeline.py and is
    only consulted on the arithmetic fallback path (no spans supplied).
    """
    if pad_s is None:
        pad_s = PAD_S
    clips: List[Clip] = []

    if spans is not None:
        # Measured: a slide's encoded length already includes its pad, so
        # the out point is the span's end, not start + audio + pad.
        for idx, (slide, span) in enumerate(zip(slides, spans)):
            start, end = span.start_s, span.end_s
            role = slide.get("role") or f"slide_{idx:02d}"
            label = slide.get("label") or role
            if idx == 0:
                # §5.4: the hook segment is the whole first slide (short by law).
                clips.append(Clip("hook", label, 0.0, end + hook_extra_s,
                                  slide.get("narration") or ""))
            else:
                clips.append(Clip(role, label, start, end,
                                  slide.get("narration") or ""))
    else:
        t = 0.0
        for idx, (slide, dur) in enumerate(zip(slides, durations)):
            start = t
            end = t + dur + pad_s
            role = slide.get("role") or f"slide_{idx:02d}"
            label = slide.get("label") or role
            if idx == 0:
                # §5.4: the hook segment is the whole first slide (short by law).
                clips.append(Clip("hook", label, 0.0, end + hook_extra_s,
                                  slide.get("narration") or ""))
            else:
                clips.append(Clip(role, label, start, end,
                                  slide.get("narration") or ""))
            t = end

    # CTA: the declared role=cta slide (or the last slide re-emitted as cta).
    cta = next((c for c in clips if c.role == "cta"), None)
    if cta is None and clips:
        last = clips[-1]
        cta = Clip("cta", last.label, last.start_s, last.end_s, last.narration)
        clips.append(cta)
    return clips


def cut_segment(master_mp4: Path, out_mp4: Path, start_s: float,
                end_s: float, *, reencode: bool = False) -> Path:
    """Cut [start_s, end_s) out of the master. Stream-copy by default (cuts
    land on slide boundaries, which are concat keyframe seams); re-encode for
    frame-accurate word-boundary trims.
    """
    dur = max(0.0, end_s - start_s)
    cmd: List[str]
    if reencode:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
            "-i", str(master_mp4),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            str(out_mp4),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
            "-i", str(master_mp4), "-c", "copy",
            "-avoid_negative_ts", "make_zero", str(out_mp4),
        ]
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return out_mp4


def emit_clips(master_mp4: Path, clips: List[Clip], clips_dir: Path,
               *, stem: str = "clip") -> Tuple[Path, List[Path]]:
    """Cut every clip into `clips_dir` and write clips.json. Returns
    (manifest_path, [mp4 paths])."""
    clips_dir = Path(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = clips_dir / "clips.json"
    made: List[Path] = []
    manifest: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for c in clips:
        n = seen.get(c.role, 0)
        seen[c.role] = n + 1
        suffix = "" if n == 0 else f"_{n}"
        name = f"{stem}_{c.role}{suffix}_{_fmt_ts(c.start_s)}-{_fmt_ts(c.end_s)}.mp4"
        out = clips_dir / name
        cut_segment(master_mp4, out, c.start_s, c.end_s)
        made.append(out)
        manifest.append({**asdict(c), "file": name,
                         "master": str(master_mp4)})
    manifest_path.write_text(json.dumps({
        "master": str(master_mp4),
        "segments": manifest,
    }, indent=2))
    return manifest_path, made


def clips_cfg(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve the clips config. `clips: {"emit": true}` opts in;
    `clips: false`/absent opts out. Defaults emitted when just true."""
    cfg = (config or {}).get("clips")
    if cfg is False or cfg is None:
        return None
    if cfg is True:
        return {"emit": True}
    return dict(cfg) if cfg.get("emit", True) else None