"""
explainer_base.py — the ExplainerScene base class for the Manim explainer lane.

Owns EVERYTHING the LLM must never write by hand: brand config (semantic color
palette, fonts, background), pacing defaults, and the verb helpers that map the
closed `visual_verb` enum (§2.2) to idiomatic Manim. Generated scene code (from
`codegen.py`) only supplies *content + verbs*; style lives here.

Color convention (org-wide, §3.2 of DESIGN.md):
    BLUE   = established / known quantities
    YELLOW = the thing under examination (unknowns, current question)
    GREEN  = results / derived truth
    RED    = danger / error bounds
    TEAL   = secondary annotation (axes, gridlines, marks)

The LLM references roles (role -> COL_*), never hex values.
"""
from __future__ import annotations

from manim import (
    BLUE,
    GREEN,
    RED,
    TEAL,
    YELLOW,
    UP,
    FadeIn,
    MathTex,
    ReplacementTransform,
    Scene,
    Tex,
    TexTemplate,
    Text,
    VMobject,
    Write,
)


# --- Semantic color palette (§3.2) -------------------------------------------
# Hex set here; the LLM/render pipeline touches only the role names.
COL_KNOWN = BLUE          # known / given facts
COL_QUESTION = YELLOW     # the thing under examination
COL_RESULT = GREEN        # results / derived truth
COL_DANGER = RED          # danger / error bounds
COL_ANNOTATION = TEAL     # secondary annotation

# Dark, premium 3b1b-style canvas.
DEFAULT_BACKGROUND = "#0B0F14"

# Role -> semantic color (used by codegen/verb helpers).
ROLE_TO_COLOR = {
    "known": COL_KNOWN,
    "question": COL_QUESTION,
    "result": COL_RESULT,
    "danger": COL_DANGER,
    "annotation": COL_ANNOTATION,
}

# Minimal, reproducible TeX preamble (DESIGN.md §3.1). We pin our own AMS
# template instead of manim's heavy 3b1b default (which pulls dsfont/physics/
# wasysym/…), so the lane runs on any basic TeX Live + dvisvgm install and
# notation is identical across scenes. `latex` -> DVI -> dvisvgm -> SVG.
LANE_TEX_TEMPLATE = TexTemplate(
    tex_compiler="latex",
    output_format=".dvi",
    preamble=r"""
\usepackage[english]{babel}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{xcolor}
""",
)


class ExplainerScene(Scene):
    """Base for every explainer-lane scene.

    Subclasses (generated or hand-written) define `construct()` as an ordered
    sequence of verb-helper calls mirroring the scene-list beat array.
    """

    # Pacing defaults (§3.4) — tune against retention data, not vibes.
    HOLD_AFTER = 0.8            # ≥0.5–1s pause after every completed transform
    DEFAULT_RUN_TIME = 2.0      # fallback when a beat has no est_seconds anchor
    WRITE_RUN_TIME = 1.5        # border-then-fill draw

    def setup(self):
        super().setup()
        # Brand canvas lives on the base class, not in generated code.
        self.camera.background_color = DEFAULT_BACKGROUND

    # -- math registry ---------------------------------------------------------
    def _mob_for(self, latex: str, color) -> MathTex:
        """Every symbol is LaTeX-rendered (§3.1). Color from role, never hex."""
        return MathTex(latex, color=color, tex_template=LANE_TEX_TEMPLATE)

    def _color_for_role(self, role: str):
        return ROLE_TO_COLOR.get(role, COL_ANNOTATION)

    # -- verb helpers (closed enum -> Manim) ------------------------------------
    def write_latex(self, latex: str, role: str, obj_name: str, run_time=None):
        """WRITE — draw a new object, border-then-fill (§2.2)."""
        mob = self._mob_for(latex, self._color_for_role(role))
        mob.set_name(obj_name)
        self.play(self._write(mob), run_time=run_time or self.WRITE_RUN_TIME)
        self.wait(self.HOLD_AFTER)
        return mob

    def _write(self, mob: VMobject):
        # Write = draw border, then fill. Cheap approximation of 3b1b's draw.
        return Write(mob)

    def morph(self, source, target_latex: str, target_role: str, run_time=None):
        """TRANSFORM — morph A -> B; mathematical structure persists (§3.3)."""
        target = self._mob_for(target_latex, self._color_for_role(target_role))
        self.play(ReplacementTransform(source, target), run_time=run_time or 1.5)
        self.wait(self.HOLD_AFTER)
        return target

    def highlight(self, mob, role: str, run_time=None):
        """HIGHLIGHT — recolor/box an existing object; persists, never flashes."""
        self.play(mob.animate.set_color(self._color_for_role(role)), run_time=run_time or 0.6)
        self.wait(self.HOLD_AFTER)
        return mob

    def hold(self, seconds: float):
        """HOLD — pause on the result (§2.2)."""
        self.wait(seconds)

    def place_title(self, text: str, role: str = "annotation", edge=None, buff: float = 0.4):
        """on_screen_text — slide-style prose label (Pango, non-math)."""
        if "$" in text:
            mob = Tex(text, color=self._color_for_role(role), tex_template=LANE_TEX_TEMPLATE)
        else:
            mob = Text(text, color=self._color_for_role(role))
        mob.to_edge(edge if edge is not None else UP, buff=buff)
        self.play(FadeIn(mob), run_time=0.5)
        return mob
