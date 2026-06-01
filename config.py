"""
config.py
=========
Application configuration loaded from environment variables.
All settings have sensible local-dev defaults so nothing is required to run locally.
"""
from __future__ import annotations

import os


class Config:
    # Lichess Study ID forming the game archive (ADR 0001).
    # Required for gunicorn deployment; supplied via --study CLI flag locally.
    STUDY_ID: str = os.environ.get("LICHESS_STUDY_IDS", "").strip()

    # Optional Lichess API token — only needed if a designated Study is private.
    LICHESS_API_TOKEN: str | None = os.environ.get("LICHESS_API_TOKEN", "").strip() or None

    # Player name override. Empty string → auto-detect from the Games.
    PLAYER_NAME: str | None = os.environ.get("PLAYER_NAME", "").strip() or None

    # Server binding
    HOST: str = os.environ.get("HOST", "127.0.0.1")
    PORT: int = int(os.environ.get("PORT", "8050"))

    # Enable Dash debug mode / hot reload
    DEBUG: bool = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


config = Config()
