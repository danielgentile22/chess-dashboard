"""
data.py
=======
Application-wide data singleton.

The PGN is parsed exactly once (at startup) and stored as a module-level
DataFrame.  All callbacks read from ``get_df()`` — they never mutate it.
``apply_filters`` inside pgn_stats_core always copies before filtering,
so this is thread-safe for concurrent Dash callbacks.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from pgn_stats_core import load_games_df

logger = logging.getLogger(__name__)

_df: pd.DataFrame = pd.DataFrame()
_player: str = ""


def initialize(pgn_path: str, player_name: Optional[str] = None) -> tuple[pd.DataFrame, str]:
    """
    Parse *pgn_path* and cache the resulting DataFrame module-wide.

    Returns the (df, player_name) tuple so callers can use it immediately.
    Raises ``FileNotFoundError`` or ``RuntimeError`` on failure.
    """
    global _df, _player
    logger.info("Parsing PGN: %s", pgn_path)
    _df, _player = load_games_df(pgn_path, player_name=player_name)
    if _df.empty:
        raise RuntimeError(f"No games found in PGN: {pgn_path!r}")
    logger.info("Loaded %d games for player %r", len(_df), _player)
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
