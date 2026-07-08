"""
pages/analysis.py
=================
The Analysis page (issues #58 [F2] + #61 [F3]) — the view onto Daniel's
engine-derived **error profile** and how it moves over time.

It leads with one number that matters: the split of his mistakes into
**tactical** (forcing shots missed or material dropped to a combination) and
**positional** (slow eval bleeds) across every analysed Game — "the single
biggest weakness at a glance".  Around it sit the trends [F3]: per-Game
**accuracy** over time with his rating, the **mistake-type trend** (do tactical
errors fall as positional ones grow with his level?), a **phase × type matrix**
(his worst specific combination), and a **move-number histogram** of where his
mistakes land (the time-trouble fingerprint).

Before any Game has had computer analysis requested on Lichess the page degrades
to a clear empty state, and it always names the Chapters still **awaiting
analysis** (the one click at the board); those Games carry no profile and are
excluded from every aggregate.

All the chess logic lives in the pure, deeply-tested ``engine_analysis_core`` /
``analysis_trends``; this page only assembles already-computed aggregates
(smoke-tested only — see ``../tests/test_ui_smoke.py``).
"""
from __future__ import annotations

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

import data
from components import content_card, empty_state, page_header
from styles import COLORS, apply_dark_theme, empty_fig

dash.register_page(
    __name__, path="/analysis", name="Analysis",
    title="Analysis — Chess Dashboard", order=7,
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
# The trends (issue #61 [F3]) — accuracy, type-over-time, phase×type, histogram
# ---------------------------------------------------------------------------

# The rating overlay is a neutral reference line (gold stays reserved for
# achievements, red/green for outcomes, blue for the interactive/tactical hue).
_RATING_LINE = dict(color=COLORS["text"], width=1, dash="dot")
_PHASE_LABEL = {"opening": "Opening", "middlegame": "Middlegame",
                "endgame": "Endgame"}


def _chart(graph_id: str, fig: go.Figure, *, height: str = "300px") -> dcc.Graph:
    return dcc.Graph(
        id=graph_id, figure=fig,
        style={"height": height, "width": "100%"},
        config={"displayModeBar": False, "responsive": True},
    )


def _add_rating_overlay(fig: go.Figure, trend: pd.DataFrame) -> None:
    """Overlay the player's rating on a right-hand axis, if any is known."""
    if "Rating" not in trend.columns or not trend["Rating"].notna().any():
        return
    fig.add_trace(go.Scatter(
        x=trend["Date_dt"], y=trend["Rating"], name="Rating",
        mode="lines", line=_RATING_LINE, yaxis="y2",
        hovertemplate="rating %{y:.0f}<extra></extra>",
    ))
    fig.update_layout(yaxis2=dict(
        overlaying="y", side="right", showgrid=False,
        title_text="Rating", color=COLORS["muted"],
    ))


def _accuracy_fig(trend: pd.DataFrame) -> go.Figure:
    """A per-Game accuracy line over time, with the rating overlaid."""
    if trend.empty:
        return empty_fig("No analysed games yet — accuracy appears here.")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=trend["Date_dt"], y=trend["Accuracy"], name="Accuracy",
        mode="lines+markers",
        line=dict(color=COLORS["primary"], width=2),
        marker=dict(size=7, color=COLORS["primary"]),
        customdata=trend[["Opponent", "Date"]],
        hovertemplate=("<b>%{y:.1f}%</b> accuracy · %{customdata[0]}"
                       "<br>%{customdata[1]}<extra></extra>"),
    ))
    apply_dark_theme(fig, yaxis_title="Accuracy %", show_legend=True)
    fig.update_yaxes(range=[0, 100])
    _add_rating_overlay(fig, trend)
    return fig


def _accuracy_card(trend: pd.DataFrame) -> html.Div:
    return content_card(
        "Accuracy over time",
        html.Div(
            "One quality number per analysed Game — how close your moves were "
            "to the engine's best, regardless of the result — with your rating "
            "for context.",
            className="analysis-explain",
        ),
        html.Div(_chart("analysis-accuracy-trend", _accuracy_fig(trend)),
                 className="chart-body"),
    )


def _type_trend_fig(trend: pd.DataFrame) -> go.Figure:
    """Tactical/positional counts per Game over time, rating overlaid."""
    if trend.empty:
        return empty_fig("No analysed games yet — the trend appears here.")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=trend["Date_dt"], y=trend["Tactical"], name="Tactical",
        marker_color=COLORS["primary"],
        hovertemplate="<b>%{y}</b> tactical<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=trend["Date_dt"], y=trend["Positional"], name="Positional",
        marker_color=COLORS["muted"],
        hovertemplate="<b>%{y}</b> positional<extra></extra>",
    ))
    apply_dark_theme(fig, yaxis_title="Mistakes", show_legend=True)
    fig.update_layout(barmode="stack")
    _add_rating_overlay(fig, trend)
    return fig


def _type_trend_card(trend: pd.DataFrame) -> html.Div:
    return content_card(
        "Mistake types over time",
        html.Div(
            "Your tactical and positional mistakes per analysed Game, with your "
            "rating — do the tactical errors fall as the positional ones grow "
            "with your level?",
            className="analysis-explain",
        ),
        html.Div(_chart("analysis-mistake-type-trend", _type_trend_fig(trend)),
                 className="chart-body"),
    )


def _phase_matrix_fig(matrix: pd.DataFrame) -> go.Figure:
    """A phase × type heatmap — the worst specific combination is the hottest."""
    if matrix.empty:
        return empty_fig("No mistakes to map yet — clean play.")
    phases = ["opening", "middlegame", "endgame"]
    full = matrix.reindex(index=phases, columns=["tactical", "positional"],
                          fill_value=0)
    z = full.to_numpy()
    fig = go.Figure(go.Heatmap(
        z=z,
        x=["Tactical", "Positional"],
        y=[_PHASE_LABEL[p] for p in phases],
        colorscale=[[0, COLORS["card2"]], [1, COLORS["primary"]]],
        text=z, texttemplate="%{text}",
        textfont=dict(color=COLORS["text"], size=13),
        showscale=False,
        hovertemplate="%{y} · %{x}: <b>%{z}</b><extra></extra>",
    ))
    apply_dark_theme(fig)
    return fig


def _phase_matrix_card(matrix: pd.DataFrame) -> html.Div:
    return content_card(
        "Where mistakes happen",
        html.Div(
            "Your mistakes by game phase and kind. The hottest cell is your "
            "worst specific combination to train.",
            className="analysis-explain",
        ),
        html.Div(_chart("analysis-phase-type-matrix", _phase_matrix_fig(matrix),
                        height="260px"),
                 className="chart-body"),
    )


def _histogram_fig(hist: pd.DataFrame) -> go.Figure:
    """A histogram of the move numbers your mistakes land on."""
    if hist.empty:
        return empty_fig("No mistakes to chart yet — clean play.")
    fig = go.Figure(go.Bar(
        x=hist["MoveNumber"], y=hist["Count"],
        marker_color=COLORS["primary"],
        hovertemplate="move %{x}: <b>%{y}</b><extra></extra>",
    ))
    apply_dark_theme(fig, xaxis_title="Move number", yaxis_title="Mistakes")
    return fig


def _histogram_card(hist: pd.DataFrame) -> html.Div:
    return content_card(
        "When mistakes happen",
        html.Div(
            "How many of your mistakes land on each move number. A spike late "
            "in the Game is the fingerprint of time-trouble; a flat spread rules "
            "it out.",
            className="analysis-explain",
        ),
        html.Div(_chart("analysis-move-histogram", _histogram_fig(hist)),
                 className="chart-body"),
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

    sections: list = [
        _distribution_card(data.get_mistake_type_distribution()),
        _accuracy_card(data.get_accuracy_trend()),
        _type_trend_card(data.get_mistake_type_trend()),
        _phase_matrix_card(data.get_phase_type_matrix()),
        _histogram_card(data.get_mistake_move_histogram()),
    ]
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
