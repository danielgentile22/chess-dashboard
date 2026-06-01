"""
data.py
=======
Application-wide data store.

At startup the designated Lichess Studies are Synced (fetched, merged,
deduped) and stored as a module-level DataFrame.  All callbacks read from
``get_df()`` — they never mutate it.  ``apply_filters`` inside pgn_stats_core
always copies before filtering, so this is thread-safe for concurrent Dash
callbacks.
"""
from __future__ import annotations

import logging

import pandas as pd

from sync import sync_studies

logger = logging.getLogger(__name__)

_df: pd.DataFrame = pd.DataFrame()
_player: str = ""
_sync_failures: list[tuple[str, str]] = []


def initialize(
    study_ids: list[str],
    player_name: str | None = None,
    token: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """
    Sync all designated Studies and cache the merged DataFrame module-wide.

    Returns the (df, player_name) tuple so callers can use it immediately.

    Raises
    ------
    sync.SyncError : no designated Study could be fetched.
    RuntimeError   : the Studies contained no games.
    """
    global _df, _player, _sync_failures
    logger.info("Syncing %d designated Studies from Lichess", len(study_ids))
    result = sync_studies(study_ids, player_name=player_name, token=token)
    if result.df.empty:
        raise RuntimeError(f"No games found in designated Studies: {study_ids}")
    _df, _player, _sync_failures = result.df, result.player, result.failures
    logger.info("Synced %d games for player %r", len(_df), _player)
    return _df, _player


def get_df() -> pd.DataFrame:
    """Return the full (unfiltered) DataFrame. Never mutate the result."""
    return _df


def get_player() -> str:
    """Return the detected / configured player name."""
    return _player


def get_sync_failures() -> list[tuple[str, str]]:
    """(study_id, reason) for each designated Study the last Sync could not fetch."""
    return _sync_failures


def is_loaded() -> bool:
    """True if data has been successfully initialised."""
    return not _df.empty


def reset() -> None:
    """Clear the store (used by tests)."""
    global _df, _player, _sync_failures
    _df = pd.DataFrame()
    _player = ""
    _sync_failures = []
