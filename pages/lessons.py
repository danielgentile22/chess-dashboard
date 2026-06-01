"""
pages/lessons.py
================
The Lessons page — every takeaway you've written, in one place.

Placeholder until issue #12 builds the Lessons list, Tag filtering, and the
tag summary strip.
"""
from __future__ import annotations

import dash
from dash import html

from components import empty_state, page_header

dash.register_page(
    __name__, path="/lessons", name="Lessons", title="Lessons — Chess Stats", order=6,
)


def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Lessons", "What your games have taught you"),
        empty_state(
            "♘",
            "On its way",
            "Every Lesson you write on Lichess — filterable by Tag, opponent,",
            "and date — lands here, together with the Tag taxonomy.",
        ),
    ])
