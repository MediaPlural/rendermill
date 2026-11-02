"""Timeline is measured, not assumed.

The regression these guard is specific: the old render computed each slide's
start as a running sum of `audio + 0.15`, which is an assumption about the
encoder. ffprobe-measured offsets must be used instead, and a slide that came
out longer than its audio must push everything after it by exactly that much.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rendermill import timeline as tl


def _fake_probe(monkeypatch, table):
    """Pretend ffprobe returns `table[filename]` seconds."""
    def fake(path):
        name = Path(path).name
        if name not in table:
            raise tl.TimelineError(f"cannot probe missing file: {path}")
        dur = table[name]
        if dur <= 0:
            raise tl.TimelineError(f"{name} measured {dur}s — refusing to build a timeline from it")
        return dur
    monkeypatch.setattr(tl, "probe_duration", fake)


def test_pad_defined_once():
    """PAD_S is the single definition. gates/clips/ffmpeg import it from here,
    which is how the four hand-copied 0.15s values drifted apart before.

    The check is identity, not a grep: a module may legitimately alias the
    import (gates keeps SLIDE_PAD_S for back-compat), but no module may
    assign its own literal 0.15.
    """
    assert tl.PAD_S == 0.15
    for mod_name in ("gates", "clips", "ffmpeg_video"):
        mod = __import__(f"rendermill.{mod_name}", fromlist=[mod_name])
        assert mod.PAD_S is tl.PAD_S, f"{mod_name} restates its own PAD_S"
        # No module may define the pad as a bare literal of its own.
        assigns = re.findall(r"^(\w*PAD\w*)\s*=\s*([0-9.]+)\s*$",
                             Path(mod.__file__).read_text(), re.M)
        assert not assigns, f"{mod_name} defines its own pad literal: {assigns}"


def test_build_timeline_uses_measured_durations(monkeypatch):
    """A slide whose MP4 is 40ms longer than its audio pushes the next slide
    by 40ms — that is what actually happened to the video."""
    _fake_probe(monkeypatch, {
        "s0.mp4": 3.000, "s0.wav": 2.850,
        "s1.mp4": 5.000, "s1.wav": 4.900,   # 100ms longer than audio
        "s2.mp4": 2.000, "s2.wav": 2.000,
    })
    spans = tl.build_timeline(
        [Path("s0.mp4"), Path("s1.mp4"), Path("s2.mp4")],
        [Path("s0.wav"), Path("s1.wav"), Path("s2.wav")],
        [{"narration": "a"}, {"narration": "b"}, {"narration": "c"}],
    )
    assert [s.start_s for s in spans] == [0.0, 3.0, 8.0]
    # Arithmetic on audio+pad would have put s2 at 2.850+.15+4.900+.15 = 8.05.
    assert spans[2].start_s != pytest.approx(8.05), "timeline regressed to arithmetic"
    assert spans[1].pad_s == pytest.approx(0.100)


def test_build_timeline_length_mismatch_fails_loudly():
    with pytest.raises(tl.TimelineError, match="disagree in length"):
        tl.build_timeline([Path("a.mp4")], [Path("a.wav"), Path("b.wav")], [{"narration": "a"}])


def test_probe_duration_missing_file(monkeypatch, tmp_path):
    with pytest.raises(tl.TimelineError, match="missing file"):
        tl.probe_duration(tmp_path / "nope.mp4")


def test_probe_duration_no_ffprobe(monkeypatch, tmp_path):
    """No ffprobe means no trustworthy timeline — fail, do not fall back."""
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")

    def boom(*a, **k):
        raise FileNotFoundError("ffprobe")
    monkeypatch.setattr(tl.subprocess, "run", boom)
    with pytest.raises(tl.TimelineError, match="ffprobe not found"):
        tl.probe_duration(f)


def test_probe_duration_non_numeric(monkeypatch, tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": "N/A", "stderr": ""})())
    with pytest.raises(tl.TimelineError, match="non-numeric"):
        tl.probe_duration(f)


def test_probe_duration_zero_rejected(monkeypatch, tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": "0.000000", "stderr": ""})())
    with pytest.raises(tl.TimelineError, match="refusing to build"):
        tl.probe_duration(f)


def test_drift_report_quantifies_the_old_arithmetic(monkeypatch):
    """drift_report must show how wrong the assumption would have been, so the
    fix is visible in the render report instead of merely asserted.

    Drift is observed one slide LATE: a mismatch between slide N's encoded
    length and its audio+pad only moves slide N+1's start. So the fixture puts
    the mismatch on slide 0 and reads the error off slide 1.
    """
    s0_mp4, s0_wav = 3.400, 2.850      # assumption: 2.850 + .15 = 3.000, truth 3.400
    expected_drift = s0_mp4 - (s0_wav + tl.PAD_S)
    assert expected_drift > 0.05, "fixture must produce real drift to be meaningful"

    _fake_probe(monkeypatch, {"s0.mp4": s0_mp4, "s0.wav": s0_wav,
                              "s1.mp4": 2.000, "s1.wav": 2.000})
    spans = tl.build_timeline(
        [Path("s0.mp4"), Path("s1.mp4")],
        [Path("s0.wav"), Path("s1.wav")],
        [{"narration": "a"}, {"narration": "b"}],
    )
    # Measured: slide 1 starts at 3.400. Assumed: 3.000.
    assert spans[1].start_s == pytest.approx(s0_mp4)
    rep = tl.drift_report(spans)
    assert rep["assumed_pad_s"] == tl.PAD_S
    assert rep["worst_drift_s"] == pytest.approx(expected_drift)
    assert rep["per_slide"][1]["error_s"] == pytest.approx(expected_drift)
    assert rep["per_slide"][0]["error_s"] == pytest.approx(0.0)


def test_drift_report_is_zero_when_the_assumption_holds(monkeypatch):
    """A perfectly-encoded deck reports no drift — the report must not
    manufacture a number."""
    _fake_probe(monkeypatch, {"s0.mp4": 3.000, "s0.wav": 2.850,
                              "s1.mp4": 2.000, "s1.wav": 1.850})
    spans = tl.build_timeline(
        [Path("s0.mp4"), Path("s1.mp4")], [Path("s0.wav"), Path("s1.wav")],
        [{"narration": "a"}, {"narration": "b"}],
    )
    assert tl.drift_report(spans)["worst_drift_s"] == pytest.approx(0.0, abs=1e-9)


def test_timeline_duration_and_offsets(monkeypatch):
    _fake_probe(monkeypatch, {"s0.mp4": 2.0, "s0.wav": 1.9, "s1.mp4": 3.0, "s1.wav": 3.0})
    spans = tl.build_timeline(
        [Path("s0.mp4"), Path("s1.mp4")], [Path("s0.wav"), Path("s1.wav")],
        [{"narration": "a"}, {"narration": "b"}],
    )
    assert tl.timeline_duration(spans) == 5.0
    assert tl.audio_offsets(spans) == [0.0, 2.0]
    assert tl.timeline_duration([]) == 0.0


def test_empty_timeline_is_not_an_error(monkeypatch):
    assert tl.build_timeline([], [], []) == []


def test_span_serialises_for_the_report():
    s = tl.SlideSpan(idx=0, start_s=0.0, end_s=1.0, audio_s=0.9, encoded_s=1.0,
                     pad_s=0.1, role="body", label="Intro", narration="hi")
    d = s.to_dict()
    assert d["label"] == "Intro" and d["pad_s"] == 0.1