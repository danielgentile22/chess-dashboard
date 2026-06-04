"""
pages/analysis.py
=================
The Analysis page (issue #58 [F2]) — the first view onto Daniel's engine-derived
**error profile**.

It leads with one number that matters: the split of his mistakes into
**tactical** (forcing shots missed or material dropped to a combination) and
**positional** (slow eval bleeds) across every analysed Game — "the single
biggest weakness at a glance".  Before any Game has had computer analysis
requested on Lichess the page degrades to a clear empty state, and it always
names the Chapters still **awaiting analysis** (the one click at the board);
those Games carry no profile and are excluded from the distribution math.

All the chess logic lives in the pure, deeply-tested ``engine_analysis_core``;
this page only assembles already-computed aggregates (smoke-tested only — see
``../tests/test_ui_smoke.py``).
"""
from __future__ import annotations

import dash
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

import data
from components import content_card, empty_state, page_header
from styles import COLORS, apply_dark_theme, empty_fig

dash.register_page(
    __name__, path="/analysis", name="Analysis",
    title="Analysis — Chess Stats", order=7,
)

# Tactical reads as the sharp, interactive blue; positional as a calm grey — two
# neutral hues (gold stays reserved for achievements, red/green for outcomes).
_TYPE_COLOR = {"tactical": COLORS["primary"], "positional": COLORS["muted"]}
_TYPE_LABEL = {"tactical": "Tactical", "positional": "Positional"}


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Analysis", "Where your mistakes come from"),
        html.Div(id="analysis-content"),
    ])


# ---------------------------------------------------------------------------
# The mistake-type distribution
# ---------------------------------------------------------------------------

def _distribution_fig(distribution: dict[str, int]) -> go.Figure:
    """A donut of tactical vs positional mistakes, or a calm note when there are
    none to show yet."""
    kinds = [k for k in ("tactical", "positional") if distribution.get(k, 0) > 0]
    if not kinds:
        return empty_fig("No mistakes in your analysed games yet — clean play.")

    values = [distribution[k] for k in kinds]
    fig = go.Figure(go.Pie(
        labels=[_TYPE_LABEL[k] for k in kinds],
        values=values,
        hole=0.54,
        marker=dict(
            colors=[_TYPE_COLOR[k] for k in kinds],
            line=dict(color=COLORS["card"], width=2),
        ),
        textinfo="percent+label",
        textfont=dict(size=12, color=COLORS["text"]),
        hovertemplate="<b>%{value}</b> %{label} · %{percent}<extra></extra>",
    ))
    total = sum(values)
    fig.add_annotation(
        text=f"<b>{total}</b><br><span style='font-size:11px'>mistakes</span>",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=16, color=COLORS["text"]),
    )
    apply_dark_theme(fig, show_legend=False)
    return fig


def _distribution_card(distribution: dict[str, int]) -> html.Div:
    return content_card(
        "Mistake types",
        html.Div(
            "Your non-best moves across every analysed Game, split by kind — "
            "tactical (a forcing shot missed or material dropped to a "
            "combination) versus positional (a slow eval bleed).",
            className="analysis-explain",
        ),
        html.Div(className="chart-body", children=[
            dcc.Graph(
                id="analysis-type-distribution",
                figure=_distribution_fig(distribution),
                style={"height": "320px", "width": "100%"},
                config={"displayModeBar": False, "responsive": True},
            ),
        ]),
    )


# ---------------------------------------------------------------------------
# The awaiting-analysis list
# ---------------------------------------------------------------------------

def _awaiting_row(row) -> html.Div:
    name = str(row.get("ChapterName") or row.get("Opponent") or "Game")
    url = str(row.get("ChapterURL") or "")
    date = str(row.get("Date") or "")
    children: list = [html.Span(name, className="analysis-awaiting-name")]
    if date:
        children.append(html.Span(date, className="analysis-awaiting-date"))
    if url:
        children.append(html.A(
            [html.I(className="bi bi-box-arrow-up-right"), " Request on Lichess"],
            href=url, target="_blank", className="analysis-awaiting-link",
        ))
    return html.Div(children, className="analysis-awaiting-row")


def _awaiting_card(awaiting) -> html.Div:
    return content_card(
        f"Awaiting analysis ({len(awaiting)})",
        html.Div(
            "These Games have no computer analysis yet, so they don't count "
            "toward the split above. Request analysis on Lichess (one click at "
            "the board) and the next Sync reads it in.",
            className="analysis-awaiting-note",
        ),
        html.Div([_awaiting_row(r) for _, r in awaiting.iterrows()],
                 className="analysis-awaiting-list"),
    )


# ---------------------------------------------------------------------------
# The page body
# ---------------------------------------------------------------------------

def _render_analysis() -> html.Div:
    awaiting = data.get_awaiting_analysis()

    if not data.has_any_analysis():
        children: list = [empty_state(
            "♟",
            "No games analyzed yet",
            "Request computer analysis on a Chapter on Lichess — one click at "
            "the board — and the next Sync reads it in.",
            "Your mistake profile, tactical versus positional, appears here once "
            "a Game is analysed.",
        )]
        if not awaiting.empty:
            children.append(_awaiting_card(awaiting))
        return html.Div(children, className="card-stack")

    sections: list = [_distribution_card(data.get_mistake_type_distribution())]
    if not awaiting.empty:
        sections.append(_awaiting_card(awaiting))
    return html.Div(sections, className="card-stack")


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------

@callback(Output("analysis-content", "children"), Input("sync-store", "data"))
def update_analysis(_sync):
    """The page follows Syncs — every Sync re-reads the analysis the export carried."""
    return _render_analysis()
