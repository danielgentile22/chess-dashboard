"""
data.py
=======
Application-wide data store.

At startup the designated Lichess Study is Synced (fetched and parsed) and
stored as a module-level DataFrame.  All callbacks read from ``get_df()`` —
they never mutate it.  ``apply_filters`` inside pgn_stats_core always copies
before filtering, so this is thread-safe for concurrent Dash callbacks.
"""
from __future__ import annotations

import logging

import pandas as pd

from lichess_client import fetch_study_pgn
from pgn_stats_core import load_games_from_text

logger = logging.getLogger(__name__)

_df: pd.DataFrame = pd.DataFrame()
_player: str = ""


def initialize(
    study_id: str,
    player_name: str | None = None,
    token: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """
    Sync Study *study_id* from Lichess and cache the resulting DataFrame
    module-wide.

    Returns the (df, player_name) tuple so callers can use it immediately.

    Raises
    ------
    lichess_client.LichessError : Lichess could not be reached / study not found.
    RuntimeError                : The Study contained no games.
    """
    global _df, _player
    logger.info("Syncing Study %s from Lichess", study_id)
    pgn_text = fetch_study_pgn(study_id, token=token)
    df, player = load_games_from_text(pgn_text, player_name=player_name)
    if df.empty:
        raise RuntimeError(f"No games found in Study {study_id!r}")
    _df, _player = df, player
    logger.info("Synced %d games for player %r", len(_df), _player)
    return _df, _player


def get_df() -> pd.DataFrame:
    """Return the full (unfiltered) DataFrame. Never mutate the result."""
    return _df


def get_player() -> str:
    """Return the detected / configured player name."""
    return _player


def is_loaded() -> bool:
    """True if data has been successfully initialised."""
    return not _df.empty


def reset() -> None:
    """Clear the store (used by tests)."""
    global _df, _player
    _df = pd.DataFrame()
    _player = ""
