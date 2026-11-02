"""Backdrop resolution from the backdrops library index (the canon manifest).

Pins the §5.3 / video-slide wiring: a backdrop *preset* resolves through the
bridge-generated ``library_index.json`` into an absolute mp4 path, and
``core.make_video`` performs that resolution before any render work.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_presets_known():
    from rendermill import backdrops
    presets = backdrops.presets()
    assert "brownian_drift" in presets
    assert "field_flow" in presets
    assert "pulse_grid" in presets


def test_resolve_preset_landscape():
    from rendermill import backdrops
    mp4 = backdrops.resolve_mp4("brownian_drift", "landscape")
    assert mp4.exists()
    assert mp4.suffix == ".mp4"


def test_resolve_preset_portrait():
    from rendermill import backdrops
    mp4 = backdrops.resolve_mp4("brownian_drift", "portrait")
    assert mp4.exists()
    assert "3f3dab25cf7f" in mp4.name  # portrait content-hash from the manifest


def test_orientation_mapping():
    from rendermill import backdrops
    assert backdrops.orientation_for("horizontal") == "landscape"
    assert backdrops.orientation_for("short") == "portrait"
    assert backdrops.orientation_for("vertical") == "portrait"


def test_resolve_unknown_preset_raises():
    from rendermill import backdrops
    with pytest.raises(FileNotFoundError):
        backdrops.resolve_mp4("nope_not_a_preset", "landscape")


def test_apply_backdrop_slide_preset():
    from rendermill import backdrops
    slide = {"type": "video", "backdrop_preset": "pulse_grid", "title": "Demo"}
    path = backdrops.apply_backdrop(slide, orientation="short")
    assert path is not None and path.exists()
    assert slide["backdrop"] == str(path)


def test_apply_backdrop_dict():
    from rendermill import backdrops
    slide = {"backdrop": {"preset": "field_flow"}}
    path = backdrops.apply_backdrop(slide, orientation="horizontal")
    assert path is not None and path.exists()
    assert slide["backdrop"] == str(path)


def test_apply_backdrop_default_preset():
    from rendermill import backdrops
    slide = {"type": "html"}
    path = backdrops.apply_backdrop(slide, orientation="horizontal",
                                    default_preset="brownian_drift")
    assert path is not None and path.exists()
    assert slide["backdrop"] == str(path)


def test_apply_backdrop_explicit_path_untouched():
    from rendermill import backdrops
    slide = {"backdrop": "assets/custom.mp4"}
    path = backdrops.apply_backdrop(slide, orientation="horizontal")
    assert path is None
    assert slide["backdrop"] == "assets/custom.mp4"


def test_apply_backdrop_no_backdrop():
    from rendermill import backdrops
    slide = {"type": "image"}
    assert backdrops.apply_backdrop(slide, orientation="horizontal") is None
    assert "backdrop" not in slide


def test_make_video_resolves_backdrop_preset_before_render(tmp_path, monkeypatch):
    """Dry-run wiring: make_video resolves a backdrop preset (and the Viiy
    voice) before any PNG/TTS work. We stop it at the render_pngs boundary."""
    from rendermill import core

    slides = [{
        "type": "video",
        "narration": "Hello world.",
        "backdrop_preset": "brownian_drift",
    }]
    workdir = tmp_path / "work"
    workdir.mkdir()

    class _Sentinel(Exception):
        pass

    def _stop(slides_arg, **kwargs):
        # apply_backdrop runs in make_video *before* render_pngs is reached.
        raise _Sentinel()

    monkeypatch.setattr(core, "render_pngs", _stop)

    with pytest.raises(_Sentinel):
        core.make_video(
            slides, workdir=workdir, orientation="horizontal",
            out_path=workdir / "video.mp4", skip_preview=True,
        )

    assert Path(slides[0]["backdrop"]).exists()
