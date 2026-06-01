"""
components.py
=============
Shared UI building blocks for the multi-page Chess Stats Dashboard.

Every page composes its layout from these helpers so the whole app keeps a
single visual language: page headers, chart cards, KPI cards, empty states,
and the dark DataTable styles.
"""
from __future__ import annotations

from dash import dcc, html

from styles import COLORS

# ---------------------------------------------------------------------------
# Dark DataTable styles (shared by every table in the app)
# ---------------------------------------------------------------------------

TABLE_CELL = dict(
    fontFamily="'IBM Plex Mono', ui-monospace, monospace", fontSize="12px",
    padding="7px 10px", whiteSpace="normal", height="auto",
    minWidth="70px", maxWidth="200px",
    backgroundColor=COLORS["card"], color=COLORS["text"],
    border=f"1px solid {COLORS['border']}",
)
TABLE_HEADER = dict(
    fontFamily="Inter, system-ui, sans-serif",
    fontWeight="700", backgroundColor=COLORS["card2"],
    color=COLORS["accent"], border=f"1px solid {COLORS['border']}",
    fontSize="10px", letterSpacing="0.07em", textTransform="uppercase",
)
TABLE_DATA_COND = [
    {"if": {"filter_query": '{Outcome} = "Win"'},
     "backgroundColor": "rgba(63,185,80,.13)", "color": COLORS["text"]},
    {"if": {"filter_query": '{Outcome} = "Loss"'},
     "backgroundColor": "rgba(248,81,73,.11)", "color": COLORS["text"]},
    {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]},
]


# ---------------------------------------------------------------------------
# Page scaffolding
# ---------------------------------------------------------------------------

def page_header(title: str, subtitle: str = "") -> html.Div:
    """The serif title block at the top of every page."""
    children: list = [html.H1(title, className="page-title")]
    if subtitle:
        children.append(html.Div(subtitle, className="page-subtitle"))
    return html.Div(children, className="page-header")


def chart_card(title: str, graph_id: str, *, height: int = 380) -> html.Div:
    """A dark card holding one Plotly graph."""
    return html.Div(
        className="chart-card",
        style={"height": f"{height}px"},
        children=[
            html.Div(title, className="chart-title"),
            html.Div(className="chart-body", children=[
                dcc.Graph(
                    id=graph_id,
                    style={"height": "100%", "width": "100%"},
                    config={"displayModeBar": False, "responsive": True},
                )
            ]),
        ],
    )


def content_card(title: str, *children, card_id: str | None = None,
                 height: int | None = None) -> html.Div:
    """A dark card holding arbitrary content (tables, stat grids, …)."""
    style = {"height": f"{height}px"} if height else {}
    kwargs = {"id": card_id} if card_id else {}
    return html.Div(
        className="chart-card",
        style=style,
        children=[html.Div(title, className="chart-title"), *children],
        **kwargs,
    )


def kpi_card(label: str, value_id: str, value_class: str = "") -> html.Div:
    """One KPI tile: uppercase label over a big mono numeral."""
    return html.Div(className="kpi-card", children=[
        html.Div(label, className="kpi-label"),
        html.Div("—", id=value_id, className=f"kpi-value {value_class}"),
    ])


def empty_state(glyph: str, title: str, *lines) -> html.Div:
    """
    A deliberate empty state: a chess glyph, a serif heading, and explanation
    lines (used for placeholder pages and no-data situations).
    """
    return html.Div(className="empty-state", children=[
        html.Div(glyph, className="empty-state-glyph"),
        html.Div(title, className="empty-state-title"),
        *[html.Div(line, className="empty-state-line") for line in lines],
    ])
