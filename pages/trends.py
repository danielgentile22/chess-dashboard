"""
pages/trends.py
===============
The Trends page — rating, win rate, and activity over time.

Per the PRD layout decision, Game Length and Activity content folds in here
alongside the timeline charts.
"""
from __future__ import annotations

import dash
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from dash import Output, callback, html

from components import chart_card, content_card, page_header
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import (
    activity_data,
    game_length_data,
    player_rating_over_time,
    win_rate_over_time,
)
from styles import COLORS, WDL_COLOR_MAP, apply_dark_theme, empty_fig

dash.register_page(
    __name__, path="/trends", name="Trends", title="Trends — Chess Stats", order=1,
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Trends", "Rating, win rate, and activity over time"),

        html.Div(className="g2", children=[
            chart_card("Your rating over time", "rating-line"),
            chart_card("Cumulative win rate over time", "winrate-line"),
        ]),
        html.Div(className="g2", children=[
            chart_card("Games per month", "monthly-bar"),
            chart_card("Win rate by day of week", "dow-bar"),
        ]),
        html.Div(className="g2", children=[
            chart_card("Move count distribution by outcome", "length-hist"),
            content_card("Average game length", html.Div(id="length-stats")),
        ]),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("rating-line", "figure"), FILTER_INPUTS)
def update_rating(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    pr = player_rating_over_time(df_f)
    if pr.empty:
        return empty_fig("No rating data")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pr["Date_dt"], y=pr["PlayerRating"],
        mode="lines+markers",
        line=dict(color=COLORS["accent"], width=2),
        marker=dict(size=5, color=COLORS["accent"]),
        hovertemplate="%{x|%Y-%m-%d}: %{y}<extra></extra>",
        name="Rating",
    ))
    # Linear trend overlay
    if len(pr) >= 3:
        x_num = np.arange(len(pr))
        coeffs = np.polyfit(x_num, pr["PlayerRating"].values, 1)
        trend = np.polyval(coeffs, x_num)
        fig.add_trace(go.Scatter(
            x=pr["Date_dt"], y=trend,
            mode="lines",
            line=dict(color=COLORS["muted"], width=1, dash="dot"),
            hoverinfo="skip",
            name="Trend",
        ))
    apply_dark_theme(fig, yaxis_title="Rating", legend_title="")
    return fig


@callback(Output("winrate-line", "figure"), FILTER_INPUTS)
def update_winrate(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    wr = win_rate_over_time(df_f)
    if wr.empty:
        return empty_fig("No dated games")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=wr["Date_dt"], y=wr["WinRate"],
        mode="lines+markers",
        line=dict(color=COLORS["win"], width=2),
        marker=dict(size=5, color=COLORS["win"]),
        fill="tozeroy",
        fillcolor="rgba(63,185,80,.10)",
        hovertemplate="%{x|%Y-%m-%d}<br>Win rate: %{y:.1f}%<br>(%{customdata[0]}W / %{customdata[1]} games)<extra></extra>",
        customdata=wr[["CumWins", "CumGames"]].values,
        name="Win rate",
    ))
    fig.add_hline(
        y=50, line_dash="dash",
        line_color=COLORS["muted"], line_width=1,
        annotation_text="50%", annotation_position="right",
        annotation_font=dict(color=COLORS["muted"], size=10),
    )
    apply_dark_theme(fig, yaxis_title="Win % (cumulative)")
    fig.update_yaxes(range=[0, 100])
    return fig


@callback(Output("monthly-bar", "figure"), FILTER_INPUTS)
def update_monthly(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    m, _ = activity_data(df_f)
    if m.empty:
        return empty_fig("No dated games")
    fig = px.bar(
        m, x="YearMonth", y="Games",
        custom_data=["WinRate"],
        color_discrete_sequence=[COLORS["primary"]],
    )
    fig.update_traces(
        hovertemplate="%{x}<br>%{y} games<br>Win rate: %{customdata[0]:.1f}%<extra></extra>",
    )
    apply_dark_theme(fig, yaxis_title="Games")
    fig.update_xaxes(tickangle=45, automargin=True)
    return fig


@callback(Output("dow-bar", "figure"), FILTER_INPUTS)
def update_dow(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    _, dw = activity_data(df_f)
    if dw.empty:
        return empty_fig("No dated games")
    fig = px.bar(
        dw, x="DayOfWeek", y="WinRate",
        custom_data=["Games"],
        color="WinRate",
        color_continuous_scale=[[0, COLORS["loss"]], [0.5, COLORS["muted"]], [1, COLORS["win"]]],
        range_color=[30, 70],
    )
    fig.update_traces(
        hovertemplate="%{x}<br>Win rate: %{y:.1f}%<br>(%{customdata[0]} games)<extra></extra>",
    )
    apply_dark_theme(fig, yaxis_title="Win rate (%)")
    fig.update_coloraxes(showscale=False)
    fig.add_hline(y=50, line_dash="dash", line_color=COLORS["muted"], line_width=1)
    return fig


@callback(Output("length-hist", "figure"), FILTER_INPUTS)
def update_length_hist(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    hist_df, _ = game_length_data(df_f)
    if hist_df.empty:
        return empty_fig("No data")
    fig = px.histogram(
        hist_df, x="FullMoves", color="Outcome",
        barmode="overlay", nbins=30,
        color_discrete_map=WDL_COLOR_MAP,
        opacity=0.75,
    )
    apply_dark_theme(fig, xaxis_title="Moves", yaxis_title="Games", legend_title="Outcome")
    return fig


@callback(Output("length-stats", "children"), FILTER_INPUTS)
def update_length_stats(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    _, avgs = game_length_data(df_f)
    if not avgs:
        return html.Div("No data", style={"color": COLORS["dim"]})

    def _row(label, val, cls=""):
        if val is None:
            return None
        return html.Div(
            style={"display": "flex", "justifyContent": "space-between",
                   "padding": "10px 0", "borderBottom": f"1px solid {COLORS['border']}"},
            children=[
                html.Span(label, style={"color": COLORS["muted"], "fontSize": "13px"}),
                html.Span(f"{val} moves", className=cls,
                          style={"fontWeight": "700", "fontSize": "16px",
                                 "fontFamily": "'IBM Plex Mono', monospace"}),
            ],
        )

    rows = [
        _row("Avg moves (Wins)",   avgs.get("Win"),  "text-win"),
        _row("Avg moves (Draws)",  avgs.get("Draw"), "text-muted"),
        _row("Avg moves (Losses)", avgs.get("Loss"), "text-loss"),
    ]
    return html.Div([r for r in rows if r is not None])
