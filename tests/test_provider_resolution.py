"""provider:auto resolves from the keys present + local-voice availability."""
from __future__ import annotations
import pytest


def _patch_keys(monkeypatch, mapping):
    from rendermill import keys
    monkeypatch.setattr(keys, "get", lambda name: mapping.get(name))


def _patch_local_avail(monkeypatch, available: bool):
    from rendermill import tts
    monkeypatch.setattr(tts, "_local", _FakeLocal(available))


class _FakeLocal:
    def __init__(self, available: bool):
        self._available = available

    def is_available(self) -> bool:
        return self._available


def test_explicit_provider_passes_through(monkeypatch):
    from rendermill import tts
    _patch_keys(monkeypatch, {})
    assert tts.resolve_provider({"provider": "deepgram"}) == "deepgram"
    assert tts.resolve_provider({"provider": "elevenlabs"}) == "elevenlabs"


def test_macos_aliases_to_local(monkeypatch):
    from rendermill import tts
    _patch_keys(monkeypatch, {})
    for name in ("macos", "say", "macos-say", "local", "free"):
        assert tts.resolve_provider({"provider": name}) == "local"


def test_auto_prefers_elevenlabs(monkeypatch):
    from rendermill import tts
    _patch_keys(monkeypatch, {"ELEVENLABS_API_KEY": "x", "DEEPGRAM_API_KEY": "y"})
    assert tts.resolve_provider({"provider": "auto"}) == "elevenlabs"


def test_auto_falls_to_deepgram(monkeypatch):
    from rendermill import tts
    _patch_keys(monkeypatch, {"DEEPGRAM_API_KEY": "y"})
    assert tts.resolve_provider({"provider": "auto"}) == "deepgram"


def test_auto_falls_to_local_when_available(monkeypatch):
    from rendermill import tts
    _patch_keys(monkeypatch, {})
    _patch_local_avail(monkeypatch, True)
    assert tts.resolve_provider({"provider": "auto"}) == "local"


def test_auto_errors_when_no_local_and_no_keys(monkeypatch):
    from rendermill import tts
    _patch_keys(monkeypatch, {})
    _patch_local_avail(monkeypatch, False)
    with pytest.raises(RuntimeError, match="No TTS"):
        tts.resolve_provider({"provider": "auto"})


def test_unset_provider_defaults_to_auto(monkeypatch):
    from rendermill import tts
    _patch_keys(monkeypatch, {"ELEVENLABS_API_KEY": "x"})
    assert tts.resolve_provider({}) == "elevenlabs"
