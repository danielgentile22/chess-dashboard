"""
pages/opponents.py
==================
The Opponents page — who you play, how those games go, and the Scouting
Report (issue #13): the pre-game dossier on one opponent.

Pick an opponent and the dossier shows the head-to-head score and rating
gap, every game with dates, the openings they've played against you (split
by your color), how those games ended, and every Lesson you wrote after
facing them.  Optimized for the at-the-club phone use case: opponent
search → full dossier in two taps.

Per the PRD layout decision, Strength Analysis (rating buckets + outcome
scatter) also lives here.
"""
from __future__ import annotations

import dash
import plotly.express as px
from dash import Input, Output, State, callback, dash_table, dcc, html

import data
from components import (
    TABLE_CELL,
    TABLE_DATA_COND,
    TABLE_HEADER,
    chart_card,
    content_card,
    lesson_card,
    lichess_link,
    page_header,
    row_click_to_game,
)
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import (
    opponent_rating_bucket_summary,
    opponent_summary,
    outcome_vs_rating_data,
    scouting_report,
)
from styles import COLORS, WDL_COLOR_MAP, apply_dark_theme, empty_fig

dash.register_page(
    __name__, path="/opponents", name="Opponents", title="Opponents — Chess Stats", order=3,
)


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
        page_header("Opponents", "Scouting reports, records, and strength analysis"),

        # Scouting Report (issue #13): opponent search → full dossier
        content_card(
            "Scouting Report",
            dcc.Dropdown(
                id="scout-opponent",
                options=_opponent_options(),
                placeholder="Search an opponent…",
                clearable=True,
                className="scout-picker",
            ),
            html.Div(id="scouting-report"),
        ),

        # Who you play and how it goes
        chart_card("Opponent W/D/L (top 25, played >1 game)", "opponent-bar", height=420),
        html.Div(className="g2", children=[
            chart_card("W/D/L by opponent rating difference", "rating-bucket-bar"),
            chart_card("Outcome vs opponent rating", "outcome-scatter"),
        ]),
    ])


# ---------------------------------------------------------------------------
# Scouting Report rendering
# ---------------------------------------------------------------------------

def _stat(label: str, value: str, cls: str = "") -> html.Div:
    return html.Div(className="h2h-stat", children=[
        html.Div(label, className="h2h-stat-label"),
        html.Div(value, className=f"h2h-stat-val {cls}"),
    ])


def _openings_panel(title: str, openings: list[dict]) -> html.Div:
    if openings:
        rows = [
            html.Div(className="scout-opening-row", children=[
                html.Span(o["Opening"] or o["ECO"], className="scout-opening-name"),
                html.Span(o["ECO"], className="scout-opening-eco"),
                html.Span(f"{o['Win']}W {o['Draw']}D {o['Loss']}L",
                          className="scout-opening-record"),
            ])
            for o in openings
        ]
    else:
        rows = [html.Div("No games with this color yet",
                         className="scout-empty-line")]
    return html.Div(className="scout-openings-panel", children=[
        html.Div(title, className="scout-section-title"), *rows,
    ])


def _render_dossier(report: dict) -> html.Div:
    """The full Scouting Report for one opponent."""
    gap = report["rating_gap"]
    if gap is None:
        gap_str, gap_cls = "—", ""
    else:
        # Positive gap = they're rated above you = the harder game
        gap_str = f"+{gap}" if gap > 0 else str(gap)
        gap_cls = "loss" if gap > 0 else "win"

    timeline_table = dash_table.DataTable(
        id="scout-games-table",
        columns=[
            {"name": "Date",        "id": "Date"},
            {"name": "Color",       "id": "Color"},
            {"name": "Result",      "id": "Outcome"},
            {"name": "My Rtg",      "id": "MyRating"},
            {"name": "Opp Rtg",     "id": "OppRating"},
            {"name": "Event",       "id": "Event"},
            {"name": "Termination", "id": "Termination"},
            {"name": "Moves",       "id": "FullMoves"},
            {"name": "Lichess",     "id": "Lichess", "presentation": "markdown"},
        ],
        data=[
            {**row, "Lichess": lichess_link(row.get("ChapterURL", ""))}
            for row in report["timeline"]
        ],
        page_size=20, sort_action="native",
        markdown_options={"link_target": "_blank"},
        style_table={"overflowX": "auto"},
        style_cell={**TABLE_CELL, "fontSize": "11px", "padding": "5px 8px"},
        style_header=TABLE_HEADER,
        style_data_conditional=TABLE_DATA_COND,
    )

    lessons = [lesson_card(lesson, show_opponent=False) for lesson in report["lessons"]]

    return html.Div(className="scout-dossier", children=[
        # The headline numbers
        html.Div(className="h2h-stat-grid scout-stat-grid", children=[
            _stat("Score", report["score"]),
            _stat("Wins",   str(report["win"]),  "win"),
            _stat("Draws",  str(report["draw"]), "draw"),
            _stat("Losses", str(report["loss"]), "loss"),
            _stat("Their rating", str(report["their_rating"] or "—")),
            _stat("Rating gap", gap_str, gap_cls),
        ]),

        # Every game, click a row to open it
        html.Div(className="scout-section", children=[
            html.Div("Your games — click one to open it", className="scout-section-title"),
            html.Div(className="clickable-rows", children=[timeline_table]),
        ]),

        # The openings they bring, split by your color
        html.Div(className="g2 scout-openings", children=[
            _openings_panel("When you have White", report["openings_as_white"]),
            _openings_panel("When you have Black", report["openings_as_black"]),
        ]),

        # How the games ended
        html.Div(className="scout-section", children=[
            html.Div("How your games ended", className="scout-section-title"),
            html.Div(className="scout-terminations", children=[
                html.Span([t["Termination"], html.Span(f"×{t['Games']}", className="scout-term-count")],
                          className="scout-term-chip")
                for t in report["terminations"]
            ]),
        ]),

        # The differentiator: what facing them taught you
        html.Div(className="scout-section", children=[
            html.Div(f"What facing {report['opponent']} taught you",
                     className="scout-section-title"),
            *(lessons or [html.Div(
                "No Lessons written from these games yet.",
                className="scout-empty-line",
            )]),
        ]),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output("scout-opponent", "options"),
    Input("sync-store", "data"),
    prevent_initial_call=True,  # the layout holds correct values at page load
)
def update_scout_options(_sync):
    """Keep the opponent picker in step with the data after a Sync."""
    return _opponent_options()


@callback(
    Output("scouting-report", "children"),
    Input("scout-opponent", "value"),
    FILTER_INPUTS,
)
def update_scouting_report(opponent, colors, outcomes, terminations, start, end,
                           events, moves, _sync=None):
    """Opponent picked → their full dossier. Nothing picked → a hint."""
    if not opponent:
        return html.Div(
            "Pick an opponent to see your score, the openings they play "
            "against you, and every lesson facing them taught you.",
            className="scout-hint",
        )
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    report = scouting_report(df_f, opponent)
    if report["total"] == 0:
        return html.Div(f"No games vs {opponent} in the current filter.",
                        className="scout-hint")
    return _render_dossier(report)


@callback(
    Output("url", "href", allow_duplicate=True),
    Output("scout-games-table", "active_cell"),
    Input("scout-games-table", "active_cell"),
    State("scout-games-table", "derived_viewport_data"),
    prevent_initial_call=True,
)
def navigate_to_game_from_scout(active_cell, viewport_rows):
    """Clicking a Game in the Scouting Report timeline opens its detail view."""
    return row_click_to_game(active_cell, viewport_rows), None


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
