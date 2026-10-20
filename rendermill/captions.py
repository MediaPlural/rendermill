"""SRT + ASS caption generation from ElevenLabs `with-timestamps` alignment.

Public entry points:
  - build_srt_for_slide(idx, offset_s, rate, workdir) -> [(start, end, text), ...]
  - write_master_srt(entries, out_path)
  - write_master_ass(entries, out_path, *, width, height)

Each ElevenLabs alignment JSON has parallel arrays:
  characters, character_start_times_seconds, character_end_times_seconds

We reassemble word boundaries on whitespace/punctuation, then group ~7 words
per chunk, breaking on punctuation when possible. If audio was sped up via
atempo (rate != 1.0), original-speed timings are divided by rate to match
the sped-up timeline.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Tuple


Entry = Tuple[float, float, str]


def fmt_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


_SENTENCE_END = (".", "!", "?", "…")
# Breaks that read as a natural pause when mid-sentence.
_CLAUSE_BREAKS = (",", ";", ":", "—")


def _group_into_chunks(
    words: List[Tuple[float, float, str]],
    max_words: int = 8,
) -> List[List[Tuple[float, float, str]]]:
    """Group timed words into caption chunks that break at a natural boundary.

    The old rule was "6+ words ending in . or , — else hard-break at 9",
    which ignores semicolons, colons, em-dashes, question/exclamation marks,
    and quotes. It split real sentences in half ("…but shipped / a broken
    install") because a chunk boundary is only readable when it lands on
    punctuation.

    Breaks, strongest first: sentence end >=3 words, clause break >=5, hard
    cap at max_words. A chunk also grows past the soft target only when the
    next word would otherwise start a new sentence.
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
    return chunks


def build_srt_for_slide(idx: int, offset_s: float, rate: float,
                        workdir: Path) -> List[Entry]:
    """Read `narr_NN.alignment.json` from workdir; return caption chunks already
    offset by `offset_s` (cumulative start time of this slide in the final
    concatenated video).
    """
    align_path = workdir / f"narr_{idx:02d}.alignment.json"
    if not align_path.exists():
        return []
    align = json.loads(align_path.read_text())
    chars = align.get("characters") or []
    starts = align.get("character_start_times_seconds") or []
    ends = align.get("character_end_times_seconds") or []
    if not chars or not starts or not ends:
        return []

    r = max(rate, 1e-3)

    # Reassemble word boundaries: split on whitespace + retain trailing punctuation.
    words: List[Tuple[float, float, str]] = []
    cur_word = ""
    cur_start: float | None = None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace() or ch in ".,!?;:":
            if cur_word:
                tail = ch if ch in ".,!?;:" else ""
                words.append((cur_start / r, e / r, cur_word + tail))
                cur_word = ""
                cur_start = None
            continue
        if not cur_word:
            cur_start = s
        cur_word += ch
    if cur_word and cur_start is not None:
        words.append((cur_start / r, ends[-1] / r, cur_word))

    # Group into chunks of ~7 words, breaking on punctuation if possible.
    chunks = _group_into_chunks(words)

    entries: List[Entry] = []
    for c in chunks:
        start = c[0][0] + offset_s
        end = c[-1][1] + offset_s
        text = " ".join(w[2] for w in c).strip()
        entries.append((start, end, text))
    return entries


def build_estimated_srt_for_slide(text: str, offset_s: float,
                                  duration: float) -> List[Entry]:
    """Caption chunks with timing ESTIMATED from text + measured audio duration.

    Used when the TTS provider emits no word-level alignment (local TTS,
    Deepgram). Each word's share of `duration` is proportional to its character
    length, so longer words occupy proportionally more time. `duration` is the
    measured, post-atempo audio length, so no speaking-rate division is needed.
    Returns entries already offset by `offset_s`.
    """
    raw_words = text.split()
    if not raw_words or duration <= 0:
        return []

    weights = [max(len(w), 1) for w in raw_words]
    total = sum(weights)
    words: List[Tuple[float, float, str]] = []
    t = 0.0
    for w, wt in zip(raw_words, weights):
        share = duration * (wt / total)
        words.append((t, t + share, w))
        t += share

    entries: List[Entry] = []
    for c in _group_into_chunks(words):
        start = c[0][0] + offset_s
        end = c[-1][1] + offset_s
        chunk_text = " ".join(w[2] for w in c).strip()
        entries.append((start, end, chunk_text))
    return entries


def _clamp_overlaps(entries: List[Entry]) -> List[Entry]:
    """Ensure each caption's start is >= the previous caption's end.

    ElevenLabs character timings can yield word boundaries that overlap by a
    few ms once we round and group into chunks. Players accept overlap but it
    looks sloppy when two captions are on-screen at once.
    """
    out: List[Entry] = []
    prev_end = 0.0
    for start, end, text in entries:
        if start < prev_end:
            start = prev_end + 0.001
        if end <= start:
            end = start + 0.05
        out.append((start, end, text))
        prev_end = end
    return out


def write_master_srt(entries: List[Entry], out_path: Path) -> None:
    entries = _clamp_overlaps(entries)
    with out_path.open("w") as f:
        for i, (start, end, text) in enumerate(entries, start=1):
            f.write(f"{i}\n{fmt_srt_time(start)} --> {fmt_srt_time(end)}\n{text}\n\n")


def write_master_ass(entries: List[Entry], out_path: Path, *,
                     width: int = 1920, height: int = 1080,
                     style=None) -> None:
    """ASS subtitle file with styling baked in (avoids ffmpeg force_style quoting
    headaches).

    `style` is a theme.CaptionStyle. When omitted, the canvas-appropriate
    default is used, so a caller that never heard of themes still gets the
    sensible vertical-vs-horizontal sizing it always got. Passing the theme's
    style is what ties a burned caption to the deck it sits on: same accent,
    same fg/outline pairing, same margins.
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
    parts = [header]
    for (start, end, text) in _clamp_overlaps(entries):
        t = text.replace("{", "\\{").replace("}", "\\}")
        parts.append(
            f"Dialogue: 0,{fmt_ass_time(start)},{fmt_ass_time(end)},Default,,0,0,0,,{t}\n"
        )
    out_path.write_text("".join(parts))
