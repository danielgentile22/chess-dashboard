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

from sync import SyncError, SyncResult, detect_new_games, load_from_cache, sync_studies

logger = logging.getLogger(__name__)

# --- store state (swapped atomically under _sync_lock) ---------------------
_df: pd.DataFrame = pd.DataFrame()
_player: str = ""
_sync_failures: list[tuple[str, str]] = []
_synced_at: datetime | None = None
_source: str = "lichess"          # "lichess" | "cache"
_cached_at: datetime | None = None  # only meaningful when _source == "cache"

# --- the designated Studies (remembered so refresh() can re-Sync) ----------
_study_ids: list[str] = []
_player_name: str | None = None
_token: str | None = None
_cache_path: str | None = None

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
) -> tuple[pd.DataFrame, str]:
    """
    Sync all designated Studies and cache the merged DataFrame module-wide.

    Remembers the Study list / token / cache path so ``refresh()`` can
    re-Sync later.  When Lichess is unreachable and a cache from a previous
    successful Sync exists at *cache_path*, the app boots from the cache
    instead (the dashboard never goes blank because Lichess is down).

    Raises
    ------
    sync.SyncError : no designated Study could be fetched AND no cache exists.
    RuntimeError   : the Studies contained no games.
    """
    global _study_ids, _player_name, _token, _cache_path
    _study_ids, _player_name, _token, _cache_path = (
        list(study_ids), player_name, token, cache_path,
    )

    logger.info("Syncing %d designated Studies from Lichess", len(study_ids))
    try:
        result = sync_studies(
            study_ids, player_name=player_name, token=token, cache_path=cache_path
        )
    except SyncError as exc:
        return _boot_from_cache(exc)

    if result.df.empty:
        raise RuntimeError(f"No games found in designated Studies: {study_ids}")
    _swap(result)
    logger.info("Synced %d games for player %r", len(_df), _player)
    return _df, _player


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
