"""Render gates — machine-checked quality laws (spec §5.2 + §5.10).

§5.2 hook gate (the ad-hook lane): reject any render whose slide-1
narration measures > ~3s or whose opening frame is text-only. The
authoritative check runs right after slide-0 TTS, before the rest of the
pipeline spends anything. A cheap word-count preflight (§5.10's measured
147 wpm) rejects obvious violators before ANY TTS spend.

§5.10 explainer gates (explainer mode): hook <=3s, question by ~23s
(template hard limit 25s), stakes by ~33s (hard 35s), recap <=20s.
Measured from actual per-slide audio durations, not storyboard estimates.

Gate failures raise GateError; the CLI maps that to a non-zero exit and
nothing further renders. Config (`gates:` in slides.py) tunes or disables:
    "gates": {"hook_max_s": 3.0,        # None disables the hook gate
              "explainer": False,       # turn on the §5.10 timing gates
              "question_max_s": 25.0, "question_target_s": 23.0,
              "stakes_max_s": 35.0,   "stakes_target_s": 33.0,
              "recap_max_s": 20.0}
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# §5.10 measured exemplar: 147 wpm narration, median 9 words/sentence.
WPM = 147.0
WPS = WPM / 60.0

DEFAULT_HOOK_MAX_S = 3.0
DEFAULT_QUESTION_MAX_S = 25.0   # EXPLAINER-80 template: question <=25s
DEFAULT_QUESTION_TARGET_S = 23.0  # §5.10 measured exemplar target
DEFAULT_STAKES_MAX_S = 35.0     # EXPLAINER-80 template: stakes <=35s
DEFAULT_STAKES_TARGET_S = 33.0   # §5.10 measured exemplar target
DEFAULT_RECAP_MAX_S = 20.0      # EXPLAINER-80 template: recap <=20s

# Inter-slide pad used by core.make_video (must stay in sync).
SLIDE_PAD_S = 0.15


class GateError(RuntimeError):
    """A machine-checked render gate failed. The render must not proceed."""


def estimate_duration_s(text: str) -> float:
    """Word-count estimate of spoken duration at the §5.10 measured 147 wpm."""
    words = len((text or "").split())
    return words / WPS if WPS else 0.0


def _cfg_float(gates_cfg: Optional[Dict[str, Any]], key: str, default: float) -> Optional[float]:
    v = (gates_cfg or {}).get(key, default)
    return None if v is None else float(v)


# ---------------------------------------------------------------------------
# §5.2 hook gate
# ---------------------------------------------------------------------------

def preflight_hook_estimate(slides: List[Dict[str, Any]],
                            gates_cfg: Optional[Dict[str, Any]]) -> None:
    """Pre-TTS rejection: slide-1 narration that cannot possibly fit ~3s.

    Word-count based (147 wpm), so it carries 1.35x slack — the measured
    gate below is the authoritative one. Catches gross violators before a
    single paid TTS character is spent.
    """
    max_s = _cfg_float(gates_cfg, "hook_max_s", DEFAULT_HOOK_MAX_S)
    if max_s is None or not slides:
        return
    est = estimate_duration_s(slides[0].get("narration") or "")
    if est > max_s * 1.35:
        raise GateError(
            f"[hook-gate][preflight] slide-1 narration is ~{est:.1f}s at "
            f"{WPM:.0f} wpm ({len((slides[0].get('narration') or '').split())} words) "
            f"— way over the {max_s:.1f}s hook budget. Rewrite the opening line "
            f"(§5.10 exemplar: 6 words, 2.0s)."
        )


def _slide0_declares_visual(slide: Dict[str, Any]) -> bool:
    """The opening frame is visual by construction when it carries b-roll."""
    return bool(slide.get("backdrop")) or slide.get("type") == "video" \
        or slide.get("visual") is True


def looks_text_only(png_path: Path, *, dominant_min: float = 0.50,
                    max_colors: int = 48, max_edge_frac: float = 0.085) -> bool:
    """Heuristic: is this frame basically a flat background with text on it?

    Text-only frames (the §5.2 anti-pattern) have one dominant flat
    background color, few distinct colors, and sparse edges. Visual frames
    (b-roll, imagery, gradients, motion) spread their histogram. Tuned
    against real renders from this pipeline (see tests/test_hook_gate.py).
    """
    from PIL import Image, ImageFilter  # Pillow is a hard dep of this package

    img = Image.open(png_path).convert("RGB")
    img.thumbnail((320, 320))
    total = img.width * img.height
    q = img.quantize(colors=64)
    counts = sorted(q.getcolors(total) or [], reverse=True)
    dominant = (counts[0][0] / total) if counts else 1.0
    n_colors = len(counts)
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    eh = edges.histogram()
    edge_frac = sum(eh[33:]) / max(total, 1)
    return dominant >= dominant_min and n_colors <= max_colors \
        and edge_frac <= max_edge_frac


def hook_gate(slides: List[Dict[str, Any]], workdir: Path, first_dur_s: float,
              gates_cfg: Optional[Dict[str, Any]],
              *, text_only_check: bool = True) -> None:
    """The authoritative §5.2 gate: measured slide-1 audio + opening frame.

    Runs right after slide-0 TTS so a violation costs one slide of audio,
    not a full render. `first_dur_s` is the post-silenceremove measured
    narration length. The opening-frame check is skipped when slide 0
    carries a backdrop / embedded video (visual by construction).
    """
    max_s = _cfg_float(gates_cfg, "hook_max_s", DEFAULT_HOOK_MAX_S)
    if max_s is None:
        return
    errs: List[str] = []
    if first_dur_s > max_s:
        errs.append(
            f"slide-1 narration measures {first_dur_s:.2f}s > {max_s:.2f}s "
            f"(§5.2: first sentence spoken in ~3s)"
        )
    if text_only_check and slides and not _slide0_declares_visual(slides[0]):
        png = (Path(workdir) / "slide_00.png") if workdir else None
        if png is not None and png.exists() and looks_text_only(png):
            errs.append(
                "opening frame is text-only (§5.2) — fuse the hook with a "
                "visual: animated backdrop, embedded video, or imagery"
            )
    if errs:
        raise GateError("[hook-gate] REJECT: " + "; ".join(errs))


# ---------------------------------------------------------------------------
# §5.10 explainer gates
# ---------------------------------------------------------------------------

def role_starts(slides: List[Dict[str, Any]],
                durations: List[float]) -> List[Tuple[Dict[str, Any], float, float]]:
    """(slide, start_s, dur_s) on the master timeline, using the same
    cumulative + 0.15s pad arithmetic as core.make_video."""
    out: List[Tuple[Dict[str, Any], float, float]] = []
    t = 0.0
    for s, d in zip(slides, durations):
        out.append((s, t, d))
        t += d + SLIDE_PAD_S
    return out


def explainer_gate(slides: List[Dict[str, Any]], durations: List[float],
                   gates_cfg: Optional[Dict[str, Any]]) -> List[str]:
    """§5.10 machine checks. Raises GateError on a hard violation; returns
    report lines (warnings included) for the caller to print.

    Hard limits come from the EXPLAINER-80 template; the §5.10 exemplar
    targets print as warnings when exceeded but not violated.
    """
    q_max = _cfg_float(gates_cfg, "question_max_s", DEFAULT_QUESTION_MAX_S)
    q_tgt = _cfg_float(gates_cfg, "question_target_s", DEFAULT_QUESTION_TARGET_S)
    st_max = _cfg_float(gates_cfg, "stakes_max_s", DEFAULT_STAKES_MAX_S)
    st_tgt = _cfg_float(gates_cfg, "stakes_target_s", DEFAULT_STAKES_TARGET_S)
    recap_max = _cfg_float(gates_cfg, "recap_max_s", DEFAULT_RECAP_MAX_S)

    timeline = role_starts(slides, durations)
    report: List[str] = []
    errs: List[str] = []

    def _find(role: str) -> Optional[Tuple[Dict[str, Any], float, float]]:
        for item in timeline:
            if (item[0].get("role") or "") == role:
                return item
        return None

    # Hook: measured duration of slide 0 (gate again here so explainer mode
    # reports it in one place even if hook_max_s was disabled).
    hook = _find("hook") or (timeline[0] if timeline else None)
    if hook:
        hook_max = _cfg_float(gates_cfg, "hook_max_s", DEFAULT_HOOK_MAX_S)
        if hook_max is not None and hook[2] > hook_max:
            errs.append(f"hook measures {hook[2]:.2f}s > {hook_max:.2f}s")
        else:
            report.append(f"[gate][explainer] hook: {hook[2]:.2f}s OK")

    for role, max_s, tgt in (("question", q_max, q_tgt), ("stakes", st_max, st_tgt)):
        hit = _find(role)
        if hit is None:
            errs.append(
                f"no slide declares role={role!r} — explainer mode requires a "
                f"{role} beat (declare slide roles in slides.py)"
            )
            continue
        start = hit[1]
        if max_s is not None and start > max_s:
            errs.append(f"{role} starts at {start:.2f}s > {max_s:.2f}s (EXPLAINER-80)")
        elif tgt is not None and start > tgt:
            report.append(
                f"[gate][explainer] {role} starts at {start:.2f}s — over the "
                f"{tgt:.0f}s exemplar target but inside the {max_s:.0f}s template limit (warning)"
            )
        else:
            report.append(f"[gate][explainer] {role} at {start:.2f}s OK")

    recap = _find("recap")
    if recap is not None and recap_max is not None and recap[2] > recap_max:
        errs.append(
            f"recap runs {recap[2]:.2f}s > {recap_max:.0f}s (EXPLAINER-80: recap "
            f"<=20s, zero new information in the close)"
        )
    # "Zero new information in the close" needs a human/LLM reviewer — the
    # storyboard gate (storyboard_gate.py) owns that check; this module
    # machine-checks timing only.

    if errs:
        raise GateError("[gate][explainer] REJECT: " + "; ".join(errs))
    return report