"""
pages/games.py
==============
The Games page — every Game in the archive, filterable and sortable, with
Open-on-Lichess links, Lesson indicators (💡), and Tags.

Clicking any row opens that Game's detail view (issue #11).
"""
from __future__ import annotations

import dash
from dash import Output, callback, dash_table, html

from components import (
    QUIET_TABLE_CELL,
    QUIET_TABLE_DATA_COND,
    QUIET_TABLE_HEADER,
    content_card,
    lichess_link,
    page_header,
    quiet_table,
    register_game_navigation,
    uscf_status_label,
)
from filters import FILTER_INPUTS, get_filtered

dash.register_page(
    __name__, path="/games", name="Games", title="Games — Chess Stats", order=5,
)

# Columns shown in the games table, in display order.  The table is built
# around the player — opponent, opponent rating, my rating, my color, outcome
# — not the raw PGN: the redundant White/Black name + rating pairs, the raw
# Result, and the synthetic Index are dropped (the player-centric columns
# already say who I played and whether I won).  Round is the numeric RoundNum
# so the browser's native sort puts round 10 after round 9, not after round 1.
_DISPLAY_COLS = [
    "Date", "Event", "RoundNum", "Opponent", "OpponentRating",
    "Color", "PlayerRating", "Outcome", "Termination",
    "FullMoves", "ECO", "Opening",
]
_COL_LABELS = {
    "RoundNum": "Round",
    "OpponentRating": "Opp Rating",
    "PlayerRating": "My Rating",
}
_NUMERIC_COLS = {"RoundNum", "FullMoves"}


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    cols = [
        {"name": _COL_LABELS.get(c, c), "id": c,
         **({"type": "numeric"} if c in _NUMERIC_COLS else {})}
        for c in _DISPLAY_COLS
    ]
    # Lesson indicator (💡) and Tags from chapter comments (ADR 0002)
    cols.append({"name": "💡", "id": "LessonIndicator"})
    cols.append({"name": "Tags", "id": "TagsDisplay"})
    # The Game's USCF status (issues #28/#29): ✓ matched by ID, ≈ matched by
    # name, "Forfeit" for no-shows, blank for no USCF Game Record
    cols.append({"name": "USCF", "id": "USCF"})
    # Open-on-Lichess link — rendered as markdown so it's clickable
    cols.append({"name": "Lichess", "id": "Lichess", "presentation": "markdown"})

    return html.Div(className="page", children=[
        page_header("Games", "Every game in your archive"),

        content_card(
            "All games (filtered) — click a row to open the game",
            quiet_table(
                dash_table.DataTable(
                    id="games-table",
                    columns=cols, data=[],
                    page_size=25, sort_action="native",
                    filter_action="native",
                    markdown_options={"link_target": "_blank"},
                    style_table={"overflowX": "auto"},
                    style_cell=QUIET_TABLE_CELL,
                    style_header=QUIET_TABLE_HEADER,
                    style_data_conditional=QUIET_TABLE_DATA_COND,
                ),
                clickable=True,
            ),
        ),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("games-table", "data"), FILTER_INPUTS)
def update_games_table(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    cols = [c for c in _DISPLAY_COLS if c in df_f.columns]
    out = df_f[cols].copy()
    if "Lessons" in df_f.columns:
        out["LessonIndicator"] = df_f["Lessons"].map(lambda les: "💡" if les else "")
    if "Tags" in df_f.columns:
        out["TagsDisplay"] = df_f["Tags"].map(
            lambda tags: " ".join(f"#{t}" for t in tags)
        )
    if "UscfMatched" in df_f.columns:
        # The Game's USCF status (issues #28/#29/#30)
        out["USCF"] = [
            uscf_status_label(matched_by, forfeit, conflict)
            for matched_by, forfeit, conflict in zip(
                df_f["UscfMatchedBy"], df_f["Forfeit"], df_f["UscfColorConflict"]
            )
        ]
    if "ChapterURL" in df_f.columns:
        out["Lichess"] = df_f["ChapterURL"].map(lichess_link)
        # Not a displayed column — carried in the row data so clicking the row
        # knows which Game to open (issue #11)
        out["ChapterURL"] = df_f["ChapterURL"]
    return out.to_dict("records")


navigate_to_game = register_game_navigation(
    "games-table", "Clicking a Game row opens its detail view.")
