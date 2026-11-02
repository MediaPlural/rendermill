"""fal.ai OmniHuman lipsync and circular-badge preprocessing.

Two jobs, both optional and both cached:

  1. ``fal_omnihuman_lipsync`` — send a narration clip and a headshot to
     fal.ai's OmniHuman model, poll until it finishes, and save the returned
     video. This is the only paid lipsync step.
  2. ``crop_to_circle_video`` — cut that video down to a square and punch a
     circular alpha mask into it, producing the bottom-right presenter badge
     that ffmpeg composites over the slide.

Safety rails (a runaway encode once produced a 131 GB file, so both are
belt-and-suspenders):
  * the ffmpeg circle crop runs under a hard wall-clock timeout and refuses
    output larger than a fixed ceiling;
  * the fal queue poll has a bounded budget so a stuck job fails loudly
    instead of hanging a batch render.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import keys

_ENDPOINT = "https://queue.fal.run/fal-ai/bytedance/omnihuman/{version}"
_UPLOAD_INITIATE = "https://rest.alpha.fal.ai/storage/upload/initiate"

_POLL_INTERVAL_S = 4
_DEFAULT_VERSION = "v1.5"
_DEFAULT_RESOLUTION = "720p"
_DEFAULT_BUDGET_S = 600
_CIRCLE_CODEC = "qtrle"


def _require_key() -> str:
    api_key = keys.get("FAL_API_KEY")
    if not api_key:
        raise RuntimeError(
            "fal.ai lipsync requested but no FAL_API_KEY found.\n"
            "Run: rendermill keys set FAL=<your-key>"
        )
    return api_key


def _content_type_for(path: Path) -> str:
    return {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".mp4": "video/mp4",
    }.get(path.suffix.lower(), "application/octet-stream")


def _fal_upload(path: Path, cache) -> str:
    """Upload one file to fal.ai storage and return its public URL.

    Memoized by content hash in a sidecar table, so the same narration or
    avatar re-uploaded on a re-render reuses the prior public URL for free.
    """
    api_key = _require_key()
    digest = cache.hash([path])
    table_path = cache.root / "fal_uploads.json"
    table = json.loads(table_path.read_text()) if table_path.exists() else {}
    if digest in table:
        return table[digest]

    initiate = urllib.request.Request(
        _UPLOAD_INITIATE,
        data=json.dumps({
            "file_name": path.name,
            "content_type": _content_type_for(path),
        }).encode(),
        headers={"Authorization": f"Key {api_key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(initiate, timeout=60) as resp:
            info = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:300]
        raise RuntimeError(f"fal upload initiate failed {e.code}: {detail}") from e

    upload_url = info["upload_url"]
    file_url = info["file_url"]
    put = urllib.request.Request(
        upload_url, data=path.read_bytes(), method="PUT",
        headers={"Content-Type": info.get("content_type", _content_type_for(path))},
    )
    with urllib.request.urlopen(put, timeout=300) as resp:
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"fal upload PUT failed: HTTP {resp.status}")

    table[digest] = file_url
    table_path.write_text(json.dumps(table, indent=2))
    return file_url


def fal_omnihuman_lipsync(audio_path: Path, image_path: Path, *, cache,
                          version: str = _DEFAULT_VERSION,
                          resolution: str = _DEFAULT_RESOLUTION,
                          budget_seconds: int = _DEFAULT_BUDGET_S) -> Path:
    """Generate a lipsync MP4 from (audio, headshot), cached by all inputs."""

    def _produce(out_path: Path) -> None:
        api_key = _require_key()
        print("  [fal] uploading narration + avatar...")
        image_url = _fal_upload(image_path, cache=cache)
        audio_url = _fal_upload(audio_path, cache=cache)

        submit = urllib.request.Request(
            _ENDPOINT.format(version=version),
            data=json.dumps({
                "image_url": image_url,
                "audio_url": audio_url,
                "resolution": resolution,
            }).encode(),
            headers={"Authorization": f"Key {api_key}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(submit, timeout=60) as resp:
            job = json.load(resp)
        status_url = job["status_url"]
        response_url = job["response_url"]
        print(f"  [fal] queued {job['request_id']}; polling...")

        deadline = time.time() + budget_seconds
        while True:
            if time.time() >= deadline:
                raise RuntimeError(
                    f"fal lipsync timed out after {budget_seconds}s"
                )
            time.sleep(_POLL_INTERVAL_S)
            with urllib.request.urlopen(
                urllib.request.Request(
                    status_url, headers={"Authorization": f"Key {api_key}"}
                ), timeout=30,
            ) as resp:
                state = json.load(resp)
            status = state.get("status")
            print(f"  [fal] status={status}")
            if status == "COMPLETED":
                break
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"fal lipsync {status}: {state}")

        with urllib.request.urlopen(
            urllib.request.Request(
                response_url, headers={"Authorization": f"Key {api_key}"}
            ), timeout=60,
        ) as resp:
            result = json.load(resp)
        video = result.get("video")
        video_url = video.get("url") if isinstance(video, dict) else video
        if not video_url:
            raise RuntimeError(f"fal lipsync returned no video URL: {result}")
        with urllib.request.urlopen(video_url, timeout=180) as resp:
            out_path.write_bytes(resp.read())
        print(f"  [fal] saved {out_path.name} ({out_path.stat().st_size} bytes)")

    return cache.run(
        "lipsync", [audio_path, image_path, version, resolution], "mp4", _produce,
    )


def crop_to_circle_video(lipsync_mp4: Path, *, cache,
                         diameter: int = 280,
                         size_ceiling_bytes: int = 100 * 1024 * 1024,
                         ffmpeg_timeout: int = 180) -> Path:
    """Pre-process the lipsync video into a circular badge MOV with alpha.

    The ffmpeg graph strips any source alpha, crops to a centered square,
    scales to ``diameter``, re-adds an alpha plane, then paints alpha=255
    inside the circle and 0 outside — so the badge composites cleanly over
    any slide without a rectangular box around the presenter.
    """

    def _produce(out_path: Path) -> None:
        radius = diameter / 2
        # Commas inside geq expressions are escaped (\ ,) because ffmpeg
        # otherwise reads them as filter-chain separators.
        vf = (
            "format=yuv420p,"
            f"crop='min(iw\\,ih)':'min(iw\\,ih)',"
            f"scale={diameter}:{diameter},"
            "format=yuva420p,"
            "geq="
            "lum='lum(X\\,Y)':"
            "cb='cb(X\\,Y)':"
            "cr='cr(X\\,Y)':"
            f"a='if(lt(sqrt(pow(X-{radius}\\,2)+pow(Y-{radius}\\,2))"
            f"\\,{radius - 2}),255,0)'"
        )
        cmd = [
            "ffmpeg", "-y", "-i", str(lipsync_mp4),
            "-vf", vf, "-c:v", _CIRCLE_CODEC, "-an", "-t", "60",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, timeout=ffmpeg_timeout,
                       stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        size = out_path.stat().st_size
        if size > size_ceiling_bytes:
            out_path.unlink()
            raise RuntimeError(
                f"circle overlay unexpectedly large ({size}B > "
                f"{size_ceiling_bytes}B); aborting"
            )

    return cache.run("circle", [lipsync_mp4, diameter], "mov", _produce)
