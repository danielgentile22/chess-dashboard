"""
pages/trends.py
===============
The Trends page — rating, win rate, and activity over time.

Per the PRD layout decision, Game Length and Activity content folds in here
alongside the timeline charts.  The activity heatmap calendar (issue #14)
leads the page: one GitHub-contribution-style calendar per year, cells
colored by that day's results.

Issue #17 adds the conditions analytics: score by time control, score by
round number (the fatigue check), and the upset tracker — giant kills and
upset losses ranked by rating margin, each clickable into its Game.
"""
from __future__ import annotations

import dash
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Output, callback, dash_table, dcc, html

import data
from components import (
    TABLE_CELL,
    TABLE_HEADER,
    chart_card,
    content_card,
    empty_state,
    page_header,
    rating_basis_note,
    register_game_navigation,
)
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import (
    activity_data,
    daily_activity,
    game_length_data,
    player_rating_over_time,
    round_performance,
    time_control_summary,
    upset_tracker,
    win_rate_over_time,
)
from styles import COLORS, WDL_COLOR_MAP, apply_dark_theme, empty_fig
from uscf_core import LIVE_LENS, OFFICIAL_LENS, rating_trend_series

dash.register_page(
    __name__, path="/trends", name="Trends", title="Trends — Chess Stats", order=1,
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

# Columns shown in both upset tables (Margin is pre-formatted with its sign)
_UPSET_TABLE_COLS = [
    {"name": "Date",     "id": "Date"},
    {"name": "Opponent", "id": "Opponent"},
    {"name": "Them",     "id": "OpponentRating"},
    {"name": "Me",       "id": "PlayerRating"},
    {"name": "Margin",   "id": "Margin"},
    {"name": "Event",    "id": "Event"},
]


def _upset_table(table_id: str) -> dash_table.DataTable:
    """One of the two upset tables — rows click through to the Game (issue #11)."""
    return dash_table.DataTable(
        id=table_id,
        columns=_UPSET_TABLE_COLS,
        data=[],
        page_size=8,
        style_table={"overflowX": "auto"},
        style_cell={**TABLE_CELL, "fontSize": "11px", "padding": "5px 8px"},
        style_header=TABLE_HEADER,
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]}
        ],
    )


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

        # Playing conditions (issue #17): time control + round fatigue
        html.Div(className="g2", children=[
            chart_card("Results by time control", "tc-bar"),
            chart_card("Score by round — the fatigue check", "round-bar"),
        ]),

        # Upset tracker (issue #17): giant kills and upset losses
        html.Div(className="g2", children=[
            content_card(
                "Giant kills — wins over higher-rated opponents",
                html.Div(id="upset-wins-status"),
                html.Div(className="clickable-rows", children=[
                    _upset_table("upset-wins-table"),
                ]),
            ),
            content_card(
                "Upset losses — losses to lower-rated opponents",
                html.Div(id="upset-losses-status"),
                html.Div(className="clickable-rows", children=[
                    _upset_table("upset-losses-table"),
                ]),
            ),
        ]),
        # Upset margins are rating-diff — say what basis they mix (issue #32)
        rating_basis_note(),
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
        # day.day instead of strftime %-d: the no-leading-zero directive is
        # platform-specific (glibc/BSD only)
        day_label = f"{day:%b} {day.day}, {day.year}"
        if day in by_day.index:
            row = by_day.loc[day]
            game_z[weekday][week] = max(-_NET_CLAMP, min(_NET_CLAMP, int(row["Net"])))
            plural = "s" if row["Games"] > 1 else ""
            hover[weekday][week] = (
                f"<b>{day_label}</b> — {row['Games']} game{plural}<br>{row['Detail']}"
            )
        else:
            base_z[weekday][week] = 0
            hover[weekday][week] = f"{day_label}<br>No games"

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
def update_activity_calendar(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    """One calendar block per year with Games, newest year first."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
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

# The two rating series, distinguishable at a glance in dark mode (issue #31):
# the Official Rating is the solid, neutral published number; the Live Rating
# wears the same blue as the profile card's Live value (.uscf-live-value).
_OFFICIAL_COLOR = COLORS["text"]
_LIVE_COLOR = COLORS["primary"]


def _typed_rating_fig(pr: pd.DataFrame) -> go.Figure:
    """The pre-USCF rating chart: typed header values plus a linear trend.

    Kept as the fallback when USCF was never reached (ADR 0003) — the chart
    degrades to what the Studies alone can say."""
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


def _dual_line_rating_fig(official, live, lens: str) -> go.Figure:
    """
    The dual-line rating trend (issue #31): the Official step line and the
    Live per-Rated-Event line, both always drawn.  The active lens is full
    strength; the other stays readable but recedes.
    """
    official_active = lens != LIVE_LENS

    fig = go.Figure()
    # The Official Rating: a step function changing only at supplement dates
    fig.add_trace(go.Scatter(
        x=[p.month for p in official],
        y=[p.rating for p in official],
        name="Official",
        mode="lines+markers",
        line=dict(color=_OFFICIAL_COLOR, shape="hv",
                  width=2.5 if official_active else 1.5),
        marker=dict(size=7 if official_active else 5, symbol="square"),
        opacity=1.0 if official_active else 0.4,
        hovertemplate="<b>%{x|%B %Y} supplement</b><br>"
                      "Official Rating: %{y}<extra></extra>",
    ))
    # The Live Rating: one point per Rated Event.  The chain is plotted at
    # full precision but every number the hover shows is whole — ratings
    # display without decimal places.  Names render verbatim — including
    # USCF's own typos.
    fig.add_trace(go.Scatter(
        x=[p.end_date for p in live],
        y=[p.post for p in live],
        name="Live",
        mode="lines+markers",
        line=dict(color=_LIVE_COLOR, width=2.5 if not official_active else 1.5),
        marker=dict(size=7 if not official_active else 5),
        opacity=1.0 if not official_active else 0.4,
        customdata=[
            [p.event_name, p.section_name,
             "unrated" if p.pre is None else f"{p.pre:.0f}"]
            for p in live
        ],
        hovertemplate="<b>%{customdata[0]}</b> · %{customdata[1]}<br>"
                      "Live Rating: %{customdata[2]} → %{y:.0f}<br>"
                      "%{x|%Y-%m-%d}<extra></extra>",
    ))
    apply_dark_theme(fig, yaxis_title="Rating", legend_title="")
    return fig


@callback(Output("rating-line", "figure"), FILTER_INPUTS)
def update_rating(colors, outcomes, terminations, start, end, events, moves,
                  _sync=None, lens=None):
    """The rating trend: dual-line (Official + Live) when USCF data exists,
    typed header values otherwise (ADR 0003 — enrichment, never a dependency)."""
    full_official = data.get_official_series()
    full_live = data.get_live_series()

    if not full_official and not full_live:
        # USCF never reached or not configured → the Studies' own numbers
        df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
        pr = player_rating_over_time(df_f)
        if pr.empty:
            return empty_fig("No rating data")
        return _typed_rating_fig(pr)

    official, live = rating_trend_series(
        full_official, full_live, date_start=start, date_end=end,
    )
    if not official and not live:
        return empty_fig("No rating data in this date range")
    return _dual_line_rating_fig(official, live, lens or OFFICIAL_LENS)


@callback(Output("winrate-line", "figure"), FILTER_INPUTS)
def update_winrate(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
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
def update_monthly(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
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
def update_dow(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
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
def update_length_hist(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
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


# ---------------------------------------------------------------------------
# Time control, fatigue, and upsets (issue #17)
# ---------------------------------------------------------------------------

# Label for Games whose PGN has no TimeControl header
_NO_TC_LABEL = "(not recorded)"


@callback(Output("tc-bar", "figure"), FILTER_INPUTS)
def update_time_control(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    """Stacked W/D/L per time control, slowest first, speed class in the hover."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    tc = time_control_summary(df_f)
    if tc.empty:
        return empty_fig("No finished games in this filter")

    tc = tc.copy()
    # Games without a TimeControl header still need an axis label; their
    # Speed stays the "Unknown" the summary already gave them
    tc["TimeControl"] = tc["TimeControl"].replace("", _NO_TC_LABEL)

    fig = go.Figure()
    for outcome in ("Win", "Draw", "Loss"):
        fig.add_trace(go.Bar(
            y=tc["TimeControl"], x=tc[outcome],
            name=outcome, orientation="h",
            marker_color=WDL_COLOR_MAP[outcome],
            customdata=tc[["Speed", "Games", "WinRate"]].values,
            hovertemplate=(
                "<b>%{y}</b> · %{customdata[0]}<br>"
                + outcome + ": %{x} of %{customdata[1]} games"
                + "<br>Win rate: %{customdata[2]:.0f}%<extra></extra>"
            ),
        ))
    fig.update_layout(barmode="stack")
    apply_dark_theme(fig, xaxis_title="Games", legend_title="Outcome")
    # Slowest control on top (the summary is already sorted slowest-first)
    fig.update_yaxes(autorange="reversed")
    return fig


def _round_fig(rounds: pd.DataFrame) -> go.Figure:
    """
    Score% per round: solid bars where there's enough data to mean something,
    dimmed bars (with an honest hover) where there isn't.
    """
    reliable = rounds[rounds["Reliable"]]
    thin = rounds[~rounds["Reliable"]]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=reliable["Round"], y=reliable["ScorePct"],
        marker=dict(
            color=reliable["ScorePct"],
            colorscale=[[0, COLORS["loss"]], [0.5, COLORS["muted"]], [1, COLORS["win"]]],
            cmin=0, cmax=100,
        ),
        customdata=reliable[["Games", "Win", "Draw", "Loss"]].values,
        hovertemplate=(
            "<b>Round %{x}</b><br>Score: %{y:.0f}%<br>"
            "%{customdata[1]}W %{customdata[2]}D %{customdata[3]}L"
            " (%{customdata[0]} games)<extra></extra>"
        ),
        name="",
    ))
    fig.add_trace(go.Bar(
        x=thin["Round"], y=thin["ScorePct"],
        marker_color=COLORS["border"],
        customdata=thin[["Games", "Win", "Draw", "Loss"]].values,
        hovertemplate=(
            "<b>Round %{x}</b><br>Score: %{y:.0f}%<br>"
            "Only %{customdata[0]} game(s) — too few to conclude<extra></extra>"
        ),
        name="",
    ))
    fig.add_hline(
        y=50, line_dash="dash",
        line_color=COLORS["muted"], line_width=1,
        annotation_text="50%", annotation_position="right",
        annotation_font=dict(color=COLORS["muted"], size=10),
    )
    apply_dark_theme(fig, xaxis_title="Round", yaxis_title="Score %", show_legend=False)
    # overlay, not the default group: the two traces never share a round, so
    # grouping would shrink every bar and shift it off its integer tick
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(dtick=1)
    fig.update_yaxes(range=[0, 105])
    return fig


@callback(Output("round-bar", "figure"), FILTER_INPUTS)
def update_round_performance(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    """Score by round number — late-round fatigue shows up as a downhill slope."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    rounds = round_performance(df_f)
    if rounds.empty:
        return empty_fig("No games with round numbers in this filter")
    return _round_fig(rounds)


def _upset_rows(upsets: list[dict], sign: str) -> list[dict]:
    """Upset tracker rows → table rows with a signed, readable margin."""
    return [
        {**row, "Margin": f"{sign}{row['Margin']}"}
        for row in upsets
    ]


@callback(
    Output("upset-wins-table", "data"),
    Output("upset-wins-status", "children"),
    Output("upset-losses-table", "data"),
    Output("upset-losses-status", "children"),
    FILTER_INPUTS,
)
def update_upsets(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    """Both upset tables + the lines that explain them when they're empty."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    upsets = upset_tracker(df_f)

    wins_status = None
    if not upsets["wins"]:
        wins_status = html.Div(
            "No giant kills in this filter yet — beat someone rated above you "
            "and they show up here.",
            className="upset-empty-line",
        )

    losses_status = None
    if not upsets["losses"]:
        losses_status = html.Div(
            "No upset losses — you hold serve against lower-rated opponents.",
            className="upset-empty-line",
        )

    return (
        _upset_rows(upsets["wins"], "+"),
        wins_status,
        _upset_rows(upsets["losses"], "−"),
        losses_status,
    )


navigate_to_game_from_upset_win = register_game_navigation(
    "upset-wins-table", "Clicking a giant kill opens that Game's detail view.")
navigate_to_game_from_upset_loss = register_game_navigation(
    "upset-losses-table", "Clicking an upset loss opens that Game's detail view.")


@callback(Output("length-stats", "children"), FILTER_INPUTS)
def update_length_stats(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
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
