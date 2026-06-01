"""
pages/opponents.py
==================
The Opponents page — who you play and how those games go.

Per the PRD layout decision, Strength Analysis (rating buckets + outcome
scatter) folds in here alongside the per-opponent records and the
head-to-head analyzer.
"""
from __future__ import annotations

import dash
import plotly.express as px
from dash import Input, Output, State, callback, dash_table, dcc, html

import data
from components import (
    TABLE_CELL,
    TABLE_HEADER,
    chart_card,
    content_card,
    page_header,
    row_click_to_game,
)
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import (
    head_to_head,
    opponent_rating_bucket_summary,
    opponent_summary,
    outcome_vs_rating_data,
)
from styles import COLORS, WDL_COLOR_MAP, apply_dark_theme, empty_fig

dash.register_page(
    __name__, path="/opponents", name="Opponents", title="Opponents — Chess Stats", order=3,
)


def _lichess_link(chapter_url: str) -> str:
    """Markdown 'Open on Lichess' link for a Game's ChapterURL ('' if none)."""
    return f"[Open ↗]({chapter_url})" if chapter_url else ""


def _opponent_options() -> list[dict]:
    df = data.get_df()
    if df.empty:
        return []
    return [{"label": o, "value": o} for o in sorted(df["Opponent"].dropna().unique())]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Opponents", "Head-to-head records and strength analysis"),

        html.Div(className="g2", children=[
            chart_card("Opponent W/D/L (top 25, played >1 game)", "opponent-bar", height=440),
            content_card(
                "Head-to-Head Analyzer",
                dcc.Dropdown(
                    id="h2h-opponent",
                    options=_opponent_options(),
                    placeholder="Select an opponent…",
                    style={"marginBottom": "10px"},
                ),
                html.Div(id="h2h-stats", style={"flex": "1", "overflow": "auto"}),
                height=440,
            ),
        ]),
        html.Div(className="g2", children=[
            chart_card("W/D/L by opponent rating difference", "rating-bucket-bar"),
            chart_card("Outcome vs opponent rating", "outcome-scatter"),
        ]),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("opponent-bar", "figure"), FILTER_INPUTS)
def update_opponents(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    opp = opponent_summary(df_f)
    if opp.empty:
        return empty_fig("No repeat opponents in current filter")
    top = opp.head(25).copy()
    long = top.melt(
        id_vars=["Opponent"],
        value_vars=["Win", "Draw", "Loss"],
        var_name="Outcome", value_name="Count",
    )
    fig = px.bar(
        long, x="Opponent", y="Count", color="Outcome",
        barmode="stack", color_discrete_map=WDL_COLOR_MAP,
    )
    apply_dark_theme(fig, legend_title="Outcome")
    fig.update_xaxes(tickangle=35, automargin=True)
    return fig


@callback(
    Output("h2h-opponent", "options"),
    Input("sync-store", "data"),
    prevent_initial_call=True,  # the layout holds correct values at page load
)
def update_h2h_options(_sync):
    """Keep the opponent picker in step with the data after a Sync."""
    return _opponent_options()


@callback(
    Output("h2h-stats", "children"),
    Input("h2h-opponent", "value"),
    FILTER_INPUTS,
)
def update_h2h(opponent, colors, outcomes, terminations, start, end, events, moves, _sync=None):
    if not opponent:
        return html.Div("Select an opponent above.",
                        style={"color": COLORS["dim"], "fontSize": "12px"})
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    h = head_to_head(df_f, opponent)
    if h["total"] == 0:
        return html.Div(f"No games vs {opponent} in current filter.",
                        style={"color": COLORS["dim"], "fontSize": "12px"})

    avg_str = f" (avg rating {int(h['avg_opp_rating'])})" if h["avg_opp_rating"] else ""

    def _stat(label, value, cls=""):
        return html.Div(className="h2h-stat", children=[
            html.Div(label, className="h2h-stat-label"),
            html.Div(value, className=f"h2h-stat-val {cls}"),
        ])

    return html.Div([
        html.Div(f"{h['total']} games{avg_str}",
                 style={"fontSize": "12px", "color": COLORS["muted"], "marginBottom": "10px"}),
        html.Div(className="h2h-stat-grid", children=[
            _stat("Wins",   str(h["win"]),  "win"),
            _stat("Draws",  str(h["draw"]), "draw"),
            _stat("Losses", str(h["loss"]), "loss"),
            _stat("As White W/D/L", f"{h['as_white_w']}/{h['as_white_d']}/{h['as_white_l']}"),
            _stat("As Black W/D/L", f"{h['as_black_w']}/{h['as_black_d']}/{h['as_black_l']}"),
            _stat("Score", f"{h['win'] + .5 * h['draw']:g}/{h['total']}"),
        ]),
        html.Div(style={"overflow": "auto", "maxHeight": "180px"},
                 className="clickable-rows", children=[
            dash_table.DataTable(
                id="h2h-games-table",
                columns=[
                    {"name": "Date",        "id": "Date"},
                    {"name": "Color",       "id": "Color"},
                    {"name": "Result",      "id": "Outcome"},
                    {"name": "My Rtg",      "id": "MyRating"},
                    {"name": "Opp Rtg",     "id": "OppRating"},
                    {"name": "Moves",       "id": "FullMoves"},
                    {"name": "Termination", "id": "Termination"},
                    {"name": "Lichess",     "id": "Lichess",
                     "presentation": "markdown"},
                ],
                data=[
                    {**row, "Lichess": _lichess_link(row.get("ChapterURL", ""))}
                    for row in h["game_rows"]
                ],
                page_size=20, sort_action="native",
                markdown_options={"link_target": "_blank"},
                style_table={"overflowX": "auto"},
                style_cell={**TABLE_CELL, "fontSize": "11px", "padding": "5px 8px"},
                style_header=TABLE_HEADER,
                style_data_conditional=[
                    {"if": {"filter_query": '{Outcome} = "Win"'},
                     "backgroundColor": "rgba(63,185,80,.13)"},
                    {"if": {"filter_query": '{Outcome} = "Loss"'},
                     "backgroundColor": "rgba(248,81,73,.11)"},
                    {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]},
                ],
            ),
        ]),
    ])


@callback(
    Output("url", "href", allow_duplicate=True),
    Output("h2h-games-table", "active_cell"),
    Input("h2h-games-table", "active_cell"),
    State("h2h-games-table", "derived_viewport_data"),
    prevent_initial_call=True,
)
def navigate_to_game_from_h2h(active_cell, viewport_rows):
    """Clicking a Game in the head-to-head list opens its detail view."""
    return row_click_to_game(active_cell, viewport_rows), None


@callback(Output("rating-bucket-bar", "figure"), FILTER_INPUTS)
def update_bucket(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    buckets = opponent_rating_bucket_summary(df_f)
    if buckets.empty:
        return empty_fig("Need rated games")
    long = buckets.melt(
        id_vars=["Bucket"],
        value_vars=["Win", "Draw", "Loss"],
        var_name="Outcome", value_name="Count",
    )
    fig = px.bar(
        long, x="Bucket", y="Count", color="Outcome",
        barmode="stack", color_discrete_map=WDL_COLOR_MAP,
    )
    apply_dark_theme(fig, xaxis_title="Opponent rating difference",
                     yaxis_title="Games", legend_title="Outcome")
    return fig


@callback(Output("outcome-scatter", "figure"), FILTER_INPUTS)
def update_scatter(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    sc = outcome_vs_rating_data(df_f)
    if sc.empty:
        return empty_fig("Need rated games")
    fig = px.scatter(
        sc, x="OpponentRatingNum", y="OutcomeNum",
        color="Outcome", color_discrete_map=WDL_COLOR_MAP,
        hover_data={"Opponent": True, "Date": True,
                    "OutcomeNum": False, "OpponentRatingNum": True},
        labels={"OpponentRatingNum": "Opponent Rating", "OutcomeNum": "Outcome"},
    )
    fig.update_traces(
        marker=dict(size=8, opacity=0.8, line=dict(width=1, color=COLORS["border"])),
    )
    fig.update_yaxes(
        tickvals=[0, 0.5, 1],
        ticktext=["Loss", "Draw", "Win"],
        range=[-0.15, 1.15],
    )
    apply_dark_theme(fig, xaxis_title="Opponent rating", legend_title="Outcome")
    return fig
