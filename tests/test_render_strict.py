"""Pin the F6 burn-failure escape hatch: `rendermill render --strict` exits
non-zero when the caption burn did not complete, instead of letting a
caption-less MP4 ship out the door with exit 0.

The render path itself (core.make_video) cannot raise on a burn failure
without breaking the existing free-flow demos that re-attempt the burn on
the user's machine. The strict flag upgrades the post-render report check
into a process-level exit code so CI without the verifier cannot silently
ship (QA audit F6).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse  # noqa: E402

from rendermill import cli  # noqa: E402


def _make_report(workdir: Path, *, burned: bool, failure: str | None,
                 out_path: Path):
    """Build a minimal rendermill-report.json with the burn bits.

    `workdir` is where cmd_render looks for the report — usually
    `<project_dir>/video_build/`.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    report = {
        "tool": "rendermill",
        "out_path": str(out_path),
        "captions": {
            "chunks": 0 if not burned else 19,
            "burned": burned,
            "srt": None,
            "failure": failure,
        },
        "timeline": {
            "source": "measured",
            "spans": [],
            "measured_total_s": 0.0,
        },
    }
    (workdir / "rendermill-report.json").write_text(json.dumps(report))
    return workdir / "rendermill-report.json"


def _strict_args(project_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        project_dir=str(project_dir),
        out=None,
        yes=True,
        no_preview=True,
        preview_ttl=None,
        strict=True,
    )


def test_strict_exits_nonzero_when_burn_failed(tmp_path, monkeypatch):
    """A render whose burn failed must exit 5 under --strict. Without
    --strict the same render exits 0 — that is the regression that F6
    pinned down by allowing CI to opt in."""
    project_dir = tmp_path / "demo"
    out_path = project_dir / "out" / "demo.mp4"
    workdir = project_dir / "video_build"

    # Skip core.make_video entirely — we only care about the report check.
    def _noop_make_video(*a, **k):
        pass
    monkeypatch.setattr(cli.core, "make_video", _noop_make_video)
    monkeypatch.setattr(cli, "cfg_mod", type("M", (), {
        "load": staticmethod(lambda d: type("P", (), {
            "out_dir": workdir,
            "out_path": out_path,
            "project_dir": project_dir,
            "orientation": "horizontal",
            "config": {"captions": {"burn": True}, "music": {}},
            "slides": [],
        })()),
    })())
    monkeypatch.setattr(cli, "_ensure_keys_for", lambda p: None)
    monkeypatch.setattr(cli, "_validate_preview_ttl", lambda v: v)

    _make_report(workdir, burned=False, failure="ffmpeg no libass",
                 out_path=out_path)

    rc = cli.cmd_render(_strict_args(project_dir))
    assert rc == 5, f"expected exit 5 for burn failure under --strict; got {rc}"


def test_strict_passes_when_burn_succeeded(tmp_path, monkeypatch):
    """Successful renders still exit 0 under --strict — it's an opt-in
    hardener, not a behaviour change for working renders."""
    project_dir = tmp_path / "demo"
    out_path = project_dir / "out" / "demo.mp4"
    workdir = project_dir / "video_build"

    def _noop_make_video(*a, **k):
        pass
    monkeypatch.setattr(cli.core, "make_video", _noop_make_video)
    monkeypatch.setattr(cli, "cfg_mod", type("M", (), {
        "load": staticmethod(lambda d: type("P", (), {
            "out_dir": workdir,
            "out_path": out_path,
            "project_dir": project_dir,
            "orientation": "horizontal",
            "config": {"captions": {"burn": True}, "music": {}},
            "slides": [],
        })()),
    })())
    monkeypatch.setattr(cli, "_ensure_keys_for", lambda p: None)
    monkeypatch.setattr(cli, "_validate_preview_ttl", lambda v: v)

    _make_report(workdir, burned=True, failure=None, out_path=out_path)

    rc = cli.cmd_render(_strict_args(project_dir))
    assert rc == 0, f"expected exit 0 for a clean render under --strict; got {rc}"
