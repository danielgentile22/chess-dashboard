"""
config.py
=========
Application configuration loaded from environment variables.
All settings have sensible local-dev defaults so nothing is required to run locally.
"""
from __future__ import annotations

import os


class Config:
    # Path to the PGN file (required for gunicorn deployment; supplied via --pgn CLI locally)
    PGN_PATH: str = os.environ.get("PGN_PATH", "").strip()

    # Player name override. Empty string → auto-detect from PGN.
    PLAYER_NAME: str | None = os.environ.get("PLAYER_NAME", "").strip() or None

    # Server binding
    HOST: str = os.environ.get("HOST", "127.0.0.1")
    PORT: int = int(os.environ.get("PORT", "8050"))

    # Enable Dash debug mode / hot reload
    DEBUG: bool = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


config = Config()
