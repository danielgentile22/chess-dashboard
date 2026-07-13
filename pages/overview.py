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
from dash import Input, Output, callback, dcc, html

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
from styles import (
    COLORS,
    WDL_COLOR_MAP,
    WDL_HOVER_WORD,
    apply_dark_theme,
    donut_fig,
    empty_fig,
)
from uscf_core import achievement_milestones, membership_alert

dash.register_page(
    __name__, path="/", name="Overview", title="Overview — Chess Dashboard", order=0,
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Overview", "Your career at a glance"),

        # KPI bar.  Values are neutral white; colour survives only where it
        # carries meaning — win % green, loss % red (PRD colour discipline).
        # Ratings, the streak, and the rest read in plain white now.
        html.Div(className="kpi-bar", children=[
            kpi_card("Total Games",        "kpi-total"),
            kpi_card("Win %",              "kpi-win-pct",  "win"),
            kpi_card("Draw %",             "kpi-draw-pct"),
            kpi_card("Loss %",             "kpi-loss-pct", "loss"),
            kpi_card("Current Rating",     "kpi-rating"),
            kpi_card("Peak Rating",        "kpi-peak"),
            kpi_card("Performance Rtg",    "kpi-perf"),
            kpi_card("Longest Win Streak", "kpi-streak"),
            kpi_card("Unique Opponents",   "kpi-opps"),
            # The favourite opening can be a long name — wrap, never truncate.
            kpi_card("Favourite Opening",  "kpi-fav-opn", text=True),
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

    # The favourite-opening KPI wraps to two lines in its text variant, so the
    # full name reaches the browser un-truncated ("Italian Game: Scotch
    # Gambit", not "Italian Game: Scot…").
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
        k["favorite_opening"],
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

    # The outcome letter (W/D/L) inside each badge is the non-color channel —
    # colour alone can't carry win vs loss (issue #88).
    badges = [
        html.Span(o[0], className=f"sbadge {o.lower()}", title=o)
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

    # Quiet lowercase outcome words for the hover (the wedge color already says
    # which outcome it is; "wins" / "draws" / "losses" stays short and calm).
    outcomes = pie_df["Outcome"]
    return donut_fig(
        labels=outcomes,
        values=pie_df["Games"],
        colors=[WDL_COLOR_MAP.get(o, COLORS["dim"]) for o in outcomes],
        center_word="games",
        hover_words=[WDL_HOVER_WORD.get(o, str(o).lower()) for o in outcomes],
    )


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
    # The y-axis carries a truncated label; the hover shows the full reason
    # as quiet context after the bold count.
    fig.update_traces(
        hovertemplate="<b>%{x}</b> games · %{customdata[0]}<extra></extra>",
    )
    apply_dark_theme(fig, xaxis_title="Games")
    fig.update_yaxes(categoryorder="total ascending")
    return fig


def _milestone_row(m: dict) -> html.Div:
    """One timeline row.  Game milestones show their game number; official
    USCF achievements (issue #36) show a gold USCF badge and link to the
    Events page, where their Rated Event lives."""
    is_uscf = m["kind"] == "uscf"
    num = html.Div("USCF" if is_uscf else f"#{m['game_num']}",
                   className="milestone-num" + (" milestone-num-uscf" if is_uscf else ""))
    description: html.Div = html.Div(
        dcc.Link(m["description"], href="/events", className="milestone-uscf-link")
        if is_uscf else m["description"],
        className="milestone-desc",
    )
    return html.Div(className="milestone-row" + (" milestone-row-uscf" if is_uscf else ""),
                    children=[
                        html.Div(className=f"milestone-dot {m['kind']}"),
                        html.Div(m["date"], className="milestone-date"),
                        num,
                        description,
                    ])


@callback(Output("milestones-content", "children"), FILTER_INPUTS)
def update_milestones(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    ms = compute_milestones(df_f)
    # Official achievements join the timeline (issue #36).  They aren't Games,
    # so only the date range applies to them — never the game filters.
    ms += achievement_milestones(data.get_uscf_achievements(),
                                 date_start=start, date_end=end)
    if not ms:
        return html.Div("No milestone data", style={"color": COLORS["dim"]})

    def _chronological(m: dict):
        # Undated entries go last (nothing to place them by), and PGN-style
        # dates ('2026.05.01') compare correctly against ISO ('2026-05-01').
        date_str = (m["date"] or "").replace(".", "-")
        return (date_str == "", date_str, m["game_num"] is None, m["game_num"] or 0)

    # Chronological across both kinds; game milestones break date ties by
    # game number, achievements (no game number) go after them.
    ms.sort(key=_chronological)
    return html.Div([_milestone_row(m) for m in ms], className="milestone-list")
