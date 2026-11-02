"""Wire the Viiy voice bridge into rendermill's TTS default + drift gate.

These tests pin the falsifiable wiring: ``import rendermill.tts`` exposes the
Viiy Signature default (eleven_v3, speaker boost off), the drift gate flags
the legacy "Jessica" config, and core/config pull the default from the same
single source rather than a duplicate copy.
"""
from __future__ import annotations


VIIY_SIGNATURE_ID = "9E7SCjnWJcKqJn9TkRLX"
LEGACY_JESSICA_ID = "cgSgspJ2msm6clMCkdW9"


def test_default_tts_is_viiy_signature():
    from rendermill import tts
    assert tts.DEFAULT_TTS["voice_id"] == VIIY_SIGNATURE_ID
    assert tts.DEFAULT_TTS["model"] == "eleven_v3"
    assert tts.DEFAULT_TTS["voice_settings"]["use_speaker_boost"] is False


def test_default_tts_sourced_from_bridge():
    """The default is pulled from the bridge canon, not a local copy."""
    from rendermill import tts
    canon = tts.rendermill_tts("viiy-signature")
    assert tts.DEFAULT_TTS["voice_id"] == canon["voice_id"]
    assert tts.DEFAULT_TTS["model"] == canon["model"]
    assert tts.DEFAULT_TTS["voice_settings"] == canon["voice_settings"]


def test_provider_stays_auto():
    """Free mode / Deepgram / local still cascade; only the voice changed."""
    from rendermill import tts
    assert tts.DEFAULT_TTS["provider"] == "auto"


def test_drift_report_flags_legacy_jessica():
    from rendermill import tts
    legacy = {
        "provider": "elevenlabs",
        "voice_id": LEGACY_JESSICA_ID,
        "model": "eleven_turbo_v2_5",
        "speaking_rate": 1.0,
        "voice_settings": {"use_speaker_boost": True},
    }
    findings = tts.drift_report(legacy)
    joined = " ".join(findings).lower()
    assert findings, "legacy Jessica config must not read as on-canon"
    assert "generic" in joined and "jessica" in joined
    assert "turbo" in joined
    assert "use_speaker_boost" in joined


def test_drift_report_clean_for_viiy():
    from rendermill import tts
    assert tts.drift_report(tts.DEFAULT_TTS) == []


def test_core_default_tts_is_viiy():
    from rendermill import core, tts
    assert core.DEFAULT_TTS is tts.DEFAULT_TTS  # deduped single source
    assert core.DEFAULT_TTS["voice_id"] == VIIY_SIGNATURE_ID


def test_config_default_tts_is_viiy():
    from rendermill import config
    tts_cfg = config.DEFAULT_CONFIG["tts"]
    assert tts_cfg["voice_id"] == VIIY_SIGNATURE_ID
    assert tts_cfg["model"] == "eleven_v3"
    assert tts_cfg["voice_settings"]["use_speaker_boost"] is False


def test_voice_bridge_resolvable():
    """The bridge module itself loads and carries the locked 3-voice set."""
    from rendermill import tts
    voices = tts.VIIY_VOICES
    assert set(voices) == {"viiy-signature", "evening", "voidcaller"}
    assert voices["viiy-signature"]["voice_id"] == VIIY_SIGNATURE_ID
