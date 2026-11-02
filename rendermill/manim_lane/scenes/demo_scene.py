"""
scenes/demo_scene.py — reference HAND-WRITTEN scene (not codegen output).

Shows the canonical style a squad agent writes by hand: class SceneS01 ->
SceneS04, each `construct()` is only verb-helper calls; no style, no hex colors,
no pacing magic numbers. Compare with scenes/generated_explainer.py (codegen
output) — they render the same story, one written, one generated.

Render:  python -m manim -ql scenes/demo_scene.py -o DemoScene.mp4
"""
from manim import DOWN, LEFT, RIGHT, UP

from rendermill.manim_lane.explainer_base import ExplainerScene


class SceneS01(ExplainerScene):
    """Hook: write the thing under examination (question = yellow)."""

    def construct(self):
        self.place_title("The power rule")
        self.write_latex(r"f(x) = x^2", "question", "fx")


class SceneS02(ExplainerScene):
    """Morph f(x)=x^2 -> f'(x)=2x (result = green). Object morphs, never pops."""

    def construct(self):
        fx = self.write_latex(r"f(x) = x^2", "question", "fx", run_time=1.0)
        self.morph(fx, r"f'(x) = 2x", "result", run_time=2.0)


class SceneS03(ExplainerScene):
    """Annotation caveat (teal), positioned below the result."""

    def construct(self):
        fx = self.write_latex(r"f'(x) = 2x", "result", "fprime")
        note = self._mob_for(r"\text{needs } n \neq 0", self._color_for_role("annotation"))
        note.next_to(fx, DOWN)
        from manim import FadeIn
        self.play(FadeIn(note), run_time=0.6)
        self.wait(self.HOLD_AFTER)


class SceneS04(ExplainerScene):
    """Highlight the caveat up to danger (red): constants have zero slope."""

    def construct(self):
        fx = self.write_latex(r"f'(x) = 2x", "result", "fprime")
        note = self._mob_for(r"\text{needs } n \neq 0", self._color_for_role("annotation"))
        note.next_to(fx, DOWN)
        self.add(note)
        self.highlight(note, "danger", run_time=0.8)
        self.hold(2.0)
