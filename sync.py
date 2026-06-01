"""
sync.py
=======
Sync orchestrator: designated Lichess Studies → one merged Games DataFrame.

A Sync (see CONTEXT.md) fetches every designated Study, concatenates their
Games, dedupes by ChapterURL (the permanent Game identity — ADR 0001), and
sorts by date.  One Study failing never loses the Games of the Studies that
succeeded; only when *every* Study fails is the Sync itself a failure.

Public API
----------
sync_studies   Fetch + merge all designated Studies → SyncResult.
SyncResult     The outcome: merged df, player, per-Study failures.
SyncError      Raised when no designated Study could be fetched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from lichess_client import LichessError, fetch_study_pgn
from pgn_stats_core import load_games_from_text

logger = logging.getLogger(__name__)

__all__ = ["SyncError", "SyncResult", "sync_studies"]


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
) -> SyncResult:
    """
    Sync every designated Study and return the merged, deduped, date-sorted
    Games.

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

    df, player = load_games_from_text("\n\n".join(pgn_texts), player_name=player_name)
    df = _dedupe_and_sort(df)

    if failures:
        logger.warning(
            "Partial Sync: %d of %d Studies failed (%s); showing %d Games "
            "from the Studies that succeeded.",
            len(failures), len(study_ids),
            ", ".join(sid for sid, _ in failures), len(df),
        )

    return SyncResult(df=df, player=player, failures=failures)


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
