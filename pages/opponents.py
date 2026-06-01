"""
pages/opponents.py
==================
The Opponents page — who you play and how those games go.

Placeholder until issue #9 migrates the opponent charts, head-to-head
analyzer, and strength analysis here.
"""
from __future__ import annotations

import dash
from dash import html

from components import empty_state, page_header

dash.register_page(
    __name__, path="/opponents", name="Opponents", title="Opponents — Chess Stats", order=3,
)


def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Opponents", "Head-to-head records and strength analysis"),
        empty_state(
            "♚",
            "On its way",
            "Per-opponent records, the head-to-head analyzer, and",
            "outcome-by-rating-difference analysis land here next.",
        ),
    ])
