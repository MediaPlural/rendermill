"""Caption tracks: SRT and ASS, word-aligned when alignment exists.

Rendermill's rule is *captions always, never silently dropped*. When the TTS
provider emits word-level alignment (ElevenLabs' ``with-timestamps``), we
rebuild word boundaries from its per-character timing and emit cues that track
the actual speech. When a provider emits no alignment (Deepgram, local OS
voices), we estimate timing from the narration text and the measured audio
length instead — still shipped, just coarser.

Outputs:
  * ``write_master_srt``  — an SRT sidecar (always safe to ship)
  * ``write_master_ass``  — a styled .ass file for libass burn-in

The .ass is biased toward the lower third and holds clear of the bottom-right
corner where the optional lipsync badge sits.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

# A caption entry is (start_seconds, end_seconds, text).
Entry = Tuple[float, float, str]

# Punctuation that terminates a sentence (a strong, natural chunk boundary).
_SENTENCE_END = (".", "!", "?", "…")
# Punctuation that reads as a natural pause mid-sentence.
_CLAUSE_BREAKS = (",", ";", ":", "—")
# Punctuation that ends a word during per-character reassembly.
_WORD_BOUNDARY = ".!?…,;:—"


def fmt_srt_time(seconds: float) -> str:
    """Seconds -> SRT timestamp ``HH:MM:SS,mmm``."""
    whole = int(seconds)
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    millis = int(round((seconds - whole) * 1000))
    if millis == 1000:  # rounding can carry into the next second
        millis = 0
        secs += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def fmt_ass_time(seconds: float) -> str:
    """Seconds -> ASS timestamp ``H:MM:SS.cc``."""
    whole = int(seconds)
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    centis = int(round((seconds - whole) * 100))
    if centis == 100:
        centis = 0
        secs += 1
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _merge_widow_chunks(
    chunks: List[List[Tuple[float, float, str]]],
    max_words: int = 8,
) -> List[List[Tuple[float, float, str]]]:
    """Post-pass: collapse 1-word "widow" chunks into the previous chunk.

    The grouping pass above can end on a single word when a punctuation break
    lands mid-phrase and the trailing fragment is too short to read as its own
    caption — "with no captions" + "at all." collapses to "with no captions at
    all." A 1-word caption that flashes for 263ms is a bug, not a feature.

    When the previous chunk has room (its length + 1 <= max_words + 1), fold
    the widow into it. Iterate because folding can expose a new widow in the
    prior chunk's own successor. A single-word input is left alone: that one
    word IS the entire caption, not a widow.
    """
    while True:
        merged = False
        out: List[List[Tuple[float, float, str]]] = []
        for c in chunks:
            if (len(c) == 1 and out
                    and len(out[-1]) + 1 <= max_words + 1):
                out[-1] = out[-1] + c
                merged = True
            else:
                out.append(list(c))
        if not merged:
            return out
        chunks = out
    return out  # unreachable, satisfies type checkers


def _group_into_chunks(
    words: List[Tuple[float, float, str]],
    max_words: int = 8,
) -> List[List[Tuple[float, float, str]]]:
    """Group timed words into caption chunks that break at a natural boundary.

    Breaks, strongest first: sentence end >=3 words, clause break >=5, hard
    cap at max_words. A post-pass merges any 1-word trailing chunk back into
    the previous chunk (a "widow" caption). See ``_merge_widow_chunks``.
    """
    chunks: List[List[Tuple[float, float, str]]] = []
    chunk: List[Tuple[float, float, str]] = []
    for w in words:
        chunk.append(w)
        n = len(chunk)
        text = w[2]
        stripped = text.rstrip("\"'”’)]")

        if n >= 3 and stripped.endswith(_SENTENCE_END):
            chunks.append(chunk)
            chunk = []
        elif n >= 5 and stripped.endswith(_CLAUSE_BREAKS):
            chunks.append(chunk)
            chunk = []
        elif n >= max_words:
            chunks.append(chunk)
            chunk = []
    if chunk:
        chunks.append(chunk)
    return _merge_widow_chunks(chunks, max_words=max_words)


def _reassemble_words(characters: List[str], starts: List[float],
                      ends: List[float], rate: float) -> List[Tuple[float, float, str]]:
    """Rebuild ``(start, end, text)`` words from per-character alignment.

    ElevenLabs' alignment is per-character. Whitespace ends a word; punctuation
    ends a word and rides along with it. Because the audio may have been sped
    up with atempo, every timestamp is divided by ``rate`` to land on the
    sped-up timeline.
    """
    scale = max(rate, 1e-3)
    words: List[Tuple[float, float, str]] = []
    buffer: List[str] = []
    buffer_start: float | None = None

    def close(end_s: float, tail: str = "") -> None:
        nonlocal buffer, buffer_start
        if buffer and buffer_start is not None:
            words.append((buffer_start / scale, end_s / scale, "".join(buffer) + tail))
        buffer, buffer_start = [], None

    for char, start_s, end_s in zip(characters, starts, ends):
        if char.isspace():
            close(end_s)
        elif char in _WORD_BOUNDARY:
            close(end_s, tail=char)
        else:
            if buffer_start is None:
                buffer_start = start_s
            buffer.append(char)
    close(ends[-1] if ends else 0.0)
    return words


def _words_to_entries(words: List[Tuple[float, float, str]],
                      offset_s: float) -> List[Entry]:
    entries: List[Entry] = []
    for cue in _group_into_chunks(words):
        start = cue[0][0] + offset_s
        end = cue[-1][1] + offset_s
        entries.append((start, end, " ".join(w[2] for w in cue).strip()))
    return entries


def build_srt_for_slide(idx: int, offset_s: float, rate: float,
                        workdir: Path) -> List[Entry]:
    """Word-aligned captions for one slide, from ``narr_NN.alignment.json``.

    Returns cues already shifted by ``offset_s`` (the slide's cumulative start
    in the concatenated master). An absent or empty alignment file yields no
    entries — the caller falls back to estimated timing.
    """
    align_path = workdir / f"narr_{idx:02d}.alignment.json"
    if not align_path.exists():
        return []
    data = json.loads(align_path.read_text())
    characters = data.get("characters") or []
    starts = data.get("character_start_times_seconds") or []
    ends = data.get("character_end_times_seconds") or []
    if not characters or not starts or not ends:
        return []
    words = _reassemble_words(list(characters), list(starts), list(ends), rate)
    return _words_to_entries(words, offset_s)


def build_estimated_srt_for_slide(text: str, offset_s: float,
                                  duration: float) -> List[Entry]:
    """Caption timing *estimated* from text and measured audio length.

    Each word's share of ``duration`` is proportional to its character length,
    so longer words occupy proportionally more of the audio. ``duration`` is
    the measured, post-atempo length, so no rate division is applied here.
    """
    raw_words = text.split()
    if not raw_words or duration <= 0:
        return []

    weights = [max(len(word), 1) for word in raw_words]
    total_weight = sum(weights)
    words: List[Tuple[float, float, str]] = []
    cursor = 0.0
    for word, weight in zip(raw_words, weights):
        span = duration * (weight / total_weight)
        words.append((cursor, cursor + span, word))
        cursor += span
    return _words_to_entries(words, offset_s)


def _clamp_overlaps(entries: List[Entry]) -> List[Entry]:
    """Ensure each cue starts no earlier than the previous cue's end.

    Character-level rounding can nudge two cues into a few-ms overlap; players
    accept it but it looks sloppy, so we enforce a strict monotonic timeline.
    """
    out: List[Entry] = []
    previous_end = 0.0
    for start, end, text in entries:
        if start < previous_end:
            start = previous_end + 0.001
        if end <= start:
            end = start + 0.05
        out.append((start, end, text))
        previous_end = end
    return out


def write_master_srt(entries: List[Entry], out_path: Path) -> None:
    """Write an SRT file. The safe, always-works fallback."""
    with out_path.open("w") as handle:
        for index, (start, end, text) in enumerate(_clamp_overlaps(entries), start=1):
            handle.write(
                f"{index}\n{fmt_srt_time(start)} --> {fmt_srt_time(end)}\n{text}\n\n"
            )


def _ass_color(hex_color: str) -> str:
    """``#RRGGBB`` -> ASS ``&HAABBGGRR`` (alpha first, then blue/green/red)."""
    hex_color = hex_color.lstrip("#")
    red, green, blue = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    return f"&H00{blue}{green}{red}".upper()


def write_master_ass(entries: List[Entry], out_path: Path, *,
                     width: int = 1920, height: int = 1080,
                     style=None) -> None:
    """Write a styled .ass file for libass burn-in.

    ``style`` is a ``theme.CaptionStyle``. When omitted, the canvas-appropriate
    default is used, so a caller that never heard of themes still gets the
    sensible vertical-vs-horizontal sizing it always got. Passing the theme's
    style ties the burned caption to the deck it sits on: same accent, same
    fg/outline pairing, same margins.
    """
    if style is None:
        from .theme import caption_style_for
        style = caption_style_for(None, width=width, height=height)
    a = style.to_ass()
    header = f"""[Script Info]
Title: rendermill captions
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{a['Fontname']},{a['Fontsize']},{a['PrimaryColour']},&H000000FF,{a['OutlineColour']},&HA0000000,{a['Bold']},0,0,0,100,100,0,0,1,{a['Outline']},{a['Shadow']},2,{a['MarginL']},{a['MarginR']},{a['MarginV']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, text in _clamp_overlaps(entries):
        escaped = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        lines.append(
            f"Dialogue: 0,{fmt_ass_time(start)},{fmt_ass_time(end)},"
            f"Default,,0,0,0,,{escaped}\n"
        )
    out_path.write_text("".join(lines))
