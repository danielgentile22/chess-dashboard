"""
pages/games.py
==============
The Games page — every Game, filterable and sortable.

Placeholder until issue #9 migrates the games table (with Open-on-Lichess
links, Lesson indicators, and Tags) here.
"""
from __future__ import annotations

import dash
from dash import html

from components import empty_state, page_header

dash.register_page(
    __name__, path="/games", name="Games", title="Games — Chess Stats", order=5,
)


def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Games", "Every game in your archive"),
        empty_state(
            "♟",
            "On its way",
            "The full games table — with Open-on-Lichess links, Lesson",
            "indicators, and Tags — lands here next.",
        ),
    ])
