"""
pages/events.py
===============
The Events page — tournament-by-tournament performance.
"""
from __future__ import annotations

import dash
import plotly.express as px
from dash import Input, Output, callback, dash_table, html

from components import (
    TABLE_CELL,
    TABLE_DATA_COND,
    TABLE_HEADER,
    chart_card,
    content_card,
    page_header,
    register_game_navigation,
)
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import event_summary, performance_rating_stats
from styles import COLORS, WDL_COLOR_MAP, apply_dark_theme, empty_fig

dash.register_page(
    __name__, path="/events", name="Events", title="Events — Chess Stats", order=4,
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Events", "Tournament performance, event by event"),

        html.Div(className="g2", children=[
            chart_card("Performance per event (W/D/L)", "event-bar", height=420),
            content_card(
                "Event summary (select a row for details)",
                html.Div(style={"flex": "1", "overflow": "auto"}, children=[
                    dash_table.DataTable(
                        id="event-table",
                        columns=[
                            {"name": "Date",     "id": "FirstDate"},
                            {"name": "Event",    "id": "Event"},
                            {"name": "W",        "id": "Win"},
                            {"name": "D",        "id": "Draw"},
                            {"name": "L",        "id": "Loss"},
                            {"name": "Score",    "id": "Score"},
                            {"name": "Best Opp", "id": "HighestOpp"},
                            {"name": "Best Rtg", "id": "HighestOppRating"},
                            {"name": "vs Best",  "id": "HighestOppOutcome"},
                        ],
                        data=[], page_size=10, sort_action="native",
                        row_selectable="single",
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

        # Tournament detail panel (shown when a row is selected)
        html.Div(id="tournament-detail"),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("event-bar", "figure"), FILTER_INPUTS)
def update_event_bar(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    ev = event_summary(df_f)
    if ev.empty:
        return empty_fig("No event data")
    long = ev.tail(20).melt(
        id_vars=["Event"],
        value_vars=["Win", "Draw", "Loss"],
        var_name="Outcome", value_name="Count",
    )
    fig = px.bar(long, x="Event", y="Count", color="Outcome",
                 barmode="stack", color_discrete_map=WDL_COLOR_MAP)
    apply_dark_theme(fig, legend_title="Outcome")
    fig.update_xaxes(tickangle=35, automargin=True)
    return fig


@callback(Output("event-table", "data"), FILTER_INPUTS)
def update_event_table(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    return event_summary(df_f).to_dict("records")


@callback(
    Output("tournament-detail", "children"),
    Input("event-table", "selected_rows"),
    Input("event-table", "data"),
    FILTER_INPUTS,
)
def update_tournament_detail(selected_rows, table_data, colors, outcomes,
                             terminations, start, end, events, moves, _sync=None, lens=None):
    if not selected_rows or not table_data:
        return None
    # The selection can be stale: a filter change may have shrunk the table
    # since the row was selected.
    if selected_rows[0] >= len(table_data):
        return None
    row = table_data[selected_rows[0]]
    event_name = row.get("Event", "")
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    # RoundNum, not Round: round 10 sorts after round 9, not after round 1
    ev_games = df_f[df_f["Event"] == event_name].sort_values(
        ["Date", "RoundNum"], na_position="last"
    )
    if ev_games.empty:
        return None

    pr = performance_rating_stats(ev_games)
    pr_str = f"  |  Performance rating: {pr['performance_rating']}" if pr["performance_rating"] else ""

    cols = [
        # RoundNum (numeric), not the raw string: a user clicking the header
        # to re-sort must get 1, 2, …, 10 — not the lexical 1, 10, 2
        {"name": "Round",       "id": "RoundNum", "type": "numeric"},
        {"name": "Color",       "id": "Color"},
        {"name": "Opponent",    "id": "Opponent"},
        {"name": "Opp Rating",  "id": "OpponentRating"},
        {"name": "Result",      "id": "Result"},
        {"name": "Outcome",     "id": "Outcome"},
        {"name": "Termination", "id": "Termination"},
        {"name": "Moves",       "id": "FullMoves"},
    ]
    return content_card(
        "Event detail — click a game to open it",
        html.Div(
            f"{event_name}  —  {row.get('Score', '')} points{pr_str}",
            style={"fontWeight": "600", "marginBottom": "10px",
                   "fontSize": "14px", "color": COLORS["text"]},
        ),
        html.Div(className="clickable-rows", children=[
            dash_table.DataTable(
                id="event-games-table",
                columns=cols,
                data=ev_games[["RoundNum", "Color", "Opponent", "OpponentRating",
                               "Result", "Outcome", "Termination", "FullMoves",
                               "ChapterURL"]].to_dict("records"),
                page_size=20, sort_action="native",
                style_table={"overflowX": "auto"},
                style_cell={**TABLE_CELL, "fontSize": "11px"},
                style_header=TABLE_HEADER,
                style_data_conditional=TABLE_DATA_COND,
            ),
        ]),
    )


navigate_to_game_from_event = register_game_navigation(
    "event-games-table",
    "Clicking a Game in an event's detail panel opens its detail view.")
