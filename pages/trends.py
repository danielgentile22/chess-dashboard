"""
pages/trends.py
===============
The Trends page — rating, win rate, and activity over time.

Per the PRD layout decision, Game Length and Activity content folds in here
alongside the timeline charts.  The activity heatmap calendar (issue #14)
leads the page: one GitHub-contribution-style calendar per year, cells
colored by that day's results.
"""
from __future__ import annotations

import dash
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Output, callback, dcc, html

from components import chart_card, content_card, empty_state, page_header
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import (
    activity_data,
    daily_activity,
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

        # Activity heatmap calendar (issue #14) — your chess year at a glance
        content_card("Activity", html.Div(id="activity-calendar")),

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
# Activity calendar (issue #14)
# ---------------------------------------------------------------------------

# Cell color scale: losing days → red, winning days → green, mixed → gray.
# Intensity follows |Net| (a 2-win day is brighter than a 1-win day).
_NET_CLAMP = 3
_CAL_COLORSCALE = [
    [0.0,  "#7d2a26"],            # net −3 or worse: deep red
    [0.33, "rgba(248,81,73,.55)"],  # losing day
    [0.5,  "#3a4048"],            # even day (games, no net result)
    [0.67, "rgba(63,185,80,.55)"],  # winning day
    [1.0,  "#39d353"],            # net +3 or better: bright green
]
_WEEKDAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", "Sun"]


def _year_calendar_fig(year_daily: pd.DataFrame, year: int) -> go.Figure:
    """One year of the calendar: 7 weekday rows × 53 week columns."""
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    # Week 0 starts on the Monday on/before Jan 1 (GitHub-style alignment)
    first_monday = days[0] - pd.Timedelta(days=days[0].weekday())
    n_weeks = (days[-1] - first_monday).days // 7 + 1

    by_day = year_daily.set_index("Date_dt")
    base_z: list[list] = [[None] * n_weeks for _ in range(7)]
    game_z: list[list] = [[None] * n_weeks for _ in range(7)]
    hover: list[list[str]] = [[""] * n_weeks for _ in range(7)]

    for day in days:
        week, weekday = (day - first_monday).days // 7, day.weekday()
        if day in by_day.index:
            row = by_day.loc[day]
            game_z[weekday][week] = max(-_NET_CLAMP, min(_NET_CLAMP, int(row["Net"])))
            plural = "s" if row["Games"] > 1 else ""
            hover[weekday][week] = (
                f"<b>{day:%b %-d, %Y}</b> — {row['Games']} game{plural}<br>{row['Detail']}"
            )
        else:
            base_z[weekday][week] = 0
            hover[weekday][week] = f"{day:%b %-d, %Y}<br>No games"

    # Month labels sit under the week containing the 1st of each month
    month_ticks = [(pd.Timestamp(year=year, month=m, day=1) - first_monday).days // 7
                   for m in range(1, 13)]
    month_labels = [f"{pd.Timestamp(year=year, month=m, day=1):%b}" for m in range(1, 13)]

    fig = go.Figure()
    common = dict(xgap=3, ygap=3, hoverinfo="text", showscale=False)
    # Days without Games: visibly empty cells
    fig.add_trace(go.Heatmap(
        z=base_z, text=hover,
        colorscale=[[0, COLORS["card2"]], [1, COLORS["card2"]]],
        **common,
    ))
    # Days with Games, colored by their results
    fig.add_trace(go.Heatmap(
        z=game_z, text=hover,
        zmin=-_NET_CLAMP, zmax=_NET_CLAMP,
        colorscale=_CAL_COLORSCALE,
        **common,
    ))

    apply_dark_theme(fig)
    fig.update_layout(
        height=160,
        margin=dict(l=8, r=8, t=8, b=8),
        xaxis=dict(
            tickvals=month_ticks, ticktext=month_labels,
            showgrid=False, zeroline=False, side="top",
            tickfont=dict(size=10, color=COLORS["muted"]),
        ),
        yaxis=dict(
            tickvals=list(range(7)), ticktext=_WEEKDAY_LABELS,
            showgrid=False, zeroline=False,
            autorange="reversed",  # Monday on top
            tickfont=dict(size=9, color=COLORS["dim"]),
        ),
    )
    return fig


@callback(Output("activity-calendar", "children"), FILTER_INPUTS)
def update_activity_calendar(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    """One calendar block per year with Games, newest year first."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    daily = daily_activity(df_f)
    if daily.empty:
        return empty_state("♙", "No dated games in this filter",
                           "The calendar lights up once your filtered games have dates.")

    blocks = []
    for year, year_daily in sorted(daily.groupby(daily["Date_dt"].dt.year),
                                   key=lambda pair: -pair[0]):
        wins, losses = int(year_daily["Win"].sum()), int(year_daily["Loss"].sum())
        games = int(year_daily["Games"].sum())
        blocks.append(html.Div(className="activity-year", children=[
            html.Div(className="activity-year-header", children=[
                html.Span(str(year), className="activity-year-label"),
                html.Span(f"{games} games · {wins}W {losses}L",
                          className="activity-year-stats"),
            ]),
            html.Div(className="activity-cal-scroll", children=[
                dcc.Graph(
                    figure=_year_calendar_fig(year_daily, year),
                    config={"displayModeBar": False},
                    className="activity-cal-graph",
                ),
            ]),
        ]))
    return blocks


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
