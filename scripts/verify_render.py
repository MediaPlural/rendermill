#!/usr/bin/env python3
"""verify_render.py — machine-check a rendered deck without watching it.

The §5.2 gates check the INPUTS before money is spent. This checks the OUTPUT
after the money is spent, which is the half that was unguarded: a render can
pass every preflight gate and still ship a file whose captions never burned,
whose timeline has a gap, or whose background is not the theme it claims.

Checks (all exit non-zero on failure, so CI can gate on it):

  container   the MP4 exists, has an h264 video and an aac audio stream
  duration    container duration matches the report within one frame
  timeline    spans are contiguous — no gap, no overlap, no negative length
  captions    the report says the burn actually happened
  pixels      every chunk's burned ink is provably in its frame (per-chunk
              sampling against the pre-caption master), and the dominant
              frame colour matches the declared theme background

Usage:
    python scripts/verify_render.py examples/deterministic-demo
    python scripts/verify_render.py examples/demo --mp4 out/custom.mp4
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# One frame at 30fps. Anything tighter is noise from the encoder.
FRAME_TOLERANCE_S = 1.0 / 30.0

# Per-chunk diff threshold against the pre-caption master.
#
# Scales with the chunk's text length so a 1-word widow caption (the shipped
# demo's last slide measures ~0.42% band diff at "all.") still passes when it
# IS present, while a chunk whose burn silently fails (~0.00% diff) fails
# regardless of chunk length. The floor sits under the measured 1-word
# baseline; the slope approximates the linear growth in glyph pixels.
_CHUNK_THRESHOLD_FLOOR = 0.0025   # 0.25% of band
_CHUNK_THRESHOLD_SLOPE = 0.0003   # additional per character of caption text


class CheckFailed(Exception):
    pass


def _run(cmd, *, text=True):
    # text=False for raw pixel reads: ffmpeg's rawvideo stdout is binary and
    # text=True would both corrupt it and raise UnicodeDecodeError on it.
    return subprocess.run(cmd, capture_output=True, text=text)


def _probe_duration(path: Path) -> float:
    res = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    if res.returncode != 0:
        raise CheckFailed(f"ffprobe failed on {path.name}: {res.stderr.strip()[:200]}")
    try:
        return float(res.stdout.strip())
    except ValueError as e:
        raise CheckFailed(f"non-numeric duration {res.stdout!r}") from e


def _streams(path: Path):
    res = _run(["ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,codec_name", "-of", "json", str(path)])
    if res.returncode != 0:
        raise CheckFailed(f"ffprobe stream probe failed: {res.stderr.strip()[:200]}")
    return json.loads(res.stdout).get("streams", [])


def _frame_rgb(path: Path, at_s: float, width: int = 240, height: int = 135):
    """Decode one frame downscaled to width x height as (w, h, 3) bytes."""
    raw = _run([
        "ffmpeg", "-v", "error", "-ss", f"{at_s:.3f}", "-i", str(path),
        "-frames:v", "1", "-vf", f"scale={width}:{height}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], text=False)
    if raw.returncode != 0:
        err = raw.stderr.decode("utf8", "replace")[:200]
        raise CheckFailed(f"frame decode failed at {at_s:.2f}s: {err}")
    data = raw.stdout
    need = width * height * 3
    if len(data) < need:
        raise CheckFailed(f"short frame at {at_s:.2f}s: {len(data)} < {need} bytes")
    return width, height, data[:need]


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _parse_srt_time(s: str) -> float:
    """Parse an SRT timestamp like `00:00:01,516` -> 1.516 seconds."""
    h, m, rest = s.split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000.0


def parse_srt(path: Path) -> list:
    """Parse an SRT file into [(start_s, end_s, text), ...].

    Used by check_pixels to know what captions to sample for and at what
    midpoint. The text field is the joined body lines after the timing
    line and is the ground truth that gets burned into the master.
    """
    out = []
    blocks = re.split(r"\n\s*\n", path.read_text().strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        # lines[0] = caption index, lines[1] = timing, lines[2..] = body
        m = re.match(
            r"(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)", lines[1],
        )
        if not m:
            continue
        start = _parse_srt_time(m.group(1))
        end = _parse_srt_time(m.group(2))
        text = "\n".join(lines[2:]).replace("\n", " ").strip()
        out.append((start, end, text))
    return out


def chunk_diff_threshold(text: str) -> float:
    """Lowest band-diff fraction that proves ink was drawn for THIS chunk.

    A 1-word widow caption ("all.", 4 chars) measures ~0.42% band diff at the
    pre-caption master's mid-frame; the floor sits just below that so a
    present widow still passes. A longer chunk draws more glyphs and tolerates
    a proportionally higher threshold. A chunk whose burn is missing produces
    ~0.00% diff and fails regardless of length.
    """
    chars = max(len(text), 1)
    return _CHUNK_THRESHOLD_FLOOR + _CHUNK_THRESHOLD_SLOPE * chars


def _resolve_captions_srt(mp4: Path, project_dir: Path = None) -> Path | None:
    """Find the SRT sidecar that lists the captions the MP4 should contain.

    The per-chunk pixel check parses this file to know WHICH captions to
    sample for and at WHAT midpoint. Without it, the check reverts to a
    single-sample bright-pixel heuristic that missed a 75% burn drop in QA
    audit F1.

    Search order:
    1. `<project_dir>/video_build/captions.srt` (the canonical home)
    2. `<mp4.parent>/video_build/captions.srt` (works when `out_path`
       defaulted to `<project_dir>/out/<title>.mp4`)
    3. `<mp4.parent.parent>/video_build/captions.srt` (a sibling layout)
    """
    candidates = []
    if project_dir is not None:
        candidates.append(Path(project_dir) / "video_build" / "captions.srt")
    candidates.append(mp4.parent / "video_build" / "captions.srt")
    candidates.append(mp4.parent.parent / "video_build" / "captions.srt")
    for c in candidates:
        if c.exists():
            return c
    return None


def _resolve_intermediate_video(mp4: Path, project_dir: Path = None) -> Path | None:
    """Find the pre-caption `concat_raw.mp4` to diff against.

    The per-chunk pixel check compares each caption's burned frame against
    the same frame in the master BEFORE captions were burned, so any band
    pixel that differs IS caption ink. Without the intermediate, the check
    falls back to a bright-pixel heuristic that QA audit F1 showed can
    silently pass a 75%-missing poisoned master.
    """
    candidates = []
    if project_dir is not None:
        candidates.append(Path(project_dir) / "video_build" / "concat_raw.mp4")
    candidates.append(mp4.parent / "video_build" / "concat_raw.mp4")
    candidates.append(mp4.parent.parent / "video_build" / "concat_raw.mp4")
    for c in candidates:
        if c.exists():
            return c
    return None


def check_container(mp4: Path) -> str:
    if not mp4.exists():
        raise CheckFailed(f"no output file at {mp4}")
    codecs = {s.get("codec_type"): s.get("codec_name") for s in _streams(mp4)}
    if codecs.get("video") != "h264":
        raise CheckFailed(f"expected h264 video, got {codecs.get('video')!r}")
    if not codecs.get("audio"):
        raise CheckFailed(f"no audio stream in {mp4.name}")
    return f"{codecs['video']}/{codecs['audio']}, {mp4.stat().st_size / 1e6:.2f} MB"


def check_duration(mp4: Path, report: dict) -> str:
    actual = _probe_duration(mp4)
    claimed = report.get("duration_s")
    if claimed is None:
        raise CheckFailed("report has no duration_s")
    if abs(actual - claimed) > FRAME_TOLERANCE_S:
        raise CheckFailed(f"duration mismatch: container {actual:.3f}s vs "
                          f"report {claimed:.3f}s")
    return f"{actual:.3f}s (matches report)"


def check_timeline(report: dict) -> str:
    tl = report.get("timeline")
    if not tl:
        raise CheckFailed("report has no timeline block")
    if tl.get("source") != "measured":
        raise CheckFailed(f"timeline source is {tl.get('source')!r}, not 'measured'")
    spans = tl.get("spans") or []
    if not spans:
        raise CheckFailed("timeline has no spans")
    if abs(spans[0].get("start_s", -1)) > 1e-6:
        raise CheckFailed(f"first slide starts at {spans[0]['start_s']}, expected 0")
    worst_gap = 0.0
    for a, b in zip(spans, spans[1:]):
        gap = b["start_s"] - a["end_s"]
        worst_gap = max(worst_gap, abs(gap))
        if gap < -1e-6:
            raise CheckFailed(f"overlap between slide {a['idx']} and {b['idx']}: {gap:+.4f}s")
        if gap > 1e-6:
            raise CheckFailed(f"gap between slide {a['idx']} and {b['idx']}: {gap:+.4f}s")
    total = tl.get("measured_total_s")
    if total is not None and abs(spans[-1]["end_s"] - total) > 1e-3:
        raise CheckFailed(f"last span ends {spans[-1]['end_s']:.3f} but "
                          f"measured_total_s={total:.3f}")
    drift = tl.get("arithmetic_drift_worst_s")
    drift_txt = f", arithmetic would have drifted {drift * 1000:.0f}ms" if drift else ""
    return f"{len(spans)} spans contiguous (max seam error {worst_gap * 1000:.2f}ms){drift_txt}"


def check_captions(report: dict) -> str:
    caps = report.get("captions")
    if caps is None:
        return "no captions configured"
    if not caps.get("burned"):
        raise CheckFailed(f"captions did NOT burn: {caps.get('failure')!r}")
    n = caps.get("chunks", 0)
    if not n:
        raise CheckFailed("captions burned but produced 0 chunks")
    return f"{n} chunks burned"


def check_pixels(mp4: Path, report: dict, project_dir: Path = None) -> str:
    """Prove each caption actually drew ink onto the frame.

    The caption band is the bottom ~18% of the frame. A burned-in caption
    puts glyph pixels there; a missing caption leaves it flat.

    Per-chunk sampling at each chunk's midpoint against the pre-caption
    `video_build/concat_raw.mp4`. A single sample only proves that SOME
    chunk burned — a poisoned master with 15 of 20 captions silently
    dropping out measures clean at the first chunk. Sampling every chunk
    fails the missing ones regardless of length.

    The per-chunk threshold scales with the chunk's text length so a 1-word
    widow still passes when it IS present (~0.42% band diff measured in the
    shipped demo's "all." caption at t=40.05s), while a missing chunk
    (~0.00%) fails no matter how long it would have been.
    """
    spans = report["timeline"]["spans"]
    if not spans:
        raise CheckFailed("no spans to sample")

    # The master measured-total sets the upper bound for any caption.
    total_s = spans[-1]["end_s"]

    # The pre-caption intermediate. Without it, the bright-pixel fallback
    # had ~2-4% noise (slide content itself paints the band), so a threshold
    # between 0% and ~5% was arbitrary. Per-chunk diff against the master
    # is decisive: any pixel in the caption band that differs IS caption ink.
    raw = _resolve_intermediate_video(mp4, project_dir)

    srt = _resolve_captions_srt(mp4, project_dir)
    chunks = parse_srt(srt) if srt else []
    if not chunks:
        raise CheckFailed(
            f"no captions.srt under {{video_build/}} — cannot per-chunk "
            f"sample; the previous version of this check missed 75% burn "
            f"drops (QA audit F1)."
        )

    # Skip captions entirely outside the measured master so a stale SRT
    # can't trigger false alarms.
    chunks = [(s, e, t) for s, e, t in chunks if t.strip()
              and s < total_s and e > 0]
    if not chunks:
        raise CheckFailed("captions.srt has no in-range chunks")

    # Resolve the declared theme to its real background colour so we can
    # prove the pixels are actually that theme, not some other palette.
    bg = None
    theme_name = report.get("theme")
    if theme_name:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from rendermill import theme as _theme
            bg = _hex_to_rgb(_theme.resolve(theme_name).bg)
        except Exception:
            bg = None

    w, h = 240, 135
    band_top = int(h * 0.82)
    band_pixels = (h - band_top) * w

    raw_data_cache = {}

    def _raw_at(at_s: float):
        """Cache pre-caption master frames by mid-chunk timestamp to avoid
        re-decoding the same frame across multiple sample rounds."""
        if raw is None:
            return None
        # Round to the nearest 0.05s — caption midpoints cluster.
        key = round(at_s / 0.05) * 0.05
        if key not in raw_data_cache:
            _, _, raw_data_cache[key] = _frame_rgb(
                raw, at_s, width=w, height=h,
            )
        return raw_data_cache[key]

    def _band_diff(data, raw_data):
        """Fraction of band pixels whose summed-channel delta > 24."""
        if raw_data is None:
            # No pre-caption master to diff against — caller falls back to
            # a bright-pixel count and uses the same threshold.
            return -1.0
        changed = 0
        for y in range(band_top, h):
            base = y * w * 3
            for x in range(w):
                i = base + x * 3
                if (abs(data[i] - raw_data[i])
                        + abs(data[i + 1] - raw_data[i + 1])
                        + abs(data[i + 2] - raw_data[i + 2])) > 24:
                    changed += 1
        return changed / max(band_pixels, 1)

    failures = []
    sampled = 0
    sampled_at = []
    for s, e, text in chunks:
        at_s = max(0.05, min((s + e) / 2.0, total_s - 0.05))
        try:
            _, _, pixels = _frame_rgb(mp4, at_s, width=w, height=h)
        except CheckFailed as exc:
            failures.append(f"chunk '{text[:40]!r}' at {at_s:.2f}s: {exc}")
            continue
        if raw is not None and raw.exists():
            raw_data = _raw_at(at_s)
            if raw_data is not None:
                frac = _band_diff(pixels, raw_data)
                threshold = chunk_diff_threshold(text)
                if frac < threshold:
                    failures.append(
                        f"chunk '{text[:40]!r}' at {at_s:.2f}s: only "
                        f"{frac * 100:.2f}% band differs (threshold "
                        f"{threshold * 100:.2f}% for {len(text)} chars) — "
                        f"this caption did not burn"
                    )
                    continue
        else:
            # Without a pre-caption master, fall back to a bright-pixel check.
            # Treat the same per-chunk threshold as a fraction of the band's
            # bright count rather than a diff count (same units, different
            # numerator).
            bright = 0
            for y in range(band_top, h):
                base = y * w * 3
                for x in range(w):
                    i = base + x * 3
                    lum = (0.2126 * pixels[i] + 0.7152 * pixels[i + 1]
                           + 0.0722 * pixels[i + 2])
                    if lum > 140:
                        bright += 1
            frac = bright / max(band_pixels, 1)
            threshold = chunk_diff_threshold(text)
            if frac < threshold:
                failures.append(
                    f"chunk '{text[:40]!r}' at {at_s:.2f}s: only "
                    f"{frac * 100:.2f}% bright pixels (threshold "
                    f"{threshold * 100:.2f}%) — this caption did not "
                    f"burn (WEAK — no pre-caption master to diff against)"
                )
                continue
        sampled += 1
        sampled_at.append(at_s)

    if failures:
        n = len(chunks)
        head = "; ".join(failures[:3])
        more = f"; ... {len(failures) - 3} more" if len(failures) > 3 else ""
        raise CheckFailed(
            f"{len(failures)} of {n} captions did not burn: {head}{more}"
        )

    msg = (f"per-chunk ink confirmed across {sampled} captions "
           f"(first @ {sampled_at[0]:.2f}s, last @ {sampled_at[-1]:.2f}s)")
    if bg:
        # Dominant colour of the top band should be the theme background.
        # Sample at the first chunk's midpoint — the theme is deck-wide.
        at_s = sampled_at[0]
        try:
            _, _, pixels = _frame_rgb(mp4, at_s, width=w, height=h)
            counts = {}
            for y in range(0, max(1, band_top - 1)):
                base = y * w * 3
                for x in range(w):
                    i = base + x * 3
                    key = (pixels[i], pixels[i + 1], pixels[i + 2])
                    counts[key] = counts.get(key, 0) + 1
            dom = max(counts, key=counts.get)
            if max(abs(dom[i] - bg[i]) for i in range(3)) > 12:
                raise CheckFailed(
                    f"dominant frame colour {dom} is not the declared "
                    f"theme bg {bg}")
            msg += (f", bg matches theme "
                    f"(#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x})")
        except CheckFailed:
            raise
        except Exception:
            # Theme bg match is a bonus check; never block on a probe failure.
            pass
    return msg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", help="Project directory containing out/rendermill-report.json")
    ap.add_argument("--mp4", default=None, help="Override the MP4 path.")
    args = ap.parse_args()

    proj = Path(args.project_dir).resolve()
    report_path = proj / "out" / "rendermill-report.json"
    if not report_path.exists():
        print(f"FAIL  no report at {report_path}", file=sys.stderr)
        return 2
    report = json.loads(report_path.read_text())
    mp4 = Path(args.mp4) if args.mp4 else Path(report["out_path"])
    if not mp4.is_absolute():
        mp4 = proj / mp4

    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        print("FAIL  ffmpeg/ffprobe not on PATH", file=sys.stderr)
        return 2

    checks = [
        ("container", lambda: check_container(mp4)),
        ("duration",  lambda: check_duration(mp4, report)),
        ("timeline",  lambda: check_timeline(report)),
        ("captions",  lambda: check_captions(report)),
        ("pixels",    lambda: check_pixels(mp4, report, project_dir=proj)),
    ]
    print(f"verifying {mp4.name}")
    failed = 0
    for name, fn in checks:
        try:
            print(f"  PASS  {name:<9} {fn()}")
        except CheckFailed as e:
            failed += 1
            print(f"  FAIL  {name:<9} {e}")
    if failed:
        print(f"\n{failed}/{len(checks)} checks FAILED")
        return 1
    print(f"\nall {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
