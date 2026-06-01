"""
config.py
=========
Application configuration loaded from environment variables.
All settings have sensible local-dev defaults so nothing is required to run locally.
"""
from __future__ import annotations

import os


def parse_study_ids(raw: str) -> list[str]:
    """Parse a comma-separated list of Lichess study IDs ('a, b' → ['a', 'b'])."""
    return [s.strip() for s in raw.split(",") if s.strip()]


class Config:
    # Lichess Study IDs forming the game archive (ADR 0001), comma-separated.
    # Required for gunicorn deployment; supplied via --study CLI flag(s) locally.
    STUDY_IDS: list[str] = parse_study_ids(os.environ.get("LICHESS_STUDY_IDS", ""))

    # Optional Lichess API token — only needed if a designated Study is private.
    LICHESS_API_TOKEN: str | None = os.environ.get("LICHESS_API_TOKEN", "").strip() or None

    # Where the last successful Sync's PGN is cached for offline fallback.
    # Disposable, gitignored, never a source of truth (ADR 0001).
    CACHE_PATH: str = os.environ.get("CACHE_PATH", "games.pgn").strip()

    # Player name override. Empty string → auto-detect from the Games.
    PLAYER_NAME: str | None = os.environ.get("PLAYER_NAME", "").strip() or None

    # Server binding
    HOST: str = os.environ.get("HOST", "127.0.0.1")
    PORT: int = int(os.environ.get("PORT", "8050"))

    # Enable Dash debug mode / hot reload
    DEBUG: bool = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


config = Config()
