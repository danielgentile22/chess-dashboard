"""
styles.py
=========
Shared Plotly style constants and helpers for the Chess Stats Dashboard.

All chart-building code should call ``apply_dark_theme(fig)`` after creating
a figure to ensure visual consistency across the entire dashboard.
"""
from __future__ import annotations

import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

COLORS = {
    # Outcome colours — used consistently on every chart
    "win":   "#3fb950",   # green
    "draw":  "#6e7681",   # gray
    "loss":  "#f85149",   # red
    # UI chrome
    "bg":     "#0d1117",  # page background
    "card":   "#161b22",  # card background
    "card2":  "#1c2128",  # alternate/hover card background
    "border": "#30363d",  # border / grid line
    "text":   "#e6edf3",  # primary text
    "muted":  "#8b949e",  # secondary / axis label text
    "dim":    "#6e7681",  # tertiary / placeholder text
    # Accents
    "accent":  "#d29922",  # gold / amber
    "primary": "#58a6ff",  # blue highlight
}

# Outcome → colour mapping used in Plotly ``color_discrete_map``
WDL_COLOR_MAP: dict[str, str] = {
    "Win":  COLORS["win"],
    "Draw": COLORS["draw"],
    "Loss": COLORS["loss"],
}

# Ordered colour sequence for multi-series charts
WDL_COLOR_SEQUENCE: list[str] = [COLORS["win"], COLORS["draw"], COLORS["loss"]]

# ECO family labels
ECO_FAMILY: dict[str, str] = {
    "A": "A — Flank / Indian",
    "B": "B — Semi-Open",
    "C": "C — Open",
    "D": "D — Closed / Semi-Closed",
    "E": "E — Indian Defences",
}

# ---------------------------------------------------------------------------
# Shared Plotly layout base
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
    Apply the dashboard's dark chess theme to *fig* in-place and return it.

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
            family="Inter, 'DM Sans', system-ui, sans-serif",
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
            bordercolor=COLORS["border"],
            borderwidth=1,
            font=dict(size=11, color=COLORS["muted"]),
            title_font=dict(color=COLORS["muted"]),
            title_text=legend_title,
        ),
        xaxis=dict(**_BASE_AXIS, title_text=xaxis_title),
        yaxis=dict(**_BASE_AXIS, title_text=yaxis_title),
        hoverlabel=dict(
            bgcolor=COLORS["card2"],
            bordercolor=COLORS["border"],
            font=dict(color=COLORS["text"], size=12),
        ),
    )
    if show_legend is not None:
        layout_kwargs["showlegend"] = show_legend
    fig.update_layout(**layout_kwargs)
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
