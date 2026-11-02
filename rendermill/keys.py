"""API-key resolution and validation.

Rendermill never *requires* a key to run: ``provider: "auto"`` cascades from
ElevenLabs to Deepgram to the OS's local voice, and lipsync is opt-in per
slide. A key becomes mandatory only when a caller pins a paid provider or asks
for a paid feature. This module owns the one place a key can live on disk, the
merge order that resolves it, and the live probes behind ``rendermill keys
test``.

Resolution order for any provider key:
  1. process environment (highest precedence, always wins)
  2. ``~/.config/rendermill/keys.env`` — a flat ``NAME=value`` file
  3. unset — the caller decides whether that provider is required

The file lives in a 0700 directory with mode 0600 and is never echoed back to
the user in full; ``mask`` reduces a key to a short, safe hint.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

XDG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
CONFIG_DIR = XDG_HOME / "rendermill"
KEYS_FILE = CONFIG_DIR / "keys.env"

# Short aliases accepted by `keys set EL=...` mapped to the canonical env-var
# name used everywhere else. Unknown names pass through unchanged, so a caller
# can store any key it likes.
ALIASES: Dict[str, str] = {
    "EL": "ELEVENLABS_API_KEY",
    "ELEVENLABS": "ELEVENLABS_API_KEY",
    "ELEVENLABS_API_KEY": "ELEVENLABS_API_KEY",
    "FAL": "FAL_API_KEY",
    "FAL_API_KEY": "FAL_API_KEY",
    "DEEPGRAM": "DEEPGRAM_API_KEY",
    "DEEPGRAM_API_KEY": "DEEPGRAM_API_KEY",
    "DG": "DEEPGRAM_API_KEY",
}

# The three providers rendermill knows how to validate.
PROVIDERS = ("ELEVENLABS_API_KEY", "FAL_API_KEY", "DEEPGRAM_API_KEY")


def mask(value: str) -> str:
    """Reduce a key to a safe hint: ``sk_3cea...8ba6``.

    Short or empty values carry no useful tail to reveal, so they collapse to
    ``***`` / ``(empty)`` rather than leaking even a truncated prefix.
    """
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _canonical(name: str) -> str:
    return ALIASES.get(name.upper(), name)


def _read_env_file(path: Path) -> Dict[str, str]:
    """Parse a ``NAME=value`` file, skipping blanks, comments and quotes."""
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def load_keys() -> Dict[str, str]:
    """Resolve every provider key: environment wins, then the keys file."""
    from_file = _read_env_file(KEYS_FILE)
    merged: Dict[str, str] = {}
    for name in PROVIDERS:
        value = os.environ.get(name) or from_file.get(name)
        if value:
            merged[name] = value
    return merged


def get(name: str) -> Optional[str]:
    """Fetch one key by canonical name or alias (``get("FAL")`` works)."""
    return load_keys().get(_canonical(name))


def key_provenance(name: str) -> Optional[Dict[str, str]]:
    """Describe *where* an existing key came from, so the wizard can say so.

    Returns ``None`` when the key is unset, otherwise a dict:
      {"source": "env" | "file", "where": <human-readable location>,
       "value": <raw key>, "mtime": <YYYY-MM-DD or "">}
    """
    env_val = os.environ.get(name)
    if env_val:
        return {"source": "env", "where": f"${name} (environment variable)",
                "value": env_val, "mtime": ""}

    from_file = _read_env_file(KEYS_FILE)
    if name not in from_file:
        return None

    mtime = ""
    try:
        import datetime as _dt
        mtime = _dt.datetime.fromtimestamp(KEYS_FILE.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        pass

    path_str = str(KEYS_FILE)
    try:
        home = str(Path.home())
        if path_str.startswith(home):
            path_str = "~" + path_str[len(home):]
    except Exception:  # noqa: BLE001 — best-effort prettifying only
        pass

    return {"source": "file", "where": path_str,
            "value": from_file[name], "mtime": mtime}


def save_keys(updates: Dict[str, str]) -> Path:
    """Merge ``updates`` into the keys file, creating it 0600 in a 0700 dir."""
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = _read_env_file(KEYS_FILE)
    canonical_updates = {_canonical(k): v for k, v in updates.items()}
    merged = {**existing, **canonical_updates}

    body = ["# rendermill API keys. Do not check this file in.",
            "# Edit via: rendermill keys set NAME=value", ""]
    body += [f"{k}={v}" for k, v in sorted(merged.items())]
    KEYS_FILE.write_text("\n".join(body) + "\n")
    try:
        KEYS_FILE.chmod(0o600)
    except OSError:
        pass
    return KEYS_FILE


# -- live probes ---------------------------------------------------------
# Each tester returns (ok, message). They only ever read — none spends tokens.


def _json_probe(url, *, method="GET", headers=None, payload=None,
                timeout: int = 15) -> tuple[bool, int | None, Any]:
    """Issue a small JSON request; return (ok, status, parsed-or-error)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            try:
                return True, resp.status, json.loads(resp.read())
            except ValueError:
                return True, resp.status, {}
    except urllib.error.HTTPError as e:
        return False, e.code, e.read().decode(errors="ignore")[:160]
    except Exception as e:  # noqa: BLE001 — report any transport failure
        return False, None, f"{type(e).__name__}: {e}"


def _probe_error(status, detail) -> str:
    if status is None:
        return str(detail)
    return f"HTTP {status}: {detail}"


def test_elevenlabs(api_key: str) -> tuple[bool, str]:
    """Validate against ElevenLabs' user endpoint (reads subscription tier)."""
    if not api_key:
        return False, "no key"
    ok, status, data = _json_probe(
        "https://api.elevenlabs.io/v1/user",
        headers={"xi-api-key": api_key},
    )
    if not ok:
        return False, _probe_error(status, data)
    tier = (data or {}).get("subscription", {}).get("tier", "unknown")
    return True, f"ok (tier: {tier})"


def test_fal(api_key: str) -> tuple[bool, str]:
    """Validate fal.ai auth by initiating an upload we never complete.

    A valid key returns a presigned upload URL (200); a bad key returns
    401/403. We stop at the initiate call, so nothing is ever stored.
    """
    if not api_key:
        return False, "no key"
    ok, status, data = _json_probe(
        "https://rest.alpha.fal.ai/storage/upload/initiate",
        method="POST",
        headers={"Authorization": f"Key {api_key}",
                 "Content-Type": "application/json"},
        payload={"file_name": "ping.txt", "content_type": "text/plain"},
    )
    return (True, "ok") if ok else (False, _probe_error(status, data))


def test_deepgram(api_key: str) -> tuple[bool, str]:
    """Validate against Deepgram's projects endpoint (free, read-only)."""
    if not api_key:
        return False, "no key"
    ok, status, data = _json_probe(
        "https://api.deepgram.com/v1/projects",
        headers={"Authorization": f"Token {api_key}"},
    )
    return (True, "ok") if ok else (False, _probe_error(status, data))


TESTERS = {
    "ELEVENLABS_API_KEY": test_elevenlabs,
    "FAL_API_KEY": test_fal,
    "DEEPGRAM_API_KEY": test_deepgram,
}


def test_all() -> Dict[str, tuple[bool, str]]:
    """Probe every configured key. Unset keys report ``("not set")``, never fail.

    The CLI renders ``[opt]`` for unset optional keys; only a configured-but-
    invalid key is a failure.
    """
    keys = load_keys()
    out: Dict[str, tuple[bool, str]] = {}
    for name, tester in TESTERS.items():
        value = keys.get(name)
        out[name] = tester(value) if value else (False, "not set")
    return out
