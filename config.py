"""
config.py
=========
Application configuration loaded from environment variables.
All settings have sensible local-dev defaults so nothing is required to run locally.
"""
from __future__ import annotations

import os

from user_config import UserRecord, parse_users


def parse_study_ids(raw: str) -> list[str]:
    """Parse a comma-separated list of Lichess study IDs ('a, b' → ['a', 'b'])."""
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_member_id(raw: str) -> str | None:
    """Parse the USCF member ID setting ('' / whitespace → None: USCF disabled)."""
    return raw.strip() or None


def parse_bool(raw: str) -> bool:
    """Parse a boolean env setting."""
    return raw.strip().lower() in ("1", "true", "yes")


class Config:
    # The shipped fallback session-signing key.  Fine for a private single-user
    # laptop, but multi-user auth refuses to start on it (issue #89): it is
    # public, so any cookie signed with it is forgeable.
    DEFAULT_SECRET_KEY: str = "dev-insecure-change-me"

    # Lichess Study IDs forming the game archive (ADR 0001), comma-separated.
    # Required for gunicorn deployment; supplied via --study CLI flag(s) locally.
    STUDY_IDS: list[str] = parse_study_ids(os.environ.get("LICHESS_STUDY_IDS", ""))

    # Optional Lichess API token — only needed if a designated Study is private.
    LICHESS_API_TOKEN: str | None = os.environ.get("LICHESS_API_TOKEN", "").strip() or None

    # USCF member ID whose record enriches the Games (ADR 0003).
    # Optional: without it the dashboard runs Lichess-only, exactly as before.
    USCF_MEMBER_ID: str | None = parse_member_id(os.environ.get("USCF_MEMBER_ID", ""))

    # Where the last successful Sync's PGN is cached for offline fallback.
    # Disposable, gitignored, never a source of truth (ADR 0001).
    CACHE_PATH: str = os.environ.get("CACHE_PATH", "games.pgn").strip()

    # The committed, anonymized game history demo mode boots from.  Unlike
    # CACHE_PATH this file is tracked, so it must never hold real opponent data
    # (regenerate it with scripts/anonymize_pgn.py).  Kept separate from
    # CACHE_PATH so a real Sync can never overwrite a tracked file.
    DEMO_CACHE_PATH: str = os.environ.get(
        "DEMO_CACHE_PATH", "tests/data/demo-games.pgn"
    ).strip()

    # Where USCF responses are cached so USCF surfaces survive the API being
    # down (ADR 0003). Disposable, gitignored, never a source of truth.
    USCF_CACHE_PATH: str = os.environ.get("USCF_CACHE_PATH", "uscf_cache.json").strip()

    # Optional Anthropic API key for the AI game summaries (issue #59 [F5]).
    # Without it the summary step is a no-op — the dashboard runs unchanged.
    ANTHROPIC_API_KEY: str | None = (
        os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
    )

    # Where engine-analysis AI summaries are cached so unchanged Games aren't
    # re-billed (issue #59).  Disposable, gitignored, never a source of truth
    # (ADR 0004), exactly like the USCF cache.
    ANALYSIS_CACHE_PATH: str = os.environ.get(
        "ANALYSIS_CACHE_PATH", "analysis_cache.json"
    ).strip()

    # Player name override. Empty string → auto-detect from the Games.
    PLAYER_NAME: str | None = os.environ.get("PLAYER_NAME", "").strip() or None

    # Demo mode: boot from the committed PGN cache only.  No external calls,
    # no cache writes, no auth gate.
    DEMO_MODE: bool = parse_bool(os.environ.get("DEMO_MODE", ""))

    # Multi-user access (issue #71 [G1]).  A JSON array of user records (see
    # user_config); empty means the dashboard runs single-user and ungated,
    # exactly as before.  A malformed block raises clearly here, at load.
    USERS: dict[str, UserRecord] = parse_users(os.environ.get("USCF_DASHBOARD_USERS", ""))

    # Signs the login session cookie (issue #71).  MUST be set to a stable,
    # secret value in any multi-user deployment so sessions survive restarts
    # and cannot be forged; the dev default is fine only for a private laptop.
    SECRET_KEY: str = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY).strip()

    # Where each user's disposable caches live (issue #72 [G2]): one subdirectory
    # per user under here, so users' PGN/USCF/analysis caches never collide.
    # Disposable like every other cache (ADR 0001/0003); a host without a
    # writable disk just goes without them.
    DATA_DIR: str = os.environ.get("DATA_DIR", ".user-data").strip()

    # Server binding
    HOST: str = os.environ.get("HOST", "127.0.0.1")
    PORT: int = int(os.environ.get("PORT", "8050"))

    # Enable Dash debug mode / hot reload
    DEBUG: bool = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


config = Config()
