"""
sync.py
=======
Sync orchestrator: designated Lichess Studies → one merged Games DataFrame,
plus the member's USCF record as enrichment.

A Sync (see CONTEXT.md) fetches every designated Study, concatenates their
Games, dedupes by ChapterURL (the permanent Game identity — ADR 0001), and
sorts by date.  One Study failing never loses the Games of the Studies that
succeeded; only when *every* Study fails is the Sync itself a failure.

The USCF half is different (ADR 0003): a Sync that reaches Lichess but not
USCF still *succeeds*.  ``sync_uscf`` therefore never raises — it returns a
result that says whether USCF data is available and why not.

A successful Sync also refreshes a local PGN cache so the dashboard can boot
when Lichess is unreachable.  The cache is disposable and never a source of
truth (ADR 0001); a host without a writable disk just goes without it.

Public API
----------
sync_studies      Fetch + merge all designated Studies → SyncResult.
sync_uscf         Fetch the USCF record → UscfSyncResult (never raises).
detect_new_games  Which Games of a Sync are new vs. the previous one.
load_from_cache   Parse the PGN cache of the last successful Sync.
SyncResult        The outcome: merged df, player, per-Study failures.
UscfSyncResult    The USCF outcome: profile, freshness, failure reason.
SyncError         Raised when no designated Study could be fetched.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from coach_match_core import CoachMatchResult, match_coach_study
from lichess_client import LichessError, LichessRateLimitedError, fetch_study_pgn
from pgn_stats_core import load_games_from_text
from uscf_client import (
    UscfError,
    fetch_event_standings,
    fetch_member_awards,
    fetch_member_events,
    fetch_member_games,
    fetch_member_norms,
    fetch_member_profile,
    fetch_member_sections,
    fetch_rating_supplements,
)
from uscf_core import (
    LiveRatingPoint,
    OfficialRatingPoint,
    StandingEntry,
    UscfAchievement,
    UscfEvent,
    UscfGameRecord,
    UscfProfile,
    build_achievements,
    build_game_records,
    build_live_series,
    build_member_events,
    build_official_series,
    build_standings,
    parse_member_profile,
)

logger = logging.getLogger(__name__)

# Serializes UscfCache writes to the same file across live instances so a
# read-union-write of the append-only user state stays atomic (issue #87 [8]).
_CACHE_WRITE_LOCK = threading.Lock()

__all__ = [
    "CoachSyncResult",
    "SyncError",
    "SyncResult",
    "UscfCache",
    "UscfSyncResult",
    "detect_new_games",
    "load_from_cache",
    "sync_coach",
    "sync_studies",
    "sync_uscf",
]


class SyncError(Exception):
    """No designated Study could be fetched — there is nothing to show."""


@dataclass
class SyncResult:
    """The outcome of a Sync across all designated Studies."""

    df: pd.DataFrame
    player: str
    # (study_id, reason) for each Study that could not be fetched
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        """True if some (but not all) Studies failed to fetch."""
        return bool(self.failures)


def sync_studies(
    study_ids: list[str],
    player_name: str | None = None,
    token: str | None = None,
    cache_path: str | None = None,
) -> SyncResult:
    """
    Sync every designated Study and return the merged, deduped, date-sorted
    Games.

    When *cache_path* is given, a successful Sync also (over)writes the PGN
    cache there.  A failed cache write is logged and ignored — the cache is
    an offline fallback, never a requirement.

    Raises
    ------
    SyncError : every Study failed to fetch (the per-Study reasons are in
                the exception message).
    """
    pgn_texts, failures = _fetch_all_pgns(study_ids, token, "Study")

    if not pgn_texts:
        details = "; ".join(f"{sid}: {reason}" for sid, reason in failures)
        raise SyncError(f"No designated Study could be fetched. {details}")

    merged_pgn = "\n\n".join(pgn_texts)
    df, player = load_games_from_text(merged_pgn, player_name=player_name)
    df = _dedupe_and_sort(df)

    if failures:
        logger.warning(
            "Partial Sync: %d of %d Studies failed (%s); showing %d Games "
            "from the Studies that succeeded.",
            len(failures), len(study_ids),
            ", ".join(sid for sid, _ in failures), len(df),
        )

    # Only refresh the boot fallback when this Sync actually produced games —
    # a reachable-but-gameless Sync must not clobber the last good cache, or an
    # offline boot afterwards would fail with no games to load.
    if cache_path and not df.empty:
        _write_cache(cache_path, merged_pgn)

    return SyncResult(df=df, player=player, failures=failures)


def _fetch_all_pgns(
    study_ids: list[str], token: str | None, what: str
) -> tuple[list[str], list[tuple[str, str]]]:
    """Fetch each Study's PGN, degrading per Study (one failure never loses the
    rest).  A 429 aborts the loop: the remaining Studies are marked rate-limited
    rather than fired into the same window (Lichess may block the token)."""
    pgn_texts: list[str] = []
    failures: list[tuple[str, str]] = []
    for i, study_id in enumerate(study_ids):
        try:
            pgn_texts.append(fetch_study_pgn(study_id, token=token))
        except LichessRateLimitedError as exc:
            logger.warning("Rate-limited on %s %r — aborting remaining fetches: %s",
                           what, study_id, exc)
            failures.append((study_id, str(exc)))
            failures.extend((sid, "skipped: rate-limited") for sid in study_ids[i + 1:])
            break
        except LichessError as exc:
            logger.warning("Could not fetch %s %r: %s", what, study_id, exc)
            failures.append((study_id, str(exc)))
    return pgn_texts, failures


# ---------------------------------------------------------------------------
# The USCF half of a Sync (ADR 0003: enrichment, never a dependency)
# ---------------------------------------------------------------------------

class UscfCache:
    """
    The local cache of raw USCF API responses (issue #26).

    Like the PGN cache: a disposable local JSON file, never a source of truth
    (ADR 0003).  Every filesystem misfortune — missing file, corrupt file,
    unwritable disk — degrades to "no cache", never to an error.

    Five kinds of entries:

    * **current** — the member's current state (profile, …).  Overwritten as
      a whole on every successful Sync; ``fetched_at()`` says when.
    * **immutable** — USCF data that can never change once written (rated
      crosstables, past supplements).  Stored once, then served from the
      cache forever — ``fetch_immutable`` never re-fetches them.
    * **aged** — data that changes slowly (opponent current ratings —
      issue #35).  Served from the cache within a freshness window,
      re-fetched only after it; never touched by ``replace_current``.
    * **dismissals** — Reconciliation entries Daniel has judged (issue #30).
      User state, not API responses: never touched by ``replace_current``.
    * **seen achievements** — which norms/awards previous Syncs have already
      seen (issue #36), so a fresh one is celebrated exactly once.  Like
      dismissals: bookkeeping, never touched by ``replace_current``.
    """

    def __init__(self, path: str | None):
        self._path = path
        self._data: dict[str, Any] = self._read()

    # -- current entries (refreshed every Sync) -----------------------------

    def get_current(self, key: str) -> Any | None:
        """A current-state entry from the last successful Sync, or None."""
        return self._data.get("current", {}).get(key)

    def replace_current(self, entries: dict[str, Any]) -> None:
        """Overwrite all current-state entries (a successful Sync's results)."""
        self._data["current"] = entries
        self._data["fetched_at"] = datetime.now(timezone.utc).isoformat()
        self._write()

    def fetched_at(self) -> datetime | None:
        """When the current entries were written (UTC), or None if never."""
        stamp = self._data.get("fetched_at")
        if not stamp:
            return None
        try:
            return datetime.fromisoformat(stamp)
        except ValueError:
            return None

    # -- immutable entries (never re-fetched once stored) -------------------

    def fetch_immutable(self, key: str, fetcher) -> Any:
        """
        The immutable entry for *key*, fetching it only the first time.

        Once stored, *fetcher* is never called again for this key — immutable
        USCF data (rated crosstables, past supplements) cannot change, so a
        cache hit is always correct and saves a call to an API we were not
        invited to use.
        """
        immutable = self._data.setdefault("immutable", {})
        if key in immutable:
            return immutable[key]
        value = fetcher()
        immutable[key] = value
        self._write()
        return value

    def get_immutable(self, key: str) -> Any | None:
        """The immutable entry for *key* if already stored, else None — never
        fetches.  The degraded path (USCF down) reads crosstables this way."""
        return self._data.get("immutable", {}).get(key)

    # -- aged entries (refreshed only past a freshness window) ---------------

    def fetch_aged(self, key: str, fetcher, *, max_age: timedelta) -> Any:
        """
        The aged entry for *key*, re-fetching only when it is older than
        *max_age* (issue #35: opponent current ratings refresh at most weekly).

        A fetch failure propagates — callers decide whether stale data beats
        nothing (``get_aged``).
        """
        aged = self._data.setdefault("aged", {})
        entry = aged.get(key)
        now = datetime.now(timezone.utc)
        if entry is not None:
            try:
                fetched_at = datetime.fromisoformat(entry["fetched_at"])
                if now - fetched_at <= max_age:
                    return entry["value"]
            except (KeyError, TypeError, ValueError):
                pass  # malformed entry → treat as stale

        value = fetcher()
        aged[key] = {"value": value, "fetched_at": now.isoformat()}
        self._write()
        return value

    def get_aged(self, key: str) -> Any | None:
        """The aged entry for *key* at any age, or None — never fetches.
        Stale data beats nothing when USCF is unreachable."""
        entry = self._data.get("aged", {}).get(key)
        return entry.get("value") if isinstance(entry, dict) else None

    # -- dismissals (user judgements — survive every Sync) -------------------

    def dismissals(self) -> list[str]:
        """Entry IDs of dismissed Reconciliation entries (issue #30)."""
        return list(self._data.get("dismissals", []))

    def add_dismissal(self, entry_id: str) -> None:
        """
        Remember that *entry_id* was dismissed ("USCF is wrong" /
        "intentionally skipped").  Best-effort persistence: on a host without
        a writable disk the dismissal lasts for this run only.
        """
        dismissals = self._data.setdefault("dismissals", [])
        if entry_id not in dismissals:
            dismissals.append(entry_id)
            self._write()

    # -- seen achievements (celebration bookkeeping — issue #36) -------------

    def seen_achievements(self) -> list[str] | None:
        """
        Achievement IDs every previous Sync has seen, or None if never recorded.

        The None/[] distinction matters: None means this is the first Sync
        that knows about achievements (record them silently — celebrating
        months-old norms would be noise); [] means the member verifiably had
        none, so their next achievement is genuinely new.
        """
        seen = self._data.get("seen_achievements")
        return list(seen) if seen is not None else None

    def record_achievements(self, achievement_ids: list[str]) -> None:
        """Remember these achievement IDs as seen.  Best-effort persistence,
        like dismissals."""
        self._data["seen_achievements"] = list(achievement_ids)
        self._write()

    # -- file I/O (failures degrade, never raise) ----------------------------

    def _read(self) -> dict[str, Any]:
        if not self._path or not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read USCF cache %r (starting empty): %s",
                           self._path, exc)
            return {}
        if not isinstance(data, dict):
            return {}
        # A right-top-level / wrong-nested-type file (what a truncated or
        # hand-edited JSON produces) must still degrade to "no cache", never
        # raise AttributeError from an accessor: drop any misshaped section.
        for section in ("current", "immutable", "aged"):
            if not isinstance(data.get(section), dict):
                data.pop(section, None)
        for section in ("dismissals", "seen_achievements"):
            if section in data and not isinstance(data[section], list):
                data.pop(section, None)
        return data

    def _write(self) -> None:
        if not self._path:
            return
        with _CACHE_WRITE_LOCK:
            # Union the append-only user state (dismissals, seen achievements)
            # with whatever is on disk: another live instance of this file can
            # write mid-Sync, and a stale instance must not clobber it.
            # (ponytail: the deployed single worker serializes requests — ADR
            # 0006 — so this only bites on the threaded dev server; the union
            # keeps user judgement correct there.)
            self._merge_user_state()
            try:
                tmp_path = f"{self._path}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f)
                os.replace(tmp_path, self._path)
            except OSError as exc:
                logger.warning("Could not write USCF cache %r (continuing "
                               "without): %s", self._path, exc)

    def _merge_user_state(self) -> None:
        """Fold on-disk dismissals / seen achievements into this instance's copy
        before writing, so a concurrent write is never lost.  Both sections are
        append-only sets, so a union is always correct regardless of order."""
        on_disk = self._read()
        disk_dismissals = on_disk.get("dismissals", [])
        if disk_dismissals or "dismissals" in self._data:
            merged = list(self._data.get("dismissals", []))
            merged.extend(d for d in disk_dismissals if d not in merged)
            self._data["dismissals"] = merged
        disk_seen = on_disk.get("seen_achievements")
        mem_seen = self._data.get("seen_achievements")
        # None means "never recorded" — keep it absent; only merge once either
        # side has verifiably recorded a set (issue #36's None/[] distinction).
        if disk_seen is not None or mem_seen is not None:
            merged_seen = list(mem_seen or [])
            merged_seen.extend(a for a in (disk_seen or []) if a not in merged_seen)
            self._data["seen_achievements"] = merged_seen


@dataclass
class UscfSyncResult:
    """The outcome of the USCF half of a Sync — never required for success."""

    profile: UscfProfile | None = None
    # The Official Rating series (one point per supplement month) — issue #27
    official_series: list[OfficialRatingPoint] = field(default_factory=list)
    # The Live Rating series (one point per Regular-rated Section) — issue #27
    live_series: list[LiveRatingPoint] = field(default_factory=list)
    # Every USCF Game Record — the matching engine's input (issue #28)
    game_records: list[UscfGameRecord] = field(default_factory=list)
    # Every Rated Event entered — the Events page's grouping data (issue #33)
    member_events: list[UscfEvent] = field(default_factory=list)
    # Crosstables of played OTB Sections, keyed by (event_id, section name) —
    # standings, placements, and real round numbers (issue #34)
    standings: dict[tuple[str, str], list[StandingEntry]] = field(default_factory=dict)
    # Opponents' current profiles, keyed by member ID — the "they're 1580 now"
    # half of the then-vs-now insight (issue #35)
    opponent_profiles: dict[str, UscfProfile] = field(default_factory=dict)
    # Official achievements: norms and awards, chronological (issue #36)
    achievements: list[UscfAchievement] = field(default_factory=list)
    # When USCF was last successfully reached: the fetch time for live data,
    # the cached data's age when degraded (None if USCF has never been reached)
    synced_at: datetime | None = None
    # Why USCF is unavailable ('' when it isn't)
    failure: str = ""
    # True when the data shown is the previous successful Sync's cache
    from_cache: bool = False

    @property
    def available(self) -> bool:
        """True when USCF data (live or cached) is available to show."""
        return self.profile is not None


def sync_uscf(member_id: str, cache_path: str | None = None) -> UscfSyncResult:
    """
    Fetch the USCF record for *member_id*: profile, rating supplements
    (the Official series), sections (the Live series), and USCF Game Records
    (the matching engine's input).

    Never raises: USCF data is enrichment, never a dependency (ADR 0003).
    A successful fetch refreshes the local cache at *cache_path*; any failure
    falls back to that cache as a whole — partial USCF data would be
    inconsistent, so the cached snapshot wins over a half-fresh one.
    """
    cache = UscfCache(cache_path)

    try:
        raw_profile = fetch_member_profile(member_id)
        raw_supplements = fetch_rating_supplements(member_id)
        raw_sections = fetch_member_sections(member_id)
        raw_games = fetch_member_games(member_id)
        raw_events = fetch_member_events(member_id)
        raw_norms = fetch_member_norms(member_id)
        raw_awards = fetch_member_awards(member_id)
    except UscfError as exc:
        logger.warning("USCF unavailable — continuing without it (ADR 0003): %s", exc)
        return _uscf_from_cache(cache, failure=str(exc))

    cache.replace_current({
        "profile": raw_profile,
        "supplements": raw_supplements,
        "sections": raw_sections,
        "games": raw_games,
        "events": raw_events,
        "norms": raw_norms,
        "awards": raw_awards,
    })
    game_records = build_game_records(raw_games)
    return UscfSyncResult(
        profile=parse_member_profile(raw_profile),
        official_series=build_official_series(raw_supplements),
        live_series=build_live_series(raw_sections),
        game_records=game_records,
        member_events=build_member_events(raw_events),
        standings=_fetch_standings(cache, raw_sections),
        opponent_profiles=_fetch_opponent_profiles(cache, game_records),
        achievements=build_achievements(raw_norms, raw_awards),
        synced_at=datetime.now(timezone.utc),
    )


# Opponent current ratings refresh at most this often (issue #35) — they only
# change when the opponent plays, and "roughly current" is all the then-vs-now
# insight needs.
_OPPONENT_REFRESH_AGE = timedelta(days=7)


def _fetch_opponent_profiles(
    cache: UscfCache, game_records: list[UscfGameRecord], *, allow_fetch: bool = True
) -> dict[str, UscfProfile]:
    """
    The current profile of every unique opponent (issue #35), politely:
    one call per opponent, served from the cache for a week before
    re-fetching, and failures degrade per opponent — stale data (or no data)
    for one opponent never costs the others or the Sync.

    With *allow_fetch* False (the USCF-down path), only cached profiles are
    served, at any age — stale beats nothing.
    """
    profiles: dict[str, UscfProfile] = {}
    opponent_ids = sorted({r.opponent_id for r in game_records if r.opponent_id})
    for opponent_id in opponent_ids:
        key = f"opponent:{opponent_id}"
        raw = None
        if allow_fetch:
            try:
                raw = cache.fetch_aged(
                    key,
                    lambda oid=opponent_id: fetch_member_profile(oid),
                    max_age=_OPPONENT_REFRESH_AGE,
                )
            except UscfError as exc:
                logger.warning(
                    "Could not fetch opponent %s's profile (using cached if any "
                    "— ADR 0003): %s", opponent_id, exc,
                )
        if raw is None:
            raw = cache.get_aged(key)
        if raw is not None:
            profiles[opponent_id] = parse_member_profile(raw)
    return profiles


def _fetch_standings(
    cache: UscfCache, raw_sections: list[dict], *, allow_fetch: bool = True
) -> dict[tuple[str, str], list[StandingEntry]]:
    """
    The crosstables of every OTB Section the member played (issue #34),
    keyed by (event_id, section name) — how the enriched Games know them.

    Crosstables of rated events are immutable: each is fetched exactly once,
    ever, and served from the permanent cache after that (ADR 0003).  Unlike
    the member snapshot, failures degrade *individually* — one unreachable
    crosstable costs that Section's standings, never the whole Sync.  With
    *allow_fetch* False (the USCF-down path), only already-cached crosstables
    are served.
    """
    standings: dict[tuple[str, str], list[StandingEntry]] = {}
    for item in raw_sections:
        # Online sections (OR/OQ/OB) never have Games — the Study is OTB-only
        if item.get("ratingSystem") not in ("R", "D"):
            continue
        event_id = str(item.get("event", {}).get("id", ""))
        section_number = item.get("sectionNumber")
        section_name = str(item.get("sectionName", ""))
        if not event_id or section_number is None:
            continue

        key = f"standings:{event_id}:{section_number}"
        if allow_fetch:
            try:
                raw = cache.fetch_immutable(
                    key,
                    lambda eid=event_id, n=section_number: fetch_event_standings(eid, n),
                )
            except UscfError as exc:
                logger.warning(
                    "Could not fetch standings for event %s section %s "
                    "(skipping — ADR 0003): %s", event_id, section_number, exc,
                )
                continue
        else:
            raw = cache.get_immutable(key)
            if raw is None:
                continue
        standings[(event_id, section_name)] = build_standings(raw)
    return standings


def _uscf_from_cache(cache: UscfCache, failure: str) -> UscfSyncResult:
    """The degraded USCF result: cached data if there is any, clearly marked."""
    raw_profile = cache.get_current("profile")
    if raw_profile is None:
        return UscfSyncResult(failure=failure)

    logger.info("Showing cached USCF data from %s", cache.fetched_at())
    game_records = build_game_records(cache.get_current("games") or [])
    return UscfSyncResult(
        profile=parse_member_profile(raw_profile),
        official_series=build_official_series(cache.get_current("supplements") or []),
        live_series=build_live_series(cache.get_current("sections") or []),
        game_records=game_records,
        member_events=build_member_events(cache.get_current("events") or []),
        # Crosstables are immutable — the cached ones are always still correct
        standings=_fetch_standings(cache, cache.get_current("sections") or [],
                                   allow_fetch=False),
        # Stale opponent ratings beat none at all
        opponent_profiles=_fetch_opponent_profiles(cache, game_records,
                                                   allow_fetch=False),
        achievements=build_achievements(cache.get_current("norms") or [],
                                        cache.get_current("awards") or []),
        synced_at=cache.fetched_at(),
        failure=failure,
        from_cache=True,
    )


# ---------------------------------------------------------------------------
# The coach half of a Sync (issue #74 [G4]) — enrichment, never a dependency
# ---------------------------------------------------------------------------

@dataclass
class CoachSyncResult:
    """The outcome of the coach-ingestion pass — never required for success."""

    result: CoachMatchResult = field(default_factory=CoachMatchResult)
    # When the coach Studies were last successfully fetched (cached age when
    # degraded; None if never reached)
    synced_at: datetime | None = None
    # Why coach content is unavailable ('' when it isn't / none configured)
    failure: str = ""
    # True when the coach content shown is the previous Sync's cache
    from_cache: bool = False

    @property
    def available(self) -> bool:
        """True when any coach Chapter matched a Game (there is content to show)."""
        return bool(self.result.matches)


def sync_coach(
    coach_study_ids: list[str],
    games_df: pd.DataFrame,
    *,
    token: str | None = None,
    cache_path: str | None = None,
) -> CoachSyncResult:
    """
    Fetch the designated coach Studies and match their Chapters to *games_df*
    (issue #74), the same disposable-cache lifecycle as USCF (ADR 0003).

    Private coach Studies are read with the user's *token*.  A successful fetch
    refreshes the local PGN cache at *cache_path*; if every coach Study is
    unreachable, the cached coach PGN is used instead — coach content survives a
    brief outage.  Never raises: a coach Study being down never fails the Sync.
    The user's main Study stays the source of truth (ADR 0001) — an unmatched
    coach Chapter never creates a Game.
    """
    if not coach_study_ids:
        return CoachSyncResult()

    pgn_texts, failures = _fetch_all_pgns(coach_study_ids, token, "coach Study")

    if pgn_texts:
        merged = "\n\n".join(pgn_texts)
        if cache_path:
            _write_cache(cache_path, merged)
        return CoachSyncResult(
            result=match_coach_study(games_df, merged),
            synced_at=datetime.now(timezone.utc),
            failure="; ".join(f"{sid}: {why}" for sid, why in failures),
        )

    # Every coach Study was unreachable — fall back to the cached coach PGN.
    reason = "; ".join(f"{sid}: {why}" for sid, why in failures) or "unreachable"
    cached = _read_text_cache(cache_path)
    if not cached:
        logger.warning("Coach content unavailable and no cache (ADR 0003): %s", reason)
        return CoachSyncResult(failure=reason)

    logger.info("Showing cached coach content (coach Studies unreachable)")
    return CoachSyncResult(
        result=match_coach_study(games_df, cached),
        synced_at=_cache_mtime(cache_path),
        failure=reason,
        from_cache=True,
    )


def _read_text_cache(cache_path: str | None) -> str:
    """Read a disposable text cache; '' when missing or unreadable."""
    if not cache_path or not os.path.exists(cache_path):
        return ""
    try:
        with open(cache_path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError as exc:
        logger.warning("Could not read cache %r: %s", cache_path, exc)
        return ""


def _cache_mtime(cache_path: str | None) -> datetime | None:
    """When a cache file was last written (UTC), or None."""
    if not cache_path or not os.path.exists(cache_path):
        return None
    return datetime.fromtimestamp(os.path.getmtime(cache_path), tz=timezone.utc)


def load_from_cache(
    cache_path: str,
    player_name: str | None = None,
) -> tuple[pd.DataFrame, str, datetime]:
    """
    Parse the PGN cache of the last successful Sync.

    Returns (df, player, cached_at) where *cached_at* is when that Sync
    happened (the file's modification time, UTC).

    Raises FileNotFoundError if there is no cache.
    """
    with open(cache_path, encoding="utf-8", errors="ignore") as f:
        pgn_text = f.read()
    df, player = load_games_from_text(pgn_text, player_name=player_name)
    df = _dedupe_and_sort(df)
    cached_at = datetime.fromtimestamp(os.path.getmtime(cache_path), tz=timezone.utc)
    return df, player, cached_at


def _write_cache(cache_path: str, pgn_text: str) -> None:
    """Atomically (over)write the PGN cache; failures are logged, never raised."""
    try:
        tmp_path = f"{cache_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(pgn_text)
        os.replace(tmp_path, cache_path)
        logger.info("Wrote Sync cache: %s", cache_path)
    except OSError as exc:
        logger.warning("Could not write Sync cache %r (continuing without): %s",
                       cache_path, exc)


def detect_new_games(df: pd.DataFrame, previous_chapter_urls: set[str]) -> pd.DataFrame:
    """
    The Games in *df* that were not present in the previous Sync.

    Identity is the ChapterURL (ADR 0001); Games without one are never
    reported as new (they have no identity to compare).
    """
    if df.empty:
        return df
    is_new = (df["ChapterURL"] != "") & ~df["ChapterURL"].isin(previous_chapter_urls)
    return df[is_new]


def _dedupe_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop Games whose ChapterURL was already seen (first occurrence wins) and
    sort by date across all Studies.  Games stay comparable regardless of
    which Study they came from.
    """
    if df.empty:
        return df

    # Dedupe by ChapterURL — but never collapse Games without one
    # (a missing ChapterURL is "no identity", not a shared identity).
    has_url = df["ChapterURL"] != ""
    deduped = pd.concat([
        df[has_url].drop_duplicates(subset="ChapterURL", keep="first"),
        df[~has_url],
    ])

    # Sort by date (undated last), then original parse order for stable ties
    deduped = deduped.sort_values(
        ["Date_dt", "Index"], na_position="last"
    ).reset_index(drop=True)

    # Reassign Index to the merged chronological order so downstream
    # tie-breaking (streaks, milestones) is consistent across Studies.
    deduped["Index"] = range(1, len(deduped) + 1)

    return deduped
