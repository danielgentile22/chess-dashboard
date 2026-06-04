"""
analysis_trends.py
==================
The Analysis page's trend aggregates over the engine error profile (issue #61
[F3]) — the charts that were the whole point of reading engine data
automatically.

Pure, framework-agnostic, and deeply testable like the Phase-4 analytics it
mirrors (``pgn_stats_core.time_control_summary`` / ``upset_tracker``): a Games
DataFrame in — the one already carrying the ``Analysis`` /  ``Analyzed`` columns
``engine_analysis_core.enrich_games_with_analysis`` attaches — plain
DataFrames out, zero Dash imports.  Every aggregate excludes Games still
**awaiting analysis** (``analyzed=False``): they carry no profile and no
accuracy, so they simply contribute nothing without any special-casing.

Public API
----------
accuracy_trend         Per-Game accuracy over time, with the player's rating.
mistake_type_trend     Per-Game tactical/positional counts over time + rating.
phase_type_matrix      Mistakes as a phase × type matrix (the worst combination).
mistake_move_histogram Counts of mistakes by the move number they happened on.
"""
from __future__ import annotations

import pandas as pd

from engine_analysis_core import GameAnalysis

__all__ = [
    "accuracy_trend",
    "mistake_type_trend",
    "phase_type_matrix",
    "mistake_move_histogram",
]


_PHASE_ORDER = ["opening", "middlegame", "endgame"]
_TYPE_ORDER = ["tactical", "positional"]


def _analysis(row) -> GameAnalysis | None:
    """The row's GameAnalysis if it is an *analysed* one, else None."""
    analysis = row.get("Analysis")
    if not isinstance(analysis, GameAnalysis) or not analysis.analyzed:
        return None
    return analysis


# ---------------------------------------------------------------------------
# Per-Game accuracy, trended over time with rating
# ---------------------------------------------------------------------------

_ACCURACY_COLS = ["Date_dt", "Date", "Accuracy", "Rating", "Opponent", "ChapterURL"]


def accuracy_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Per-Game move accuracy over time — one quality number per analysed Game.

    One row per analysed Game with a known accuracy, oldest first, carrying the
    player's rating for that Game so the page can overlay it: as the rating
    climbs, does the accuracy hold?  ``Rating`` is the numeric player rating;
    ``Accuracy`` is the 0–100 number ``engine_analysis_core`` already computed.
    Games still awaiting analysis are excluded.
    """
    if df.empty or "Analysis" not in df.columns:
        return pd.DataFrame(columns=_ACCURACY_COLS)

    rows: list[dict] = []
    for _, row in df.iterrows():
        analysis = _analysis(row)
        if analysis is None or analysis.accuracy is None:
            continue
        rows.append({
            "Date_dt": row.get("Date_dt"),
            "Date": row.get("Date"),
            "Accuracy": analysis.accuracy,
            "Rating": row.get("PlayerRatingNum"),
            "Opponent": row.get("Opponent"),
            "ChapterURL": row.get("ChapterURL"),
        })
    if not rows:
        return pd.DataFrame(columns=_ACCURACY_COLS)

    out = pd.DataFrame(rows)
    return out.sort_values("Date_dt", na_position="last").reset_index(drop=True)


def _all_mistakes(df: pd.DataFrame):
    """Every Mistake in every analysed Game's error profile (a generator)."""
    for _, row in df.iterrows():
        analysis = _analysis(row)
        if analysis is None:
            continue
        yield from analysis.error_profile


# ---------------------------------------------------------------------------
# Phase × type matrix (find the worst specific combination)
# ---------------------------------------------------------------------------

def phase_type_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Mistakes as a phase × type matrix, summed across analysed Games.

    Rows are the game phases that actually carry a mistake (opening → middlegame
    → endgame), columns are ``tactical`` / ``positional``, cells are counts — so
    the worst specific combination (say tactical-middlegame) is the biggest
    number.  An absent combination reads as 0, never a KeyError.  Empty when no
    analysed Game has a mistake yet.
    """
    if df.empty or "Analysis" not in df.columns:
        return pd.DataFrame(columns=_TYPE_ORDER)

    counts: dict[tuple[str, str], int] = {}
    for mistake in _all_mistakes(df):
        key = (mistake.phase, mistake.mistake_type)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return pd.DataFrame(columns=_TYPE_ORDER)

    phases = [p for p in _PHASE_ORDER if any(phase == p for phase, _ in counts)]
    data = {t: [counts.get((p, t), 0) for p in phases] for t in _TYPE_ORDER}
    return pd.DataFrame(data, index=phases)


# ---------------------------------------------------------------------------
# Mistake-type trend over time, with rating overlaid
# ---------------------------------------------------------------------------

_TYPE_TREND_COLS = ["Date_dt", "Date", "Tactical", "Positional", "Rating",
                    "Opponent", "ChapterURL"]


def mistake_type_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Tactical/positional mistake counts per analysed Game, over time + rating.

    One row per analysed Game, oldest first, with that Game's count of
    ``Tactical`` and ``Positional`` mistakes and the player's ``Rating`` — so the
    page can watch tactical errors fall as positional ones grow with his level.
    Every analysed Game is a point (a clean Game contributes a 0/0 row) so the
    rating line stays continuous; Games awaiting analysis are excluded.
    """
    if df.empty or "Analysis" not in df.columns:
        return pd.DataFrame(columns=_TYPE_TREND_COLS)

    rows: list[dict] = []
    for _, row in df.iterrows():
        analysis = _analysis(row)
        if analysis is None:
            continue
        types = [m.mistake_type for m in analysis.error_profile]
        rows.append({
            "Date_dt": row.get("Date_dt"),
            "Date": row.get("Date"),
            "Tactical": types.count("tactical"),
            "Positional": types.count("positional"),
            "Rating": row.get("PlayerRatingNum"),
            "Opponent": row.get("Opponent"),
            "ChapterURL": row.get("ChapterURL"),
        })
    if not rows:
        return pd.DataFrame(columns=_TYPE_TREND_COLS)

    out = pd.DataFrame(rows)
    return out.sort_values("Date_dt", na_position="last").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Critical-mistake move-number histogram (the time-trouble signal)
# ---------------------------------------------------------------------------

_HISTOGRAM_COLS = ["MoveNumber", "Count"]


def mistake_move_histogram(df: pd.DataFrame) -> pd.DataFrame:
    """How many of the player's mistakes fall on each move number.

    Every error-profile mistake — inaccuracy, mistake, and blunder alike —
    tallied by the move number it happened on, ascending.  A spike late in the
    Game (say around move 40) is the fingerprint of time-trouble; a flat spread
    rules it out.  Empty when no analysed Game has a mistake yet.
    """
    if df.empty or "Analysis" not in df.columns:
        return pd.DataFrame(columns=_HISTOGRAM_COLS)

    counts: dict[int, int] = {}
    for mistake in _all_mistakes(df):
        counts[mistake.move_number] = counts.get(mistake.move_number, 0) + 1
    if not counts:
        return pd.DataFrame(columns=_HISTOGRAM_COLS)

    return pd.DataFrame(
        {"MoveNumber": sorted(counts), "Count": [counts[m] for m in sorted(counts)]}
    )
