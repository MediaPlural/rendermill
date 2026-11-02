"""Pins the F5 missing branches: error paths and passthroughs that the suite
left uncovered. Without these a regression that turns a TimelineError into
a silent return — or that breaks the resolve-passthroughs — could ship
green.
"""
from __future__ import annotations

import subprocess as stdlib_subprocess

import pytest

from rendermill import theme as th
from rendermill import timeline as tl


# ---------- timeline.py: probe_duration's error branches (F5) ----------

def test_probe_duration_nonzero_returncode_raises(monkeypatch, tmp_path):
    """ffprobe returning rc!=0 must surface as TimelineError — not return 0
    and put bad data into the timeline."""
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(
        tl.subprocess, "run",
        lambda *a, **k: type(
            "R", (), {"returncode": 1, "stdout": "", "stderr": "boom"})(),
    )
    with pytest.raises(tl.TimelineError, match="rc=1"):
        tl.probe_duration(f)


def test_probe_duration_timeout_raises(monkeypatch, tmp_path):
    """A hanging ffprobe must surface as TimelineError, not block the render
    until the dispatcher kills it."""
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")

    def boom(*a, **k):
        raise stdlib_subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)

    monkeypatch.setattr(tl.subprocess, "run", boom)
    with pytest.raises(tl.TimelineError, match="timed out"):
        tl.probe_duration(f)


def test_probe_duration_carries_ffprobe_stderr_in_message(monkeypatch, tmp_path):
    """When ffprobe fails, the stderr tail is the only signal the user gets
    for what went wrong. Make sure it isn't silently dropped."""
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(
        tl.subprocess, "run",
        lambda *a, **k: type("R", (), {
            "returncode": 2,
            "stdout": "",
            "stderr": "Invalid data found when processing input",
        })(),
    )
    with pytest.raises(tl.TimelineError, match="Invalid data"):
        tl.probe_duration(f)


# ---------- theme.py: resolve() branches (F5) ----------

def test_resolve_theme_object_is_passthrough():
    """An already-built Theme must come back unchanged — resolve() must not
    rebuild a Theme from a Theme, which would lose dataclass identity and
    break `is` checks elsewhere in the code."""
    t = th.resolve(th.FIELD)
    assert t is th.FIELD, (
        f"resolve(Theme) must pass through; got {t!r} instead of FIELD"
    )


def test_resolve_empty_string_falls_back_to_default():
    """`resolve('')` used to be ambiguous — empty string is a common default
    in caller code. It must NOT raise; it falls back to the default theme.
    """
    t = th.resolve("")
    assert t.name == th.DEFAULT_THEME_NAME
    # And still has the default's caption style as a real CaptionStyle, not
    # some proxy that would crash on `.to_ass()`.
    assert t.caption.to_ass()["Fontname"]


def test_vertical_canvas_halves_horizontal_default_margins():
    """Vertical canvases use a narrower left/right margin (half the
    horizontal default) so captions don't crowd the shorter edges."""
    v = th.caption_style_for("amber", width=1080, height=1920)
    expected = int(th.AMBER.caption.margin_l * 0.5)
    assert v.margin_l == expected, (
        f"vertical margin_l should be half the horizontal default "
        f"({expected}); got {v.margin_l}"
    )
    # Vertical layouts are symmetric — both edges pull in equally.
    assert v.margin_r == v.margin_l, (
        f"vertical margins should be symmetric; got L={v.margin_l} R={v.margin_r}"
    )
