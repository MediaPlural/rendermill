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
  pixels      the caption band really contains glyph pixels (a burned-in
              caption, not just a report claim), and the dominant frame colour
              matches the declared theme background

Usage:
    python scripts/verify_render.py examples/deterministic-demo
    python scripts/verify_render.py examples/demo --mp4 out/custom.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# One frame at 30fps. Anything tighter is noise from the encoder.
FRAME_TOLERANCE_S = 1.0 / 30.0


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


def check_pixels(mp4: Path, report: dict) -> str:
    """Prove the pixels agree with the report.

    The caption band is the bottom ~18% of the frame. A burned caption puts
    bright glyph pixels there; a silent no-caption burn leaves it flat. This
    is the check that catches "the report said True but nothing was drawn".
    """
    spans = report["timeline"]["spans"]
    if not spans:
        raise CheckFailed("no spans to sample")

    # Resolve the declared theme to its real background colour so we can prove
    # the pixels are actually that theme, not some other palette.
    bg = None
    theme_name = report.get("theme")
    if theme_name:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from rendermill import theme as _theme
            bg = _hex_to_rgb(_theme.resolve(theme_name).bg)
        except Exception:
            bg = None  # theme optional; band-contrast check still runs

    # Sample inside a slide that has narration long enough to hold a caption.
    target = None
    for s in spans:
        if s["audio_s"] >= 3.0:
            target = s
            break
    if target is None:
        target = spans[0]
    at_s = target["start_s"] + min(1.2, target["audio_s"] / 2.0)

    w, h, data = _frame_rgb(mp4, at_s)

    # Bottom band = bottom 18% of rows.
    band_top = int(h * 0.82)
    total = (h - band_top) * w

    msg = None
    # PRIMARY: diff against the pre-caption intermediate.
    #
    # A bright-pixel threshold alone has no teeth — slide content itself puts
    # bright pixels in the bottom band (a table row, a panel border), so an
    # uncaptioned master measures 2.3% bright while a captioned one measures
    # 4.0%. Any threshold between those passes or fails arbitrarily.
    #
    # Diffing the SAME timestamp against `video_build/concat_raw.mp4` is
    # decisive: the raw file is the master before the caption burn, so any
    # pixel that differs in the caption band IS caption ink. Verified as a
    # negative control — this check fails on the raw file and passes on the
    # final.
    raw = mp4.parent / "video_build" / "concat_raw.mp4"
    if not raw.exists():
        raw = mp4.parent.parent / "video_build" / "concat_raw.mp4"
    if raw.exists():
        _, _, raw_data = _frame_rgb(raw, at_s, width=w, height=h)
        changed = 0
        for y in range(band_top, h):
            base = y * w * 3
            for x in range(w):
                i = base + x * 3
                if (abs(data[i] - raw_data[i])
                        + abs(data[i + 1] - raw_data[i + 1])
                        + abs(data[i + 2] - raw_data[i + 2])) > 24:
                    changed += 1
        frac = changed / max(total, 1)
        if frac < 0.005:
            raise CheckFailed(
                f"caption band at {at_s:.2f}s is identical to the pre-caption "
                f"master ({frac * 100:.2f}% changed) — captions did not burn"
            )
        msg = (f"caption ink confirmed at {at_s:.2f}s: {frac * 100:.2f}% of the "
               f"band differs from the pre-caption master")
    else:
        # FALLBACK: no intermediate to diff against. Say so plainly rather
        # than implying a guarantee this run cannot make.
        bright = 0
        for y in range(band_top, h):
            row = y * w * 3
            for x in range(w):
                i = row + x * 3
                lum = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]
                if lum > 140:
                    bright += 1
        frac = bright / max(total, 1)
        if frac < 0.005:
            raise CheckFailed(
                f"caption band at {at_s:.2f}s has almost no bright pixels "
                f"({frac * 100:.2f}%)"
            )
        msg = (f"caption band at {at_s:.2f}s: {frac * 100:.2f}% bright pixels "
               f"(WEAK — no pre-caption master to diff against)")

    if bg:
        # Dominant colour of the top band should be the theme background.
        counts = {}
        for y in range(0, max(1, band_top - 1)):
            row = y * w * 3
            for x in range(w):
                i = row + x * 3
                key = (data[i], data[i + 1], data[i + 2])
                counts[key] = counts.get(key, 0) + 1
        dom = max(counts, key=counts.get)
        if max(abs(dom[i] - bg[i]) for i in range(3)) > 12:
            raise CheckFailed(
                f"dominant frame colour {dom} is not the declared theme bg {bg}")
        msg += f", bg matches theme (#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x})"
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
        ("pixels",    lambda: check_pixels(mp4, report)),
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