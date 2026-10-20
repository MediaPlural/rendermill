"""One theme declaration styles a whole deck.

These lock the two properties the theme system exists for: a typo fails at
load instead of producing a half-styled deck, and the burned-in caption style
inherits the deck's tokens while keeping the canvas sizing that existing decks
already rely on.
"""
from __future__ import annotations

import pytest

from rendermill import theme as th


def test_builtin_names():
    assert th.theme_names() == ["amber", "field", "paper", "signal"]
    assert th.resolve(None).name == th.DEFAULT_THEME_NAME


def test_unknown_theme_name_fails_with_the_valid_set():
    with pytest.raises(th.ThemeError, match="Available"):
        th.resolve("chartreuse")


def test_unknown_override_key_fails_with_the_valid_set():
    with pytest.raises(th.ThemeError, match="unknown theme override key"):
        th.resolve({"name": "amber", "bgcolour": "#fff"})


def test_bad_type_fails():
    with pytest.raises(th.ThemeError, match="must be a name, dict, or None"):
        th.resolve(42)


def test_dict_override_only_touches_named_tokens():
    t = th.resolve({"name": "field", "accent": "#ffb347"})
    assert t.accent == "#ffb347"
    assert t.bg == th.FIELD.bg, "override leaked into untokenized fields"
    assert t.name == "field"


def test_caption_overrides_are_scoped_to_caption():
    t = th.resolve({"name": "amber", "size": 60, "primary": "#123456"})
    assert t.caption.size == 60 and t.caption.primary == "#123456"
    assert t.p_size == th.AMBER.p_size


def test_hex_to_ass_channel_order():
    """ASS is &HAABBGGRR — blue before green before red. Getting this wrong
    silently renders every caption the wrong colour."""
    assert th._hex_to_ass("#FF8000") == "&H000080FF"


def test_hex_to_ass_rejects_bad_input():
    with pytest.raises(ValueError, match="#RRGGBB"):
        th._hex_to_ass("#FFF")


def test_caption_style_for_keeps_historical_wide_canvas_defaults():
    """Opting into a theme must not silently re-flow every caption in a deck
    that already shipped at 44pt with a 560px right margin."""
    c = th.caption_style_for("amber", width=1920, height=1080)
    assert c.size == 44
    assert c.margin_r == 560
    assert c.margin_l == 120


def test_caption_style_for_scales_vertical():
    v = th.caption_style_for("amber", width=1080, height=1920)
    assert v.size == int(th.AMBER.caption.size * 1.25)
    assert v.margin_v > th.AMBER.caption.margin_v


def test_caption_style_for_avatar_slot_clears_the_badge():
    assert th.caption_style_for("amber", width=1920, height=1080, avatar_slot=True).margin_r >= 560


def test_caption_colour_follows_the_theme():
    """A burned caption must separate from the slide it sits on — that means
    it inherits the theme's text colour, not a hard-coded default."""
    assert th.caption_style_for("paper").primary == th.PAPER.caption.primary
    assert th.caption_style_for("signal").primary == th.SIGNAL.caption.primary


def test_caption_ass_keys_match_libass_field_names():
    ass = th.caption_style_for("amber").to_ass()
    for k in ("Fontname", "Fontsize", "PrimaryColour", "OutlineColour",
              "Bold", "Outline", "Shadow", "MarginL", "MarginR", "MarginV"):
        assert k in ass


def test_shared_css_is_token_driven():
    css = th.shared_css("field")
    assert f"--accent:  {th.FIELD.accent};" in css
    assert f"--bg:      {th.FIELD.bg};" in css
    assert f"--caption-fg:   {th.FIELD.caption.primary};" in css
    assert "GENERATED FILE" in css


def test_write_shared_css(tmp_path):
    out = th.write_shared_css(tmp_path, "signal")
    assert out == tmp_path / "slides" / "_shared.css"
    body = out.read_text()
    assert th.SIGNAL.accent in body
    # Round-trips: re-resolving the name gives the same tokens.
    assert f"--accent:  {th.resolve('signal').accent};" in body


def test_write_shared_css_defaults_to_amber(tmp_path):
    out = th.write_shared_css(tmp_path)
    assert th.AMBER.accent in out.read_text()


def test_themes_meet_contrast_floor():
    """Every theme keeps body text and accent legible on its own bg (>=4.5:1).
    This is the rule that keeps a burned caption from disappearing."""
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    for t in th.BUILTIN.values():
        assert ratio(t.text, t.bg) >= 4.5, f"{t.name}: text on bg {ratio(t.text, t.bg):.2f}"
        assert ratio(t.accent, t.bg) >= 3.0, f"{t.name}: accent on bg {ratio(t.accent, t.bg):.2f}"
        assert ratio(t.muted, t.bg) >= 3.0, f"{t.name}: muted on bg {ratio(t.muted, t.bg):.2f}"