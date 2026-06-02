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
build_game_records      raw game items → typed USCF Game Records.
match_games             USCF Game Records ↔ Games (the matching engine).
enrich_games            Games df + MatchResult → df with USCF enrichment columns.
reconcile               Every disagreement between the Studies and USCF.
UscfProfile             Who the member is according to USCF.
UscfRating              One rating system's entry (rating, provisional, floor).
OfficialRatingPoint     One supplement month's Official Rating.
LiveRatingPoint         One Section's pre→post Live Rating change.
UscfGameRecord          USCF's official record of one rated game (CONTEXT.md).
GameMatch               One Game ↔ USCF Game Record pairing.
MatchResult             Everything matching produced: matches + both leftovers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

__all__ = [
    "GameMatch",
    "LiveRatingPoint",
    "MatchResult",
    "OfficialRatingPoint",
    "ReconciliationEntry",
    "UscfGameRecord",
    "UscfProfile",
    "UscfRating",
    "build_game_records",
    "build_live_series",
    "build_official_series",
    "enrich_games",
    "match_games",
    "membership_alert",
    "parse_member_profile",
    "reconcile",
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
    opponent_name: str        # as USCF registers it ("JOHN FONTAINE", "Wade Robertson")


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

    matches: tuple[GameMatch, ...]
    unmatched_chapter_urls: tuple[str, ...]    # Games with no USCF Game Record
    unmatched_records: tuple[UscfGameRecord, ...]  # USCF Game Records with no Game


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
    matches.extend(_name_pass(df, records, used,
                              matched_urls={m.chapter_url for m in matches}))

    matched_urls = {m.chapter_url for m in matches}
    return MatchResult(
        matches=tuple(matches),
        unmatched_chapter_urls=tuple(
            url for url in df["ChapterURL"] if url not in matched_urls
        ) if not df.empty else (),
        unmatched_records=tuple(r for i, r in enumerate(records) if i not in used),
    )


def _id_pass(
    df: pd.DataFrame, records: list[UscfGameRecord], used: set[int]
) -> list[GameMatch]:
    """The primary matching pass: opponent USCF member ID + result."""
    games_by_key: dict[tuple[str, str], list[pd.Series]] = {}
    for _, game in df.iterrows():
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
        if url in matched_urls or _opponent_id(game):
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
    name matches exactly ('Kaiser Kaiyrberli' ↔ 'Kaisar Kaiyrberli').
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
    # Disagreement between sources is flagged, never silently resolved (#30)
    enriched["UscfColorConflict"] = (
        enriched["UscfMatched"] & (enriched["UscfColor"] != enriched["Color"])
    )
    return enriched


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
    records_by_url = {m.chapter_url: m.record for m in result.matches}
    entries = []
    for _, game in df[df["UscfColorConflict"]].iterrows():
        record = records_by_url[game["ChapterURL"]]
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
    for record in result.unmatched_records:
        event_dates = (f"{record.event_start} – {record.event_end}"
                       if record.event_start and record.event_end else "")
        system = record.rating_system
        entries.append(ReconciliationEntry(
            entry_id=(f"uscf-only:{record.event_id}:{record.opponent_id}:"
                      f"{record.player_color}"),
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
    records_by_url = {m.chapter_url: m.record for m in result.matches}
    entries = []
    for _, game in df.iterrows():
        if _opponent_id(game):
            continue
        record = records_by_url.get(game["ChapterURL"])
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
    official_by_month = {point.month: point.rating for point in official_series}
    games_by_url = {game["ChapterURL"]: game for _, game in df.iterrows()}

    entries = []
    for match in result.matches:
        game = games_by_url.get(match.chapter_url)
        start = match.record.event_start
        if game is None or start is None or pd.isna(game["PlayerRatingNum"]):
            continue
        official = official_by_month.get(date(start.year, start.month, 1))
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
