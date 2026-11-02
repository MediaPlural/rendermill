"""rendermill.manim_lane — 3Blue1Brown-style math explainer lane.

A first-class feature of rendermill (the WIRING LAW: built is installed, not
left as a separate prototype). Consumes a strict-JSON scene list, generates
deterministic Manim code, and renders per-scene MP4s that drop into the fork's
existing concat stage unchanged.

The scene list (stage 2) is the contract artifact. Pipeline:

    scene-list JSON  --validate (JSON Schema)-->  lint (continuity law)
        -->  codegen (scene-list -> Python scene)  -->  manim render (--tier)

Manim is imported lazily: importing this package does NOT import manim, so the
schema/lint/codegen stages (and their tests) run in a bare environment. Only
`explainer_base` (the `ExplainerScene` base) and the render stage need manim.
"""
from __future__ import annotations

from . import codegen, lint, schema

# The public surface mirrors the prototype build.py, for drop-in continuity.
generate_scene_module = codegen.generate_scene_module
validate = schema.validate
load_schema = schema.load_schema
lint_scene_list = lint.lint_scene_list
lint_hook = lint.lint_hook

__all__ = [
    "codegen",
    "lint",
    "schema",
    "generate_scene_module",
    "validate",
    "load_schema",
    "lint_scene_list",
    "lint_hook",
]
