"""Cross-platform local TTS dispatcher + backend command construction.

The macOS backend is exercised live by test_say_tts.py; here we cover the
dispatcher routing and the Windows/Linux command construction — the parts
that are testable without those OSes installed.
"""
from __future__ import annotations
import platform
import subprocess
from pathlib import Path

import pytest


def test_dispatches_to_macos_on_darwin(monkeypatch, tmp_path):
    from rendermill.tts import local, macos
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos, "synthesize",
                        lambda text, out_path, *, voice="auto": Path(out_path))
    out = tmp_path / "n.wav"
    assert local.synthesize("hi", out) == out


def test_dispatches_to_windows_on_windows(monkeypatch, tmp_path):
    from rendermill.tts import local, windows
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows, "synthesize",
                        lambda text, out_path, *, voice="auto": Path(out_path))
    out = tmp_path / "n.wav"
    assert local.synthesize("hi", out) == out


def test_dispatches_to_linux_otherwise(monkeypatch, tmp_path):
    from rendermill.tts import local, linux
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(linux, "synthesize",
                        lambda text, out_path, *, voice="auto": Path(out_path))
    out = tmp_path / "n.wav"
    assert local.synthesize("hi", out) == out


def test_windows_ps_escape_single_quotes():
    from rendermill.tts import windows
    assert windows._ps_escape("it's") == "it''s"


def test_windows_command_shape_and_escaping(monkeypatch, tmp_path):
    from rendermill.tts import windows
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    captured = {}
    out_path = (tmp_path / "n.wav").resolve()

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_path.write_bytes(b"RIFFxxxx")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    windows.synthesize("it's a test", out_path, voice="Microsoft David")

    assert captured["cmd"][0] == "powershell"
    script = captured["cmd"][-1]
    assert "Microsoft David" in script
    assert "it''s a test" in script
    assert out_path.exists()


def test_windows_rejects_non_windows(monkeypatch, tmp_path):
    from rendermill.tts import windows
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    with pytest.raises(RuntimeError, match="only runs on Windows"):
        windows.synthesize("hi", tmp_path / "n.wav")


def test_linux_requires_espeak(monkeypatch, tmp_path):
    from rendermill.tts import linux
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(linux.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="espeak-ng"):
        linux.synthesize("hi", tmp_path / "n.wav")
