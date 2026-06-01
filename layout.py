"""
layout.py
=========
Dash layout for the Chess Stats Dashboard.
Call ``make_layout(df, player_name)`` to get the app's root component.
All heavy computation (stats, figures) is done in callbacks.py; the layout
only sets up the skeleton and initial empty state.
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
from dash import dash_table, dcc, html

from styles import COLORS

# ---------------------------------------------------------------------------
# Shared table style
# ---------------------------------------------------------------------------

_TABLE_CELL = dict(
    fontFamily="Inter, system-ui, sans-serif", fontSize="12px",
    padding="7px 10px", whiteSpace="normal", height="auto",
    minWidth="70px", maxWidth="200px",
    backgroundColor=COLORS["card"], color=COLORS["text"],
    border=f"1px solid {COLORS['border']}",
)
_TABLE_HEADER = dict(
    fontWeight="700", backgroundColor=COLORS["card2"],
    color=COLORS["accent"], border=f"1px solid {COLORS['border']}",
    fontSize="10px", letterSpacing="0.07em", textTransform="uppercase",
)
_TABLE_DATA_COND = [
    {"if": {"filter_query": '{Outcome} = "Win"'},
     "backgroundColor": "rgba(63,185,80,.13)", "color": COLORS["text"]},
    {"if": {"filter_query": '{Outcome} = "Loss"'},
     "backgroundColor": "rgba(248,81,73,.11)", "color": COLORS["text"]},
    {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]},
]


# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------

def _kpi(label: str, component_id: str, value_class: str = "") -> html.Div:
    return html.Div(className="kpi-card", children=[
        html.Div(label, className="kpi-label"),
        html.Div("—", id=component_id, className=f"kpi-value {value_class}"),
    ])


def _kpi_bar() -> html.Div:
    return html.Div(className="kpi-bar", children=[
        _kpi("Total Games",        "kpi-total"),
        _kpi("Win %",              "kpi-win-pct",  "win"),
        _kpi("Draw %",             "kpi-draw-pct"),
        _kpi("Loss %",             "kpi-loss-pct", "loss"),
        _kpi("Current Rating",     "kpi-rating",   "accent"),
        _kpi("Peak Rating",        "kpi-peak",     "accent"),
        _kpi("Performance Rtg",    "kpi-perf",     "primary"),
        _kpi("Longest Win Streak", "kpi-streak",   "win"),
        _kpi("Unique Opponents",   "kpi-opps"),
        _kpi("Favourite Opening",  "kpi-fav-opn"),
    ])


# ---------------------------------------------------------------------------
# Chart card helper
# ---------------------------------------------------------------------------

def _chart_card(title: str, graph_id: str, height: int = 400) -> html.Div:
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


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _filters_section(df: pd.DataFrame) -> dbc.AccordionItem:
    termination_options = sorted(
        [t for t in df["Termination"].dropna().unique() if str(t).strip()]
    )
    event_options = sorted(
        [e for e in df["Event"].dropna().unique() if str(e).strip()]
    )
    df_dates = df[df["Date_dt"].notna()]
    min_date = df_dates["Date_dt"].min().date().isoformat() if not df_dates.empty else None
    max_date = df_dates["Date_dt"].max().date().isoformat() if not df_dates.empty else None

    full_moves = df["FullMoves"].dropna()
    min_mv = int(full_moves.min()) if not full_moves.empty else 1
    max_mv = int(full_moves.max()) if not full_moves.empty else 100

    return dbc.AccordionItem(
        title="Filters",
        item_id="filters",
        children=[
            # Preset buttons
            html.Div(className="preset-row", children=[
                html.Button("All games",    id="preset-all",    className="preset-btn"),
                html.Button("Last 20",      id="preset-last20", className="preset-btn"),
                html.Button("This year",    id="preset-year",   className="preset-btn"),
                html.Button("White only",   id="preset-white",  className="preset-btn"),
                html.Button("Black only",   id="preset-black",  className="preset-btn"),
                html.Button("Wins only",    id="preset-wins",   className="preset-btn"),
            ]),
            dbc.Row([
                dbc.Col([
                    html.Label("Color", className="text-muted",
                               style={"fontSize": "11px", "fontWeight": "600",
                                      "textTransform": "uppercase", "letterSpacing": ".07em"}),
                    dcc.Checklist(
                        id="color-filter",
                        options=[{"label": " White", "value": "White"},
                                 {"label": " Black", "value": "Black"}],
                        value=["White", "Black"], inline=True,
                        inputStyle={"marginRight": "4px"},
                    ),
                ], width=2),
                dbc.Col([
                    html.Label("Outcome", className="text-muted",
                               style={"fontSize": "11px", "fontWeight": "600",
                                      "textTransform": "uppercase", "letterSpacing": ".07em"}),
                    dcc.Checklist(
                        id="outcome-filter",
                        options=[{"label": " Win",  "value": "Win"},
                                 {"label": " Draw", "value": "Draw"},
                                 {"label": " Loss", "value": "Loss"}],
                        value=["Win", "Draw", "Loss"], inline=True,
                        inputStyle={"marginRight": "4px"},
                    ),
                ], width=3),
                dbc.Col([
                    html.Label("Termination", className="text-muted",
                               style={"fontSize": "11px", "fontWeight": "600",
                                      "textTransform": "uppercase", "letterSpacing": ".07em"}),
                    dcc.Dropdown(
                        id="termination-filter",
                        options=[{"label": t, "value": t} for t in termination_options],
                        value=[], multi=True, placeholder="All",
                    ),
                ], width=3),
                dbc.Col([
                    html.Label("Date range", className="text-muted",
                               style={"fontSize": "11px", "fontWeight": "600",
                                      "textTransform": "uppercase", "letterSpacing": ".07em"}),
                    dcc.DatePickerRange(
                        id="date-filter",
                        min_date_allowed=min_date, max_date_allowed=max_date,
                        start_date=min_date, end_date=max_date,
                        display_format="YYYY-MM-DD",
                    ),
                ], width=4),
            ], className="mb-2"),
            dbc.Row([
                dbc.Col([
                    html.Label("Events", className="text-muted",
                               style={"fontSize": "11px", "fontWeight": "600",
                                      "textTransform": "uppercase", "letterSpacing": ".07em"}),
                    dcc.Dropdown(
                        id="event-filter",
                        options=[{"label": e, "value": e} for e in event_options],
                        value=[], multi=True, placeholder="All events",
                    ),
                ], width=6),
                dbc.Col([
                    html.Label(f"Game length (moves)  [{min_mv}–{max_mv}]",
                               className="text-muted",
                               style={"fontSize": "11px", "fontWeight": "600",
                                      "textTransform": "uppercase", "letterSpacing": ".07em"}),
                    dcc.RangeSlider(
                        id="moves-filter",
                        min=min_mv, max=max_mv,
                        value=[min_mv, max_mv],
                        step=1,
                        marks=None,
                        tooltip={"placement": "bottom", "always_visible": True},
                    ),
                ], width=6),
            ], className="mt-2"),
            html.Div(id="filter-badge", className="mt-2",
                     style={"fontSize": "12px", "color": COLORS["muted"]}),
        ],
    )


def _overview_section() -> dbc.AccordionItem:
    return dbc.AccordionItem(
        title="Performance Overview",
        item_id="overview",
        children=[
            html.Div(className="g3", children=[
                # Streak card
                html.Div(className="chart-card", children=[
                    html.Div("Last 20 games", className="chart-title"),
                    html.Div(id="streak-badges", className="streak-badges"),
                    html.Div(id="streak-stats", className="streak-stats"),
                ]),
                _chart_card("Win / Draw / Loss",       "wdl-pie"),
                _chart_card("How games ended",          "termination-bar"),
            ]),
        ],
    )


def _timeline_section() -> dbc.AccordionItem:
    return dbc.AccordionItem(
        title="Timeline",
        item_id="timeline",
        children=[
            html.Div(className="g2", children=[
                _chart_card("Cumulative win rate over time", "winrate-line"),
                _chart_card("Your rating over time",         "rating-line"),
            ]),
        ],
    )


def _openings_section() -> dbc.AccordionItem:
    return dbc.AccordionItem(
        title="Openings",
        item_id="openings",
        children=[
            html.Div(className="g2", children=[
                _chart_card("Win rate by ECO family",    "opening-family-bar"),
                html.Div(className="chart-card", children=[
                    html.Div("Top openings", className="chart-title"),
                    html.Div(style={"flex": "1", "overflow": "auto"}, children=[
                        dash_table.DataTable(
                            id="opening-table",
                            columns=[
                                {"name": "ECO",     "id": "ECO"},
                                {"name": "Opening", "id": "Opening"},
                                {"name": "Games",   "id": "Games"},
                                {"name": "W",       "id": "Win"},
                                {"name": "D",       "id": "Draw"},
                                {"name": "L",       "id": "Loss"},
                                {"name": "Win %",   "id": "WinRate"},
                            ],
                            data=[], page_size=12, sort_action="native",
                            style_table={"overflowX": "auto"},
                            style_cell=_TABLE_CELL,
                            style_header=_TABLE_HEADER,
                            style_data_conditional=[
                                {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]}
                            ],
                        ),
                    ]),
                ]),
            ]),
        ],
    )


def _opponents_section(df: pd.DataFrame) -> dbc.AccordionItem:
    all_opponents = sorted(df["Opponent"].dropna().unique().tolist())
    return dbc.AccordionItem(
        title="Opponents",
        item_id="opponents",
        children=[
            html.Div(className="g2", children=[
                _chart_card("Opponent W/D/L (top 25, played >1 game)", "opponent-bar"),
                html.Div(className="chart-card", style={"height": "400px"}, children=[
                    html.Div("Head-to-Head Analyzer", className="chart-title"),
                    dcc.Dropdown(
                        id="h2h-opponent",
                        options=[{"label": o, "value": o} for o in all_opponents],
                        placeholder="Select an opponent…",
                        style={"marginBottom": "10px"},
                    ),
                    html.Div(id="h2h-stats"),
                ]),
            ]),
        ],
    )


def _strength_section() -> dbc.AccordionItem:
    return dbc.AccordionItem(
        title="Strength Analysis",
        item_id="strength",
        children=[
            html.Div(className="g2", children=[
                _chart_card("W/D/L by opponent rating difference", "rating-bucket-bar"),
                _chart_card("Outcome vs opponent rating",          "outcome-scatter"),
            ]),
        ],
    )


def _game_length_section() -> dbc.AccordionItem:
    return dbc.AccordionItem(
        title="Game Length",
        item_id="game-length",
        children=[
            html.Div(className="g2", children=[
                _chart_card("Move count distribution by outcome", "length-hist"),
                html.Div(className="chart-card", children=[
                    html.Div("Average game length", className="chart-title"),
                    html.Div(id="length-stats"),
                ]),
            ]),
        ],
    )


def _activity_section() -> dbc.AccordionItem:
    return dbc.AccordionItem(
        title="Activity",
        item_id="activity",
        children=[
            html.Div(className="g2", children=[
                _chart_card("Games per month",        "monthly-bar"),
                _chart_card("Win rate by day of week", "dow-bar"),
            ]),
        ],
    )


def _events_section() -> dbc.AccordionItem:
    return dbc.AccordionItem(
        title="Events & Tournaments",
        item_id="events",
        children=[
            html.Div(className="g2", children=[
                _chart_card("Performance per event (W/D/L)", "event-bar"),
                html.Div(className="chart-card", style={"height": "400px"}, children=[
                    html.Div("Event summary (click row for details)", className="chart-title"),
                    html.Div(style={"flex": "1", "overflow": "auto"}, children=[
                        dash_table.DataTable(
                            id="event-table",
                            columns=[
                                {"name": "Date",       "id": "FirstDate"},
                                {"name": "Event",      "id": "Event"},
                                {"name": "W",          "id": "Win"},
                                {"name": "D",          "id": "Draw"},
                                {"name": "L",          "id": "Loss"},
                                {"name": "Score",      "id": "Score"},
                                {"name": "Best Opp",   "id": "HighestOpp"},
                                {"name": "Best Rtg",   "id": "HighestOppRating"},
                                {"name": "vs Best",    "id": "HighestOppOutcome"},
                            ],
                            data=[], page_size=10, sort_action="native",
                            row_selectable="single",
                            style_table={"overflowX": "auto"},
                            style_cell=_TABLE_CELL,
                            style_header=_TABLE_HEADER,
                            style_data_conditional=[
                                {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]}
                            ],
                        ),
                    ]),
                ]),
            ]),
            # Tournament detail panel (shown when a row is selected)
            html.Div(id="tournament-detail", style={"marginTop": "14px"}),
        ],
    )


def _milestones_section() -> dbc.AccordionItem:
    return dbc.AccordionItem(
        title="Milestones",
        item_id="milestones",
        children=[html.Div(id="milestones-content")],
    )


def _games_table_section(df: pd.DataFrame) -> dbc.AccordionItem:
    display_cols = [
        "Index", "Date", "Event", "Round", "White", "WhiteRating",
        "Black", "BlackRating", "Result", "Outcome", "Color",
        "PlayerRating", "OpponentRating", "Termination",
        "FullMoves", "ECO", "Opening",
    ]
    cols = [{"name": c, "id": c} for c in display_cols if c in df.columns]
    # Open-on-Lichess link — rendered as markdown so it's clickable
    cols.append({"name": "Lichess", "id": "Lichess", "presentation": "markdown"})

    return dbc.AccordionItem(
        title="All Games",
        item_id="all-games",
        children=[
            html.Div(
                style={
                    "resize": "both", "overflow": "hidden",
                    "minHeight": "440px", "height": "600px",
                    "display": "flex", "flexDirection": "column",
                    "border": f"1px solid {COLORS['border']}",
                    "borderRadius": "8px", "padding": "12px",
                    "background": COLORS["card"],
                },
                children=[
                    html.Div("All games (filtered)", className="chart-title"),
                    html.Div(style={"flex": "1", "overflow": "auto"}, children=[
                        dash_table.DataTable(
                            id="games-table",
                            columns=cols, data=[],
                            page_size=25, sort_action="native",
                            filter_action="native",
                            markdown_options={"link_target": "_blank"},
                            style_table={"overflowX": "auto"},
                            style_cell=_TABLE_CELL,
                            style_header=_TABLE_HEADER,
                            style_data_conditional=_TABLE_DATA_COND,
                        ),
                    ]),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Root layout
# ---------------------------------------------------------------------------

def make_layout(df: pd.DataFrame, player_name: str) -> html.Div:
    """Build and return the root Dash layout component."""
    total = len(df)
    date_range = ""
    if not df.empty:
        dated = df[df["Date_dt"].notna()]
        if not dated.empty:
            lo = dated["Date_dt"].min().strftime("%b %Y")
            hi = dated["Date_dt"].max().strftime("%b %Y")
            date_range = f"{lo} – {hi}"

    return html.Div(children=[
        # ── Sticky header ──────────────────────────────────────
        html.Div(className="app-header", children=[
            html.Div(className="app-header-left", children=[
                html.Span("♟", className="app-header-icon"),
                html.Span(f"Chess Stats — {player_name}"),
            ]),
            html.Div(className="app-header-right", children=[
                html.Span([html.Strong(f"{total}"), " games"], className="app-header-stat"),
                html.Span(date_range, className="app-header-stat"),
            ]),
        ]),

        # ── Page body ──────────────────────────────────────────
        dbc.Container(fluid=True, style={"maxWidth": "1580px", "padding": "0 16px 40px"}, children=[
            # KPI bar
            _kpi_bar(),

            # Sections
            dbc.Accordion(
                id="main-accordion",
                always_open=True,
                active_item=[
                    "filters", "overview", "timeline", "openings",
                    "opponents", "strength", "game-length", "activity",
                    "events", "milestones", "all-games",
                ],
                children=[
                    _filters_section(df),
                    _overview_section(),
                    _timeline_section(),
                    _openings_section(),
                    _opponents_section(df),
                    _strength_section(),
                    _game_length_section(),
                    _activity_section(),
                    _events_section(),
                    _milestones_section(),
                    _games_table_section(df),
                ],
            ),
        ]),
    ])
