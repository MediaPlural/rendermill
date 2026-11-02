"""The continuity law (DESIGN.md §2.4) — enforced pre-render.

If beat N+1 shows a mutated version of beat N's object, the scene list must name
it in `continuity`; codegen is then forbidden from emitting `FadeIn`/`FadeOut`
where a `Transform` was declared. The lint rule: any object appearing in two
adjacent beats with different `latex` but no `continuity` entry is an error.
"""
from __future__ import annotations

from typing import Dict, List


def lint_scene_list(scene_list: Dict) -> List[str]:
    """Return a list of error strings; an empty list means the continuity law holds."""
    errors: List[str] = []
    beats = scene_list.get("beats", [])
    for prev, cur in zip(beats, beats[1:]):
        prev_objs = {o["name"]: o["latex"] for o in prev.get("objects", [])}
        cur_objs = {o["name"]: o["latex"] for o in cur.get("objects", [])}
        cont = cur.get("continuity") or {}
        declared = {cont.get("morph_from"), cont.get("morph_to")}
        for name, latex in cur_objs.items():
            if name in prev_objs and prev_objs[name] != latex and name not in declared:
                errors.append(
                    f"object '{name}' changes latex across beats "
                    f"{prev['scene_id']}->{cur['scene_id']} with no continuity entry"
                )
    return errors


def lint_hook(scene_list: Dict) -> List[str]:
    """Hook gate (§5.2): exactly one hook_role beat, and it must be the first.

    Returned as warnings (the hook gate is a soft signal today, unlike the
    continuity law which is a hard reject).
    """
    beats = scene_list.get("beats", [])
    hooks = [b for b in beats if b.get("hook_role")]
    warnings: List[str] = []
    if len(hooks) != 1 or (hooks and beats[0] is not hooks[0]):
        warnings.append("hook_role must be exactly the first beat (§5.2)")
    return warnings
