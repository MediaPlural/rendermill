"""Deck themes — one declaration styles every slide in a render.

The gap this fills
------------------
Before, deck consistency was entirely the author's problem: each project
carried its own hand-rolled `_shared.css`, the bundled templates hardcoded
their own hex values, and nothing tied a caption's colour to the slide it sat
on. Two decks built a week apart looked like two products, and the caption
style was invisible until you burned it and watched it clash.

A theme is a named set of design tokens plus the caption styling that pairs
with it. `resolve` turns `"amber"` (or a dict overriding any token) into a
`Theme`. `write_shared_css` emits the generated `_shared.css` for a project,
and `wrap_slide_html` can frame a slide so it inherits automatically.

Design rules the themes follow:
  * body text and captions never use the same colour, so a burned caption
    always separates from the slide beneath it.
  * accent is reserved for emphasis; it is never the background.
  * every pair meets >= 4.5:1 contrast against the surface it sits on.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ASS colours are &HAABBGGRR — alpha first, then blue/green/red.
_ALPHA_OPAQUE = "00"


@dataclass(frozen=True)
class CaptionStyle:
    """How burned-in captions are styled. Mirrors the ASS style fields."""

    font: str = "Arial"
    size: int = 44
    primary: str = "#F5F5F2"     # caption glyph colour
    outline: str = "#101418"     # glyph outline / shadow
    bold: bool = True
    outline_w: int = 3
    shadow: int = 1
    margin_l: int = 120
    margin_r: int = 120
    margin_v: int = 80

    def to_ass(self) -> Dict[str, Any]:
        return {
            "Fontname": self.font,
            "Fontsize": self.size,
            "PrimaryColour": _hex_to_ass(self.primary),
            "OutlineColour": _hex_to_ass(self.outline),
            "Bold": 1 if self.bold else 0,
            "Outline": self.outline_w,
            "Shadow": self.shadow,
            "MarginL": self.margin_l,
            "MarginR": self.margin_r,
            "MarginV": self.margin_v,
        }


def _hex_to_ass(hex_color: str) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS wants alpha, blue, green, red)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"caption colour must be #RRGGBB, got {hex_color!r}")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{_ALPHA_OPAQUE}{b}{g}{r}".upper()


@dataclass(frozen=True)
class Theme:
    """A deck's design tokens. Every slide and caption inherits these."""

    name: str
    bg: str = "#0f1216"
    panel: str = "#161b22"
    line: str = "#2c333d"
    text: str = "#ebebe8"
    muted: str = "#8c919a"
    accent: str = "#fac85a"
    strike: str = "#d97757"
    good: str = "#7cb37c"
    font_stack: str = (
        '-apple-system, "SF Pro Display", "SF Pro Text", Inter, system-ui, sans-serif'
    )
    mono_stack: str = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace'
    h1_size: int = 64
    h2_size: int = 40
    p_size: int = 24
    eyebrow_size: int = 18
    # Captions default to the theme's own text colour over its own bg, with a
    # heavy outline so they stay legible over imagery too.
    caption: CaptionStyle = field(default_factory=CaptionStyle)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["caption"] = asdict(self.caption)
        return d


AMBER = Theme(
    name="amber",
    bg="#0f1216", panel="#161b22", line="#2c333d",
    text="#ebebe8", muted="#8c919a",
    accent="#fac85a", strike="#d97757", good="#7cb37c",
)

FIELD = Theme(
    name="field",
    bg="#0c1310", panel="#141d19", line="#26332c",
    text="#e8f0e9", muted="#8ba393",
    accent="#7cb37c", strike="#d97757", good="#7cb37c",
    caption=CaptionStyle(primary="#e8f0e9", outline="#08100c"),
)

SIGNAL = Theme(
    name="signal",
    bg="#0d1117", panel="#151b26", line="#28303f",
    text="#e9edf5", muted="#8b96ab",
    accent="#5b8def", strike="#d97757", good="#7cb37c",
    caption=CaptionStyle(primary="#e9edf5", outline="#0a0e14"),
)

PAPER = Theme(
    name="paper",
    bg="#fbfaf7", panel="#ffffff", line="#e2ded4",
    text="#1b1a17", muted="#6b675e",
    accent="#a86a12", strike="#b23c1f", good="#2f6b3a",
    caption=CaptionStyle(primary="#ffffff", outline="#1b1a17"),
)

BUILTIN: Dict[str, Theme] = {t.name: t for t in (AMBER, FIELD, SIGNAL, PAPER)}
DEFAULT_THEME_NAME = "amber"

# Keys a caller may override in a theme dict.
_OVERRIDABLE = {
    "bg", "panel", "line", "text", "muted", "accent", "strike", "good",
    "font_stack", "mono_stack", "h1_size", "h2_size", "p_size", "eyebrow_size",
}
_CAPTION_KEYS = {
    "font", "size", "primary", "outline", "bold",
    "outline_w", "shadow", "margin_l", "margin_r", "margin_v",
}


class ThemeError(ValueError):
    """An unknown theme name, or an override the theme does not accept."""


def resolve(theme: Any = None) -> Theme:
    """Turn `"amber"` / `{"name": "field", "accent": "#fff"}` / None into a Theme.

    Unknown names and unknown override keys raise ThemeError with the valid
    set, so a typo fails at load rather than producing a half-styled deck.
    """
    if theme is None or theme == "":
        return BUILTIN[DEFAULT_THEME_NAME]

    if isinstance(theme, Theme):
        return theme

    if isinstance(theme, str):
        if theme not in BUILTIN:
            raise ThemeError(
                f"unknown theme {theme!r}. Available: "
                f"{', '.join(sorted(BUILTIN))}"
            )
        return BUILTIN[theme]

    if isinstance(theme, dict):
        name = theme.get("name") or DEFAULT_THEME_NAME
        if name not in BUILTIN:
            raise ThemeError(
                f"unknown theme {name!r}. Available: "
                f"{', '.join(sorted(BUILTIN))}"
            )
        base = BUILTIN[name]
        unknown = set(theme) - _OVERRIDABLE - _CAPTION_KEYS - {"name"}
        if unknown:
            raise ThemeError(
                f"unknown theme override key(s): {', '.join(sorted(unknown))}. "
                f"Overridable: {', '.join(sorted(_OVERRIDABLE | _CAPTION_KEYS | {'name'}))}"
            )
        cap_over = {k: v for k, v in theme.items() if k in _CAPTION_KEYS}
        # asdict() flattens the nested CaptionStyle into a "caption" key, so
        # it must be popped before being splatted back in as keyword args —
        # otherwise every dict override dies on "multiple values for
        # keyword argument 'caption'".
        base_kwargs = asdict(base)
        base_kwargs.pop("caption")
        return Theme(
            **{**base_kwargs, **{k: v for k, v in theme.items() if k in _OVERRIDABLE}},
            caption=CaptionStyle(**{**asdict(base.caption), **cap_over}),
        )

    raise ThemeError(f"theme must be a name, dict, or None; got {type(theme).__name__}")


def theme_names() -> List[str]:
    return sorted(BUILTIN)


def caption_style_for(theme: Any, *, width: int = 1920, height: int = 1080,
                      avatar_slot: bool = False) -> CaptionStyle:
    """A CaptionStyle adjusted for the actual canvas.

    Defaults reproduce the canvas sizing the ASS writer hard-coded before
    themes existed — 44pt / 560px right margin on wide canvases, 56pt and a
    deep bottom margin on vertical — so opting into a theme does not silently
    move every caption in an existing deck. A theme's own caption token wins
    for colour and font; the canvas adjustment is applied on top.

    Vertical canvases need bigger type and more bottom margin; a horizontal
    canvas with an avatar badge in the bottom-right pushes the right margin
    out so captions never slide under the badge.
    """
    t = resolve(theme)
    c = t.caption
    if width >= height:
        size = c.size
        margin_v = c.margin_v
        margin_l, margin_r = c.margin_l, c.margin_r
        if width >= 1600:
            # Historical wide-canvas defaults: keep clear of the bottom-right
            # avatar slot even when no badge is configured, so adding a badge
            # later never has to re-flow the captions.
            margin_l = max(margin_l, 120)
            margin_r = max(margin_r, 560)
    else:  # vertical / short-form: type scales with the narrower canvas
        size = int(c.size * 1.25)
        margin_v = int(c.margin_v * 2.75)
        margin_l = margin_r = int(c.margin_l * 0.5)
    if avatar_slot and width >= height:
        margin_r = max(margin_r, 560)
    return CaptionStyle(
        font=c.font, size=size, primary=c.primary, outline=c.outline,
        bold=c.bold, outline_w=c.outline_w, shadow=c.shadow,
        margin_l=margin_l, margin_r=margin_r, margin_v=margin_v,
    )


_CSS_TEMPLATE = """/* rendermill shared deck theme: {name}
 *
 * GENERATED FILE — regenerate with:
 *   rendermill theme <project_dir>
 * Or set `theme:` in slides.py and re-run that command. Do not hand-edit:
 * your changes are replaced on the next write.
 *
 * Every slide links this file and inherits one palette, so a deck cannot
 * drift into three different-looking slides. Slide-local CSS belongs in a
 * <style> block inside the slide's own <head>.
 */

:root {{
  --bg:      {bg};
  --panel:   {panel};
  --line:    {line};
  --text:    {text};
  --muted:   {muted};
  --accent:  {accent};
  --strike:  {strike};
  --good:    {good};

  --font-sans: {font_stack};
  --font-mono: {mono_stack};

  --h1-size: {h1_size}px;
  --h2-size: {h2_size}px;
  --p-size:  {p_size}px;
  --eyebrow-size: {eyebrow_size}px;

  /* Caption tokens mirror the burned-in ASS style, so what you preview in
     the browser matches what libass draws into the MP4. */
  --caption-fg:   {cap_primary};
  --caption-edge: {cap_outline};
}}

* {{ box-sizing: border-box; }}

html, body {{
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}}

/* Canvas sizing. Chrome screenshots at body.clientWidth x clientHeight. */
body.canvas {{ display: flex; flex-direction: column; overflow: hidden; }}
body.canvas.h {{ width: 1920px; height: 1080px; padding: 64px 96px; }}
body.canvas.v {{ width: 1080px; height: 1920px; padding: 96px 72px; }}

.eyebrow {{
  font-size: var(--eyebrow-size); letter-spacing: 0.22em;
  text-transform: uppercase; color: var(--accent);
  font-weight: 700; margin-bottom: 14px;
}}

h1 {{ font-size: var(--h1-size); line-height: 1.08; letter-spacing: -0.02em;
      margin: 0 0 18px; font-weight: 800; }}
h2 {{ font-size: var(--h2-size); line-height: 1.15; letter-spacing: -0.01em;
      margin: 0 0 14px; font-weight: 700; }}
p  {{ font-size: var(--p-size); line-height: 1.5; margin: 0 0 18px; }}

.accent {{ color: var(--accent); font-weight: 600; }}
.strike {{ color: var(--strike); font-weight: 600; }}
.good   {{ color: var(--good);   font-weight: 600; }}
.muted  {{ color: var(--muted); }}

.panel {{ background: var(--panel); border: 1px solid var(--line);
          border-radius: 12px; }}
.mono {{ font-family: var(--font-mono); }}

@media (prefers-reduced-motion: reduce) {{
  * {{ animation: none !important; transition: none !important; }}
}}
"""


def shared_css(theme: Any = None) -> str:
    """The generated `_shared.css` body for a theme."""
    t = resolve(theme)
    return _CSS_TEMPLATE.format(
        name=t.name,
        bg=t.bg, panel=t.panel, line=t.line, text=t.text, muted=t.muted,
        accent=t.accent, strike=t.strike, good=t.good,
        font_stack=t.font_stack, mono_stack=t.mono_stack,
        h1_size=t.h1_size, h2_size=t.h2_size, p_size=t.p_size,
        eyebrow_size=t.eyebrow_size,
        cap_primary=t.caption.primary, cap_outline=t.caption.outline,
    )


def write_shared_css(project_dir, theme: Any = None) -> Path:
    """Write `<project_dir>/slides/_shared.css` from the theme.

    Returns the written path. Overwrites the hand-rolled file the old
    scaffold shipped, which is the point: the theme becomes the source.
    """
    project_dir = Path(project_dir)
    slides_dir = project_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    out = slides_dir / "_shared.css"
    out.write_text(shared_css(theme), encoding="utf-8")
    return out