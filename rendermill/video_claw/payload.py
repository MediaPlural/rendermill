"""§5.1 engine payload input — the corpus-to-creative bridge (STUB v1).

The intended contract (documented, not yet wired to real Stage 4/5
output — no sample payload existed at implementation time):

A payload is the marketing engine's Stage 4/5 output for one video
placement. JSON shape (mirrors RESEARCH-TO-CREATIVE-LOOP-SPEC §4–5):

    {
      "vertical": "adhacks",
      "placement_format": "video first-3s script + opening frame",
      "orientation": "short",                     # from Stage 3 division
      "ranked_candidates": [                     # ranked first-sentence candidates
        {"rank": 1, "hook": "...", "score": 0.91},
        ...
      ],
      "icp_cluster_tags": ["solo-agency-owner", "b2b-leadgen"],
      "beats": [                                  # optional EXPLAINER-80 storyboard
        {"role": "hook",   "text": "...", "visual_verb": "HOLD"},
        {"role": "question", "text": "...", "visual_verb": "ZOOM"},
        {"role": "stakes", "text": "...", "visual_verb": "PLOT"},
        {"role": "cta",    "text": "...", "visual_verb": "HOLD"}
      ],
      "voice_lane": "default-signature"              # §5.5
    }

`load_payload()` returns a Project-ready slides list: each beat becomes a
slide dict with `role`, `narration`, and (when the payload carries a
backdrop assignment) an animated backdrop from the §5.3 library. Hooks are
cloned per ranked candidate for top-K A/B renders (the loop's Stage 5
pre-spend ranking: render only top-K, cache makes K cheap).

WHAT IS STUBBED: no real Stage 4/5 producer emits this JSON yet, so the
loader is exercised only by tests + the demo payload below. The slide
generation itself is real: a payload in, slides.py-ready SLIDES out.
When the first real payload lands, delete this note and the loader keeps
working unchanged (the contract is the schema above).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# §5.3 backdrop library — deterministic simulation backdrops, seed-hashed.
DEFAULT_BACKDROP_LIBRARY = "~/.config/rendermill/backdrops"

# role → backdrop preset mapping (orientations resolve per project)
ROLE_BACKDROPS = {
    "hook": "brownian_drift",
    "question": "brownian_drift",
    "stakes": "pulse_grid",
    "beat": "pulse_grid",
    "payoff": "field_flow",
    "recap": "field_flow",
    "cta": "field_flow",
}


class PayloadError(ValueError):
    pass


def validate_payload(data: Dict[str, Any]) -> None:
    """Machine-check the §5.1 contract before any render spend."""
    for key in ("ranked_candidates", "placement_format"):
        if key not in data:
            raise PayloadError(f"payload missing required key: {key!r}")
    cands = data["ranked_candidates"]
    if not cands or not isinstance(cands, list):
        raise PayloadError("ranked_candidates must be a non-empty list")
    for i, c in enumerate(cands):
        if "hook" not in c:
            raise PayloadError(f"ranked_candidates[{i}] missing 'hook'")


def load_payload(path: Path, *, top_k: int = 1,
                 backdrop_library: Optional[str] = None) -> Dict[str, Any]:
    """Read + validate a payload file. Returns a dict with CONFIG + SLIDES
    in the exact shape slides.py would carry (cfg_mod.load-compatible)."""
    path = Path(path)
    data = json.loads(path.read_text())
    validate_payload(data)

    k = max(1, int(top_k))
    candidates = data["ranked_candidates"][:k]
    beats = data.get("beats") or []
    if not beats:
        # Short-form shape: hook + cta from the ranked candidates.
        cands = [c["hook"] for c in candidates] or ["untitled"]
        beats = [{"role": "hook", "text": cands[0]}]
        beats += [{"role": "cta", "text": c} for c in cands[1:]]
        if len(beats) == 1:
            beats.append({"role": "cta", "text": cands[0]})

    lib = backdrop_library or DEFAULT_BACKDROP_LIBRARY
    slides: List[Dict[str, Any]] = []
    for beat in beats:
        role = beat.get("role") or "beat"
        slide: Dict[str, Any] = {
            "role": role,
            "narration": beat["text"],
        }
        preset = beat.get("backdrop") or ROLE_BACKDROPS.get(role)
        if preset:
            slide["backdrop"] = f"{lib}/{preset}.mp4"
        slides.append(slide)

    return {
        "config": {
            "title": data.get("vertical", "payload") + "-video",
            "orientation": data.get("orientation", "short"),
            "voice_lane": data.get("voice_lane"),
            "icp_cluster_tags": data.get("icp_cluster_tags", []),
        },
        "slides": slides,
    }


DEMO_PAYLOAD = {
    "vertical": "demo",
    "placement_format": "video first-3s script + opening frame",
    "orientation": "short",
    "ranked_candidates": [
        {"rank": 1, "hook": "Your leads wait 42 minutes. Here's the fix.", "score": 0.9},
    ],
    "icp_cluster_tags": ["demo-tag"],
    "beats": [
        {"role": "hook", "text": "Your leads wait 42 minutes. Here's the fix."},
        {"role": "question", "text": "What would a five minute reply do to your close rate?"},
        {"role": "stakes", "text": "Half your leads sign with whoever answers first."},
        {"role": "cta", "text": "Ship the engine. Answer in five minutes."},
    ],
}