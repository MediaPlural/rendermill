"""Content-hash cache for render intermediates.

Rendermill pays for narration (ElevenLabs / Deepgram) and lipsync (fal.ai) by
the call, so a re-render whose only change is a CSS rule must not re-synthesize
audio or re-queue a lipsync job. This module gives every expensive step a
*content-addressed* slot on disk: the step's inputs are hashed, the digest
names the output file, and a later render that produces the same inputs finds
the file already present and skips the work entirely.

Layout
------
Every cache is rooted at ``<workdir>/cache``. Inside that root, each *kind*
(``"tts"``, ``"m4a"``, ``"lipsync"``, ``"circle"``, ``"avatar_circle"``,
``"slide_mp4"``) owns a subdirectory, and each artifact is stored as
``<kind>/<hash>.<ext>``. The hash mixes the step's inputs — narration text,
voice id, speaking rate, and slide content bytes — so changing any one of
them routes to a fresh slot instead of silently reusing a stale artifact.

Invalidation
------------
Set ``RENDERMILL_FORCE_REGEN=1`` (``FORCE_REGEN=1`` and the legacy
``MNV_FORCE_REGEN=1`` are still honoured) in the environment to treat every
lookup as a miss for a single run.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable, Iterable

# Cache slots the render actually uses. Callers are not limited to these —
# `path`/`run` create a kind's subdirectory on demand — the list only
# pre-creates the known ones so a fresh workdir already shows its skeleton.
_KINDS = ("tts", "m4a", "lipsync", "circle", "avatar_circle", "slide_mp4")

_FORCE_REGEN_VARS = ("RENDERMILL_FORCE_REGEN", "FORCE_REGEN", "MNV_FORCE_REGEN")
_TRUTHY = {"1", "true", "yes", "on"}


def _forced() -> bool:
    """True when the environment asks to ignore every cache hit for one run."""
    return any(
        os.environ.get(name, "").strip().lower() in _TRUTHY
        for name in _FORCE_REGEN_VARS
    )


class Cache:
    """A per-workdir cache that counts hits and misses for the CLI report."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir).resolve()
        self.root = self.workdir / "cache"
        self.root.mkdir(parents=True, exist_ok=True)
        for kind in _KINDS:
            (self.root / kind).mkdir(exist_ok=True)
        self._hits: dict[str, int] = {}
        self._misses: dict[str, int] = {}

    # -- key derivation ---------------------------------------------------

    @staticmethod
    def _mix(hasher, value) -> None:
        """Fold one key input into the digest.

        A ``Path`` contributes its bytes (or a marker when absent, so a missing
        input never collides with an empty one); raw bytes contribute as-is;
        anything else contributes its ``repr``, which is stable for the scalars
        and small dicts the callers pass (voice ids, speeds, config blobs).
        """
        if isinstance(value, Path):
            hasher.update(value.read_bytes() if value.exists() else b"<absent>")
        elif isinstance(value, (bytes, bytearray)):
            hasher.update(bytes(value))
        else:
            hasher.update(repr(value).encode("utf-8"))

    def hash(self, inputs: Iterable) -> str:
        """A 16-hex digest for a sequence of key inputs."""
        hasher = hashlib.sha256()
        for item in inputs:
            self._mix(hasher, item)
        return hasher.hexdigest()[:16]

    # -- public API -------------------------------------------------------

    def path(self, kind: str, key_inputs: Iterable, ext: str) -> Path:
        """The on-disk slot ``(kind, key_inputs, ext)`` would occupy."""
        slot = self.root / kind / f"{self.hash(key_inputs)}.{ext}"
        slot.parent.mkdir(parents=True, exist_ok=True)
        return slot

    def run(
        self,
        kind: str,
        key_inputs: Iterable,
        ext: str,
        generator: Callable[[Path], None],
    ) -> Path:
        """Return the cached artifact, producing it via ``generator`` if absent.

        ``generator(out_path)`` must write the artifact to ``out_path``; on
        return we verify the file actually exists and fail otherwise, so a
        producer that died silently can never masquerade as a cache hit on the
        next run. Producers that might fail partway through should write to a
        sibling temp file and ``os.replace`` it into place.
        """
        slot = self.path(kind, key_inputs, ext)
        if slot.exists() and not _forced():
            self._note(kind, hit=True)
            return slot
        self._note(kind, hit=False)
        generator(slot)
        if not slot.exists():
            raise RuntimeError(
                f"cache producer for {kind!r} returned without writing {slot}"
            )
        return slot

    def summary(self) -> str:
        """One-line report, e.g. ``hits=3 misses=1  tts:2/3 m4a:1/1``."""
        kinds = sorted(set(self._hits) | set(self._misses))
        total_hit = sum(self._hits.values())
        total_miss = sum(self._misses.values())
        per_kind = " ".join(
            f"{k}:{self._hits.get(k, 0)}/"
            f"{self._hits.get(k, 0) + self._misses.get(k, 0)}"
            for k in kinds
        )
        return f"hits={total_hit} misses={total_miss}  {per_kind}".rstrip()

    # -- internals ---------------------------------------------------------

    def _note(self, kind: str, *, hit: bool) -> None:
        target = self._hits if hit else self._misses
        target[kind] = target.get(kind, 0) + 1
