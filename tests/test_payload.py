"""§5.1 engine-payload loader tests (the loader is real; the producer is
stubbed — these exercise the documented contract)."""
from __future__ import annotations

import json

import pytest


def _payload_file(tmp_path, data=None):
    from rendermill.payload import DEMO_PAYLOAD
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(data or DEMO_PAYLOAD))
    return p


def test_validate_requires_candidates(tmp_path):
    from rendermill.payload import load_payload, PayloadError
    p = _payload_file(tmp_path, {"placement_format": "x"})
    with pytest.raises(PayloadError, match="ranked_candidates"):
        load_payload(p)


def test_load_payload_builds_slides(tmp_path):
    from rendermill.payload import load_payload
    out = load_payload(_payload_file(tmp_path))
    assert out["config"]["orientation"] == "short"
    roles = [s["role"] for s in out["slides"]]
    assert roles == ["hook", "question", "stakes", "cta"]
    # every slide carries an animated backdrop from the §5.3 library
    assert all(s.get("backdrop", "").endswith(".mp4") for s in out["slides"])
    assert out["slides"][0]["narration"].startswith("Your leads wait")


def test_load_payload_short_form_fallback(tmp_path):
    from rendermill.payload import load_payload
    data = {
        "placement_format": "video first-3s script + opening frame",
        "ranked_candidates": [{"rank": 1, "hook": "short hook"}],
    }
    out = load_payload(_payload_file(tmp_path, data))
    roles = [s["role"] for s in out["slides"]]
    assert roles == ["hook", "cta"]


def test_demo_payload_itself_validates():
    from rendermill.payload import DEMO_PAYLOAD, validate_payload
    validate_payload(DEMO_PAYLOAD)  # the documented example always passes