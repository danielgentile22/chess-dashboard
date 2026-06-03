"""
pages/openings.py
=================
The Openings page — the repertoire tree plus ECO families and per-opening
results.

The repertoire tree (issue #16) is Daniel's personal opening explorer: every
Game as one color, arranged move by move.  Each branch shows how many Games
went that way and what they scored; branches that score below his overall
average for that color (across enough games to mean something) are flagged —
"your anti-Sicilian is leaking points".

Expansion uses native <details>/<summary> elements: no callbacks, works on a
phone, keyboard accessible.
"""
from __future__ import annotations

import dash
import plotly.express as px
from dash import Input, Output, callback, dash_table, dcc, html

from components import (
    TABLE_CELL,
    TABLE_HEADER,
    chart_card,
    content_card,
    empty_state,
    game_detail_path,
    page_header,
)
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import opening_summary, repertoire_tree
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

        # The repertoire tree (issue #16) — the personal opening explorer
        content_card(
            "Repertoire",
            dcc.RadioItems(
                id="repertoire-color",
                options=[
                    {"label": " ♔ As White", "value": "White"},
                    {"label": " ♚ As Black", "value": "Black"},
                ],
                value="White",
                inline=True,
                className="repertoire-color-toggle",
            ),
            html.Div(id="repertoire-tree", className="repertoire-tree"),
        ),

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
# Repertoire tree rendering (issue #16)
# ---------------------------------------------------------------------------

def _move_label(node: dict) -> str:
    """SAN with its move number: ply 1 → '1. d4', ply 2 → '1... Nf6'."""
    number = (node["ply"] + 1) // 2
    dots = "." if node["ply"] % 2 == 1 else "..."
    return f"{number}{dots} {node['san']}"


def _game_link(ref: dict, *, arrow: bool = True):
    """A compact link to one Game: 'Win vs Opponent A →'."""
    text = f"{ref['Outcome']} vs {ref['Opponent']}" + (" →" if arrow else "")
    path = game_detail_path(ref["ChapterURL"])
    cls = f"rep-game-link {ref['Outcome'].lower()}"
    if not path:
        return html.Span(text, className=cls)
    return dcc.Link(text, href=path, className=cls)


def _wdl_bar(node: dict) -> html.Span:
    """A proportional W/D/L mini-bar for a node row."""
    total = node["games"]
    return html.Span(className="rep-bar", children=[
        html.Span(className=f"rep-bar-seg {outcome.lower()}",
                  style={"width": f"{node[outcome.lower()] / total * 100}%"})
        for outcome in ("Win", "Draw", "Loss") if node[outcome.lower()]
    ])


def _tree_node(node: dict, baseline: float):
    """
    One move of the repertoire tree.

    Several Games → an expandable <details> branch; a single Game → a flat
    row linking straight to it (drilling further would just replay the game).
    """
    flagged = " rep-flagged" if node["underperforming"] else ""
    label = html.Span(_move_label(node), className="rep-node-move")

    if node["games"] == 1:
        return html.Div(className="rep-node rep-leaf" + flagged, children=[
            html.Div(className="rep-node-row", children=[
                label,
                _game_link(node["game_refs"][0]),
            ]),
        ])

    score_cls = "win" if node["score_pct"] >= baseline else "loss"
    summary_row = html.Summary(className="rep-node-row", children=[
        label,
        _wdl_bar(node),
        html.Span(str(node["games"]), className="rep-node-games",
                  title=f"{node['win']}W {node['draw']}D {node['loss']}L"),
        html.Span(f"{node['score_pct']:.0f}%",
                  className=f"rep-node-score {score_cls}"),
        html.Span("⚠ leaking points", className="rep-node-flag")
        if node["underperforming"] else None,
    ])

    # Games that stopped at this position get their links here; games that
    # kept going are reached by drilling into the child moves.
    ended = [
        html.Div(className="rep-node-row rep-ended-row", children=[
            html.Span("ended here", className="rep-ended-label"),
            _game_link(ref),
        ])
        for ref in node["ended_here"]
    ]

    return html.Details(className="rep-node" + flagged, children=[
        summary_row,
        html.Div(className="rep-node-children", children=[
            *[_tree_node(child, baseline) for child in node["moves"]],
            *ended,
        ]),
    ])


@callback(
    Output("repertoire-tree", "children"),
    Input("repertoire-color", "value"),
    FILTER_INPUTS,
)
def update_repertoire(color, colors, outcomes, terminations, start, end,
                      events, moves, _sync=None, lens=None):
    """The repertoire tree for one color, honoring the global filters."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    tree = repertoire_tree(df_f, color)

    if tree["games"] == 0:
        return empty_state(
            "♘" if color == "White" else "♞",
            f"No games as {color} in this filter",
            "The repertoire tree builds itself from your games' moves.",
        )

    return [
        # The baseline every branch is judged against
        html.Div(className="rep-baseline", children=[
            html.Span(f"{tree['games']} games", className="rep-baseline-games"),
            html.Span(" · "),
            html.Span(f"{tree['score_pct']}% overall score",
                      className="rep-baseline-score"),
            html.Span(f"  —  branches scoring below that across "
                      f"{tree['min_games']}+ games are flagged",
                      className="rep-baseline-hint"),
        ]),
        html.Div(className="rep-nodes", children=[
            _tree_node(node, tree["score_pct"]) for node in tree["moves"]
        ]),
    ]


# ---------------------------------------------------------------------------
# ECO family / top openings callbacks
# ---------------------------------------------------------------------------

@callback(Output("opening-family-bar", "figure"), FILTER_INPUTS)
def update_opening_family(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
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
def update_opening_table(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    _, opn = opening_summary(df_f)
    return opn.head(50).to_dict("records")
