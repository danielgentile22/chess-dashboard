"""
pages/events.py
===============
The Events page — tournament-by-tournament performance.

Placeholder until issue #9 migrates the event chart, table, and detail
panel here.
"""
from __future__ import annotations

import dash
from dash import html

from components import empty_state, page_header

dash.register_page(
    __name__, path="/events", name="Events", title="Events — Chess Stats", order=4,
)


def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Events", "Tournament performance, event by event"),
        empty_state(
            "♛",
            "On its way",
            "Per-event results, scores, and performance ratings land here next.",
        ),
    ])
