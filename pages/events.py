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
from components import (
    chart_card,
    content_card,
    empty_state,
    game_detail_path,
    page_header,
)
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import event_summary, performance_rating_stats
from styles import WDL_COLOR_MAP, apply_dark_theme, apply_wdl_hover, empty_fig
from uscf_core import (
    RoundOutcome,
    StandingEntry,
    UscfEvent,
    ordinal,
    series_summary,
    unplayed_events,
)

dash.register_page(
    __name__, path="/events", name="Events", title="Events — Chess Dashboard", order=4,
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
    """One Game line inside a Rated Event — a link into its detail view.

    The round shown is the REAL one from the crosstable when known
    (issue #34), falling back to the hand-typed Round header."""
    round_num = row.get("UscfRound")
    if round_num is None or pd.isna(round_num):
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
    detail = game_detail_path(row.get("ChapterURL", ""))
    if detail:
        return dcc.Link(children, href=detail, className="event-game-row")
    return html.Div(children, className="event-game-row")


def _round_result(outcome: RoundOutcome, games_by_round: dict[int, str]):
    """One round in a crosstable row: a result letter, linked to the Game when
    this is the member's own row and a Game exists for that round."""
    letters = {"Win": "W", "Loss": "L", "Draw": "D", "WinForfeit": "W",
               "Forfeit": "F", "ByeFull": "B", "ByeHalf": "b", "Unpaired": "·"}
    letter = letters.get(outcome.outcome, "·")
    title = f"R{outcome.round_number}: {outcome.outcome}"
    if outcome.opponent_name:
        title += f" vs {outcome.opponent_name}"

    detail = game_detail_path(games_by_round.get(outcome.round_number, ""))
    css = f"crosstable-round {letters.get(outcome.outcome, '').lower() or 'none'}"
    if detail:
        return dcc.Link(letter, href=detail, title=title,
                        className=css + " crosstable-round-link")
    return html.Span(letter, title=title, className=css)


def _crosstable_row(entry: StandingEntry, member_id: str,
                    games_by_round: dict[int, str]) -> html.Div:
    """One player's crosstable line; the member's own row is highlighted and
    its rounds link to his Games."""
    is_me = entry.member_id == member_id
    rounds = [_round_result(r, games_by_round if is_me else {})
              for r in entry.rounds]
    return html.Div(
        className="crosstable-row" + (" crosstable-row-me" if is_me else ""),
        children=[
            html.Span(str(entry.ordinal), className="crosstable-ordinal"),
            html.Span(entry.name, className="crosstable-player"),
            html.Span(_rating_change(entry.pre_rating, entry.post_rating),
                      className="crosstable-rating"),
            html.Span(f"{entry.score:g}", className="crosstable-score"),
            html.Span(rounds, className="crosstable-rounds"),
        ],
    )


def _crosstable(section_name: str, standings: list[StandingEntry],
                member_id: str, games_by_round: dict[int, str]) -> html.Details:
    """The full standings of one Section, expandable (issue #34)."""
    me = next((s for s in standings if s.member_id == member_id), None)
    placement = (f"Finished {ordinal(me.ordinal)} of {len(standings)}"
                 if me else f"{len(standings)} players")
    return html.Details(className="crosstable", children=[
        html.Summary(className="crosstable-summary", children=[
            html.Span(placement, className="crosstable-placement"),
            html.Span(f"{section_name} · full crosstable",
                      className="crosstable-hint"),
        ]),
        html.Div(className="crosstable-table", children=[
            html.Div(className="crosstable-row crosstable-header", children=[
                html.Span("#", className="crosstable-ordinal"),
                html.Span("Player", className="crosstable-player"),
                html.Span("Rating", className="crosstable-rating"),
                html.Span("Score", className="crosstable-score"),
                html.Span("Rounds", className="crosstable-rounds"),
            ]),
            *[_crosstable_row(entry, member_id, games_by_round)
              for entry in standings],
        ]),
    ])


def _rated_event_card(event: dict, performance_rating: int | None = None,
                      standings: dict | None = None, member_id: str = "",
                      extra_rows: list[dict] | None = None) -> html.Div:
    """One Rated Event inside a Series: official identity, score, rating change,
    its Games, and — when its crosstables are cached — the full standings with
    the member's placement (issue #34)."""
    meta_bits = [
        " · ".join(event["sections"]),
        f"{event['player_count']} players" if event["player_count"] else "",
        _score_label(event),
        f"Performance {performance_rating}" if performance_rating else "",
    ]

    # Real round → ChapterURL, for crosstable round links.  Forfeit rows from
    # the Series ride along (their crosstable round was attached via this very
    # event's crosstable) — but never other events' unmatched games, which
    # could collide on round numbers.
    forfeit_rows = [row for row in (extra_rows or []) if row.get("Forfeit")]
    games_by_round = {
        int(row["UscfRound"]): row["ChapterURL"]
        for row in [*event["rows"], *forfeit_rows]
        if pd.notna(row.get("UscfRound")) and row.get("ChapterURL")
    }
    crosstables = [
        _crosstable(section, (standings or {})[(event["event_id"], section)],
                    member_id, games_by_round)
        for section in event["sections"]
        if (event["event_id"], section) in (standings or {})
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
        *crosstables,
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


def _series_group(series: dict, performance_by_event: dict,
                  standings: dict, member_id: str) -> html.Details:
    """One Series as a native expandable group."""
    body: list = [
        _rated_event_card(event, performance_by_event.get(event["event_id"]),
                          standings=standings, member_id=member_id,
                          extra_rows=series["unmatched"])
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
    # Horizontal bars so tournament names read straight across — no rotated,
    # overlapping labels (PRD: "horizontal bars wherever names are long").
    fig = px.bar(long, x="Count", y="Event", color="Outcome",
                 orientation="h", barmode="stack", color_discrete_map=WDL_COLOR_MAP)
    # The y-axis already names the event; the hover shows only "<b>N</b> wins".
    apply_wdl_hover(fig, value_axis="x")
    apply_dark_theme(fig, xaxis_title="Games", legend_title="Outcome")
    # Most-recent Series on top; let names claim whatever width they need.
    fig.update_yaxes(autorange="reversed", automargin=True)
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

    # Crosstables + whose row to highlight in them (issue #34)
    standings = data.get_uscf_standings()
    profile = data.get_uscf_profile()
    member_id = profile.member_id if profile else ""

    return html.Div(className="series-list", children=[
        content_card(
            "Series → Rated Events",
            html.Div(
                "Each Series is a tournament as you name it; inside are the "
                "Rated Events USCF actually rated, with your official results.",
                className="series-list-hint",
            ),
            *[_series_group(s, performance_by_event, standings, member_id)
              for s in summary],
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
