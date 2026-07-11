"""
styles.py
=========
The theme-tokens module — the single source of truth for every color, font,
and radius in the Chess Dashboard.

Both the Plotly chart code and the CSS ``:root`` variable block consume the
*same* token definitions, so they can never silently drift apart:

* Python chart code reads :data:`COLORS` / the fill helpers and calls
  :func:`apply_dark_theme` after building a figure.
* The browser stylesheet (``assets/custom.css``) consumes ``var(--cs-*)``
  variables.  Those variables are generated from the tokens here by
  :func:`css_root_block` and injected into ``<head>`` at app startup (see
  ``app.py``).  ``assets/custom.css`` no longer defines them by hand.

The palette is Apple dark mode: a near-black background, borderless cards
separated by fill, hairline separators, the system colors (green / red /
blue / orange / gray), a softened achievement gold, and white + secondary /
tertiary text.  Typography is the Apple system stack; the mono face survives
only for inline ``code``.
"""
from __future__ import annotations

import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """``"#30d158"`` → ``(48, 209, 88)``."""
    h = value.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgba(value: str, alpha: float) -> str:
    """A token hex (or ``r,g,b`` triple) plus *alpha* → a CSS ``rgba(...)``.

    Every tint, wash, and glow in the app derives from a token through this
    helper, so nothing can drift from the token it is meant to echo.
    """
    r, g, b = _hex_to_rgb(value)
    # Render alpha CSS-style: ".5" not "0.5", "1" not "1.0" — matching the
    # hand-authored token values (e.g. the hairline border rgba(84,84,88,.5)).
    a = f"{alpha:g}"
    if a.startswith("0."):
        a = a[1:]
    return f"rgba({r},{g},{b},{a})"


# ---------------------------------------------------------------------------
# Apple dark-mode palette — the only place these values are written
# ---------------------------------------------------------------------------

COLORS = {
    # Outcome colours — used consistently on every chart (full saturation;
    # large fills get the reduced-opacity variants below).
    "win":   "#30d158",   # systemGreen
    "draw":  "#8e8e93",   # systemGray
    "loss":  "#ff453a",   # systemRed
    # UI chrome
    "bg":     "#0a0a0c",  # page background (near-black)
    "card":   "#1c1c1e",  # card surface — no border, separation by fill
    "card2":  "#2c2c2e",  # nested / elevated surface — no border
    "border": "rgba(84,84,88,.5)",  # hairline separator (inside lists only)
    "text":   "#ffffff",  # primary text
    "muted":  "rgba(235,235,245,.6)",  # secondary / axis label text
    "dim":    "rgba(235,235,245,.3)",  # tertiary / placeholder text
    # Accents
    "accent":  "#d9a13d",  # achievement gold (softened) — achievements only
    "primary": "#0a84ff",  # systemBlue — interactive
    "warning": "#ff9f0a",  # systemOrange — conflict / warning
    # Chess board — the light square; the pgn-viewer overlays dark squares at
    # 20% opacity on top, so one muted-slate value yields a dark board that
    # reads against both piece colours, no flashbang (issue #60 [F6]).
    "board":   "#595d66",
}

# Outcome → colour mapping used in Plotly ``color_discrete_map``
WDL_COLOR_MAP: dict[str, str] = {
    "Win":  COLORS["win"],
    "Draw": COLORS["draw"],
    "Loss": COLORS["loss"],
}

# ---------------------------------------------------------------------------
# Typography — Apple system stack; mono only for inline code
# ---------------------------------------------------------------------------

# The system stack renders as SF Pro on Apple devices, Segoe UI on Windows,
# Roboto on Android — native everywhere.  No web fonts are loaded.
FONT_SYSTEM = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
    "'Helvetica Neue', Arial, sans-serif"
)
# Mono survives only for inline ``code`` (the Lesson: convention hints).
FONT_MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"

# ---------------------------------------------------------------------------
# Derived fill / wash tokens — all computed from the palette above
# ---------------------------------------------------------------------------

# Large area fills (donut wedges, calendar cells, win-rate area) are toned
# down so big blocks of green/red don't dominate; small elements (dots,
# lines, badges, text) keep full saturation.
WIN_FILL = rgba(COLORS["win"], 0.55)
LOSS_FILL = rgba(COLORS["loss"], 0.55)
DRAW_FILL = rgba(COLORS["draw"], 0.55)

# The subtle table-row washes (Win / Loss rows in the Games table, etc.).
WIN_WASH = rgba(COLORS["win"], 0.13)
LOSS_WASH = rgba(COLORS["loss"], 0.11)
DRAW_WASH = rgba(COLORS["draw"], 0.13)

# The faint win-rate trend area fill.
WIN_AREA = rgba(COLORS["win"], 0.10)

# ---------------------------------------------------------------------------
# Token registry — pairs every CSS ``--cs-*`` variable with its Python value.
# This is the single mapping the CSS generator and the theme tests read.
# The big body of rules in assets/custom.css consumes these by name; only the
# *values* and their *source* (Python) live here.
# ---------------------------------------------------------------------------

THEME: dict[str, str] = {
    # Surfaces
    "--cs-bg":      COLORS["bg"],
    "--cs-card":    COLORS["card"],
    "--cs-card2":   COLORS["card2"],
    "--cs-hover":   COLORS["card2"],   # hover elevation == nested-card fill
    "--cs-border":  COLORS["border"],
    # Text
    "--cs-text":    COLORS["text"],
    "--cs-muted":   COLORS["muted"],
    "--cs-dim":     COLORS["dim"],
    # Outcomes + their reduced-opacity washes
    "--cs-win":     COLORS["win"],
    "--cs-win-bg":  WIN_WASH,
    "--cs-draw":    COLORS["draw"],
    "--cs-draw-bg": DRAW_WASH,
    "--cs-loss":    COLORS["loss"],
    "--cs-loss-bg": LOSS_WASH,
    # Accents
    "--cs-accent":  COLORS["accent"],
    "--cs-primary": COLORS["primary"],
    "--cs-warning": COLORS["warning"],
    # Chess board square (pgn-viewer) — issue #60 [F6]
    "--cs-board":   COLORS["board"],
    # Gold-discipline washes — every gold tint derives from the gold token so
    # nothing can drift from it.
    "--cs-accent-wash":   rgba(COLORS["accent"], 0.12),
    "--cs-accent-wash-2": rgba(COLORS["accent"], 0.05),
    "--cs-accent-line":   rgba(COLORS["accent"], 0.45),
    "--cs-accent-glow":   rgba(COLORS["accent"], 0.45),
    "--cs-accent-shadow": rgba(COLORS["accent"], 0.15),
    # Warning / conflict (systemOrange) washes — Reconciliation badges
    "--cs-warning-wash":    rgba(COLORS["warning"], 0.12),
    "--cs-warning-wash-2":  rgba(COLORS["warning"], 0.1),
    "--cs-warning-line":    rgba(COLORS["warning"], 0.45),
    "--cs-warning-line-2":  rgba(COLORS["warning"], 0.4),
    "--cs-warning-hover":   rgba(COLORS["warning"], 0.22),
    "--cs-warning-hover-2": rgba(COLORS["warning"], 0.18),
    # Interactive (systemBlue) washes
    "--cs-primary-wash":   rgba(COLORS["primary"], 0.12),
    "--cs-primary-wash-2": rgba(COLORS["primary"], 0.08),
    "--cs-primary-wash-3": rgba(COLORS["primary"], 0.1),
    "--cs-primary-line":   rgba(COLORS["primary"], 0.35),
    "--cs-primary-line-2": rgba(COLORS["primary"], 0.3),
    "--cs-primary-glow":   rgba(COLORS["primary"], 0.075),
    # Outcome glows / lines used on dots, badges, repertoire flags
    "--cs-win-glow":   rgba(COLORS["win"], 0.6),
    "--cs-win-line":   rgba(COLORS["win"], 0.5),
    "--cs-loss-glow":  rgba(COLORS["loss"], 0.45),
    "--cs-loss-line":  rgba(COLORS["loss"], 0.5),
    "--cs-loss-wash-2": rgba(COLORS["loss"], 0.10),
    "--cs-loss-wash-3": rgba(COLORS["loss"], 0.20),
    "--cs-loss-wash-4": rgba(COLORS["loss"], 0.03),
    "--cs-loss-line-2": rgba(COLORS["loss"], 0.4),
    "--cs-loss-line-3": rgba(COLORS["loss"], 0.3),
    # Neutral background washes (counters, scrims) — derived from the bg token
    "--cs-bg-wash":    rgba(COLORS["bg"], 0.35),
    "--cs-bg-wash-2":  rgba(COLORS["bg"], 0.25),
    "--cs-bg-scrim":   rgba(COLORS["bg"], 0.7),
    "--cs-bg-shadow":  rgba(COLORS["bg"], 0.65),
    # Translucent header backdrop — the card surface at glass opacity
    "--cs-card-glass": rgba(COLORS["card"], 0.88),
    # Streak-fire glow — derived from the warning (systemOrange) token so the
    # achievement-fire halo can't drift from the palette.
    "--cs-fire-glow":   rgba(COLORS["warning"], 0.85),
    "--cs-fire-glow-2": rgba(COLORS["warning"], 0.7),
    "--cs-fire-glow-3": rgba(COLORS["warning"], 1),
    # Neutral text washes (focus / active fills, atmosphere texture)
    "--cs-text-wash":  rgba(COLORS["text"], 0.06),
    "--cs-text-wash-2": rgba(COLORS["text"], 0.10),
    "--cs-text-glow":  rgba(COLORS["text"], 0.25),
    "--cs-text-texture": rgba(COLORS["text"], 0.016),
    # Geometry + fonts
    "--cs-radius":   "12px",
    "--cs-header-h": "58px",
    "--cs-font-body":    FONT_SYSTEM,
    "--cs-font-display": FONT_SYSTEM,   # display == system; weight + size, not a serif
    "--cs-font-data":    FONT_SYSTEM,   # data values: system stack + tabular-nums
    "--cs-font-mono":    FONT_MONO,     # inline <code> only
}

# Dash 4 component variables (Dropdown, DatePicker, Slider, …).  Dash injects
# light-theme values at runtime, so the doubled ``:root:root`` selector
# outranks them.  These derive from the tokens above.
_DASH_THEME: dict[str, str] = {
    "--Dash-Text-Primary":  "var(--cs-text)",
    "--Dash-Text-Strong":   "var(--cs-text)",
    "--Dash-Text-Weak":     "var(--cs-muted)",
    "--Dash-Text-Disabled": "var(--cs-dim)",
    "--Dash-Stroke-Strong": "var(--cs-border)",
    "--Dash-Stroke-Weak":   "var(--cs-border)",
    "--Dash-Fill-Interactive-Strong": "var(--cs-primary)",
    "--Dash-Fill-Interactive-Weak":   "var(--cs-primary-wash)",
    "--Dash-Fill-Inverse-Strong": "var(--cs-card2)",
    "--Dash-Fill-Inverse-strong": "var(--cs-card2)",  # dash ships both spellings
    "--Dash-Fill-Primary-Hover":  "var(--cs-text-wash)",
    "--Dash-Fill-Primary-Active": "var(--cs-text-wash-2)",
    "--Dash-Fill-Disabled": rgba(COLORS["draw"], 0.25),
    "--Dash-Shading-Strong": rgba(COLORS["bg"], 0.55),
    "--Dash-Shading-Weak":   rgba(COLORS["bg"], 0.35),
    "--Dash-Tooltip-Background-Color": "var(--cs-card2)",
    "--Dash-Tooltip-Border-Color": "var(--cs-border)",
}


def css_root_block() -> str:
    """Generate the ``:root`` CSS variable block from the token registry.

    The returned string is injected into ``<head>`` at app startup so the
    browser variables come from the same Python definition the charts use.
    """
    lines = ["  /* Theme tokens — generated from styles.THEME (do not hand-edit). */"]
    for name, value in THEME.items():
        lines.append(f"  {name}: {value};")
    root = ":root {\n" + "\n".join(lines) + "\n}"

    dash_lines = [f"  {name}: {value};" for name, value in _DASH_THEME.items()]
    dash_root = ":root:root {\n" + "\n".join(dash_lines) + "\n}"

    return f"{root}\n{dash_root}\n"


# ---------------------------------------------------------------------------
# Shared Plotly chart theme — reads the same tokens as the CSS
# ---------------------------------------------------------------------------

_BASE_AXIS = dict(
    gridcolor=COLORS["border"],
    linecolor=COLORS["border"],
    tickcolor=COLORS["dim"],
    zerolinecolor=COLORS["border"],
    tickfont=dict(color=COLORS["muted"], size=11),
    title_font=dict(color=COLORS["muted"], size=11),
)


def apply_dark_theme(
    fig: go.Figure,
    *,
    title: str = "",
    xaxis_title: str = "",
    yaxis_title: str = "",
    legend_title: str = "",
    show_legend: bool | None = None,
) -> go.Figure:
    """
    Apply the dashboard's Apple dark theme to *fig* in-place and return it.

    System font, thin/faint gridlines, a borderless legend, and soft hover
    labels — every color reads from :data:`COLORS` so charts and CSS share
    one definition.

    Parameters
    ----------
    fig           : Plotly figure to modify.
    title         : Chart subtitle shown in muted text above the plot.
    xaxis_title   : Label for the x-axis (empty = no label).
    yaxis_title   : Label for the y-axis (empty = no label).
    legend_title  : Label shown above the legend.
    show_legend   : Override legend visibility.  None = Plotly default.
    """
    layout_kwargs: dict = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            color=COLORS["text"],
            family=FONT_SYSTEM,
            size=12,
        ),
        title=dict(
            text=title,
            font=dict(size=12, color=COLORS["muted"]),
            x=0,
            pad=dict(l=0, t=0),
        ) if title else dict(text=""),
        autosize=True,
        height=None,
        margin=dict(l=8, r=8, t=36 if title else 12, b=8),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(0,0,0,0)",   # borderless — separation by space
            borderwidth=0,
            font=dict(size=11, color=COLORS["muted"]),
            title_font=dict(color=COLORS["muted"]),
            title_text=legend_title,
        ),
        xaxis=dict(**_BASE_AXIS, title_text=xaxis_title),
        yaxis=dict(**_BASE_AXIS, title_text=yaxis_title),
        hoverlabel=dict(
            bgcolor=COLORS["card2"],      # nested-card surface
            bordercolor=COLORS["border"],  # hairline separator
            font=dict(color=COLORS["text"], size=13, family=FONT_SYSTEM),
        ),
    )
    if show_legend is not None:
        layout_kwargs["showlegend"] = show_legend
    fig.update_layout(**layout_kwargs)
    # Thin, faint gridlines (separation by space, not by gridlines).
    fig.update_xaxes(gridwidth=1, griddash="solid")
    fig.update_yaxes(gridwidth=1, griddash="solid")
    return fig


# Quiet lowercase outcome words for hover labels.  The bar's color already
# says Win / Draw / Loss; the word stays short and lowercase, never a
# "Outcome=" prefix.
WDL_HOVER_WORD: dict[str, str] = {"Win": "wins", "Draw": "draws", "Loss": "losses"}


def apply_wdl_hover(fig: go.Figure, *, value_axis: str = "x") -> go.Figure:
    """Give each W/D/L trace of a stacked bar a quiet ``<b>N</b> wins`` hover.

    Plotly Express splits a ``color="Outcome"`` stacked bar into one trace per
    outcome, named "Win" / "Draw" / "Loss".  The row label (event / opponent /
    family) is already on the other axis, so the hover never repeats it — it
    shows only the bold count and the lowercase outcome word, ending with the
    empty ``<extra>`` box.  *value_axis* is the count axis ("x" for horizontal
    bars, "y" for vertical).
    """
    for trace in fig.data:
        word = WDL_HOVER_WORD.get(getattr(trace, "name", ""), str(trace.name).lower())
        trace.hovertemplate = f"<b>%{{{value_axis}}}</b> {word}<extra></extra>"
    return fig


def empty_fig(message: str = "No data") -> go.Figure:
    """Return a blank dark-themed figure with a centred *message*."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(color=COLORS["dim"], size=13),
    )
    apply_dark_theme(fig)
    return fig
