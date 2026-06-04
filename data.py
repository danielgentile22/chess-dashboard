"""
data.py
=======
Application-wide refreshable data store.

At startup the designated Lichess Studies are Synced (fetched, merged,
deduped) and stored as a module-level DataFrame.  A Sync can also happen
while the app is running (the Sync button): ``refresh()`` re-fetches
everything and atomically swaps the in-memory data — readers always see
either the previous complete dataset or the new one, never a mix, and a
failed Sync never disturbs current data.

Offline resilience: every successful Sync refreshes a local PGN cache.
When Lichess is unreachable at startup, the app boots from that cache
(``source()`` reports "cache" so the UI can say so); when there is no
cache either, startup fails with a clear error.

All callbacks read via ``get_df()`` and never mutate the result;
``apply_filters`` inside pgn_stats_core copies before filtering, so reads
are safe under concurrent Dash callbacks.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from engine_analysis_core import (
    GameAnalysis,
    enrich_games_with_analysis,
    mistake_type_distribution,
)
from sync import (
    SyncError,
    SyncResult,
    UscfCache,
    UscfSyncResult,
    detect_new_games,
    load_from_cache,
    sync_studies,
    sync_uscf,
)
from uscf_core import (
    LiveRatingPoint,
    MatchResult,
    OfficialRatingPoint,
    ReconciliationEntry,
    StandingEntry,
    UscfAchievement,
    UscfEvent,
    UscfProfile,
    attach_round_numbers,
    enrich_games,
    match_games,
    reconcile,
)

logger = logging.getLogger(__name__)

# --- store state (swapped atomically under _sync_lock) ---------------------
_df: pd.DataFrame = pd.DataFrame()
_player: str = ""
_sync_failures: list[tuple[str, str]] = []
_synced_at: datetime | None = None
_source: str = "lichess"          # "lichess" | "cache"
_cached_at: datetime | None = None  # only meaningful when _source == "cache"

# --- USCF enrichment (ADR 0003: optional, never required) ------------------
_uscf: UscfSyncResult = UscfSyncResult()
# USCF Game Records ↔ Games (issue #28); empty when USCF is off/unavailable
_match_result: MatchResult = MatchResult()
# Dismissed Reconciliation entries (issue #30): in-memory now, persisted
# best-effort in the USCF cache so they survive restarts
_dismissed: set[str] = set()
# Achievement IDs every Sync so far has seen (issue #36): None = never recorded.
# In-memory state seeded from / persisted to the USCF cache, like dismissals.
_seen_achievement_ids: set[str] | None = None
# Achievements first seen by the last Sync — the ones to celebrate (issue #36)
_new_achievements: list[UscfAchievement] = []

# --- the designated Studies (remembered so refresh() can re-Sync) ----------
_study_ids: list[str] = []
_player_name: str | None = None
_token: str | None = None
_cache_path: str | None = None
_uscf_member_id: str | None = None
_uscf_cache_path: str | None = None

# Guards against doubled Syncs (button mashing); refresh() never blocks on it.
_sync_lock = threading.Lock()


@dataclass
class RefreshOutcome:
    """What a refresh() attempt produced — feeds the Sync toast in the UI."""

    status: str  # "success" | "error" | "already_running"
    # newly appeared Games: [{"Opponent": ..., "Outcome": ..., "Result": ..., "Date": ...}]
    new_games: list[dict] = field(default_factory=list)
    error: str = ""
    failures: list[tuple[str, str]] = field(default_factory=list)


def initialize(
    study_ids: list[str],
    player_name: str | None = None,
    token: str | None = None,
    cache_path: str | None = None,
    uscf_member_id: str | None = None,
    uscf_cache_path: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """
    Sync all designated Studies and cache the merged DataFrame module-wide.

    Remembers the Study list / token / cache path so ``refresh()`` can
    re-Sync later.  When Lichess is unreachable and a cache from a previous
    successful Sync exists at *cache_path*, the app boots from the cache
    instead (the dashboard never goes blank because Lichess is down).

    When *uscf_member_id* is given, the Sync also fetches that member's USCF
    record as enrichment.  USCF being unreachable never fails the Sync
    (ADR 0003); with a *uscf_cache_path*, USCF surfaces degrade to the last
    successful Sync's cached data instead of disappearing.

    Raises
    ------
    sync.SyncError : no designated Study could be fetched AND no cache exists.
    RuntimeError   : the Studies contained no games.
    """
    global _study_ids, _player_name, _token, _cache_path
    global _uscf_member_id, _uscf_cache_path
    _study_ids, _player_name, _token, _cache_path = (
        list(study_ids), player_name, token, cache_path,
    )
    _uscf_member_id, _uscf_cache_path = uscf_member_id, uscf_cache_path

    logger.info("Syncing %d designated Studies from Lichess", len(study_ids))
    try:
        result = sync_studies(
            study_ids, player_name=player_name, token=token, cache_path=cache_path
        )
    except SyncError as exc:
        _boot_from_cache(exc)
        _sync_uscf_into_store()
        _run_analysis_into_store()
        return _df, _player

    if result.df.empty:
        raise RuntimeError(f"No games found in designated Studies: {study_ids}")
    _swap(result)
    _sync_uscf_into_store()
    _run_analysis_into_store()
    logger.info("Synced %d games for player %r", len(_df), _player)
    return _df, _player


def _sync_uscf_into_store() -> None:
    """
    Run the USCF half of a Sync and enrich the Games with whatever matching
    produces (a no-op USCF result when no member ID is configured).

    The enrichment columns always exist afterwards — with USCF off or down,
    every Game is simply unmatched — so pages never check for their presence.
    """
    global _uscf, _match_result, _df, _dismissed
    if not _uscf_member_id:
        _uscf = UscfSyncResult()
    else:
        _uscf = sync_uscf(_uscf_member_id, cache_path=_uscf_cache_path)

    # Match & enrich (issue #28): USCF Game Records attach to Games
    _match_result = match_games(_df, _uscf.game_records)
    _df = enrich_games(_df, _match_result)
    # Real round numbers from the crosstables (issue #34)
    _df = attach_round_numbers(_df, _uscf.standings, _uscf_member_id or "")

    # Dismissed Reconciliation entries survive restarts via the cache (#30)
    _dismissed = set(UscfCache(_uscf_cache_path).dismissals()) | _dismissed

    # Which achievements has this Sync seen for the first time? (issue #36)
    _detect_new_achievements()


def _run_analysis_into_store() -> None:
    """
    Read the engine analysis Lichess embedded in each Game's Study export and
    attach it as enrichment (issue #57 [F1]), following the USCF pattern.

    The Analysis / Analyzed columns always exist afterwards — a Game with no
    requested computer analysis simply degrades to ``analyzed=False`` — so
    pages never check for their presence.  Analysis is enrichment, never a
    dependency (ADR 0004): a Sync that reached Lichess succeeds whether or not
    any Game is analysed.
    """
    global _df
    _df = enrich_games_with_analysis(_df)


def _detect_new_achievements() -> None:
    """
    Compare this Sync's achievements against everything previous Syncs have
    seen, so genuinely fresh norms/awards get celebrated — exactly once.

    The very first recording (no seen-state anywhere) registers everything
    silently: celebrating months-old achievements on the first Sync after
    this feature lands would be noise, not news.  A USCF outage records
    nothing — cached/absent achievements are never "new".
    """
    global _new_achievements, _seen_achievement_ids
    if not _uscf.available or _uscf.from_cache:
        _new_achievements = []
        return

    cache = UscfCache(_uscf_cache_path)
    seen: set[str] | None
    if _seen_achievement_ids is not None:
        seen = _seen_achievement_ids               # this run already knows
    else:
        cached = cache.seen_achievements()
        seen = set(cached) if cached is not None else None

    current = _uscf.achievements
    if seen is None:
        _new_achievements = []                     # first recording — silent
    else:
        _new_achievements = [a for a in current if a.achievement_id not in seen]

    _seen_achievement_ids = (seen or set()) | {a.achievement_id for a in current}
    cache.record_achievements(sorted(_seen_achievement_ids))


def _boot_from_cache(sync_error: SyncError) -> tuple[pd.DataFrame, str]:
    """Fall back to the PGN cache of the last successful Sync, if there is one."""
    global _df, _player, _sync_failures, _synced_at, _source, _cached_at

    if not _cache_path or not os.path.exists(_cache_path):
        # No cache → the original Sync failure is the clearest error to show
        raise sync_error

    logger.warning("Lichess unreachable (%s) — booting from cache %s",
                   sync_error, _cache_path)
    df, player, cached_at = load_from_cache(_cache_path, player_name=_player_name)
    if df.empty:
        raise sync_error

    _df, _player, _sync_failures = df, player, []
    _synced_at = None  # there has been no successful Sync this run
    _source, _cached_at = "cache", cached_at
    logger.info("Loaded %d games from cache (last Synced %s)", len(df), cached_at)
    return _df, _player


def refresh() -> RefreshOutcome:
    """
    Re-Sync all designated Studies and atomically swap the in-memory data.

    Never raises and never disturbs current data on failure.  If a Sync is
    already running, reports ``already_running`` instead of starting another.
    """
    if not _study_ids:
        return RefreshOutcome(status="error", error="The data store was never initialized.")

    if not _sync_lock.acquire(blocking=False):
        logger.info("Sync already running — ignoring duplicate trigger")
        return RefreshOutcome(status="already_running")

    try:
        previous_urls = (
            set(_df["ChapterURL"]) - {""} if not _df.empty else set()
        )
        result = sync_studies(
            _study_ids, player_name=_player_name, token=_token, cache_path=_cache_path
        )
        if result.df.empty:
            return RefreshOutcome(
                status="error",
                error=f"No games found in designated Studies: {_study_ids}",
            )

        new_df = detect_new_games(result.df, previous_urls)
        new_games = new_df[["Opponent", "Outcome", "Result", "Date"]].to_dict("records")

        _swap(result)
        _sync_uscf_into_store()  # USCF failing never fails the Sync (ADR 0003)
        _run_analysis_into_store()  # nor does engine analysis (ADR 0004)
        logger.info(
            "Sync complete: %d games (%d new)", len(result.df), len(new_games)
        )
        return RefreshOutcome(
            status="success", new_games=new_games, failures=result.failures
        )
    except Exception as exc:  # a failed Sync must never break the running app
        logger.warning("Sync failed; keeping current data: %s", exc)
        return RefreshOutcome(status="error", error=str(exc))
    finally:
        _sync_lock.release()


def _swap(result: SyncResult) -> None:
    """Atomically replace the store's contents with a (live) Sync result."""
    global _df, _player, _sync_failures, _synced_at, _source, _cached_at
    # Single bytecode-level rebind per name; readers see old or new, never a mix.
    _df, _player, _sync_failures, _synced_at, _source, _cached_at = (
        result.df,
        result.player,
        result.failures,
        datetime.now(timezone.utc),
        "lichess",
        None,
    )


def get_df() -> pd.DataFrame:
    """Return the full (unfiltered) DataFrame. Never mutate the result."""
    return _df


def get_game_analysis(chapter_url: str) -> GameAnalysis:
    """
    The engine analysis for the Game at *chapter_url* (issue #57 [F1]).

    Always returns a GameAnalysis — an empty one (``analyzed=False``) for a
    Game with no requested analysis, an unknown URL, or before the first Sync —
    so pages never guard on its presence (ADR 0004).
    """
    if not chapter_url or _df.empty or "Analysis" not in _df.columns:
        return GameAnalysis(chapter_url=chapter_url or "")
    matches = _df[_df["ChapterURL"] == chapter_url]
    if matches.empty:
        return GameAnalysis(chapter_url=chapter_url)
    analysis = matches.iloc[0]["Analysis"]
    return analysis if isinstance(analysis, GameAnalysis) else GameAnalysis(
        chapter_url=chapter_url
    )


def get_awaiting_analysis() -> pd.DataFrame:
    """
    The Games still awaiting computer analysis (issue #57 [F1]): a real Chapter
    (one with a ChapterURL) whose Study export carried no engine evaluations.

    Returns a copy so callers never mutate the store.  Empty when every Game is
    analysed, or before the first Sync.  The accessor always exists.
    """
    if _df.empty or "Analyzed" not in _df.columns:
        return _df.copy()
    awaiting = _df[(~_df["Analyzed"].astype(bool)) & (_df["ChapterURL"] != "")]
    return awaiting.copy()


def get_mistake_type_distribution() -> dict[str, int]:
    """
    The tactical-vs-positional split of Daniel's mistakes across every analysed
    Game (issue #58) — the Analysis page's headline aggregate.

    Always returns the two-key tally; ``{"tactical": 0, "positional": 0}`` before
    the first Sync or when nothing is analysed yet.  Games still awaiting analysis
    contribute nothing, so they are excluded from the distribution.
    """
    if _df.empty or "Analysis" not in _df.columns:
        return mistake_type_distribution([])
    return mistake_type_distribution(_df["Analysis"])


def has_any_analysis() -> bool:
    """
    True once at least one Game carries requested computer analysis (issue #58).

    Drives the Analysis page's empty state: before this is true there is nothing
    to distribute, so the page shows its "request analysis" placeholder instead
    of an empty chart.
    """
    if _df.empty or "Analyzed" not in _df.columns:
        return False
    return bool(_df["Analyzed"].astype(bool).any())


def get_uscf_profile() -> UscfProfile | None:
    """The member's USCF profile, or None when unavailable / not configured."""
    return _uscf.profile


def get_uscf_matches() -> MatchResult:
    """
    The last Sync's USCF Game Record ↔ Game matching (issue #28).

    Both leftovers (unmatched Games, unmatched records) are exposed —
    Reconciliation is built from them.  Empty when USCF is off/unavailable.
    """
    return _match_result


def get_reconciliation() -> list[ReconciliationEntry]:
    """
    Every open disagreement between the Studies and USCF (issue #30),
    dismissed entries excluded.

    Empty when USCF isn't configured or has never been reached — with no
    USCF data at all there is nothing meaningful to reconcile against.
    """
    if not uscf_enabled() or not _uscf.available:
        return []
    # Mid-Sync, the store briefly holds the freshly-swapped Lichess df before
    # USCF enrichment rebinds it.  No enrichment columns yet → nothing to
    # reconcile yet; the next callback (post-Sync) sees the full picture.
    if "UscfColorConflict" not in _df.columns:
        return []
    return reconcile(
        _df, _match_result, _uscf.official_series, dismissed=frozenset(_dismissed)
    )


def dismiss_reconciliation_entry(entry_id: str) -> None:
    """
    Dismiss a Reconciliation entry ("USCF is wrong" / "intentionally skipped").

    Takes effect immediately; persists best-effort in the USCF cache so it
    survives Syncs and restarts (a redeploy may resurrect it — issue #30's
    documented limitation).
    """
    _dismissed.add(entry_id)
    UscfCache(_uscf_cache_path).add_dismissal(entry_id)


def get_official_series() -> list[OfficialRatingPoint]:
    """The Official Rating series: one point per supplement month, chronological."""
    return _uscf.official_series


def get_live_series() -> list[LiveRatingPoint]:
    """The Live Rating series: one point per Regular-rated Section, chronological,
    decimals preserved. Continuous: each post-rating is the next pre-rating."""
    return _uscf.live_series


def get_uscf_events() -> list[UscfEvent]:
    """Every Rated Event the member has entered, chronological (issue #33).
    Empty when USCF is off/unavailable."""
    return _uscf.member_events


def get_uscf_standings() -> dict[tuple[str, str], list[StandingEntry]]:
    """The crosstables of played OTB Sections (issue #34), keyed by
    (event_id, section name).  Empty when USCF is off/unavailable."""
    return _uscf.standings


def get_opponent_profiles() -> dict[str, UscfProfile]:
    """Opponents' current USCF profiles keyed by member ID (issue #35) —
    the "they're 1580 now" half of then-vs-now.  Empty when USCF is
    off/unavailable; missing opponents are simply absent."""
    return _uscf.opponent_profiles


def get_uscf_achievements() -> list[UscfAchievement]:
    """The member's official achievements — norms and awards, chronological
    (issue #36). Empty when USCF is off/unavailable."""
    return _uscf.achievements


def get_new_achievements() -> list[UscfAchievement]:
    """
    Achievements first seen by the last Sync — the ones to celebrate
    (issue #36).  Empty on the first-ever recording (existing achievements
    are registered silently) and whenever USCF data is cached or absent.
    """
    return _new_achievements


def uscf_synced_at() -> datetime | None:
    """When USCF data was last successfully fetched (None if never)."""
    return _uscf.synced_at


def uscf_failure() -> str:
    """Why USCF data is unavailable ('' when it isn't, or USCF isn't configured)."""
    return _uscf.failure


def uscf_from_cache() -> bool:
    """True when the USCF data shown is the previous successful Sync's cache."""
    return _uscf.from_cache


def uscf_unavailable_since() -> str | None:
    """
    'USCF unavailable since <time>' when showing cached USCF data, else None.

    The one place this wording lives — the header freshness label and the
    profile card staleness notice both use it.
    """
    if not _uscf.from_cache:
        return None
    when = (f"{_uscf.synced_at:%Y-%m-%d %H:%M} UTC" if _uscf.synced_at
            else "an earlier run")
    return f"USCF unavailable since {when}"


def uscf_enabled() -> bool:
    """True when a USCF member ID is configured for this run."""
    return _uscf_member_id is not None


def get_player() -> str:
    """Return the detected / configured player name."""
    return _player


def get_sync_failures() -> list[tuple[str, str]]:
    """(study_id, reason) for each designated Study the last Sync could not fetch."""
    return _sync_failures


def synced_at() -> datetime | None:
    """When the last successful Sync completed (UTC), or None if never."""
    return _synced_at


def source() -> str:
    """Where the current data came from: 'lichess' (live Sync) or 'cache'."""
    return _source


def cached_at() -> datetime | None:
    """When the cache being shown was written (only set when source() == 'cache')."""
    return _cached_at


def is_loaded() -> bool:
    """True if data has been successfully initialised."""
    return not _df.empty


def reset() -> None:
    """Clear the store (used by tests)."""
    global _df, _player, _sync_failures, _synced_at, _source, _cached_at
    global _study_ids, _player_name, _token, _cache_path
    global _uscf, _uscf_member_id, _uscf_cache_path, _match_result, _dismissed
    global _seen_achievement_ids, _new_achievements
    _df = pd.DataFrame()
    _player = ""
    _sync_failures = []
    _synced_at = None
    _source = "lichess"
    _cached_at = None
    _study_ids = []
    _player_name = None
    _token = None
    _cache_path = None
    _uscf = UscfSyncResult()
    _uscf_member_id = None
    _uscf_cache_path = None
    _match_result = MatchResult()
    _dismissed = set()
    _seen_achievement_ids = None
    _new_achievements = []
