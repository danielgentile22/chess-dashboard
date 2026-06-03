"""
uscf_core.py
============
Pure functions that interpret raw USCF API responses (no HTTP, no Dash).

uscf_client fetches raw JSON from the MUIR API; this module turns it into
typed records the rest of the app understands — the same split as
lichess_client (raw PGN) / pgn_stats_core (parsed Games).

The MUIR API is undocumented (ADR 0003), so parsing is deliberately tolerant:
unrated systems appear as entries with no `rating` key, decimals can be
missing, and `fideId` of "0" means "none".

Public API
----------
parse_member_profile    raw /members/{id} response → UscfProfile
membership_alert        Warning text when the membership has lapsed / expires soon.
build_official_series   raw supplement items → the Official Rating series.
build_live_series       raw section items → the Live Rating series (continuous chain).
rating_trend_series     both series trimmed to a date range (the Trends chart's data).
build_game_records      raw game items → typed USCF Game Records.
build_member_events     raw event items → typed Rated Events (issue #33).
build_achievements      raw norm + award items → typed UscfAchievements (issue #36).
achievement_milestones  achievements → Milestone-timeline entries (issue #36).
match_games             USCF Game Records ↔ Games (the matching engine).
enrich_games            Games df + MatchResult → df with USCF enrichment columns.
apply_rating_lens       Games df with player ratings rewritten per the Official/Live lens.
reconcile               Every disagreement between the Studies and USCF.
UscfProfile             Who the member is according to USCF.
UscfRating              One rating system's entry (rating, provisional, floor).
OfficialRatingPoint     One supplement month's Official Rating.
LiveRatingPoint         One Section's pre→post Live Rating change.
UscfGameRecord          USCF's official record of one rated game (CONTEXT.md).
UscfAchievement         One official achievement — a norm or an award.
GameMatch               One Game ↔ USCF Game Record pairing.
MatchResult             Everything matching produced: matches + both leftovers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

__all__ = [
    "LIVE_LENS",
    "OFFICIAL_LENS",
    "GameMatch",
    "LiveRatingPoint",
    "MatchResult",
    "OfficialRatingPoint",
    "ReconciliationEntry",
    "RoundOutcome",
    "StandingEntry",
    "UscfAchievement",
    "UscfEvent",
    "UscfGameRecord",
    "UscfProfile",
    "UscfRating",
    "achievement_milestones",
    "apply_rating_lens",
    "attach_round_numbers",
    "build_achievements",
    "build_game_records",
    "build_live_series",
    "build_member_events",
    "build_official_series",
    "build_standings",
    "enrich_games",
    "match_games",
    "membership_alert",
    "parse_member_profile",
    "rating_trend_series",
    "reconcile",
    "series_summary",
    "unplayed_events",
]

# Warn this many days before the membership expires — enough time to renew
# before the next monthly tournament sneaks up.
_EXPIRATION_WARNING_DAYS = 90


# ---------------------------------------------------------------------------
# Typed records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UscfRating:
    """One rating system's entry in a member profile (system codes: R, Q, B, OR, OQ, OB)."""

    system: str
    rating: int | None        # None → unrated in this system
    is_provisional: bool
    games_played: int | None  # provisional game count (absent once established)
    floor: int | None         # the rating this member can never fall below


@dataclass(frozen=True)
class UscfProfile:
    """A member profile: ratings, ranks, floor, and membership status."""

    member_id: str
    name: str
    state: str
    national_rank: int | None
    state_rank: int | None
    ratings: tuple[UscfRating, ...]
    membership_status: str
    membership_expires: date | None

    def rating(self, system: str) -> UscfRating | None:
        """The entry for one rating system ('R', 'Q', 'OR', …), or None if absent."""
        return next((r for r in self.ratings if r.system == system), None)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_member_profile(raw: dict) -> UscfProfile:
    """
    Interpret a raw /members/{id} response.

    Tolerant of the API's quirks: unrated systems have no `rating` key,
    established ratings have no `gamesPlayed`, and unrated systems no `floor`.
    """
    ratings = tuple(
        UscfRating(
            system=str(entry.get("ratingSystem", "")),
            rating=entry.get("rating"),
            is_provisional=bool(entry.get("isProvisional", False)),
            games_played=entry.get("gamesPlayed"),
            floor=entry.get("floor"),
        )
        for entry in raw.get("ratings", [])
    )

    first = str(raw.get("firstName", "")).strip()
    last = str(raw.get("lastName", "")).strip()

    return UscfProfile(
        member_id=str(raw.get("id", "")),
        name=" ".join(part for part in (first, last) if part),
        state=str(raw.get("stateRep", "")),
        national_rank=raw.get("rank"),
        state_rank=raw.get("stateRank"),
        ratings=ratings,
        membership_status=str(raw.get("status", "")),
        membership_expires=_parse_date(raw.get("expirationDate")),
    )


# ---------------------------------------------------------------------------
# The Official Rating series (issue #27)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OfficialRatingPoint:
    """The Official Rating as published in one monthly supplement."""

    month: date   # the supplement date
    rating: int


def build_official_series(supplement_items: list[dict]) -> list[OfficialRatingPoint]:
    """
    The Official Rating series: one integer per supplement month,
    chronological, starting at the first supplement.

    Months before the first supplement have no official value and are never
    invented; neither are gap months nor months whose supplement carries no
    Regular rating.
    """
    points = []
    for item in supplement_items:
        month = _parse_date(item.get("ratingSupplementDate"))
        if month is None:
            continue
        regular = next(
            (entry for entry in item.get("ratings", []) if entry.get("source") == "R"),
            None,
        )
        if regular is None or regular.get("rating") is None:
            continue
        points.append(OfficialRatingPoint(month=month, rating=regular["rating"]))

    return sorted(points, key=lambda p: p.month)


# ---------------------------------------------------------------------------
# The Live Rating series (issue #27)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveRatingPoint:
    """One Section's effect on the Live Rating: pre → post, decimals preserved."""

    event_id: str
    event_name: str
    section_name: str
    end_date: date
    pre: float | None   # None for the first Section ever played
    post: float


def build_live_series(section_items: list[dict]) -> list[LiveRatingPoint]:
    """
    The Live Rating series: one pre→post pair per Regular-rated Section,
    decimals preserved, ordered chronologically by event end date.

    Dual-rated (D) Sections move the Regular chain via their Regular rating
    record; Quick and Online (Q/OR/OQ/OB) Sections are excluded.  The result
    is a continuous chain — each Section's post-rating is the next Section's
    pre-rating — including Sections that end on the same day, which are
    ordered by following that chain.
    """
    points = []
    for item in section_items:
        # Only Sections that move the Regular rating (the backbone — PRD #24)
        if item.get("ratingSystem") not in ("R", "D"):
            continue
        record = next(
            (r for r in item.get("ratingRecords", []) if r.get("ratingSource") == "R"),
            None,
        )
        if record is None:
            continue
        post = record.get("postRatingDecimal", record.get("postRating"))
        if post is None:
            continue  # a Section with no rating outcome moves nothing
        event = item.get("event", {})
        end_date = _parse_date(event.get("endDate")) or _parse_date(item.get("endDate"))
        if end_date is None:
            continue
        points.append(LiveRatingPoint(
            event_id=str(event.get("id", "")),
            event_name=str(event.get("name", "")),
            section_name=str(item.get("sectionName", "")),
            end_date=end_date,
            pre=record.get("preRatingDecimal", record.get("preRating")),
            post=post,
        ))

    points.sort(key=lambda p: p.end_date)
    return _chain_same_day_sections(points)


def _chain_same_day_sections(points: list[LiveRatingPoint]) -> list[LiveRatingPoint]:
    """
    Order Sections that end on the same day by following the rating chain.

    USCF rates same-day Sections in sequence; their pre/post values say which
    came first.  The API's response order does not.
    """
    ordered: list[LiveRatingPoint] = []
    i = 0
    while i < len(points):
        j = i
        while j < len(points) and points[j].end_date == points[i].end_date:
            j += 1
        group = points[i:j]
        if len(group) > 1:
            group = _chain_group(group, ordered[-1].post if ordered else None)
        ordered.extend(group)
        i = j
    return ordered


def _chain_group(
    group: list[LiveRatingPoint], previous_post: float | None
) -> list[LiveRatingPoint]:
    """Greedily link a same-day group: each Section's pre is the previous post."""
    remaining = list(group)
    chained: list[LiveRatingPoint] = []
    current = previous_post
    while remaining:
        next_point = next((p for p in remaining if p.pre == current), None)
        if next_point is None:
            # The chain can't tell us the order — keep what's left as-is
            chained.extend(remaining)
            break
        remaining.remove(next_point)
        chained.append(next_point)
        current = next_point.post
    return chained


# ---------------------------------------------------------------------------
# The dual-line rating trend (issue #31)
# ---------------------------------------------------------------------------

def rating_trend_series(
    official_series: list[OfficialRatingPoint],
    live_series: list[LiveRatingPoint],
    *,
    date_start: str | date | None = None,
    date_end: str | date | None = None,
) -> tuple[list[OfficialRatingPoint], list[LiveRatingPoint]]:
    """
    The Trends chart's data: both rating series, trimmed to the global
    date-range filter (the only global filter that applies to rating series —
    they are Rated-Event facts, not Games).

    The chart is the one place the lens hides nothing — both series always
    render; the active lens only controls which is emphasized.  Dash sends
    the range as ISO strings; date objects work too.
    """
    start, end = _coerce_date(date_start), _coerce_date(date_end)

    def in_range(day: date) -> bool:
        return (start is None or day >= start) and (end is None or day <= end)

    return (
        [p for p in official_series if in_range(p.month)],
        [p for p in live_series if in_range(p.end_date)],
    )


def _coerce_date(value: str | date | None) -> date | None:
    """An ISO string / date / None from the UI as a date (None when absent)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        # datetime is-a date, but comparing it against plain dates raises
        return value.date()
    if isinstance(value, date):
        return value
    # The Dash date picker can send 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'
    return _parse_date(str(value)[:10])


# ---------------------------------------------------------------------------
# USCF Game Records (issue #28)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UscfGameRecord:
    """USCF's official record of one rated game (see CONTEXT.md)."""

    event_id: str
    event_name: str
    event_start: date | None
    event_end: date | None
    section_name: str
    rating_system: str        # "R" / "D" are over-the-board; "OR"/"OQ"/"OB" are online
    player_color: str         # the member's color: "White" | "Black"
    player_outcome: str       # the member's result: "Win" | "Loss" | "Draw"
    opponent_id: str          # the opponent's USCF member ID
    opponent_name: str        # as USCF registers it ("JOHN BAKER", "Wade Harris")


def build_game_records(game_items: list[dict]) -> list[UscfGameRecord]:
    """Interpret raw /members/{id}/games items as typed USCF Game Records."""
    records = []
    for item in game_items:
        event = item.get("event", {})
        player = item.get("player", {})
        opponent = item.get("opponent", {})
        first = str(opponent.get("firstName", "")).strip()
        last = str(opponent.get("lastName", "")).strip()
        records.append(UscfGameRecord(
            event_id=str(event.get("id", "")),
            event_name=str(event.get("name", "")),
            event_start=_parse_date(event.get("startDate")),
            event_end=_parse_date(event.get("endDate")),
            section_name=str(item.get("section", {}).get("name", "")),
            rating_system=str(item.get("ratingSystem", "")),
            player_color=str(player.get("color", "")),
            player_outcome=str(player.get("outcome", "")),
            opponent_id=str(opponent.get("id", "")),
            opponent_name=" ".join(part for part in (first, last) if part),
        ))
    return records


# ---------------------------------------------------------------------------
# Member events → typed Rated Events (issue #33)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UscfEvent:
    """
    One Rated Event the member entered (CONTEXT.md): USCF's official unit of
    competition, with its official identity, dates, and field.
    """

    event_id: str
    name: str
    start_date: date | None
    end_date: date | None
    section_count: int | None
    player_count: int | None
    city: str
    state: str


def build_member_events(event_items: list[dict]) -> list[UscfEvent]:
    """
    Interpret raw /members/{id}/events items as typed Rated Events,
    chronological (the API sends newest first).

    Parsing is tolerant (ADR 0003): missing dates or counts degrade to None,
    never to errors.
    """
    events = [
        UscfEvent(
            event_id=str(item.get("id", "")),
            name=str(item.get("name", "")),
            start_date=_parse_date(item.get("startDate")),
            end_date=_parse_date(item.get("endDate")),
            section_count=item.get("sectionCount"),
            player_count=item.get("playerCount"),
            city=str(item.get("city", "")),
            state=str(item.get("stateCode", "")),
        )
        for item in event_items
    ]
    return sorted(events, key=lambda e: (e.start_date is None, e.start_date or date.max))


# ---------------------------------------------------------------------------
# Standings → typed crosstables (issue #34)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoundOutcome:
    """One round in a player's crosstable row (issue #34)."""

    round_number: int
    outcome: str            # Win | Loss | Draw | WinForfeit | Forfeit
    #                         | ByeFull | ByeHalf | Unpaired
    color: str              # White | Black | Unknown (byes/forfeits have no color)
    opponent_member_id: str  # '' for byes and unpaired rounds
    opponent_name: str


@dataclass(frozen=True)
class StandingEntry:
    """One player's row in a Rated Event Section's crosstable (issue #34)."""

    ordinal: int            # final placement: 1 = the Section winner
    member_id: str
    name: str
    score: float
    pre_rating: float | None    # Regular pre-rating, decimals (None = unrated)
    post_rating: float | None
    rounds: tuple[RoundOutcome, ...]


def build_standings(standing_items: list[dict]) -> list[StandingEntry]:
    """
    Interpret raw standings items as a typed crosstable, ordered by final
    placement (issue #34).

    Dual-rated Sections carry Quick and Regular records per player — Regular
    is the backbone (PRD #24).  Players who walked in unrated have no
    pre-rating: None, never invented.
    """
    entries = []
    for item in standing_items:
        first = str(item.get("firstName", "")).strip()
        last = str(item.get("lastName", "")).strip()
        regular: dict = next(
            (r for r in item.get("ratings", []) if r.get("ratingSystem") == "R"),
            {},
        )
        rounds = tuple(
            RoundOutcome(
                round_number=int(r.get("roundNumber", 0)),
                outcome=str(r.get("outcome", "")),
                color=str(r.get("color", "")),
                opponent_member_id=str(r.get("opponentMemberId") or ""),
                opponent_name=" ".join(part for part in (
                    str(r.get("opponentFirstName", "")).strip(),
                    str(r.get("opponentLastName", "")).strip(),
                ) if part),
            )
            for r in item.get("roundOutcomes", [])
        )
        entries.append(StandingEntry(
            ordinal=int(item.get("ordinal", 0)),
            member_id=str(item.get("memberId", "")),
            name=" ".join(part for part in (first, last) if part),
            score=float(item.get("score", 0)),
            pre_rating=regular.get("preRatingDecimal", regular.get("preRating")),
            post_rating=regular.get("postRatingDecimal", regular.get("postRating")),
            rounds=rounds,
        ))

    return sorted(entries, key=lambda e: e.ordinal)


# How a crosstable round outcome reads as a Game outcome: forfeit variants
# count the same way the Game's own Outcome column records them.
_ROUND_OUTCOME_AS_GAME = {
    "Win": "Win", "WinForfeit": "Win",
    "Loss": "Loss", "Forfeit": "Loss",
    "Draw": "Draw",
}

# Crosstable outcomes that mean "no game was played over the board".
_FORFEIT_OUTCOMES = ("WinForfeit", "Forfeit")


def attach_round_numbers(
    df: pd.DataFrame,
    standings: dict[tuple[str, str], list[StandingEntry]],
    member_id: str,
) -> pd.DataFrame:
    """
    Return a copy of *df* with the ``UscfRound`` column: each Game's real
    round number from its Rated Event Section's crosstable (issue #34).

    Matched Games look up the member's crosstable row for their Section and
    find the round played against their opponent.  Forfeit Games have no USCF
    Game Record, but the crosstable still records the forfeit round with the
    opponent's member ID — so even no-shows get their real round.

    Games with no crosstable available keep NaN (round-based analytics fall
    back to the typed round) — the column always exists (ADR 0003).
    """
    out = df.copy()
    if out.empty:
        out["UscfRound"] = pd.Series(dtype="float64")
        return out

    # The member's rounds across every cached crosstable:
    # (event_id, section_name, opponent_member_id) → [RoundOutcome, …]
    my_rounds: dict[tuple[str, str, str], list[RoundOutcome]] = {}
    for (event_id, section_name), entries in standings.items():
        me = next((e for e in entries if e.member_id == member_id), None)
        if me is None:
            continue
        for outcome in me.rounds:
            if outcome.opponent_member_id:
                key = (event_id, section_name, outcome.opponent_member_id)
                my_rounds.setdefault(key, []).append(outcome)

    out["UscfRound"] = pd.to_numeric(
        pd.Series([_real_round(game, my_rounds) for _, game in out.iterrows()],
                  index=out.index, dtype="object"),
        errors="coerce",
    )
    return out


def _real_round(
    game: pd.Series, my_rounds: dict[tuple[str, str, str], list[RoundOutcome]]
) -> int | None:
    """One Game's real round number from the crosstables, or None."""
    if game["UscfMatched"]:
        candidates = my_rounds.get(
            (game["UscfEventId"], game["UscfSection"], game["UscfOpponentId"]), [])
        # Prefer the round whose outcome agrees with the Game's (repeat
        # opponents in one Section); a single candidate wins regardless.
        for outcome in candidates:
            if _ROUND_OUTCOME_AS_GAME.get(outcome.outcome) == game["Outcome"]:
                return outcome.round_number
        return candidates[0].round_number if len(candidates) == 1 else None

    if game["Forfeit"]:
        opponent_id = _opponent_id(game)
        if not opponent_id:
            return None
        forfeit_rounds = [
            outcome.round_number
            for (_, _, oid), outcomes in my_rounds.items() if oid == opponent_id
            for outcome in outcomes if outcome.outcome in _FORFEIT_OUTCOMES
        ]
        if len(forfeit_rounds) == 1:  # ambiguity → no guess
            return forfeit_rounds[0]

    return None


# ---------------------------------------------------------------------------
# Series → Rated Event grouping (issue #33): the Events page's data
# ---------------------------------------------------------------------------

# The game facts each Rated Event / unmatched row carries for the page's
# game tables (the same columns the old tournament-detail table used).
# UscfRound (issue #34) rides along when the df has been through
# attach_round_numbers — _game_rows tolerates its absence.
_GAME_ROW_COLUMNS = ["Date", "RoundNum", "UscfRound", "Color", "Opponent",
                     "OpponentRating", "Result", "Outcome", "Termination",
                     "FullMoves", "ChapterURL", "Forfeit"]


def series_summary(
    df: pd.DataFrame,
    live_series: list[LiveRatingPoint],
    member_events: list[UscfEvent],
) -> list[dict]:
    """
    The Events page's data (issue #33): Games grouped **Series → Rated Event**.

    The Series is Daniel's name for the thing (the PGN Event header); each
    Series contains the Rated Events its matched Games belong to, in
    chronological order, each with its Section(s), score, game count, and
    live rating change.  Games with no Rated Event (Forfeits, games USCF
    hasn't rated) stay under their Series as unmatched rows — enrichment
    never hides Games (ADR 0003).

    Tournament scores follow the Forfeit rule (issue #29): a Forfeit win
    counts toward the score but is never a "game" or a win-rate event.
    """
    if df.empty:
        return []

    events_by_id = {e.event_id: e for e in member_events}
    live_by_event: dict[str, list[LiveRatingPoint]] = {}
    for point in live_series:  # chronological → chain order per event
        live_by_event.setdefault(point.event_id, []).append(point)

    summaries = []
    d = df.copy()
    d["Event"] = d["Event"].fillna("").astype(str)
    for series_name, games in d[d["Event"].str.strip() != ""].groupby("Event"):
        games = games.sort_values(["Date_dt", "Index"], na_position="last")
        rated_events = [
            _rated_event_group(event_id, event_games, events_by_id, live_by_event)
            for event_id, event_games in games[games["UscfEventId"] != ""]
                                              .groupby("UscfEventId")
        ]
        rated_events.sort(key=lambda e: (e["start"] is None, e["start"] or ""))

        unmatched = games[games["UscfEventId"] == ""]
        first_date = games["Date_dt"].min()
        summaries.append({
            "series": str(series_name),
            "first_date": (str(first_date.date()) if pd.notna(first_date) else ""),
            **_outcome_counts(games),
            "win_streak": _longest_win_streak(games),
            "rated_events": rated_events,
            "unmatched": _game_rows(unmatched),
        })

    summaries.sort(key=lambda s: (s["first_date"] == "", s["first_date"]))
    return summaries


def unplayed_events(
    df: pd.DataFrame,
    member_events: list[UscfEvent],
    *,
    date_start: str | date | None = None,
    date_end: str | date | None = None,
) -> list[UscfEvent]:
    """
    Rated Events entered but with no Games anywhere in *df* — the
    "entered, never played" group (issue #33's Rockville case, plus
    online-only events whose games are never Chapters by design).

    Pass the FULL Games df, not a filtered one: a date filter must never turn
    a played event into a "never played" one.  The date-range filter applies
    to the events themselves instead (member-level facts — the same rule as
    the rating series).
    """
    played_ids = (
        set(df["UscfEventId"]) - {""}
        if not df.empty and "UscfEventId" in df.columns else set()
    )
    start, end = _coerce_date(date_start), _coerce_date(date_end)

    def in_range(event: UscfEvent) -> bool:
        day = event.start_date
        if day is None:
            return True
        return (start is None or day >= start) and (end is None or day <= end)

    return [e for e in member_events
            if e.event_id not in played_ids and in_range(e)]


def _rated_event_group(
    event_id: str,
    games: pd.DataFrame,
    events_by_id: dict[str, UscfEvent],
    live_by_event: dict[str, list[LiveRatingPoint]],
) -> dict:
    """One Rated Event's entry inside a Series group."""
    event = events_by_id.get(event_id)
    name = event.name if event is not None else str(games.iloc[0]["UscfEventName"])

    # The Sections this Series' games were played in, and the live-rating
    # chain across them (chain order = the live series' chronological order)
    sections_played = set(games["UscfSection"]) - {""}
    points = [p for p in live_by_event.get(event_id, [])
              if p.section_name in sections_played]

    return {
        "event_id": event_id,
        "name": name,
        "start": (event.start_date.isoformat()
                  if event is not None and event.start_date else None),
        "end": (event.end_date.isoformat()
                if event is not None and event.end_date else None),
        "city": event.city if event is not None else "",
        "player_count": event.player_count if event is not None else None,
        "sections": sorted(sections_played),
        **_outcome_counts(games),
        "pre": points[0].pre if points else None,
        "post": points[-1].post if points else None,
        "rows": _game_rows(games),
    }


def _outcome_counts(games: pd.DataFrame) -> dict:
    """Game/forfeit counts and the tournament score for a group of Games.

    Forfeits count toward the score (a forfeit win is a tournament point) but
    never as games or wins — the Forfeit rule from issue #29."""
    real = games[~games["Forfeit"]]
    win = int((real["Outcome"] == "Win").sum())
    draw = int((real["Outcome"] == "Draw").sum())
    loss = int((real["Outcome"] == "Loss").sum())
    forfeit_points = float((games[games["Forfeit"]]["Outcome"] == "Win").sum())
    return {
        "games": int(len(real)),
        "forfeits": int(games["Forfeit"].sum()),
        "win": win, "draw": draw, "loss": loss,
        "score": win + 0.5 * draw + forfeit_points,
    }


def _longest_win_streak(games: pd.DataFrame) -> int:
    """The longest run of consecutive wins (date order), Forfeits excluded —
    a no-show never extends a Streak (issue #29)."""
    best = current = 0
    for outcome in games[~games["Forfeit"]]["Outcome"]:
        current = current + 1 if outcome == "Win" else 0
        best = max(best, current)
    return best


def _game_rows(games: pd.DataFrame) -> list[dict]:
    """Game rows for the page's tables (clickable through ChapterURL)."""
    if games.empty:
        return []
    columns = [c for c in _GAME_ROW_COLUMNS if c in games.columns]
    return games[columns].to_dict("records")


# ---------------------------------------------------------------------------
# Norms and awards → achievements (issue #36)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UscfAchievement:
    """
    One official USCF achievement — a norm or an award (issue #36).

    Norms and awards are member-level facts (they don't belong to any Game),
    so they join the Milestones timeline as their own entries rather than
    coming out of the Games DataFrame.
    """

    achievement_id: str   # stable identity — what new-vs-seen comparison uses
    kind: str             # "norm" | "award"
    title: str            # "Fourth Category norm" / "25th career win"
    detail: str           # "Scored 4.5 in 5 games" / ""
    date: date | None     # when earned (None when USCF gives no date at all)
    event_id: str         # the Rated Event it was earned at ('' if unknown)
    event_name: str


def build_achievements(
    norm_items: list[dict], award_items: list[dict]
) -> list[UscfAchievement]:
    """
    Interpret raw /norms and /awards items as one chronological achievement
    list (issue #36).

    Parsing is tolerant (ADR 0003): missing events, dates, or unrecognized
    award categories degrade to less-specific entries, never to errors.
    """
    achievements = [_norm_achievement(item) for item in norm_items]
    achievements += [_award_achievement(item) for item in award_items]
    # Chronological; undated achievements go last (no date to sort them by)
    return sorted(achievements, key=lambda a: (a.date is None, a.date or date.max))


def _norm_achievement(item: dict) -> UscfAchievement:
    """A norm: level + score, earned at its event's end date."""
    event = item.get("event", {})
    level = str(item.get("level", ""))
    score, games = item.get("score"), item.get("playedGames")
    detail = f"Scored {score} in {games} games" if score and games else ""

    return UscfAchievement(
        achievement_id=f"norm:{level}:{event.get('id', '')}",
        kind="norm",
        title=f"{_split_camel_case(level)} norm",
        detail=detail,
        date=_parse_date(event.get("endDate")),
        event_id=str(event.get("id", "")),
        event_name=str(event.get("name", "")),
    )


def _award_achievement(item: dict) -> UscfAchievement:
    """An award: USCF's own milestones, like the 25th career win."""
    event = item.get("event", {})
    category = str(item.get("category", ""))
    win_count = item.get("winCount")

    if category == "WinMilestone" and win_count:
        title = f"{_ordinal(win_count)} career win"
    else:
        title = f"{_split_camel_case(category)} award"

    return UscfAchievement(
        achievement_id=f"award:{item.get('id', '')}",
        kind="award",
        title=title,
        detail="",
        date=_parse_date(item.get("date")) or _parse_date(event.get("endDate")),
        event_id=str(event.get("id", "")),
        event_name=str(event.get("name", "")),
    )


def achievement_milestones(
    achievements: list[UscfAchievement],
    *,
    date_start: str | date | None = None,
    date_end: str | date | None = None,
) -> list[dict]:
    """
    Achievements as Milestone-timeline entries (issue #36) — the same dict
    shape ``compute_milestones`` produces, so the Overview renders both
    through one code path.  ``kind="uscf"`` flags them for the gold treatment
    (the design language reserves gold for achievements).

    Achievements are member-level facts, not Games, so only the global
    date-range filter applies — the same rule as ``rating_trend_series``.
    """
    start, end = _coerce_date(date_start), _coerce_date(date_end)

    entries = []
    for achievement in achievements:
        day = achievement.date
        if day is not None:
            if (start is not None and day < start) or (end is not None and day > end):
                continue
        description = achievement.title
        if achievement.event_name:
            description += f" — {achievement.event_name}"
        if achievement.detail:
            description += f" ({achievement.detail})"
        entries.append({
            "date": day.isoformat() if day is not None else "",
            "game_num": None,           # not a Game — the row shows a USCF badge
            "description": description,
            "kind": "uscf",
            "event_id": achievement.event_id,
        })
    return entries


def _split_camel_case(value: str) -> str:
    """'FourthCategory' → 'Fourth Category' (USCF's enum-style level names)."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value)


def _ordinal(n: int) -> str:
    """25 → '25th', 1 → '1st', 22 → '22nd' (career-win milestone wording)."""
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# The matching engine (issues #28 / #29)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GameMatch:
    """One Game ↔ USCF Game Record pairing."""

    chapter_url: str        # the Game's permanent identity (ADR 0001)
    record: UscfGameRecord
    matched_by: str         # "id" | "name"


@dataclass(frozen=True)
class MatchResult:
    """
    Everything the matching engine produced.

    Unmatched Games and unmatched USCF Game Records are exposed, never
    silently dropped — Reconciliation (issue #30) is built from them.
    """

    matches: tuple[GameMatch, ...] = ()
    unmatched_chapter_urls: tuple[str, ...] = ()   # Games with no USCF Game Record
    unmatched_records: tuple[UscfGameRecord, ...] = ()  # records with no Game

    def record_for(self, chapter_url: str) -> UscfGameRecord | None:
        """The USCF Game Record matched to *chapter_url*, or None."""
        return self._records_by_url().get(chapter_url)

    def _records_by_url(self) -> dict[str, UscfGameRecord]:
        # Built lazily once per MatchResult (frozen dataclass → cached via __dict__
        # is unavailable; a tuple this small makes rebuilding negligible, but the
        # single accessor keeps every consumer reading one index, not four).
        return {m.chapter_url: m.record for m in self.matches}


# Rating systems whose games are played over the board.  Online systems
# (OR/OQ/OB) never match chapters: the Study is OTB-only by design (PRD #24);
# online records surface in Reconciliation as skippable USCF-only items.
_OTB_RATING_SYSTEMS = ("R", "D")


# How far outside a Rated Event's official date range a chapter date may fall
# and still count as "inside the window" for name-fallback matching.  USCF's
# windows don't always contain the true play dates (monthly ladders).
_WINDOW_GRACE = pd.Timedelta(days=7)


def match_games(df: pd.DataFrame, records: list[UscfGameRecord]) -> MatchResult:
    """
    Match USCF Game Records to Games (issues #28 / #29).

    Primary pass — opponent USCF member ID + result.  Repeat opponents with
    identical results are disambiguated by color, then the Rated Event date
    window: tiebreakers only, never match requirements (color is itself a
    fact that can conflict between sources).

    Fallback pass — for chapters without a typed FideId only: normalized
    opponent name + result + the Rated Event date window.  Any ambiguity
    means no match (a guess could attach the wrong Rated Event); chapters
    whose typed ID matched nothing never fall back to names (a wrong ID is
    a discrepancy to surface, not to paper over).

    Unmatched Games and unmatched records are exposed in the result, never
    silently dropped.
    """
    matches: list[GameMatch] = []
    used: set[int] = set()

    matches.extend(_id_pass(df, records, used))
    matched_urls = {m.chapter_url for m in matches}
    name_matches = _name_pass(df, records, used, matched_urls=matched_urls)
    matches.extend(name_matches)
    matched_urls |= {m.chapter_url for m in name_matches}

    return MatchResult(
        matches=tuple(matches),
        unmatched_chapter_urls=tuple(
            url for url in df["ChapterURL"] if url and url not in matched_urls
        ) if not df.empty else (),
        unmatched_records=tuple(r for i, r in enumerate(records) if i not in used),
    )


def _id_pass(
    df: pd.DataFrame, records: list[UscfGameRecord], used: set[int]
) -> list[GameMatch]:
    """The primary matching pass: opponent USCF member ID + result."""
    games_by_key: dict[tuple[str, str], list[pd.Series]] = {}
    for _, game in df.iterrows():
        if not game["ChapterURL"]:
            continue  # no identity (ADR 0001) → nothing to attach a match to
        opponent_id = _opponent_id(game)
        if opponent_id:  # absence of data is not a key ('' never matches '')
            games_by_key.setdefault((opponent_id, game["Outcome"]), []).append(game)

    records_by_key: dict[tuple[str, str], list[int]] = {}
    for i, record in enumerate(records):
        if record.opponent_id and record.rating_system in _OTB_RATING_SYSTEMS:
            key = (record.opponent_id, record.player_outcome)
            records_by_key.setdefault(key, []).append(i)

    matches: list[GameMatch] = []
    for key, games in games_by_key.items():
        candidates = records_by_key.get(key, [])
        matches.extend(_pair_group(games, candidates, records, used, matched_by="id"))
    return matches


def _name_pass(
    df: pd.DataFrame,
    records: list[UscfGameRecord],
    used: set[int],
    matched_urls: set[str],
) -> list[GameMatch]:
    """
    The fallback matching pass (issue #29): normalized opponent name + result
    + Rated Event date window, for chapters without a typed FideId.

    Strictly unambiguous: a chapter matches only when exactly one record fits
    it AND no other chapter fits that record.  Everything else stays unmatched.
    """
    # Which records each eligible chapter could mean, and vice versa
    chapter_candidates: dict[str, list[int]] = {}
    record_claimants: dict[int, list[str]] = {}

    for _, game in df.iterrows():
        url = game["ChapterURL"]
        if not url or url in matched_urls or _opponent_id(game):
            continue
        for i, record in enumerate(records):
            if i in used or record.rating_system not in _OTB_RATING_SYSTEMS:
                continue
            if (_names_match(str(game["Opponent"]), record)
                    and record.player_outcome == game["Outcome"]
                    and _within_event_window(game["Date_dt"], record,
                                             grace=_WINDOW_GRACE)):
                chapter_candidates.setdefault(url, []).append(i)
                record_claimants.setdefault(i, []).append(url)

    matches: list[GameMatch] = []
    for url, candidates in chapter_candidates.items():
        if len(candidates) != 1 or len(record_claimants[candidates[0]]) != 1:
            continue  # ambiguity in either direction → no match, not a guess
        record_index = candidates[0]
        matches.append(GameMatch(url, records[record_index], "name"))
        used.add(record_index)
    return matches


def _names_match(chapter_opponent: str, record: UscfGameRecord) -> bool:
    """
    Whether a chapter's opponent name and a record's opponent are the same
    person, per the PRD's normalization rules: case- and punctuation-
    insensitive; first-name spelling variants tolerated only when the last
    name matches exactly ('Carter Clark' ↔ 'Carver Clark').
    """
    chapter_name = _normalize_name(chapter_opponent)
    record_name = _normalize_name(record.opponent_name)
    if not chapter_name or not record_name:
        return False
    if chapter_name == record_name:
        return True

    # Spelling-variant tolerance: exact last name + same first initial
    chapter_parts, record_parts = chapter_name.split(), record_name.split()
    return (chapter_parts[-1] == record_parts[-1]
            and chapter_parts[0][0] == record_parts[0][0])


def _normalize_name(name: str) -> str:
    """Lowercase, punctuation stripped, whitespace collapsed."""
    cleaned = re.sub(r"[^\w\s]", "", name.lower())
    return " ".join(cleaned.split())


def _pair_group(
    games: list[pd.Series],
    candidate_indices: list[int],
    records: list[UscfGameRecord],
    used: set[int],
    *,
    matched_by: str,
) -> list[GameMatch]:
    """
    Pair Games with candidate records that all share the same match key.

    Most groups are one Game ↔ one record.  When the same opponent was played
    more than once with the same result, the pairs that agree on color and the
    Rated Event date window win; leftovers on either side stay unmatched.
    """
    scored = sorted(
        (-_tiebreak_score(game, records[i]), game_order, record_order, i)
        for game_order, game in enumerate(games)
        for record_order, i in enumerate(candidate_indices)
        if i not in used
    )

    matches: list[GameMatch] = []
    taken_games: set[int] = set()
    for _neg_score, game_order, _record_order, i in scored:
        if game_order in taken_games or i in used:
            continue
        matches.append(GameMatch(games[game_order]["ChapterURL"], records[i], matched_by))
        taken_games.add(game_order)
        used.add(i)
    return matches


def _tiebreak_score(game: pd.Series, record: UscfGameRecord) -> int:
    """How well a candidate record agrees with a Game on the tiebreak facts.

    Color outranks the date window: USCF event windows routinely fail to
    contain the true play date (monthly ladders), while a same-color record
    of the same result against the same opponent is almost always the game."""
    score = 0
    if record.player_color == game["Color"]:
        score += 2
    if _within_event_window(game["Date_dt"], record):
        score += 1
    return score


def _within_event_window(date_dt, record: UscfGameRecord, grace=None) -> bool:
    """True when the Game's (authoritative) date falls inside the record's
    Rated Event date range, optionally widened by *grace* on both sides."""
    if pd.isna(date_dt) or record.event_start is None or record.event_end is None:
        return False
    played = date_dt.date()
    start, end = record.event_start, record.event_end
    if grace is not None:
        start, end = start - grace, end + grace
    return start <= played <= end


def _opponent_id(game: pd.Series) -> str:
    """The opponent's USCF member ID from a Game row ('' when not typed)."""
    return str(game["BlackID"] if game["Color"] == "White" else game["WhiteID"])


# ---------------------------------------------------------------------------
# Enrichment (issue #28): matched Games gain their USCF facts as columns
# ---------------------------------------------------------------------------

# Enrichment columns and their unmatched-Game values.  Always present after
# enrich_games so pages never have to check whether a column exists.
_ENRICHMENT_DEFAULTS = {
    "UscfMatched": False,
    "UscfMatchedBy": "",
    "UscfEventId": "",        # the Rated Event's USCF ID (issue #33)
    "UscfEventName": "",
    "UscfSection": "",
    "UscfRatingSystem": "",
    "UscfOpponentName": "",
    "UscfOpponentId": "",
    "UscfColor": "",          # the member's color according to USCF
    "UscfColorConflict": False,
    "Forfeit": False,
}

# "At most one move" (CONTEXT.md / issue #29): the threshold below which an
# unmatched Game is a Forfeit rather than a game USCF hasn't rated yet.
_FORFEIT_MAX_MOVES = 1


def enrich_games(df: pd.DataFrame, result: MatchResult) -> pd.DataFrame:
    """
    Return a copy of *df* with USCF enrichment columns (issues #28 / #29).

    Match & enrich (PRD #24): the Game stays the central entity; its USCF Game
    Record's facts ride along as columns.  Unmatched Games get the defaults —
    enrichment never filters, hides, or restructures Games (ADR 0003).

    Forfeit detection (issue #29): an unmatched Game with at most one move is
    a Forfeit — the opponent never showed, so USCF correctly never rated it.

    Conflict flagging (issue #30): a matched Game whose color disagrees with
    USCF's record keeps displaying the Lichess version, with UscfColorConflict
    set so the UI can badge it.
    """
    enriched = df.copy()
    if enriched.empty:
        return enriched

    for column, default in _ENRICHMENT_DEFAULTS.items():
        enriched[column] = default

    facts_by_url = {
        m.chapter_url: {
            "UscfMatched": True,
            "UscfMatchedBy": m.matched_by,
            "UscfEventId": m.record.event_id,
            "UscfEventName": m.record.event_name,
            "UscfSection": m.record.section_name,
            "UscfRatingSystem": m.record.rating_system,
            "UscfOpponentName": m.record.opponent_name,
            "UscfOpponentId": m.record.opponent_id,
            "UscfColor": m.record.player_color,
        }
        for m in result.matches
    }
    for index, url in enriched["ChapterURL"].items():
        facts = facts_by_url.get(url)
        if facts:
            for column, value in facts.items():
                enriched.loc[index, column] = value

    enriched["Forfeit"] = (
        ~enriched["UscfMatched"] & (enriched["FullMoves"] <= _FORFEIT_MAX_MOVES)
    )
    # Disagreement between sources is flagged, never silently resolved (#30).
    # A record missing its color ('') is absence of data, not a disagreement.
    enriched["UscfColorConflict"] = (
        enriched["UscfMatched"]
        & (enriched["UscfColor"] != "")
        & (enriched["UscfColor"] != enriched["Color"])
    )
    return enriched


# ---------------------------------------------------------------------------
# The rating lens (issue #32)
# ---------------------------------------------------------------------------

# The two lenses — also the values of the UI's [Official | Live] toggle.
OFFICIAL_LENS = "official"
LIVE_LENS = "live"


def apply_rating_lens(
    df: pd.DataFrame,
    lens: str,
    official_series: list[OfficialRatingPoint],
    live_series: list[LiveRatingPoint],
    match_result: MatchResult,
    standings: dict[tuple[str, str], list[StandingEntry]] | None = None,
) -> pd.DataFrame:
    """
    Return a copy of *df* whose rating columns reflect the lens basis, so
    every rating-derived stat downstream follows the lens without knowing it
    exists.

    Official — the supplement in effect at the matched Rated Event's start
    date (Daniel's long-standing convention); a Game with no USCF Game Record
    uses the supplement at its own date; Games before the first supplement
    have no value — never invented.  Opponent ratings are the typed
    pairing-sheet values (PRD #24).

    Live — the matched Section's pre-rating, rounded to a whole number;
    a Game with no matched Section falls back to the Official basis.
    Opponent ratings come from crosstable pre-ratings where cached
    (issue #35), falling back to typed values — so rating-diff and upsets
    are fully consistent with the displayed ratings.

    The lens never hides Games: only the rating columns (and the RatingDiff
    derived from them) change.

    With no USCF data at all (both series empty — USCF unreachable and never
    cached, ADR 0003), *df* is returned unchanged: typed values are all there
    is, and wiping them would turn an outage into data loss.
    """
    if df.empty or (not official_series and not live_series):
        return df

    out = df.copy()
    live_by_section = {(p.event_id, p.section_name): p.pre for p in live_series}
    # Built once, not via record_for() per row — get_filtered runs this for
    # every filter-driven callback, so per-row dict rebuilding multiplies fast.
    records_by_url = {m.chapter_url: m.record for m in match_result.matches}
    # The opponents' crosstable pre-ratings (issue #35), one flat lookup:
    # (event_id, section_name, member_id) → pre-rating
    opponent_pre = {
        (event_id, section_name, entry.member_id): entry.pre_rating
        for (event_id, section_name), entries in (standings or {}).items()
        for entry in entries
    } if lens == LIVE_LENS else {}

    values = []
    opponent_values: list[float | int | None] = []
    for _, game in out.iterrows():
        record = records_by_url.get(game["ChapterURL"])
        official_value = _official_basis(record, game, official_series)
        if lens == LIVE_LENS:
            values.append(_live_basis(record, live_by_section,
                                      fallback=official_value))
            opponent_values.append(_opponent_live_basis(record, game, opponent_pre))
        else:
            values.append(official_value)

    out["PlayerRatingNum"] = pd.to_numeric(
        pd.Series(values, index=out.index, dtype="object"), errors="coerce",
    )
    out["PlayerRating"] = [_rating_display(v) for v in values]

    if lens == LIVE_LENS and opponent_pre:
        out["OpponentRatingNum"] = pd.to_numeric(
            pd.Series(opponent_values, index=out.index, dtype="object"),
            errors="coerce",
        )
        out["OpponentRating"] = [_rating_display(v) for v in opponent_values]

    # The diff always compares the two final columns — whatever basis each came
    # from — so it agrees with the ratings displayed beside it.
    out["RatingDiff"] = out["OpponentRatingNum"] - out["PlayerRatingNum"]
    return out


def _opponent_live_basis(
    record: UscfGameRecord | None,
    game: pd.Series,
    opponent_pre: dict[tuple[str, str, str], float | None],
) -> float | int | None:
    """
    An opponent's Live Rating: their crosstable pre-rating for the Section the
    Game was played in, rounded (issue #35) — or the typed value when no
    crosstable is cached / the opponent isn't in it.
    """
    if record is not None:
        key = (record.event_id, record.section_name, record.opponent_id)
        pre = opponent_pre.get(key)
        if pre is not None:
            return round(pre)
    typed = game["OpponentRatingNum"]
    return None if pd.isna(typed) else typed


def _live_basis(
    record: UscfGameRecord | None,
    live_by_section: dict[tuple[str, str], float | None],
    fallback: int | None,
) -> float | int | None:
    """
    A Game's Live Rating: its matched Section's pre-rating, rounded to a
    whole number (ratings display without decimal places; the chain itself
    keeps its decimals in the series data).

    A Game with no matched Section can't have one → the Official basis.
    A matched Section whose pre is None means Daniel was unrated walking in —
    honestly no value, never the fallback.
    """
    if record is None:
        return fallback
    key = (record.event_id, record.section_name)
    if key not in live_by_section:
        return fallback
    pre = live_by_section[key]
    return None if pre is None else round(pre)


def _official_basis(
    record: UscfGameRecord | None,
    game: pd.Series,
    official_series: list[OfficialRatingPoint],
) -> int | None:
    """
    A Game's Official Rating: the supplement in effect at its Rated Event's
    start date (Daniel's convention) — or, for a Game with no USCF Game
    Record, at the Game's own date.  None when neither gives a basis
    (the pre-supplement era) — never invented.
    """
    if record is not None and record.event_start is not None:
        basis_date = record.event_start
    elif pd.notna(game["Date_dt"]):
        basis_date = game["Date_dt"].date()
    else:
        return None
    return _supplement_in_effect(official_series, basis_date)


def _supplement_in_effect(
    official_series: list[OfficialRatingPoint], on_date: date
) -> int | None:
    """The Official Rating in effect on a date: the latest supplement published
    on or before it.  None before the first supplement — never invented."""
    in_effect = None
    for point in official_series:  # chronological
        if point.month > on_date:
            break
        in_effect = point.rating
    return in_effect


def _rating_display(value: int | float | None) -> str:
    """A lens value as the display string the rating columns carry — always a
    whole number (Daniel's display rule), never '1047.0'."""
    if value is None:
        return ""
    if isinstance(value, float):
        return "" if pd.isna(value) else f"{value:.0f}"
    return str(value)


# ---------------------------------------------------------------------------
# Reconciliation (issue #30)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReconciliationEntry:
    """
    One disagreement between the Studies and USCF (see CONTEXT.md):
    what each source says, side by side, plus where to go to fix it.
    """

    entry_id: str       # stable identity — what a dismissal remembers
    kind: str           # "conflict" | "uscf_only" | "lichess_only"
    #                     | "missing_fide_id" | "rating_mismatch"
    opponent: str       # who the game was against
    date: str           # when (the chapter date, or the Rated Event range)
    lichess_says: str   # the Study's version ('' when there is no chapter)
    uscf_says: str      # USCF's version ('' when there is no record)
    chapter_url: str    # the fix-on-Lichess target ('' when there is no chapter)


def reconcile(
    df: pd.DataFrame,
    result: MatchResult,
    official_series: list[OfficialRatingPoint],
    *,
    dismissed: frozenset[str] | set[str] = frozenset(),
) -> list[ReconciliationEntry]:
    """
    Every disagreement between the Studies and USCF (issue #30), as actionable
    entries.  *df* is the enriched Games DataFrame (enrich_games output).

    Dismissed entries (their entry_ids in *dismissed*) are excluded — they are
    disagreements Daniel has already judged ("USCF is wrong" / "intentionally
    skipped").
    """
    if df.empty:
        return []

    entries: list[ReconciliationEntry] = []
    entries.extend(_conflict_entries(df, result))
    entries.extend(_uscf_only_entries(result))
    entries.extend(_lichess_only_entries(df, result))
    entries.extend(_missing_fide_id_entries(df, result))
    entries.extend(_rating_mismatch_entries(df, result, official_series))

    return [e for e in entries if e.entry_id not in dismissed]


def _conflict_entries(
    df: pd.DataFrame, result: MatchResult
) -> list[ReconciliationEntry]:
    """Matched Games whose facts disagree: the chapter says one color, USCF
    says the other.  The dashboard displays the Lichess version; the conflict
    is flagged here with both versions."""
    entries = []
    for _, game in df[df["UscfColorConflict"]].iterrows():
        record = result.record_for(game["ChapterURL"])
        if record is None:
            continue  # a conflict flag without a match cannot happen, but never crash
        entries.append(ReconciliationEntry(
            entry_id=f"conflict:{game['ChapterURL']}",
            kind="conflict",
            opponent=str(game["Opponent"]),
            date=str(game["Date"]),
            lichess_says=f"You played {game['Color']} ({game['Outcome']})",
            uscf_says=(f"You played {record.player_color} "
                       f"({record.player_outcome}) — {record.event_name}"),
            chapter_url=str(game["ChapterURL"]),
        ))
    return entries


def _uscf_only_entries(result: MatchResult) -> list[ReconciliationEntry]:
    """USCF Game Records with no Game: either a Chapter Daniel forgot to add,
    or one he skips on purpose (online-rated games) — his call via Dismiss."""
    entries = []
    seen_ids: dict[str, int] = {}
    for record in result.unmatched_records:
        event_dates = (f"{record.event_start} – {record.event_end}"
                       if record.event_start and record.event_end else "")
        system = record.rating_system
        entry_id = (f"uscf-only:{record.event_id}:{record.opponent_id}:"
                    f"{record.player_color}:{record.player_outcome}")
        # Identical records (a double round-robin: same opponent, same event,
        # same color, same result twice) still get distinct ids — dismissing
        # one must never dismiss the other.
        occurrence = seen_ids.get(entry_id, 0)
        seen_ids[entry_id] = occurrence + 1
        if occurrence:
            entry_id += f":{occurrence + 1}"
        entries.append(ReconciliationEntry(
            entry_id=entry_id,
            kind="uscf_only",
            opponent=record.opponent_name,
            date=event_dates,
            lichess_says="",
            uscf_says=(f"{record.player_outcome} with {record.player_color} — "
                       f"{record.event_name}, {record.section_name} ({system})"),
            chapter_url="",
        ))
    return entries


def _lichess_only_entries(
    df: pd.DataFrame, result: MatchResult
) -> list[ReconciliationEntry]:
    """Games with no USCF Game Record that are not Forfeits: USCF hasn't rated
    them (yet), or rated them in a way matching couldn't find."""
    unmatched = set(result.unmatched_chapter_urls)
    entries = []
    games = df[df["ChapterURL"].isin(unmatched) & ~df["Forfeit"]]
    for _, game in games.iterrows():
        entries.append(ReconciliationEntry(
            entry_id=f"lichess-only:{game['ChapterURL']}",
            kind="lichess_only",
            opponent=str(game["Opponent"]),
            date=str(game["Date"]),
            lichess_says=(f"{game['Outcome']} with {game['Color']} — "
                          f"{game['Event']}"),
            uscf_says="",
            chapter_url=str(game["ChapterURL"]),
        ))
    return entries


def _missing_fide_id_entries(
    df: pd.DataFrame, result: MatchResult
) -> list[ReconciliationEntry]:
    """Chapters without a typed opponent FideId — listed (even when the name
    fallback matched them) so Daniel can add the ID and make the match robust."""
    entries = []
    for _, game in df.iterrows():
        if not game["ChapterURL"] or _opponent_id(game):
            continue
        record = result.record_for(game["ChapterURL"])
        uscf_says = (
            f"USCF knows this opponent as {record.opponent_name} "
            f"(#{record.opponent_id}) — type that ID into the chapter"
            if record is not None else ""
        )
        entries.append(ReconciliationEntry(
            entry_id=f"missing-fide-id:{game['ChapterURL']}",
            kind="missing_fide_id",
            opponent=str(game["Opponent"]),
            date=str(game["Date"]),
            lichess_says="No opponent FideId typed in this chapter",
            uscf_says=uscf_says,
            chapter_url=str(game["ChapterURL"]),
        ))
    return entries


def _rating_mismatch_entries(
    df: pd.DataFrame,
    result: MatchResult,
    official_series: list[OfficialRatingPoint],
) -> list[ReconciliationEntry]:
    """Typed header ratings that disagree with the Official Rating for the
    matched Rated Event's start month.  Typed values are validation-only —
    they power no stats — so this is bookkeeping, not a data problem."""
    # Keyed by (year, month): a supplement covers its month whatever day it
    # carries, and the lookup must never miss on a date quirk.
    official_by_month = {
        (point.month.year, point.month.month): point.rating
        for point in official_series
    }
    games_by_url = {game["ChapterURL"]: game for _, game in df.iterrows()}

    entries = []
    for match in result.matches:
        game = games_by_url.get(match.chapter_url)
        start = match.record.event_start
        if game is None or start is None or pd.isna(game["PlayerRatingNum"]):
            continue
        official = official_by_month.get((start.year, start.month))
        typed = int(game["PlayerRatingNum"])
        if official is None or typed == official:
            continue
        entries.append(ReconciliationEntry(
            entry_id=f"rating-mismatch:{match.chapter_url}",
            kind="rating_mismatch",
            opponent=str(game["Opponent"]),
            date=str(game["Date"]),
            lichess_says=f"Typed rating {typed}",
            uscf_says=(f"Official Rating for {start:%B %Y}: {official} "
                       f"({match.record.event_name})"),
            chapter_url=str(match.chapter_url),
        ))
    return entries


def membership_alert(profile: UscfProfile, *, today: date) -> str | None:
    """
    The membership warning for the profile card, or None when all is well.

    Warns when the membership has lapsed or expires within 90 days — so a
    lapsed membership is discovered at home, not at the tournament hall.
    """
    expires = profile.membership_expires
    if expires is None:
        return None

    days_left = (expires - today).days
    if days_left < 0:
        return f"Membership lapsed on {expires.isoformat()} — renew before your next rated event."
    if days_left <= _EXPIRATION_WARNING_DAYS:
        return f"Membership expires in {days_left} days ({expires.isoformat()})."
    return None


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO 'YYYY-MM-DD' string, or None when absent/malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None
