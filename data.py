"""
data.py
=======
Application data store — a registry of per-user stores (issue #72 [G2]).

Before multi-user (PRD #55), this module was a single module-level singleton:
one ``_df``, one ``_uscf``, swapped atomically.  It is now a **registry of
per-user stores** keyed by the authenticated user.  Every accessor resolves
against the *current* user's store; a Sync runs per user against that user's
Study IDs, Lichess token, and USCF member ID.  One user's accessors never
return another user's data (ADR 0005).

How "current" is resolved
-------------------------
The active user is a thread-local set per request (Flask handles each request
on one thread).  With no user active — local CLI use, the ungated single-user
mode, and the entire existing test suite — the active store is a single default
store, and ``initialize()`` / ``refresh()`` / every accessor behave exactly as
they did before the registry existed.  Multi-user adds ``register_users`` /
``sync_user`` / ``activate``; the gated app activates the request's user before
any page renders.

Each store still swaps its data atomically (``_swap``): readers always see
either the previous complete dataset or the new one, never a mix, and a failed
Sync never disturbs current data.  Offline resilience (boot from PGN cache) and
USCF/analysis enrichment work per store, exactly as before.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

import ai_summary
from analysis_cache import AnalysisCache
from analysis_trends import (
    accuracy_trend,
    mistake_move_histogram,
    mistake_type_trend,
    phase_type_matrix,
)
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
from user_config import UserRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# A per-user store: everything one user's pages read, and how to re-Sync it.
# ---------------------------------------------------------------------------

@dataclass
class Store:
    """One user's data and the configuration a Sync needs to refresh it.

    Fields are swapped atomically inside the store on Sync (``_swap``); readers
    of an accessor see one consistent snapshot.
    """

    # --- data state ---------------------------------------------------------
    df: pd.DataFrame = field(default_factory=pd.DataFrame)
    player: str = ""
    sync_failures: list[tuple[str, str]] = field(default_factory=list)
    synced_at: datetime | None = None
    source: str = "lichess"            # "lichess" | "cache"
    cached_at: datetime | None = None  # only meaningful when source == "cache"

    # --- USCF enrichment (ADR 0003: optional, never required) ---------------
    uscf: UscfSyncResult = field(default_factory=UscfSyncResult)
    match_result: MatchResult = field(default_factory=MatchResult)
    dismissed: set[str] = field(default_factory=set)
    seen_achievement_ids: set[str] | None = None
    new_achievements: list[UscfAchievement] = field(default_factory=list)

    # --- the configuration a refresh()/Sync needs ---------------------------
    study_ids: list[str] = field(default_factory=list)
    coach_study_ids: list[str] = field(default_factory=list)  # ingested in #74
    player_name: str | None = None
    token: str | None = None
    cache_path: str | None = None
    uscf_member_id: str | None = None
    uscf_cache_path: str | None = None
    anthropic_api_key: str | None = None
    analysis_cache_path: str | None = None

    # True once this store has had a Sync attempted (so a multi-user deploy
    # never re-Syncs a user's store on every request — issue #72).
    initialized: bool = False

    # Guards against doubled Syncs (button mashing); refresh() never blocks on it.
    sync_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


# ---------------------------------------------------------------------------
# The registry + the active store (thread-local per request)
# ---------------------------------------------------------------------------

# The key of the single store used in single-user / ungated mode and by the
# existing test suite (no authenticated user → this store).
_DEFAULT_USER = ""

_registry: dict[str, Store] = {_DEFAULT_USER: Store()}
_active = threading.local()


def activate(username: str | None) -> None:
    """Make *username*'s store the one accessors resolve, for this thread.

    ``None`` (or an unknown user) falls back to the default store — the gated
    app calls this with the request's authenticated user before any page
    renders; everything else leaves it at the default.
    """
    _active.user = username if username else _DEFAULT_USER


def _active_key() -> str:
    return getattr(_active, "user", _DEFAULT_USER)


def _current() -> Store:
    """The store for the active user — never None (unknown users get a fresh,
    empty store so accessors degrade gracefully rather than KeyError)."""
    key = _active_key()
    store = _registry.get(key)
    if store is None:
        store = Store()
        _registry[key] = store
    return store


@dataclass
class RefreshOutcome:
    """What a refresh() attempt produced — feeds the Sync toast in the UI."""

    status: str  # "success" | "error" | "already_running"
    # newly appeared Games: [{"Opponent": ..., "Outcome": ..., "Result": ..., "Date": ...}]
    new_games: list[dict] = field(default_factory=list)
    error: str = ""
    failures: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Single-user entry point (CLI, ungated mode, existing tests)
# ---------------------------------------------------------------------------

def initialize(
    study_ids: list[str],
    player_name: str | None = None,
    token: str | None = None,
    cache_path: str | None = None,
    uscf_member_id: str | None = None,
    uscf_cache_path: str | None = None,
    anthropic_api_key: str | None = None,
    analysis_cache_path: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """
    Sync all designated Studies into the default store and serve them.

    Behaves exactly as before the registry: it configures and Syncs the single
    default store (the one every accessor resolves to when no user is active).
    When Lichess is unreachable and a cache from a previous successful Sync
    exists at *cache_path*, the store boots from the cache instead.  USCF being
    unreachable never fails the Sync (ADR 0003).

    Raises
    ------
    sync.SyncError : no designated Study could be fetched AND no cache exists.
    RuntimeError   : the Studies contained no games.
    """
    store = _registry[_DEFAULT_USER]
    _configure(
        store, study_ids, player_name=player_name, token=token,
        cache_path=cache_path, uscf_member_id=uscf_member_id,
        uscf_cache_path=uscf_cache_path, anthropic_api_key=anthropic_api_key,
        analysis_cache_path=analysis_cache_path,
    )
    logger.info("Syncing %d designated Studies from Lichess", len(study_ids))
    _sync_store(store)
    if not store.df.empty:
        logger.info("Synced %d games for player %r", len(store.df), store.player)
    return store.df, store.player


def _configure(store: Store, study_ids: list[str], **cfg) -> None:
    """Remember the Study list / token / cache paths so a Sync can run later."""
    store.study_ids = list(study_ids)
    store.coach_study_ids = list(cfg.get("coach_study_ids") or [])
    store.player_name = cfg.get("player_name")
    store.token = cfg.get("token")
    store.cache_path = cfg.get("cache_path")
    store.uscf_member_id = cfg.get("uscf_member_id")
    store.uscf_cache_path = cfg.get("uscf_cache_path")
    store.anthropic_api_key = cfg.get("anthropic_api_key")
    store.analysis_cache_path = cfg.get("analysis_cache_path")


def _sync_store(store: Store) -> None:
    """Run a full Sync into *store*: Lichess (or its cache), then USCF and
    engine-analysis enrichment.  Raises SyncError / RuntimeError the same way
    ``initialize`` documents; callers that must not fail (the lazy multi-user
    Sync) wrap this."""
    store.initialized = True
    try:
        result = sync_studies(
            store.study_ids, player_name=store.player_name,
            token=store.token, cache_path=store.cache_path,
        )
    except SyncError as exc:
        _boot_from_cache(store, exc)
        _sync_uscf_into_store(store)
        _run_analysis_into_store(store)
        return

    if result.df.empty:
        raise RuntimeError(f"No games found in designated Studies: {store.study_ids}")
    _swap(store, result)
    _sync_uscf_into_store(store)
    _run_analysis_into_store(store)


def _sync_uscf_into_store(store: Store) -> None:
    """
    Run the USCF half of a Sync and enrich the Games with whatever matching
    produces (a no-op USCF result when no member ID is configured).

    The enrichment columns always exist afterwards — with USCF off or down,
    every Game is simply unmatched — so pages never check for their presence.
    """
    if not store.uscf_member_id:
        store.uscf = UscfSyncResult()
    else:
        store.uscf = sync_uscf(store.uscf_member_id, cache_path=store.uscf_cache_path)

    # Match & enrich (issue #28): USCF Game Records attach to Games
    store.match_result = match_games(store.df, store.uscf.game_records)
    store.df = enrich_games(store.df, store.match_result)
    # Real round numbers from the crosstables (issue #34)
    store.df = attach_round_numbers(
        store.df, store.uscf.standings, store.uscf_member_id or ""
    )

    # Dismissed Reconciliation entries survive restarts via the cache (#30)
    store.dismissed = set(UscfCache(store.uscf_cache_path).dismissals()) | store.dismissed

    # Which achievements has this Sync seen for the first time? (issue #36)
    _detect_new_achievements(store)


def _run_analysis_into_store(store: Store) -> None:
    """
    Read the engine analysis Lichess embedded in each Game's Study export and
    attach it as enrichment (issue #57 [F1]), following the USCF pattern.

    The Analysis / Analyzed / Summary columns always exist afterwards — a Game
    with no requested computer analysis simply degrades to ``analyzed=False``
    with an empty Summary — so pages never check for their presence (ADR 0004).
    """
    enriched = enrich_games_with_analysis(store.df)
    enriched["Summary"] = _summaries_for(store, enriched)
    store.df = enriched


def _summaries_for(store: Store, enriched: pd.DataFrame) -> list[str]:
    """One plain-English summary per Game (issue #59 [F5]), via the AI boundary.

    Empty for an unanalysed Game, when no API key is configured, or on any
    client failure — the boundary degrades silently, so this never fails the
    Sync.  Cached by Game identity so an unchanged Game is never re-billed.
    """
    if "Analysis" not in enriched.columns:
        return [""] * len(enriched)
    cache = AnalysisCache(store.analysis_cache_path)
    return [
        ai_summary.summarize(analysis, api_key=store.anthropic_api_key, cache=cache)
        if isinstance(analysis, GameAnalysis) else ""
        for analysis in enriched["Analysis"]
    ]


def _detect_new_achievements(store: Store) -> None:
    """
    Compare this Sync's achievements against everything previous Syncs have
    seen, so genuinely fresh norms/awards get celebrated — exactly once.

    The very first recording (no seen-state anywhere) registers everything
    silently.  A USCF outage records nothing — cached/absent achievements are
    never "new".
    """
    if not store.uscf.available or store.uscf.from_cache:
        store.new_achievements = []
        return

    cache = UscfCache(store.uscf_cache_path)
    seen: set[str] | None
    if store.seen_achievement_ids is not None:
        seen = store.seen_achievement_ids               # this run already knows
    else:
        cached = cache.seen_achievements()
        seen = set(cached) if cached is not None else None

    current = store.uscf.achievements
    if seen is None:
        store.new_achievements = []                     # first recording — silent
    else:
        store.new_achievements = [
            a for a in current if a.achievement_id not in seen
        ]

    store.seen_achievement_ids = (seen or set()) | {a.achievement_id for a in current}
    cache.record_achievements(sorted(store.seen_achievement_ids))


def _boot_from_cache(store: Store, sync_error: SyncError) -> None:
    """Fall back to the PGN cache of the last successful Sync, if there is one."""
    if not store.cache_path or not os.path.exists(store.cache_path):
        # No cache → the original Sync failure is the clearest error to show
        raise sync_error

    logger.warning("Lichess unreachable (%s) — booting from cache %s",
                   sync_error, store.cache_path)
    df, player, cached_at = load_from_cache(store.cache_path,
                                            player_name=store.player_name)
    if df.empty:
        raise sync_error

    store.df, store.player, store.sync_failures = df, player, []
    store.synced_at = None  # there has been no successful Sync this run
    store.source, store.cached_at = "cache", cached_at
    logger.info("Loaded %d games from cache (last Synced %s)", len(df), cached_at)


def refresh() -> RefreshOutcome:
    """
    Re-Sync the active user's designated Studies and atomically swap their data.

    Never raises and never disturbs current data on failure.  If a Sync is
    already running for this store, reports ``already_running``.
    """
    store = _current()
    if not store.study_ids:
        return RefreshOutcome(status="error",
                              error="The data store was never initialized.")

    if not store.sync_lock.acquire(blocking=False):
        logger.info("Sync already running — ignoring duplicate trigger")
        return RefreshOutcome(status="already_running")

    try:
        previous_urls = (
            set(store.df["ChapterURL"]) - {""} if not store.df.empty else set()
        )
        result = sync_studies(
            store.study_ids, player_name=store.player_name,
            token=store.token, cache_path=store.cache_path,
        )
        if result.df.empty:
            return RefreshOutcome(
                status="error",
                error=f"No games found in designated Studies: {store.study_ids}",
            )

        new_df = detect_new_games(result.df, previous_urls)
        new_games = new_df[["Opponent", "Outcome", "Result", "Date"]].to_dict("records")

        _swap(store, result)
        _sync_uscf_into_store(store)  # USCF failing never fails the Sync (ADR 0003)
        _run_analysis_into_store(store)  # nor does engine analysis (ADR 0004)
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
        store.sync_lock.release()


def _swap(store: Store, result: SyncResult) -> None:
    """Atomically replace the store's contents with a (live) Sync result."""
    store.df = result.df
    store.player = result.player
    store.sync_failures = result.failures
    store.synced_at = datetime.now(timezone.utc)
    store.source = "lichess"
    store.cached_at = None


# ---------------------------------------------------------------------------
# Multi-user entry points (issue #72 [G2])
# ---------------------------------------------------------------------------

def register_users(
    users: dict[str, UserRecord],
    *,
    data_dir: str | None = None,
    anthropic_api_key: str | None = None,
) -> None:
    """
    Register a store per configured user, each pointed at that user's Studies,
    Lichess token, and USCF member ID (issue #72).

    Caches are per-user — a subdirectory of *data_dir* keyed by username — so
    one user's PGN/USCF/analysis caches never collide with another's.  The
    stores are *not* Synced here; call ``sync_user`` (eagerly at build, or
    lazily on first access).
    """
    base = data_dir or ".user-data"
    for username, record in users.items():
        user_dir = os.path.join(base, _safe_dirname(username))
        store = Store()
        _configure(
            store, list(record.study_ids),
            coach_study_ids=list(record.coach_study_ids),
            token=record.lichess_token,
            uscf_member_id=record.uscf_member_id,
            cache_path=os.path.join(user_dir, "games.pgn"),
            uscf_cache_path=os.path.join(user_dir, "uscf_cache.json"),
            analysis_cache_path=os.path.join(user_dir, "analysis_cache.json"),
            anthropic_api_key=anthropic_api_key,
        )
        _registry[username] = store


def sync_user(username: str) -> None:
    """
    Sync *username*'s store now (issue #72).

    Degrades gracefully: a user whose Studies are unreachable (and who has no
    cache) ends up with an empty store rather than crashing the app — coach and
    USCF content are enrichment, never a dependency, and an unreachable main
    Study at boot must not take the whole multi-user app down.
    """
    store = _registry.get(username)
    if store is None:
        return
    try:
        _sync_store(store)
    except (SyncError, RuntimeError) as exc:
        logger.warning("Could not Sync user %r (serving empty): %s", username, exc)


def ensure_synced(username: str) -> None:
    """Sync *username*'s store the first time it is needed, not on every
    request (issue #72)."""
    store = _registry.get(username)
    if store is not None and not store.initialized:
        sync_user(username)


def _safe_dirname(username: str) -> str:
    """A filesystem-safe directory name for a username (config-controlled, but
    sanitised anyway)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", username)
    return safe or "user"


def reset() -> None:
    """Clear the whole registry back to a single empty default store (tests)."""
    _registry.clear()
    _registry[_DEFAULT_USER] = Store()
    _active.user = _DEFAULT_USER


# ---------------------------------------------------------------------------
# Accessors — every one resolves against the active user's store
# ---------------------------------------------------------------------------

def get_df() -> pd.DataFrame:
    """Return the full (unfiltered) DataFrame. Never mutate the result."""
    return _current().df


def get_game_analysis(chapter_url: str) -> GameAnalysis:
    """
    The engine analysis for the Game at *chapter_url* (issue #57 [F1]).

    Always returns a GameAnalysis — an empty one (``analyzed=False``) for a
    Game with no requested analysis, an unknown URL, or before the first Sync.
    """
    df = _current().df
    if not chapter_url or df.empty or "Analysis" not in df.columns:
        return GameAnalysis(chapter_url=chapter_url or "")
    matches = df[df["ChapterURL"] == chapter_url]
    if matches.empty:
        return GameAnalysis(chapter_url=chapter_url)
    analysis = matches.iloc[0]["Analysis"]
    return analysis if isinstance(analysis, GameAnalysis) else GameAnalysis(
        chapter_url=chapter_url
    )


def get_game_summary(chapter_url: str) -> str:
    """
    The plain-English AI summary for the Game at *chapter_url* (issue #59 [F5]).

    Always returns a string — ``""`` for a Game with no requested analysis, no
    configured API key, an unknown URL, or before the first Sync.
    """
    df = _current().df
    if not chapter_url or df.empty or "Summary" not in df.columns:
        return ""
    matches = df[df["ChapterURL"] == chapter_url]
    if matches.empty:
        return ""
    return str(matches.iloc[0]["Summary"] or "")


def get_awaiting_analysis() -> pd.DataFrame:
    """
    The Games still awaiting computer analysis (issue #57 [F1]): a real Chapter
    (one with a ChapterURL) whose Study export carried no engine evaluations.

    Returns a copy so callers never mutate the store.
    """
    df = _current().df
    if df.empty or "Analyzed" not in df.columns:
        return df.copy()
    awaiting = df[(~df["Analyzed"].astype(bool)) & (df["ChapterURL"] != "")]
    return awaiting.copy()


def get_mistake_type_distribution() -> dict[str, int]:
    """
    The tactical-vs-positional split of the player's mistakes across every
    analysed Game (issue #58) — the Analysis page's headline aggregate.
    """
    df = _current().df
    if df.empty or "Analysis" not in df.columns:
        return mistake_type_distribution([])
    return mistake_type_distribution(df["Analysis"])


def get_accuracy_trend() -> pd.DataFrame:
    """Per-Game move accuracy over time, with rating (issue #61 [F3])."""
    return accuracy_trend(_current().df)


def get_mistake_type_trend() -> pd.DataFrame:
    """Tactical/positional mistake counts per analysed Game over time (#61)."""
    return mistake_type_trend(_current().df)


def get_phase_type_matrix() -> pd.DataFrame:
    """The phase × type matrix of the player's mistakes across analysed Games."""
    return phase_type_matrix(_current().df)


def get_mistake_move_histogram() -> pd.DataFrame:
    """Counts of the player's mistakes by the move number they happened on."""
    return mistake_move_histogram(_current().df)


def has_any_analysis() -> bool:
    """True once at least one Game carries requested computer analysis (#58)."""
    df = _current().df
    if df.empty or "Analyzed" not in df.columns:
        return False
    return bool(df["Analyzed"].astype(bool).any())


def get_uscf_profile() -> UscfProfile | None:
    """The member's USCF profile, or None when unavailable / not configured."""
    return _current().uscf.profile


def get_uscf_matches() -> MatchResult:
    """The last Sync's USCF Game Record ↔ Game matching (issue #28)."""
    return _current().match_result


def get_reconciliation() -> list[ReconciliationEntry]:
    """
    Every open disagreement between the Studies and USCF (issue #30),
    dismissed entries excluded.
    """
    store = _current()
    if not uscf_enabled() or not store.uscf.available:
        return []
    # Mid-Sync, the store briefly holds the freshly-swapped Lichess df before
    # USCF enrichment rebinds it.  No enrichment columns yet → nothing to
    # reconcile yet; the next callback (post-Sync) sees the full picture.
    if "UscfColorConflict" not in store.df.columns:
        return []
    return reconcile(
        store.df, store.match_result, store.uscf.official_series,
        dismissed=frozenset(store.dismissed),
    )


def dismiss_reconciliation_entry(entry_id: str) -> None:
    """Dismiss a Reconciliation entry ("USCF is wrong" / "intentionally
    skipped").  Persists best-effort in the USCF cache (issue #30)."""
    store = _current()
    store.dismissed.add(entry_id)
    UscfCache(store.uscf_cache_path).add_dismissal(entry_id)


def get_official_series() -> list[OfficialRatingPoint]:
    """The Official Rating series: one point per supplement month, chronological."""
    return _current().uscf.official_series


def get_live_series() -> list[LiveRatingPoint]:
    """The Live Rating series: one point per Regular-rated Section, chronological,
    decimals preserved. Continuous: each post-rating is the next pre-rating."""
    return _current().uscf.live_series


def get_uscf_events() -> list[UscfEvent]:
    """Every Rated Event the member has entered, chronological (issue #33)."""
    return _current().uscf.member_events


def get_uscf_standings() -> dict[tuple[str, str], list[StandingEntry]]:
    """The crosstables of played OTB Sections (issue #34), keyed by
    (event_id, section name)."""
    return _current().uscf.standings


def get_opponent_profiles() -> dict[str, UscfProfile]:
    """Opponents' current USCF profiles keyed by member ID (issue #35)."""
    return _current().uscf.opponent_profiles


def get_uscf_achievements() -> list[UscfAchievement]:
    """The member's official achievements — norms and awards, chronological."""
    return _current().uscf.achievements


def get_new_achievements() -> list[UscfAchievement]:
    """Achievements first seen by the last Sync — the ones to celebrate (#36)."""
    return _current().new_achievements


def uscf_synced_at() -> datetime | None:
    """When USCF data was last successfully fetched (None if never)."""
    return _current().uscf.synced_at


def uscf_failure() -> str:
    """Why USCF data is unavailable ('' when it isn't, or USCF isn't configured)."""
    return _current().uscf.failure


def uscf_from_cache() -> bool:
    """True when the USCF data shown is the previous successful Sync's cache."""
    return _current().uscf.from_cache


def uscf_unavailable_since() -> str | None:
    """'USCF unavailable since <time>' when showing cached USCF data, else None."""
    uscf = _current().uscf
    if not uscf.from_cache:
        return None
    when = (f"{uscf.synced_at:%Y-%m-%d %H:%M} UTC" if uscf.synced_at
            else "an earlier run")
    return f"USCF unavailable since {when}"


def uscf_enabled() -> bool:
    """True when a USCF member ID is configured for the active store."""
    return _current().uscf_member_id is not None


def get_player() -> str:
    """Return the detected / configured player name."""
    return _current().player


def get_sync_failures() -> list[tuple[str, str]]:
    """(study_id, reason) for each designated Study the last Sync could not fetch."""
    return _current().sync_failures


def synced_at() -> datetime | None:
    """When the last successful Sync completed (UTC), or None if never."""
    return _current().synced_at


def source() -> str:
    """Where the current data came from: 'lichess' (live Sync) or 'cache'."""
    return _current().source


def cached_at() -> datetime | None:
    """When the cache being shown was written (only set when source() == 'cache')."""
    return _current().cached_at


def is_loaded() -> bool:
    """True if the active store has been successfully initialised."""
    return not _current().df.empty
