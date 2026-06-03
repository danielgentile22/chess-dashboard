"""
pages/events.py
===============
The Events page — Series → Rated Event (issue #33).

The two-level model from CONTEXT.md: a **Series** is a tournament as Daniel
names it (the PGN Event header, e.g. "ACC Friday Ladder"); each Series
contains the **Rated Events** USCF actually rated (e.g. "ACC JUNE 2025" …
"ACC MAY 2026"), each with its Section(s), score, game count, and live
rating change.

The page is built from native <details> groups — they expand/collapse with
zero callbacks, work on a phone, and let several Series stay open at once.
Game rows are plain links into the Game detail view (issue #11).
"""
from __future__ import annotations

import dash
import pandas as pd
import plotly.express as px
from dash import Output, callback, dcc, html

import data
from components import chart_card, content_card, empty_state, page_header
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import event_summary, performance_rating_stats
from styles import WDL_COLOR_MAP, apply_dark_theme, empty_fig
from uscf_core import UscfEvent, series_summary, unplayed_events

dash.register_page(
    __name__, path="/events", name="Events", title="Events — Chess Stats", order=4,
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Events", "Each Series and the Rated Events inside it"),

        chart_card("Performance per Series (W/D/L)", "event-bar", height=420),

        # Series → Rated Events (issue #33) — filled by callback
        html.Div(id="series-groups"),

        # Rated Events entered but with no Games (Rockville) — filled by callback
        html.Div(id="unplayed-events"),
    ])


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _whole(value: float | None) -> str:
    """A rating for display: whole numbers, never decimals (Daniel's rule)."""
    return "" if value is None else f"{value:.0f}"


def _rating_change(pre: float | None, post: float | None) -> str:
    """'1544 → 1571 (+26)' — or 'unrated → 695' for the first event ever."""
    if post is None:
        return ""
    if pre is None:
        return f"unrated → {_whole(post)}"
    diff = round(post) - round(pre)
    return f"{_whole(pre)} → {_whole(post)} ({'+' if diff >= 0 else ''}{diff})"


def _score_label(group: dict) -> str:
    """'Score 3.5 · 4 games + 1 forfeit win' — the Forfeit rule made visible."""
    label = f"Score {group['score']:g} · {group['games']} game"
    label += "s" if group["games"] != 1 else ""
    if group["forfeits"]:
        label += f" + {group['forfeits']} forfeit win"
        label += "s" if group["forfeits"] != 1 else ""
    return label


def _event_dates(event: dict) -> str:
    """'2026-05-01 – 2026-05-29', collapsing single-day events to one date."""
    start, end = event.get("start"), event.get("end")
    if not start:
        return ""
    return start if start == end or not end else f"{start} – {end}"


def _game_row(row: dict):
    """One Game line inside a Rated Event — a link into its detail view."""
    round_num = row.get("RoundNum")
    round_label = f"R{int(round_num)}" if pd.notna(round_num) else "—"
    outcome = str(row.get("Outcome", ""))

    children = [
        html.Span(round_label, className="event-game-round"),
        html.Span(str(row.get("Color", "")), className="event-game-color"),
        html.Span(outcome, className=f"event-game-outcome {outcome.lower()}"),
        html.Span(f"vs {row.get('Opponent', '')}", className="event-game-opponent"),
        html.Span(str(row.get("OpponentRating", "") or ""),
                  className="event-game-opp-rating"),
        html.Span("Forfeit", className="event-game-forfeit-tag")
        if row.get("Forfeit") else None,
    ]
    detail = row.get("ChapterURL", "")
    if detail:
        chapter_id = detail.rstrip("/").rsplit("/", 1)[-1]
        return dcc.Link(children, href=f"/game/{chapter_id}",
                        className="event-game-row")
    return html.Div(children, className="event-game-row")


def _rated_event_card(event: dict, performance_rating: int | None = None) -> html.Div:
    """One Rated Event inside a Series: official identity, score, rating change,
    and its Games."""
    meta_bits = [
        " · ".join(event["sections"]),
        f"{event['player_count']} players" if event["player_count"] else "",
        _score_label(event),
        f"Performance {performance_rating}" if performance_rating else "",
    ]
    return html.Div(className="rated-event-card", children=[
        html.Div(className="rated-event-head", children=[
            html.Div(children=[
                html.Div(event["name"], className="rated-event-name"),
                html.Div(_event_dates(event), className="rated-event-dates"),
            ]),
            html.Div(_rating_change(event["pre"], event["post"]),
                     className="rated-event-rating",
                     title="Live Rating: walking in → walking out"),
        ]),
        html.Div(" · ".join(bit for bit in meta_bits if bit),
                 className="rated-event-meta"),
        html.Div([_game_row(row) for row in event["rows"]],
                 className="rated-event-games"),
    ])


def _unmatched_block(rows: list[dict]) -> html.Div:
    """Games in this Series with no Rated Event: Forfeits and games USCF
    hasn't rated.  They stay visible — enrichment never hides Games."""
    return html.Div(className="rated-event-card rated-event-unmatched", children=[
        html.Div("Not part of any Rated Event", className="rated-event-name"),
        html.Div("Forfeits and games USCF hasn't rated (yet)",
                 className="rated-event-meta"),
        html.Div([_game_row(row) for row in rows], className="rated-event-games"),
    ])


def _series_stats_label(series: dict) -> str:
    """'27 games · 15W 3D 9L · 16.5 pts · best streak 4W'."""
    bits = [f"{series['games']} games",
            f"{series['win']}W {series['draw']}D {series['loss']}L",
            f"{series['score']:g} pts"]
    if series["forfeits"]:
        bits.insert(1, f"{series['forfeits']} forfeit"
                       + ("s" if series["forfeits"] != 1 else ""))
    if series["win_streak"] >= 2:
        bits.append(f"best streak {series['win_streak']}W")
    return " · ".join(bits)


def _series_group(series: dict, performance_by_event: dict) -> html.Details:
    """One Series as a native expandable group."""
    body: list = [
        _rated_event_card(event, performance_by_event.get(event["event_id"]))
        for event in series["rated_events"]
    ]
    if series["unmatched"]:
        body.append(_unmatched_block(series["unmatched"]))

    return html.Details(className="series-group", children=[
        html.Summary(className="series-summary", children=[
            html.Span(series["series"], className="series-name"),
            html.Span(_series_stats_label(series), className="series-stats"),
        ]),
        html.Div(body, className="series-body"),
    ])


def _unplayed_card(event: UscfEvent) -> html.Div:
    """One entered-but-never-played Rated Event (the Rockville case)."""
    dates = ""
    if event.start_date:
        dates = (event.start_date.isoformat()
                 if event.start_date == event.end_date or not event.end_date
                 else f"{event.start_date} – {event.end_date}")
    field = f"{event.player_count} players" if event.player_count else ""
    return html.Div(className="unplayed-event-row", children=[
        html.Span(event.name, className="unplayed-event-name"),
        html.Span(dates, className="unplayed-event-dates"),
        html.Span(field, className="unplayed-event-field"),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("event-bar", "figure"), FILTER_INPUTS)
def update_event_bar(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    ev = event_summary(df_f)
    if ev.empty:
        return empty_fig("No event data")
    long = ev.tail(20).melt(
        id_vars=["Event"],
        value_vars=["Win", "Draw", "Loss"],
        var_name="Outcome", value_name="Count",
    )
    fig = px.bar(long, x="Event", y="Count", color="Outcome",
                 barmode="stack", color_discrete_map=WDL_COLOR_MAP)
    apply_dark_theme(fig, legend_title="Outcome")
    fig.update_xaxes(tickangle=35, automargin=True)
    return fig


@callback(Output("series-groups", "children"), FILTER_INPUTS)
def update_series_groups(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    """The Series → Rated Event groups (issue #33)."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    summary = series_summary(df_f, data.get_live_series(), data.get_uscf_events())
    if not summary:
        return empty_state(
            "♟", "No events in this filter",
            "Widen the global filters to see your tournaments.",
        )

    # Per-Rated-Event performance ratings (kept from the old detail panel)
    performance_by_event = {}
    if "UscfEventId" in df_f.columns:
        for event_id, games in df_f[df_f["UscfEventId"] != ""].groupby("UscfEventId"):
            performance_by_event[event_id] = (
                performance_rating_stats(games)["performance_rating"]
            )

    return html.Div(className="series-list", children=[
        content_card(
            "Series → Rated Events",
            html.Div(
                "Each Series is a tournament as you name it; inside are the "
                "Rated Events USCF actually rated, with your official results.",
                className="series-list-hint",
            ),
            *[_series_group(s, performance_by_event) for s in summary],
        ),
    ])


@callback(Output("unplayed-events", "children"), FILTER_INPUTS)
def update_unplayed(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    """Rated Events entered but never played (issue #33's Rockville case).

    Determined against the FULL Games df — a date filter must never turn a
    played event into a 'never played' one; it only bounds which unplayed
    events are listed."""
    unplayed = unplayed_events(
        data.get_df(), data.get_uscf_events(), date_start=start, date_end=end,
    )
    if not unplayed:
        return None
    return content_card(
        "Entered, never played",
        html.Div(
            "Rated Events you registered for but have no Games from — "
            "no-shows and online-only events.",
            className="series-list-hint",
        ),
        html.Div([_unplayed_card(e) for e in unplayed],
                 className="unplayed-events-list"),
    )
