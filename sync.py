"""
sync.py
=======
Sync orchestrator: designated Lichess Studies → one merged Games DataFrame.

A Sync (see CONTEXT.md) fetches every designated Study, concatenates their
Games, dedupes by ChapterURL (the permanent Game identity — ADR 0001), and
sorts by date.  One Study failing never loses the Games of the Studies that
succeeded; only when *every* Study fails is the Sync itself a failure.

A successful Sync also refreshes a local PGN cache so the dashboard can boot
when Lichess is unreachable.  The cache is disposable and never a source of
truth (ADR 0001); a host without a writable disk just goes without it.

Public API
----------
sync_studies      Fetch + merge all designated Studies → SyncResult.
detect_new_games  Which Games of a Sync are new vs. the previous one.
load_from_cache   Parse the PGN cache of the last successful Sync.
SyncResult        The outcome: merged df, player, per-Study failures.
SyncError         Raised when no designated Study could be fetched.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from lichess_client import LichessError, fetch_study_pgn
from pgn_stats_core import load_games_from_text

logger = logging.getLogger(__name__)

__all__ = [
    "SyncError",
    "SyncResult",
    "detect_new_games",
    "load_from_cache",
    "sync_studies",
]


class SyncError(Exception):
    """No designated Study could be fetched — there is nothing to show."""


@dataclass
class SyncResult:
    """The outcome of a Sync across all designated Studies."""

    df: pd.DataFrame
    player: str
    # (study_id, reason) for each Study that could not be fetched
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        """True if some (but not all) Studies failed to fetch."""
        return bool(self.failures)


def sync_studies(
    study_ids: list[str],
    player_name: str | None = None,
    token: str | None = None,
    cache_path: str | None = None,
) -> SyncResult:
    """
    Sync every designated Study and return the merged, deduped, date-sorted
    Games.

    When *cache_path* is given, a successful Sync also (over)writes the PGN
    cache there.  A failed cache write is logged and ignored — the cache is
    an offline fallback, never a requirement.

    Raises
    ------
    SyncError : every Study failed to fetch (the per-Study reasons are in
                the exception message).
    """
    pgn_texts: list[str] = []
    failures: list[tuple[str, str]] = []

    for study_id in study_ids:
        try:
            pgn_texts.append(fetch_study_pgn(study_id, token=token))
        except LichessError as exc:
            logger.warning("Could not fetch Study %r: %s", study_id, exc)
            failures.append((study_id, str(exc)))

    if not pgn_texts:
        details = "; ".join(f"{sid}: {reason}" for sid, reason in failures)
        raise SyncError(f"No designated Study could be fetched. {details}")

    merged_pgn = "\n\n".join(pgn_texts)
    df, player = load_games_from_text(merged_pgn, player_name=player_name)
    df = _dedupe_and_sort(df)

    if failures:
        logger.warning(
            "Partial Sync: %d of %d Studies failed (%s); showing %d Games "
            "from the Studies that succeeded.",
            len(failures), len(study_ids),
            ", ".join(sid for sid, _ in failures), len(df),
        )

    if cache_path:
        _write_cache(cache_path, merged_pgn)

    return SyncResult(df=df, player=player, failures=failures)


def load_from_cache(
    cache_path: str,
    player_name: str | None = None,
) -> tuple[pd.DataFrame, str, datetime]:
    """
    Parse the PGN cache of the last successful Sync.

    Returns (df, player, cached_at) where *cached_at* is when that Sync
    happened (the file's modification time, UTC).

    Raises FileNotFoundError if there is no cache.
    """
    with open(cache_path, encoding="utf-8", errors="ignore") as f:
        pgn_text = f.read()
    df, player = load_games_from_text(pgn_text, player_name=player_name)
    df = _dedupe_and_sort(df)
    cached_at = datetime.fromtimestamp(os.path.getmtime(cache_path), tz=timezone.utc)
    return df, player, cached_at


def _write_cache(cache_path: str, pgn_text: str) -> None:
    """Atomically (over)write the PGN cache; failures are logged, never raised."""
    try:
        tmp_path = f"{cache_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(pgn_text)
        os.replace(tmp_path, cache_path)
        logger.info("Wrote Sync cache: %s", cache_path)
    except OSError as exc:
        logger.warning("Could not write Sync cache %r (continuing without): %s",
                       cache_path, exc)


def detect_new_games(df: pd.DataFrame, previous_chapter_urls: set[str]) -> pd.DataFrame:
    """
    The Games in *df* that were not present in the previous Sync.

    Identity is the ChapterURL (ADR 0001); Games without one are never
    reported as new (they have no identity to compare).
    """
    if df.empty:
        return df
    is_new = (df["ChapterURL"] != "") & ~df["ChapterURL"].isin(previous_chapter_urls)
    return df[is_new]


def _dedupe_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop Games whose ChapterURL was already seen (first occurrence wins) and
    sort by date across all Studies.  Games stay comparable regardless of
    which Study they came from.
    """
    if df.empty:
        return df

    # Dedupe by ChapterURL — but never collapse Games without one
    # (a missing ChapterURL is "no identity", not a shared identity).
    has_url = df["ChapterURL"] != ""
    deduped = pd.concat([
        df[has_url].drop_duplicates(subset="ChapterURL", keep="first"),
        df[~has_url],
    ])

    # Sort by date (undated last), then original parse order for stable ties
    deduped = deduped.sort_values(
        ["Date_dt", "Index"], na_position="last"
    ).reset_index(drop=True)

    # Reassign Index to the merged chronological order so downstream
    # tie-breaking (streaks, milestones) is consistent across Studies.
    deduped["Index"] = range(1, len(deduped) + 1)

    return deduped
