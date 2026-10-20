"""Project: Deterministic by default

Demo deck for the rendermill upgrade. Six slides showing the three things the
upgrade fixed: a measured timeline, one theme for the whole deck, and captions
that land on the sentence they belong to.

Rendered in free mode ($0) so the demo reproduces on any machine.

Slide 1 is deliberately short — the §5.2 hook gate rejects any opening line
over ~3s, and it is right: a hook that takes eight seconds is not a hook.
"""

CONFIG = {
    "title": "Deterministic by default",
    "orientation": "horizontal",
    "out_path": "out/deterministic-demo.mp4",
    "mode": "free",          # $0 render: local voice, free estimated captions
    "theme": "signal",       # one palette for every slide AND the burned captions
    "tts": {"provider": "local", "local_voice": "auto", "speaking_rate": 1.0},
    "captions": {"burn": True, "estimate": True, "max_words": 8},
    "music": {"enabled": False},
}

SLIDES = [
    {
        "type": "html",
        "html": "slides/01-hook.html",
        # 8 words / ~3.2s. The gate's budget is 3.0s with 1.35x preflight slack;
        # this is measured, not estimated, and clears.
        "narration": "The timeline was guessing. Here is what changed.",
        "speed": 1.0,
    },
    {
        "type": "html",
        "html": "slides/02-problem.html",
        "narration": "Every narrated slide renderer assumes each slide lands exactly where the math says it will, so the audio plus a fixed pad.",
        "speed": 1.0,
    },
    {
        "type": "html",
        "html": "slides/03-measured.html",
        "narration": "But encoders round to frame boundaries. Measured on a five slide deck, the real file drifted by up to twenty six milliseconds per slide.",
        "speed": 1.0,
    },
    {
        "type": "html",
        "html": "slides/04-fix.html",
        "narration": "So rendermill stopped guessing. It probes every encoded slide with ffprobe and builds the timeline from what actually happened.",
        "speed": 1.0,
    },
    {
        "type": "html",
        "html": "slides/05-theme.html",
        "narration": "One theme declaration now styles every slide and the burned in captions together, so a deck can no longer drift into three different looks.",
        "speed": 1.0,
    },
    {
        "type": "html",
        "html": "slides/06-captions.html",
        "narration": "Captions break on punctuation instead of word count, and a failed burn is reported instead of silently shipping a video with no captions at all.",
        "speed": 1.0,
    },
]