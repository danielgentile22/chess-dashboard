"""
tests/test_theme.py
===================
Theme-tokens tests (issue #44 / PRD "Theme consistency" testing decision).

The theme tokens in ``styles.py`` are the single source of truth for every
color, font, and radius.  Both the Plotly chart theme and the CSS ``:root``
variable block (injected at app startup) must read from that one definition,
so they can never silently drift apart.  These tests assert exactly that —
plus the gold-discipline, no-Google-Fonts, tabular-numeral, and borderless-
card criteria from the issue.

External behavior only: what the token functions return and what the served
index contains — never pixel values or visual appearance.
"""
from __future__ import annotations

import re
from pathlib import Path

import plotly.graph_objects as go

import styles

CSS_PATH = Path(__file__).resolve().parent.parent / "assets" / "custom.css"


# ---------------------------------------------------------------------------
# Theme consistency: chart colors and CSS variables come from one definition
# ---------------------------------------------------------------------------

def _root_vars(block: str) -> dict[str, str]:
    """Parse ``--name: value;`` declarations out of a generated :root block."""
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", block)
    }


class TestThemeConsistency:
    def test_css_block_emits_every_registry_token(self):
        """The generated CSS :root block carries exactly the THEME registry —
        the same Python values the chart code reads, so they can't drift."""
        emitted = _root_vars(styles.css_root_block())
        for name, value in styles.THEME.items():
            assert name in emitted, f"{name} missing from generated :root block"
            assert emitted[name] == value, (
                f"{name} drifted: registry={value!r} css={emitted[name]!r}"
            )

    def test_chart_theme_colors_come_from_tokens(self):
        """apply_dark_theme reads its colors from the same token source the
        CSS block uses — a drifted token would fail both at once."""
        fig = styles.apply_dark_theme(go.Figure())
        layout = fig.layout

        # Font family is the system stack, not a web font.
        assert layout.font.family == styles.FONT_SYSTEM
        assert layout.font.color == styles.COLORS["text"]
        # Axis grid / tick colors derive from the border / muted tokens.
        assert layout.xaxis.gridcolor == styles.COLORS["border"]
        assert layout.yaxis.tickfont.color == styles.COLORS["muted"]
        # Hover label uses the nested-card surface + system font.
        assert layout.hoverlabel.bgcolor == styles.COLORS["card2"]
        assert layout.hoverlabel.font.family == styles.FONT_SYSTEM

    def test_outcome_map_matches_palette(self):
        """The W/D/L color map is derived from the palette tokens."""
        assert styles.WDL_COLOR_MAP["Win"] == styles.COLORS["win"]
        assert styles.WDL_COLOR_MAP["Draw"] == styles.COLORS["draw"]
        assert styles.WDL_COLOR_MAP["Loss"] == styles.COLORS["loss"]

    def test_legend_is_borderless(self):
        """Legends separate by space, not a box (Apple-quiet chrome)."""
        fig = styles.apply_dark_theme(go.Figure())
        assert fig.layout.legend.borderwidth == 0


# ---------------------------------------------------------------------------
# Apple dark palette + system typography
# ---------------------------------------------------------------------------

class TestApplePalette:
    def test_background_and_card_tokens(self):
        assert styles.COLORS["bg"] == "#0a0a0c"
        assert styles.COLORS["card"] == "#1c1c1e"
        assert styles.COLORS["card2"] == "#2c2c2e"

    def test_system_colors(self):
        assert styles.COLORS["win"] == "#30d158"     # systemGreen
        assert styles.COLORS["loss"] == "#ff453a"    # systemRed
        assert styles.COLORS["draw"] == "#8e8e93"    # systemGray
        assert styles.COLORS["primary"] == "#0a84ff"  # systemBlue
        assert styles.COLORS["warning"] == "#ff9f0a"  # systemOrange

    def test_softened_gold(self):
        assert styles.COLORS["accent"] == "#d9a13d"

    def test_white_and_translucent_text(self):
        assert styles.COLORS["text"] == "#ffffff"
        assert styles.COLORS["muted"].startswith("rgba(235,235,245")
        assert styles.COLORS["dim"].startswith("rgba(235,235,245")

    def test_system_font_has_no_web_fonts(self):
        stack = styles.FONT_SYSTEM
        assert "-apple-system" in stack
        for banned in ("Fraunces", "Inter", "IBM Plex", "DM Sans"):
            assert banned not in stack


# ---------------------------------------------------------------------------
# Reduced-opacity large fills, full-saturation small elements
# ---------------------------------------------------------------------------

class TestFillOpacity:
    def test_large_fills_are_reduced_opacity(self):
        """Big areas (donut wedges, calendar, win-rate area) are toned down."""
        assert styles.WIN_FILL == styles.rgba(styles.COLORS["win"], 0.55)
        assert styles.LOSS_FILL == styles.rgba(styles.COLORS["loss"], 0.55)
        assert styles.WIN_AREA == styles.rgba(styles.COLORS["win"], 0.10)

    def test_small_elements_stay_full_saturation(self):
        """Dots / lines / badges use the saturated tokens, not the washes."""
        assert styles.WDL_COLOR_MAP["Win"] == styles.COLORS["win"]
        assert "0.55" not in styles.COLORS["win"]

    def test_rgba_helper_derives_from_a_token(self):
        assert styles.rgba("#30d158", 0.5) == "rgba(48,209,88,.5)"


# ---------------------------------------------------------------------------
# Geometry: 12px cards, no border in the token set
# ---------------------------------------------------------------------------

class TestGeometry:
    def test_card_radius_is_12px(self):
        assert styles.THEME["--cs-radius"] == "12px"

    def test_card_token_carries_no_border(self):
        """The card surface is a fill only — no border color baked in."""
        assert styles.COLORS["card"] == "#1c1c1e"


# ---------------------------------------------------------------------------
# The stylesheet consumes tokens: no Google Fonts, no hardcoded gold
# ---------------------------------------------------------------------------

class TestStylesheet:
    def test_no_google_fonts_import(self):
        css = CSS_PATH.read_text()
        assert "fonts.googleapis.com" not in css
        assert "@import" not in css

    def test_no_hardcoded_root_token_block(self):
        """The token definitions live in Python, not hand-written in the CSS."""
        css = CSS_PATH.read_text()
        # No hand-written variable *definitions* like "--cs-bg: #0d1117;".
        assert re.search(r"--cs-bg\s*:", css) is None
        assert re.search(r"--cs-accent\s*:\s*#", css) is None

    def test_no_hardcoded_gold_literal(self):
        """Every gold tint derives from the gold token (no near-gold values)."""
        css = CSS_PATH.read_text()
        # The old gold hex and its rgb triple must not appear anywhere.
        assert "#d29922" not in css
        assert "#d9a13d" not in css
        assert re.search(r"210\s*,\s*153\s*,\s*34", css) is None

    def test_no_old_palette_literals(self):
        """The retired GitHub-dark literals don't linger as drift."""
        css = CSS_PATH.read_text()
        for retired in ("#0d1117", "#161b22", "#3fb950", "#f85149",
                        "#58a6ff", "#db6d28"):
            assert retired not in css, f"retired literal {retired} still in CSS"

    def test_inline_code_is_the_only_mono_user(self):
        """The mono var survives only for inline <code> (Lesson hints)."""
        css = CSS_PATH.read_text()
        assert css.count("var(--cs-font-mono)") == 1

    def test_data_values_use_tabular_numerals(self):
        css = CSS_PATH.read_text()
        assert "tabular-nums" in css
        assert "font-variant-numeric: tabular-nums" in css


# ---------------------------------------------------------------------------
# The tokens are injected into the served page
# ---------------------------------------------------------------------------

class TestInjection:
    def test_index_string_carries_generated_tokens(self, ui_app):
        index = ui_app.index_string
        assert "cs-theme-tokens" in index
        assert "--cs-bg: #0a0a0c;" in index
        assert "--cs-accent: #d9a13d;" in index
        assert "{%app_entry%}" in index  # still a valid Dash template

    def test_served_index_contains_tokens(self, ui_app, ui_data):
        resp = ui_app.server.test_client().get("/")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "cs-theme-tokens" in body
        assert "--cs-bg: #0a0a0c;" in body
        assert "fonts.googleapis.com" not in body
