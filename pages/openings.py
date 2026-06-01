"""
pages/openings.py
=================
The Openings page — ECO families and per-opening results.
"""
from __future__ import annotations

import dash
import plotly.express as px
from dash import Output, callback, dash_table, html

from components import TABLE_CELL, TABLE_HEADER, chart_card, content_card, page_header
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import opening_summary
from styles import COLORS, WDL_COLOR_MAP, apply_dark_theme, empty_fig

dash.register_page(
    __name__, path="/openings", name="Openings", title="Openings — Chess Stats", order=2,
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Openings", "Where your repertoire wins and leaks points"),

        html.Div(className="g2", children=[
            chart_card("Win rate by ECO family", "opening-family-bar", height=420),
            content_card(
                "Top openings",
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
                        style_cell=TABLE_CELL,
                        style_header=TABLE_HEADER,
                        style_data_conditional=[
                            {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]}
                        ],
                    ),
                ]),
                height=420,
            ),
        ]),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("opening-family-bar", "figure"), FILTER_INPUTS)
def update_opening_family(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    fam, _ = opening_summary(df_f)
    if fam.empty:
        return empty_fig("No ECO data")
    long = fam.melt(
        id_vars=["FamilyName"],
        value_vars=["Win", "Draw", "Loss"],
        var_name="Outcome", value_name="Count",
    )
    fig = px.bar(
        long, x="Count", y="FamilyName", color="Outcome",
        orientation="h", barmode="stack",
        color_discrete_map=WDL_COLOR_MAP,
    )
    apply_dark_theme(fig, xaxis_title="Games", legend_title="Outcome")
    fig.update_yaxes(categoryorder="total ascending")
    return fig


@callback(Output("opening-table", "data"), FILTER_INPUTS)
def update_opening_table(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    _, opn = opening_summary(df_f)
    return opn.head(50).to_dict("records")
