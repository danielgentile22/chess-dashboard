"""
pages/games.py
==============
The Games page — every Game in the archive, filterable and sortable, with
Open-on-Lichess links, Lesson indicators (💡), and Tags.

Clicking any row opens that Game's detail view (issue #11).
"""
from __future__ import annotations

import dash
from dash import Input, Output, State, callback, dash_table, html

from components import (
    TABLE_CELL,
    TABLE_DATA_COND,
    TABLE_HEADER,
    content_card,
    page_header,
    row_click_to_game,
)
from filters import FILTER_INPUTS, get_filtered

dash.register_page(
    __name__, path="/games", name="Games", title="Games — Chess Stats", order=5,
)

# Columns shown in the games table, in display order
_DISPLAY_COLS = [
    "Index", "Date", "Event", "Round", "White", "WhiteRating",
    "Black", "BlackRating", "Result", "Outcome", "Color",
    "PlayerRating", "OpponentRating", "Termination",
    "FullMoves", "ECO", "Opening",
]


def _lichess_link(chapter_url: str) -> str:
    """Markdown 'Open on Lichess' link for a Game's ChapterURL ('' if none)."""
    return f"[Open ↗]({chapter_url})" if chapter_url else ""


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    cols = [{"name": c, "id": c} for c in _DISPLAY_COLS]
    # Lesson indicator (💡) and Tags from chapter comments (ADR 0002)
    cols.append({"name": "💡", "id": "LessonIndicator"})
    cols.append({"name": "Tags", "id": "TagsDisplay"})
    # Open-on-Lichess link — rendered as markdown so it's clickable
    cols.append({"name": "Lichess", "id": "Lichess", "presentation": "markdown"})

    return html.Div(className="page", children=[
        page_header("Games", "Every game in your archive"),

        content_card(
            "All games (filtered) — click a row to open the game",
            html.Div(style={"flex": "1", "overflow": "auto"}, className="clickable-rows", children=[
                dash_table.DataTable(
                    id="games-table",
                    columns=cols, data=[],
                    page_size=25, sort_action="native",
                    filter_action="native",
                    markdown_options={"link_target": "_blank"},
                    style_table={"overflowX": "auto"},
                    style_cell=TABLE_CELL,
                    style_header=TABLE_HEADER,
                    style_data_conditional=TABLE_DATA_COND,
                ),
            ]),
        ),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("games-table", "data"), FILTER_INPUTS)
def update_games_table(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    cols = [c for c in _DISPLAY_COLS if c in df_f.columns]
    out = df_f[cols].copy()
    if "Lessons" in df_f.columns:
        out["LessonIndicator"] = df_f["Lessons"].map(lambda les: "💡" if les else "")
    if "Tags" in df_f.columns:
        out["TagsDisplay"] = df_f["Tags"].map(
            lambda tags: " ".join(f"#{t}" for t in tags)
        )
    if "ChapterURL" in df_f.columns:
        out["Lichess"] = df_f["ChapterURL"].map(_lichess_link)
        # Not a displayed column — carried in the row data so clicking the row
        # knows which Game to open (issue #11)
        out["ChapterURL"] = df_f["ChapterURL"]
    return out.to_dict("records")


@callback(
    Output("url", "href", allow_duplicate=True),
    Output("games-table", "active_cell"),
    Input("games-table", "active_cell"),
    State("games-table", "derived_viewport_data"),
    prevent_initial_call=True,
)
def navigate_to_game(active_cell, viewport_rows):
    """Clicking a Game row opens its detail view."""
    href = row_click_to_game(active_cell, viewport_rows)
    # Reset the selection so clicking the same row again still navigates
    return href, None
