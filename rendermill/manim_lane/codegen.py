"""codegen — scene-list JSON -> generated Python scene (deterministic).

Stage 3 of the pipeline (DESIGN.md §2). The translation is mechanical: walk the
beat array in order, track named mobjects across beats (via the `continuity`
field), and emit an ordered sequence of verb-helper calls on `ExplainerScene`.

The generated class owns NOTHING stylistic — colors/fonts/pacing live on the
base class. This is what makes a failed scene a one-scene regeneration and makes
the generated diff trivially reviewable.

Usage:
    from rendermill.manim_lane.codegen import generate_scene_module
    code = generate_scene_module(scene_list_dict)
"""
from __future__ import annotations

import re
from typing import Dict, List

# Verbs that are fully implemented on the base class today.
_IMPLEMENTED = {"WRITE", "TRANSFORM", "HIGHLIGHT", "HOLD"}
# Declared in the enum, semantics land in sprint-2 (§7). Rendered as a named HOLD.
_STUBBED = {"PLOT", "ZOOM", "ARRANGE"}

HEADER = '''\
"""GENERATED FILE — do not hand-edit. Regenerate from scene-list JSON via `rendermill manim`."""

from rendermill.manim_lane.explainer_base import ExplainerScene


class ExplainerDemo(ExplainerScene):
    """Generated from scene list {video_id!r} ({title!r})."""

    def construct(self):
{setup_lines}
'''

FOOTER = "\n"


def _sanitize(name: str) -> str:
    """Object name -> safe Python variable (prefixed to avoid keyword clashes)."""
    clean = re.sub(r"[^0-9A-Za-z_]", "_", name)
    return f"mob_{clean}"


def _pyliteral(value) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    return repr(str(value))


def generate_scene_module(scene_list: Dict) -> str:
    video_id = scene_list["video_id"]
    title = scene_list.get("title", "")

    body: List[str] = []
    registry: Dict[str, str] = {}  # object name -> python var

    for beat in scene_list["beats"]:
        verb = beat["visual_verb"]
        sid = beat["scene_id"]
        rt = max(0.5, float(beat.get("est_seconds", 2.0)))

        body.append(f"        # --- {sid} · {verb} ---")

        # on_screen_text: slide-style prose title (furniture -> FadeIn is legal).
        ost = beat.get("on_screen_text")
        if ost:
            body.append(f"        self.place_title({_pyliteral(ost)})")

        if verb == "WRITE":
            for obj in beat.get("objects", []):
                var = _sanitize(obj["name"])
                registry[obj["name"]] = var
                body.append(
                    f"        {var} = self.write_latex("
                    f"{_pyliteral(obj['latex'])}, {_pyliteral(obj['role'])}, "
                    f"{_pyliteral(obj['name'])}, run_time={rt:.2f})"
                )

        elif verb == "TRANSFORM":
            cont = beat.get("continuity") or {}
            src_name = cont.get("morph_from")
            dst_name = cont.get("morph_to")
            target = (beat.get("objects") or [{}])[0]
            if src_name and src_name in registry:
                src_var = registry[src_name]
                resolved = dst_name or target.get("name") or src_name
                dst_var = _sanitize(resolved)
                registry[resolved] = dst_var
                body.append(
                    f"        {dst_var} = self.morph("
                    f"{src_var}, {_pyliteral(target.get('latex', ''))}, "
                    f"{_pyliteral(target.get('role', 'result'))}, run_time={rt:.2f})"
                )
            else:
                # No prior object to morph from — degrade to a WRITE (never a pop).
                for obj in beat.get("objects", []):
                    var = _sanitize(obj["name"])
                    registry[obj["name"]] = var
                    body.append(
                        f"        {var} = self.write_latex("
                        f"{_pyliteral(obj['latex'])}, {_pyliteral(obj['role'])}, "
                        f"{_pyliteral(obj['name'])}, run_time={rt:.2f})"
                    )

        elif verb == "HIGHLIGHT":
            for obj in beat.get("objects", []):
                if obj["name"] in registry:
                    body.append(
                        f"        self.highlight({registry[obj['name']]}, "
                        f"{_pyliteral(obj['role'])}, run_time={rt:.2f})"
                    )
                else:
                    body.append(f"        self.hold({rt:.2f})  # HIGHLIGHT: no prior object")

        elif verb == "HOLD":
            body.append(f"        self.hold({rt:.2f})")

        elif verb in _STUBBED:
            body.append(
                f"        self.hold({rt:.2f})  # {verb}: sprint-2 (DESIGN.md §7)"
            )

        else:
            raise ValueError(f"unknown visual_verb {verb!r} in {sid}")

    setup_lines = "\n".join(body) if body else "        pass"
    return HEADER.format(video_id=video_id, title=title, setup_lines=setup_lines) + FOOTER


if __name__ == "__main__":
    import json
    import sys

    with open(sys.argv[1]) as fh:
        data = json.load(fh)
    print(generate_scene_module(data))
