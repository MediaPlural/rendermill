"""scene_list_schema.json validation (stage 1 of the manim-lane pipeline).

The scene list is the machine-checkable contract (DESIGN.md §2): one object per
beat, a closed `visual_verb` enum, and named mobjects with semantic role tags.
Validation happens pre-render and hard-rejects on any contract violation.

Uses `jsonschema` when installed (the normal case); falls back to structural
checks when it isn't, so the schema/lint/codegen stages run in a bare
environment (no manim, no jsonschema) and still catch the two errors that
matter most: a bad verb and a missing top-level field.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

SCHEMA_PATH = Path(__file__).with_name("scene_list_schema.json")

_VALID_VERBS = {"WRITE", "TRANSFORM", "HIGHLIGHT", "PLOT", "ZOOM", "ARRANGE", "HOLD"}
_REQUIRED_TOP_LEVEL = ("video_id", "title", "narration_rate_wpm", "config", "beats")


def load_schema() -> Dict:
    """The JSON Schema contract, parsed."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(scene_list: Dict) -> List[str]:
    """Return a list of error strings; an empty list means the scene list is valid."""
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - exercised only without jsonschema
        return _validate_structural(scene_list)

    schema = load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors: List[str] = []
    for err in sorted(validator.iter_errors(scene_list), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors


def _validate_structural(scene_list: Dict) -> List[str]:
    """Fallback when jsonschema is absent: the two highest-value checks only."""
    errors: List[str] = []
    for req in _REQUIRED_TOP_LEVEL:
        if req not in scene_list:
            errors.append(f"<root>: missing required field {req!r}")
    for beat in scene_list.get("beats", []):
        verb = beat.get("visual_verb")
        if verb not in _VALID_VERBS:
            errors.append(f"{beat.get('scene_id', '?')}: bad visual_verb {verb!r}")
    return errors
