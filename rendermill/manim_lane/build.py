"""build — the Manim-lane CLI orchestrator.

Pipeline: validate (JSON Schema) -> lint (continuity law) -> codegen -> render
(--tier). Renders per-scene MP4s that drop into the rendermill fork's concat
stage unchanged.

Tiers (DESIGN.md §4):
    animatic  manim -ql  (854x480 @ 15fps) — cheap preview pass (default)
    full      manim -qh  (1080p60)         — paid pass, winners only
    still     manim -s   keyframe stills   — cheapest pacing review

Invoked two ways (the WIRING LAW: built is installed, first-class):
    rendermill manim --scene-list x.json --tier animatic
    python -m rendermill.manim_lane --scene-list x.json --tier animatic
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import codegen, lint, schema

TIER_FLAGS = {
    "animatic": ["-ql"],   # 854x480 @ 15fps
    "full": ["-qh"],       # 1080p60
    "still": ["-s", "-ql"],
}
TIER_QUALITY_DIR = {"animatic": "480p15", "full": "1080p60", "still": "480p15"}

# Exit codes (documented; the rendermill CLI surfaces them as the subcommand rc).
RC_OK = 0
RC_BAD_ARGS = 2
RC_MANIM_MISSING = 3
RC_RENDER_FAILED = 4


class ManimError(Exception):
    """The render could not run (manim missing or non-zero exit)."""


def _check_manim() -> bool:
    try:
        import manim  # noqa: F401
        return True
    except ImportError:
        return False


def validate(scene_list: dict) -> List[str]:
    return schema.validate(scene_list)


def lint_scene_list(scene_list: dict) -> List[str]:
    return lint.lint_scene_list(scene_list)


def probe_duration(mp4: Path) -> Optional[float]:
    """ffprobe a rendered MP4's duration in seconds (None if unavailable)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(mp4)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def render(scene_list: dict, tier: str, generated_py: Path, media_dir: Path) -> Path:
    """Codegen -> write the generated scene -> run manim. Returns the MP4 path."""
    code = codegen.generate_scene_module(scene_list)
    generated_py.write_text(code, encoding="utf-8")
    if not _check_manim():
        raise ManimError(
            "manim is not installed in this interpreter. "
            "Install the lane extra: pip install 'rendermill[manim]'"
        )
    flags = TIER_FLAGS[tier]
    quality_dir = TIER_QUALITY_DIR[tier]
    module_name = generated_py.stem
    cmd = [
        sys.executable, "-m", "manim", *flags,
        str(generated_py),
        "-o", "ExplainerDemo.mp4",
        "--media_dir", str(media_dir),
    ]
    print("  $", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(generated_py.parent))
    if result.returncode != 0:
        raise ManimError(f"manim render FAILED (exit {result.returncode})")
    # manim writes to media/videos/{module_name}/{quality}/<output>.mp4
    out_mp4 = media_dir / "videos" / module_name / quality_dir / "ExplainerDemo.mp4"
    if not out_mp4.exists():
        hits = list(media_dir.glob(f"videos/**/{quality_dir}/ExplainerDemo.mp4"))
        out_mp4 = hits[0] if hits else out_mp4
    return out_mp4


def _dry_run(scene_list: dict, generated_py: Path) -> int:
    """Prove validate -> codegen -> import of a generated scene, no render.

    Writes the generated scene to disk and imports it as a module to prove the
    generated code compiles and resolves its base class. The import needs manim
    (the generated scene subclasses `ExplainerScene`), so it is skipped when
    manim is absent — validate + codegen + on-disk write already succeeded.
    """
    code = codegen.generate_scene_module(scene_list)
    generated_py.write_text(code, encoding="utf-8")
    print(f"  codegen -> {generated_py} ({len(code)} bytes)")
    if not _check_manim():
        print("  dry-run: manim not installed — skipping generated-scene import")
        print("  (validate + codegen + on-disk write all succeeded)")
        return RC_OK
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rendermill_manim_generated", str(generated_py))
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    cls = getattr(mod, "ExplainerDemo")
    print(f"  dry-run: imported generated scene -> {cls.__name__} (ExplainerScene subclass)")
    return RC_OK


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="rendermill manim",
        description="Manim explainer lane: validate -> lint -> codegen -> render.",
    )
    ap.add_argument("--scene-list", required=True,
                    help="Path to the scene-list JSON (the contract).")
    ap.add_argument("--tier", choices=sorted(TIER_FLAGS), default="animatic",
                    help="Render tier (default: animatic).")
    ap.add_argument("--media-dir", default=None,
                    help="manim media output dir (default: ./media).")
    ap.add_argument("--out-dir", default=None,
                    help="Dir for the generated scene file (default: <media-dir>/scenes).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Stop after codegen + generated-scene import; do not render.")
    args = ap.parse_args(argv)

    try:
        scene_list = json.loads(Path(args.scene_list).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read scene list: {e}", file=sys.stderr)
        return RC_BAD_ARGS

    beats = scene_list.get("beats", [])
    print(f"[1/4] validate {scene_list.get('video_id')!r} ({len(beats)} beats)")
    errors = validate(scene_list)
    if errors:
        for e in errors:
            print(f"  schema error: {e}", file=sys.stderr)
        print("scene-list validation FAILED", file=sys.stderr)
        return RC_BAD_ARGS

    print("[2/4] lint (continuity law, hook gate)")
    lint_errors = lint_scene_list(scene_list)
    if lint_errors:
        for e in lint_errors:
            print(f"  lint error: {e}", file=sys.stderr)
        print("scene-list lint FAILED", file=sys.stderr)
        return RC_BAD_ARGS
    for w in lint.lint_hook(scene_list):
        print(f"  warn: {w}")

    media_dir = Path(args.media_dir).resolve() if args.media_dir else Path.cwd() / "media"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else media_dir / "scenes"
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_py = out_dir / "generated_explainer.py"

    if args.dry_run:
        print("[3/4] codegen + generated-scene import (dry-run, no render)")
        return _dry_run(scene_list, generated_py)

    print(f"[3/4] codegen -> {generated_py.name}")
    print(f"[4/4] render ({args.tier})")
    try:
        out_mp4 = render(scene_list, args.tier, generated_py, media_dir)
    except ManimError as e:
        print(f"  {e}", file=sys.stderr)
        return RC_MANIM_MISSING if "not installed" in str(e) else RC_RENDER_FAILED
    print(f"  OK -> {out_mp4}")
    dur = probe_duration(out_mp4)
    if dur is not None:
        print(f"  ffprobe duration: {dur:.3f}s")
    else:
        print("  ffprobe duration: (ffprobe not available)")
    return RC_OK


if __name__ == "__main__":
    raise SystemExit(main())
