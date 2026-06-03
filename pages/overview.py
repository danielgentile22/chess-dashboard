"""
pages/overview.py
=================
The Overview page — the "how am I doing?" snapshot.

KPI bar, USCF profile card, last-20 streak badges, W/D/L donut, termination
breakdown, and the milestone timeline.  Everything responds to the global
filter drawer; the USCF card follows Syncs instead (official data has no
filters).
"""
from __future__ import annotations

from datetime import date

import dash
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, callback, html

import data
from components import (
    chart_card,
    content_card,
    kpi_card,
    page_header,
    uscf_profile_card,
    uscf_unavailable_card,
    weakness_callout,
)
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import (
    compute_milestones,
    kpi_stats,
    recurring_weaknesses,
    streaks,
    termination_counts,
    win_draw_loss_counts,
)
from styles import COLORS, WDL_COLOR_MAP, apply_dark_theme, empty_fig
from uscf_core import membership_alert

dash.register_page(
    __name__, path="/", name="Overview", title="Overview — Chess Stats", order=0,
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Overview", "Your career at a glance"),

        # KPI bar
        html.Div(className="kpi-bar", children=[
            kpi_card("Total Games",        "kpi-total"),
            kpi_card("Win %",              "kpi-win-pct",  "win"),
            kpi_card("Draw %",             "kpi-draw-pct"),
            kpi_card("Loss %",             "kpi-loss-pct", "loss"),
            kpi_card("Current Rating",     "kpi-rating",   "accent"),
            kpi_card("Peak Rating",        "kpi-peak",     "accent"),
            kpi_card("Performance Rtg",    "kpi-perf",     "primary"),
            kpi_card("Longest Win Streak", "kpi-streak",   "win"),
            kpi_card("Unique Opponents",   "kpi-opps"),
            kpi_card("Favourite Opening",  "kpi-fav-opn"),
        ]),

        # The USCF profile card (issue #25) — official identity, follows Syncs
        html.Div(id="uscf-profile-card"),

        # The most severe recurring weakness, if any (issue #18)
        html.Div(id="top-weakness"),

        # Form + outcome charts
        html.Div(className="g3", children=[
            content_card(
                "Last 20 games",
                html.Div(id="streak-badges", className="streak-badges"),
                html.Div(id="streak-stats", className="streak-stats"),
            ),
            chart_card("Win / Draw / Loss", "wdl-pie"),
            chart_card("How games ended", "termination-bar"),
        ]),

        # Milestones
        content_card("Milestones", html.Div(id="milestones-content")),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output("kpi-total",    "children"),
    Output("kpi-win-pct",  "children"),
    Output("kpi-draw-pct", "children"),
    Output("kpi-loss-pct", "children"),
    Output("kpi-rating",   "children"),
    Output("kpi-peak",     "children"),
    Output("kpi-perf",     "children"),
    Output("kpi-streak",   "children"),
    Output("kpi-opps",     "children"),
    Output("kpi-fav-opn",  "children"),
    FILTER_INPUTS,
)
def update_kpis(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    k = kpi_stats(df_f)

    def _r(x):
        return str(int(x)) if x is not None else "—"

    def _p(x):
        return f"{x}%" if x is not None else "—"

    fav = k["favorite_opening"]
    fav_short = (fav[:22] + "…") if len(fav) > 24 else fav
    return (
        str(k["total_games"]),
        _p(k["win_pct"]),
        _p(k["draw_pct"]),
        _p(k["loss_pct"]),
        _r(k["current_rating"]),
        _r(k["peak_rating"]),
        _r(k["performance_rating"]),
        str(k["longest_win_streak"]),
        str(k["unique_opponents"]),
        fav_short,
    )


@callback(Output("uscf-profile-card", "children"), Input("sync-store", "data"))
def update_uscf_card(_sync):
    """
    The USCF profile card (issue #25).

    Follows Syncs (not filters — official data is never filtered).  Degrades
    to an unavailable notice when USCF can't be reached (ADR 0003), and to
    nothing at all when no USCF member ID is configured.
    """
    if not data.uscf_enabled():
        return None
    profile = data.get_uscf_profile()
    if profile is None:
        return uscf_unavailable_card(data.uscf_failure())

    stale = data.uscf_unavailable_since()
    if stale:
        stale += " — showing the last successful Sync's data."

    # The current Live Rating: where the per-Section chain stands today (issue #27)
    live_series = data.get_live_series()
    live_rating = live_series[-1].post if live_series else None

    return uscf_profile_card(
        profile,
        alert=membership_alert(profile, today=date.today()),
        stale=stale,
        live_rating=live_rating,
    )


@callback(Output("top-weakness", "children"), FILTER_INPUTS)
def update_top_weakness(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    """The single most severe recurring weakness (issue #18). Silent below threshold."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    callouts = recurring_weaknesses(df_f)
    if not callouts:
        return None
    return weakness_callout(callouts[0], compact=True)


@callback(
    Output("streak-badges", "children"),
    Output("streak-stats",  "children"),
    FILTER_INPUTS,
)
def update_streak(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    s = streaks(df_f)

    badges = [
        html.Span(className=f"sbadge {o.lower()}", title=o)
        for o in s["last_20"]
    ]
    if not badges:
        badges = [html.Span("No games in this filter",
                            style={"color": COLORS["dim"], "fontSize": "12px"})]

    cur_class = (s["current_streak_outcome"].lower()
                 if s["current_streak_outcome"] != "N/A" else "")
    stats = [
        html.Div(className="streak-stat", children=[
            html.Div("Unbeaten streak", className="streak-stat-label"),
            html.Div(str(s["longest_streak_no_loss"]), className="streak-stat-value win"),
        ]),
        html.Div(className="streak-stat", children=[
            html.Div("Win streak", className="streak-stat-label"),
            html.Div(str(s["longest_streak_wins_only"]), className="streak-stat-value win"),
        ]),
        html.Div(className="streak-stat", children=[
            html.Div(f"Current ({s['current_streak_outcome']})", className="streak-stat-label"),
            html.Div(str(s["current_streak_same_outcome"]),
                     className=f"streak-stat-value {cur_class}"),
        ]),
    ]
    return badges, stats


@callback(Output("wdl-pie", "figure"), FILTER_INPUTS)
def update_wdl(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    counts = win_draw_loss_counts(df_f)
    pie_df = counts[counts > 0].reset_index()
    pie_df.columns = ["Outcome", "Games"]
    if pie_df.empty:
        return empty_fig("No data")

    fig = go.Figure(go.Pie(
        labels=pie_df["Outcome"],
        values=pie_df["Games"],
        hole=0.54,
        marker=dict(
            colors=[WDL_COLOR_MAP.get(o, COLORS["dim"]) for o in pie_df["Outcome"]],
            line=dict(color=COLORS["card"], width=2),
        ),
        textinfo="percent+label",
        textfont=dict(size=12, color=COLORS["text"]),
        hovertemplate="%{label}: %{value} games (%{percent})<extra></extra>",
    ))
    total = int(pie_df["Games"].sum())
    fig.add_annotation(
        text=f"<b>{total}</b><br><span style='font-size:11px'>games</span>",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=16, color=COLORS["text"]),
    )
    apply_dark_theme(fig, show_legend=False)
    return fig


@callback(Output("termination-bar", "figure"), FILTER_INPUTS)
def update_terminations(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    tc = termination_counts(df_f)
    if tc.empty:
        return empty_fig("No data")
    tc["Label"] = tc["Termination"].apply(
        lambda x: (x[:28] + "…") if len(str(x)) > 30 else x
    )
    fig = px.bar(
        tc, x="Games", y="Label", orientation="h",
        color_discrete_sequence=[COLORS["primary"]],
        hover_data={"Termination": True, "Games": True, "Label": False},
    )
    fig.update_traces(
        hovertemplate="%{customdata[0]}: %{x} games<extra></extra>",
    )
    apply_dark_theme(fig, xaxis_title="Games")
    fig.update_yaxes(categoryorder="total ascending")
    return fig


@callback(Output("milestones-content", "children"), FILTER_INPUTS)
def update_milestones(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    ms = compute_milestones(df_f)
    if not ms:
        return html.Div("No milestone data", style={"color": COLORS["dim"]})
    rows = [
        html.Div(className="milestone-row", children=[
            html.Div(className=f"milestone-dot {m['kind']}"),
            html.Div(m["date"],           className="milestone-date"),
            html.Div(f"#{m['game_num']}", className="milestone-num"),
            html.Div(m["description"],    className="milestone-desc"),
        ])
        for m in ms
    ]
    return html.Div(rows, className="milestone-list")
