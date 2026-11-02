"""The Manim explainer lane (rendermill.manim_lane): contract, codegen, lint.

These lock the three properties the lane exists for (DESIGN.md §2): the scene
list is machine-checkable (JSON Schema), codegen is deterministic (same JSON ->
same Python), and the continuity law rejects a beat whose object changes latex
across an adjacent beat without a `continuity` entry.

Everything here is manim-free: importing rendermill.manim_lane must work in a
bare environment (no manim). The one manim-gated test (generated-scene import)
skips when manim is absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import rendermill.manim_lane as ml
from rendermill.manim_lane import codegen, lint, schema

_SCENE_LIST = Path(ml.__file__).parent / "scene_lists" / "demo-derivative.json"


def _load() -> dict:
    return json.loads(_SCENE_LIST.read_text(encoding="utf-8"))


# --- import surface -----------------------------------------------------------

def test_package_import_surface():
    """The lane exposes the same surface as the prototype build.py."""
    assert callable(ml.generate_scene_module)
    assert callable(ml.validate)
    assert callable(ml.lint_scene_list)


def test_module_imports_without_manim():
    """The lane imports in a bare environment; manim is only pulled in by
    explainer_base / the render stage, never by `import rendermill.manim_lane`."""
    import sys
    assert "manim" not in sys.modules, "importing the lane must not import manim"
    assert codegen.HEADER.startswith('"""GENERATED FILE')


# --- schema validation --------------------------------------------------------

def test_demo_scene_list_is_valid():
    assert schema.validate(_load()) == []


def test_schema_rejects_unknown_visual_verb():
    data = _load()
    data["beats"][0]["visual_verb"] = "EXPLODE"
    errs = schema.validate(data)
    assert errs, "closed enum must reject a free-form verb"
    assert any("EXPLODE" in e for e in errs)


def test_schema_rejects_missing_required_beat_field():
    data = _load()
    del data["beats"][0]["est_seconds"]
    errs = schema.validate(data)
    assert errs
    assert any("est_seconds" in e for e in errs)


def test_schema_rejects_bad_scene_id():
    data = _load()
    data["beats"][0]["scene_id"] = "scene one"  # violates ^s[0-9]{2,3}$
    assert schema.validate(data)


# --- codegen determinism ------------------------------------------------------

def test_codegen_is_deterministic():
    data = _load()
    assert codegen.generate_scene_module(data) == codegen.generate_scene_module(data)


def test_codegen_emits_verb_helpers_not_fade():
    """Generated code routes through base-class verb helpers, and never emits
    FadeIn/FadeOut for math content (the continuity law, §2.4)."""
    code = codegen.generate_scene_module(_load())
    assert "class ExplainerDemo(ExplainerScene)" in code
    assert "from rendermill.manim_lane.explainer_base import ExplainerScene" in code
    assert "self.write_latex(" in code
    assert "self.morph(" in code
    assert "self.highlight(" in code
    assert "self.hold(" in code
    assert "FadeIn" not in code
    assert "FadeOut" not in code


def test_codegen_transform_declares_morph_not_write():
    """The TRANSFORM beat must morph from the prior object, not redraw it."""
    code = codegen.generate_scene_module(_load())
    # s02 morphs fx -> fprime; the generated line names the source variable.
    assert "self.morph(mob_fx" in code


# --- continuity lint ----------------------------------------------------------

def test_continuity_lint_passes_on_demo():
    assert lint.lint_scene_list(_load()) == []


def test_continuity_lint_rejects_latex_change_without_entry():
    """A beat whose object changes latex across an adjacent beat without a
    `continuity` entry must be rejected pre-render (DESIGN.md §2.4)."""
    data = _load()
    # s04 highlights 'caveat' (role danger) but keeps the SAME latex as s03, so
    # the demo passes. Change its latex while continuity stays null to trip it.
    data["beats"][3]["objects"][0]["latex"] = r"\text{DIFFERENT}"
    errs = lint.lint_scene_list(data)
    assert errs
    assert any("caveat" in e and "continuity" in e for e in errs)


def test_continuity_entry_satisfies_the_law():
    """Declaring morph_from/morph_to is exactly what exempts a latex change."""
    data = _load()
    data["beats"][3]["objects"][0]["latex"] = r"\text{DIFFERENT}"
    data["beats"][3]["continuity"] = {"morph_from": "caveat", "morph_to": "caveat"}
    assert lint.lint_scene_list(data) == []


# --- build CLI (validate -> codegen -> write, no render) ----------------------

def test_build_dry_run_writes_generated_scene(tmp_path):
    from rendermill.manim_lane import build
    rc = build.main(["--scene-list", str(_SCENE_LIST), "--dry-run",
                     "--out-dir", str(tmp_path), "--media-dir", str(tmp_path / "media")])
    assert rc == 0
    generated = tmp_path / "generated_explainer.py"
    assert generated.exists()
    body = generated.read_text(encoding="utf-8")
    assert "class ExplainerDemo(ExplainerScene)" in body
    assert "self.morph(mob_fx" in body


def test_build_rejects_invalid_scene_list(tmp_path):
    from rendermill.manim_lane import build
    bad = tmp_path / "bad.json"
    data = _load()
    data["beats"][0]["visual_verb"] = "EXPLODE"
    bad.write_text(json.dumps(data), encoding="utf-8")
    rc = build.main(["--scene-list", str(bad), "--dry-run", "--out-dir", str(tmp_path)])
    assert rc != 0


# --- generated-scene import (manim-gated) -------------------------------------

def test_generated_scene_imports(tmp_path):
    """The generated scene compiles and subclasses ExplainerScene. Requires manim,
    so it skips in a bare environment (the real render exercises this path)."""
    manim = pytest.importorskip("manim")
    import importlib.util
    import sys

    code = codegen.generate_scene_module(_load())
    p = tmp_path / "generated_explainer.py"
    p.write_text(code, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("rendermill_manim_generated", str(p))
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    from rendermill.manim_lane.explainer_base import ExplainerScene
    assert issubclass(mod.ExplainerDemo, ExplainerScene)
