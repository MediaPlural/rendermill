"""Measured slide timeline — the single source of truth for master time.

Why this module exists
----------------------
The render used to compute every slide's position on the master timeline by
running total of `audio_duration + PAD_S`. That is an *assumption*: the encoded
slide MP4 is not guaranteed to be exactly that long. libx264 rounds to frame
boundaries, the audio encoder pads to packet boundaries, and `-shortest` can
trim differently per slide. Measured on the 5-slide assessment demo, the
per-slide error ranged from -0.026s to +0.000s and the master came out
+0.046s longer than the sum of its parts.

Captions, clip cuts and the explainer gates all key off the same timeline, so
that drift does not stay in one place — it desynchronises captions from
clips, and makes the gates judge the wrong moments.

The fix is to stop guessing. `build_timeline` measures each *encoded* slide
MP4 with ffprobe and derives every downstream offset from those measurements.
Nothing downstream adds up audio durations ever again.

`PAD_S` is defined here ONCE. `gates.py`, `clips.py` and `ffmpeg_video.py`
import it rather than restating 0.15, which is how the four copies drifted
apart in the first place.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Silence appended after each slide's narration. A beat of air between slides
# reads as intentional; zero reads as a glitch. This is the ONLY definition
# in the codebase — gates/clips/ffmpeg import it from here.
PAD_S = 0.15

# ffprobe is the measurement instrument. If it is missing the render cannot
# promise a measured timeline, so we fail loudly rather than silently
# falling back to arithmetic.
_FFPROBE_TIMEOUT_S = 30


class TimelineError(RuntimeError):
    """A slide could not be measured, so no trustworthy timeline exists."""


@dataclass
class SlideSpan:
    """One slide's measured position on the master timeline."""

    idx: int
    start_s: float      # measured start on the master timeline
    end_s: float        # measured end (start + encoded slide duration)
    audio_s: float      # measured narration audio length
    encoded_s: float    # measured encoded slide-MP4 length
    pad_s: float        # trailing pad included in `encoded_s`
    role: Optional[str] = None
    label: Optional[str] = None
    narration: str = ""

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def probe_duration(path: Path) -> float:
    """Measured duration of a media file in seconds.

    Raises TimelineError rather than returning a guess — a caller that cannot
    measure must not proceed as though it had.
    """
    path = Path(path)
    if not path.exists():
        raise TimelineError(f"cannot probe missing file: {path}")
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT_S,
        )
    except FileNotFoundError as e:
        raise TimelineError(
            "ffprobe not found on PATH. rendermill measures every slide with "
            "ffprobe to build a truthful timeline; install ffmpeg first."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise TimelineError(f"ffprobe timed out probing {path.name}") from e
    if res.returncode != 0:
        raise TimelineError(
            f"ffprobe failed on {path.name} (rc={res.returncode}): "
            f"{(res.stderr or '').strip()[:300]}"
        )
    try:
        duration = float(res.stdout.strip())
    except (TypeError, ValueError) as e:
        raise TimelineError(
            f"ffprobe returned a non-numeric duration for {path.name}: "
            f"{res.stdout!r}"
        ) from e
    if duration <= 0:
        raise TimelineError(f"{path.name} measured {duration}s — refusing to build a timeline from it")
    return duration


def build_timeline(
    slide_mp4s: Sequence[Path],
    audios: Sequence[Path],
    slides: Sequence[Dict[str, Any]],
    *,
    pad_s: float = PAD_S,
) -> List[SlideSpan]:
    """Measure every encoded slide and return its span on the master timeline.

    `slide_mp4s` and `audios` are the PER-SLIDE ENCODED artefacts, in order.
    `slides` supplies the optional role/label/narration metadata.

    Positions come from the measured encoded durations, so a slide whose MP4
    came out 40ms long pushes everything after it by exactly 40ms — which is
    what actually happened to the video.
    """
    if not (len(slide_mp4s) == len(audios) == len(slides)):
        raise TimelineError(
            f"timeline inputs disagree in length: {len(slide_mp4s)} mp4s, "
            f"{len(audios)} audios, {len(slides)} slides"
        )

    spans: List[SlideSpan] = []
    cursor = 0.0
    for idx, (mp4, audio, slide) in enumerate(zip(slide_mp4s, audios, slides)):
        encoded = probe_duration(mp4)
        audio_s = probe_duration(audio)
        spans.append(SlideSpan(
            idx=idx,
            start_s=round(cursor, 6),
            end_s=round(cursor + encoded, 6),
            audio_s=round(audio_s, 6),
            encoded_s=round(encoded, 6),
            pad_s=round(max(encoded - audio_s, 0.0), 6),
            role=slide.get("role"),
            label=slide.get("label"),
            narration=slide.get("narration") or "",
        ))
        cursor += encoded
    return spans


def timeline_duration(spans: Sequence[SlideSpan]) -> float:
    """Total measured master length (0.0 for an empty timeline)."""
    return round(spans[-1].end_s, 6) if spans else 0.0


def audio_offsets(spans: Sequence[SlideSpan]) -> List[float]:
    """Each slide's start offset, for callers that build per-slide captions."""
    return [s.start_s for s in spans]


def drift_report(spans: Sequence[SlideSpan], assumed_pad_s: float = PAD_S) -> Dict[str, Any]:
    """Quantify how far the OLD arithmetic timeline would have been wrong.

    The old code assumed `start = sum(audio + PAD)` for every slide. This
    returns, per slide, the error that assumption would have introduced, plus
    the worst case. Kept in the render report so the improvement is visible
    rather than asserted.
    """
    assumed = 0.0
    worst = 0.0
    per_slide: List[Dict[str, float]] = []
    for s in spans:
        err = s.start_s - assumed
        worst = max(worst, abs(err))
        per_slide.append({"idx": s.idx, "assumed_start_s": round(assumed, 6),
                          "measured_start_s": s.start_s, "error_s": round(err, 6)})
        assumed += s.audio_s + assumed_pad_s
    return {
        "assumed_pad_s": assumed_pad_s,
        "worst_drift_s": round(worst, 6),
        "per_slide": per_slide,
    }