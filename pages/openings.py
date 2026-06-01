"""
pages/openings.py
=================
The Openings page — ECO families and per-opening results.

Placeholder until issue #9 migrates the opening charts and table here.
"""
from __future__ import annotations

import dash
from dash import html

from components import empty_state, page_header

dash.register_page(
    __name__, path="/openings", name="Openings", title="Openings — Chess Stats", order=2,
)


def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Openings", "Where your repertoire wins and leaks points"),
        empty_state(
            "♗",
            "On its way",
            "Win rate by ECO family and the full openings table land here next.",
        ),
    ])
