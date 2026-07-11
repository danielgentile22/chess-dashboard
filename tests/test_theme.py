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

    def test_legend_is_borderless(self):
        """Legends separate by space, not a box (Apple-quiet chrome)."""
        fig = styles.apply_dark_theme(go.Figure())
        assert fig.layout.legend.borderwidth == 0


# ---------------------------------------------------------------------------
# Apple dark palette + system typography
# ---------------------------------------------------------------------------

class TestApplePalette:
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
    def test_rgba_helper_derives_from_a_token(self):
        assert styles.rgba("#30d158", 0.5) == "rgba(48,209,88,.5)"


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


# ---------------------------------------------------------------------------
# Pretty hover labels across every chart (PR #53 review feedback)
#
# Every chart's hover must be hand-written, not Plotly Express's raw
# "key=value<br>" default, and its hover *chrome* (the label box) must come
# from the same theme tokens the rest of the chart does.  These two tests walk
# every page's rendered figures and guard both at once.
# ---------------------------------------------------------------------------

# Default filter arguments — everything selected, no restriction — matching
# what the UI sends before any filter is touched (see test_ui_smoke.ALL_FILTERS).
_ALL_FILTER_ARGS = (
    ["White", "Black"],            # colors
    ["Win", "Draw", "Loss"],       # outcomes
    [],                            # terminations
    None, None,                    # start, end
    [],                            # events
    None,                          # moves
    None,                          # sync
    "official",                    # lens
)

# Every callback that returns a Plotly figure, across every page.  Listed by
# hand so a newly added chart that forgets a pretty hover trips this test.
_FIGURE_CALLBACKS = [
    ("pages.overview", "update_wdl"),
    ("pages.overview", "update_terminations"),
    ("pages.events", "update_event_bar"),
    ("pages.opponents", "update_opponents"),
    ("pages.opponents", "update_bucket"),
    ("pages.opponents", "update_scatter"),
    ("pages.openings", "update_opening_family"),
    ("pages.trends", "update_rating"),
    ("pages.trends", "update_winrate"),
    ("pages.trends", "update_monthly"),
    ("pages.trends", "update_dow"),
    ("pages.trends", "update_length_hist"),
    ("pages.trends", "update_time_control"),
    ("pages.trends", "update_round_performance"),
]

# The raw Plotly Express hover signature: a field name followed by "=", e.g.
# "Outcome=Win", "Count=18", "Event=ACC Friday Ladder".
_RAW_PX_HOVER = re.compile(r"\b[A-Z]\w*=")


def _walk_components(component):
    """Yield every Dash component in a layout tree (depth-first)."""
    if component is None or isinstance(component, (str, int, float, bool)):
        return
    if isinstance(component, (list, tuple)):
        for item in component:
            yield from _walk_components(item)
        return
    yield component
    yield from _walk_components(getattr(component, "children", None))


def _all_page_figures():
    """Every Plotly figure the pages render under the default (no-op) filters.

    Covers the 14 figure-returning callbacks plus the activity calendar, whose
    figures are embedded inside the HTML blocks it returns rather than served
    as a direct figure Output.
    """
    import importlib

    figures = []
    for module_name, fn_name in _FIGURE_CALLBACKS:
        fn = getattr(importlib.import_module(module_name), fn_name)
        figures.append((f"{fn_name}", fn(*_ALL_FILTER_ARGS)))

    from pages.trends import update_activity_calendar
    blocks = update_activity_calendar(*_ALL_FILTER_ARGS)
    for comp in _walk_components(blocks):
        fig = getattr(comp, "figure", None)
        if fig is not None:
            figures.append(("update_activity_calendar", fig))
    return figures


class TestPrettyHoverLabels:
    def test_no_chart_shows_the_raw_px_hover(self, ui_app, ui_data):
        """No trace may carry Plotly Express's raw "key=value" hover — every
        hover is hand-written (the PR #53 review ask)."""
        offenders = []
        for name, fig in _all_page_figures():
            for trace in fig.data:
                template = getattr(trace, "hovertemplate", None)
                if template and _RAW_PX_HOVER.search(template):
                    offenders.append((name, template))
        assert not offenders, f"raw px key=value hover still present: {offenders}"

    def test_every_trace_has_a_hand_written_hover(self, ui_app, ui_data):
        """Every visible trace either carries an explicit hovertemplate or has
        hover deliberately turned off (skip) — none falls back to the px
        default.  Heatmap cells use text+hoverinfo, not a template."""
        for name, fig in _all_page_figures():
            for trace in fig.data:
                kind = trace.type
                has_template = bool(getattr(trace, "hovertemplate", None))
                hoverinfo = getattr(trace, "hoverinfo", None) or ""
                deliberately_off = "skip" in hoverinfo or "none" in hoverinfo
                # Heatmaps drive hover from `text` + hoverinfo="text", not a template.
                text_driven = kind == "heatmap"
                assert has_template or deliberately_off or text_driven, (
                    f"{name}: {kind} trace has no hand-written hover"
                )

    def test_hovertemplates_end_with_the_empty_extra_box(self, ui_app, ui_data):
        """Every hovertemplate closes the secondary trace-name box with
        <extra></extra> (design spec)."""
        for name, fig in _all_page_figures():
            for trace in fig.data:
                template = getattr(trace, "hovertemplate", None)
                if template:
                    assert "<extra></extra>" in template, (
                        f"{name}: hovertemplate missing <extra></extra>: {template!r}"
                    )

    def test_hover_label_chrome_comes_from_theme_tokens(self, ui_app, ui_data):
        """The hover label box (bg / border / font) reads from the same theme
        tokens as the rest of the chart — applied once in apply_dark_theme, so
        every chart inherits it."""
        for name, fig in _all_page_figures():
            hoverlabel = fig.layout.hoverlabel
            assert hoverlabel.bgcolor == styles.COLORS["card2"], name
            assert hoverlabel.bordercolor == styles.COLORS["border"], name
            assert hoverlabel.font.color == styles.COLORS["text"], name
            assert hoverlabel.font.family == styles.FONT_SYSTEM, name

    def test_chart_theme_hover_label_uses_only_tokens(self):
        """apply_dark_theme sources the hover label box from COLORS tokens —
        no hardcoded hex (guards against drift from the palette)."""
        fig = styles.apply_dark_theme(go.Figure())
        hl = fig.layout.hoverlabel
        assert hl.bgcolor == styles.COLORS["card2"]
        assert hl.bordercolor == styles.COLORS["border"]
        assert hl.font.color == styles.COLORS["text"]
