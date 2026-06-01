"""
pages/trends.py
===============
The Trends page — rating, win rate, and activity over time.

Placeholder until issue #9 migrates the timeline, activity, and game-length
charts here.
"""
from __future__ import annotations

import dash
from dash import html

from components import empty_state, page_header

dash.register_page(
    __name__, path="/trends", name="Trends", title="Trends — Chess Stats", order=1,
)


def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Trends", "Rating, win rate, and activity over time"),
        empty_state(
            "♜",
            "On its way",
            "Rating over time, cumulative win rate, games per month,",
            "day-of-week performance, and game-length distribution land here next.",
        ),
    ])
