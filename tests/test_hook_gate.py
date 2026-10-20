"""§5.2 + §5.10 gate tests. No network, no ffmpeg."""
from __future__ import annotations

import pytest


def test_estimate_duration_wpm():
    from rendermill.gates import estimate_duration_s, WPM
    # 6 words at 147 wpm ≈ 2.45s (the §5.10 exemplar hook: 6 words, 2.0s)
    assert estimate_duration_s("one two three four five six") == pytest.approx(
        6 / (WPM / 60), rel=0.01)


def test_preflight_rejects_long_hook():
    from rendermill.gates import preflight_hook_estimate, GateError
    slides = [{"narration": "This is an extremely long opening sentence "
                            "that rambles on far past the three second "
                            "hook budget for many many words"}]
    with pytest.raises(GateError, match="hook-gate"):
        preflight_hook_estimate(slides, {})


def test_preflight_passes_short_hook():
    from rendermill.gates import preflight_hook_estimate
    slides = [{"narration": "Leads wait too long."}]
    preflight_hook_estimate(slides, {})  # no raise
    # disabled gate never raises
    preflight_hook_estimate([{"narration": "x " * 100}], {"hook_max_s": None})


def test_hook_gate_measured_reject():
    from rendermill.gates import hook_gate, GateError
    slides = [{"narration": "too long"}]
    with pytest.raises(GateError, match="slide-1 narration measures"):
        hook_gate(slides, None, 4.2, {})  # workdir=None -> no png check


def test_hook_gate_passes_under_budget():
    from rendermill.gates import hook_gate
    slides = [{"narration": "ok", "backdrop": "x.mp4"}]
    hook_gate(slides, None, 2.5, {})


def test_looks_text_only_flattish(tmp_path):
    from PIL import Image
    from rendermill.gates import looks_text_only
    img = Image.new("RGB", (1920, 1080), (15, 18, 22))
    p = tmp_path / "text.png"
    img.save(p)
    assert looks_text_only(p) is True


def test_looks_text_only_visual_frame(tmp_path):
    from PIL import Image
    from rendermill.gates import looks_text_only
    # A spread histogram + many edges: not text-only.
    import random
    random.seed(7)
    img = Image.new("RGB", (1920, 1080))
    px = img.load()
    for y in range(0, 1080, 3):
        for x in range(0, 1920, 3):
            px[x, y] = (random.randint(0, 255), random.randint(0, 255),
                        random.randint(0, 255))
    p = tmp_path / "busy.png"
    img.save(p)
    assert looks_text_only(p) is False


def test_explainer_gate_timing(tmp_path):
    from rendermill.gates import explainer_gate, GateError
    slides = [
        {"role": "hook", "narration": "h"},
        {"role": "question", "narration": "q"},
        {"role": "stakes", "narration": "s"},
        {"role": "recap", "narration": "r"},
    ]
    # question at 2.5+0.15=2.65s, stakes at ~5s, recap 2s: all inside
    durs = [2.5, 2.2, 2.2, 2.0]
    report = explainer_gate(slides, durs, {})
    assert any("hook" in l for l in report)
    assert any("question" in l for l in report)


def test_explainer_gate_rejects_late_question():
    from rendermill.gates import explainer_gate, GateError
    slides = [
        {"role": "hook", "narration": "h"},
        {"role": "beat", "narration": "b" * 200},
        {"role": "question", "narration": "q"},
        {"role": "stakes", "narration": "s"},
    ]
    durs = [2.0, 25.0, 2.0, 2.0]  # question starts at 27.15s > 25s
    with pytest.raises(GateError, match="question"):
        explainer_gate(slides, durs, {})


def test_explainer_gate_rejects_missing_roles():
    from rendermill.gates import explainer_gate, GateError
    with pytest.raises(GateError, match="role='question'"):
        explainer_gate([{"narration": "x"}], [2.0], {})


def test_explainer_gate_recap_limit():
    from rendermill.gates import explainer_gate, GateError
    slides = [
        {"role": "hook"}, {"role": "question"}, {"role": "stakes"},
        {"role": "recap"},
    ]
    with pytest.raises(GateError, match="recap"):
        explainer_gate(slides, [2.0, 2.0, 2.0, 24.0], {})