"""
tests/test_analysis_trends.py
=============================
The priority suite for ``analysis_trends`` (issue #61 [F3]) — the rest of the
Analysis-page aggregates over the engine error profile.

DataFrame-in → data-out, mirroring the Phase-4 analytics tests
(``test_pgn_stats_core`` for ``time_control_summary`` / ``upset_tracker``): each
test feeds a small Games frame whose ``Analysis`` column holds hand-built
:class:`GameAnalysis` objects and asserts the shape of the aggregate that comes
back — never the internal arrangement of pandas calls.  Building the
GameAnalysis directly (rather than parsing PGN) keeps full control over each
Game's accuracy, mistakes, date, and rating.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from analysis_trends import (
    accuracy_trend,
    mistake_move_histogram,
    mistake_type_trend,
    phase_type_matrix,
)
from engine_analysis_core import GameAnalysis, Mistake, enrich_games_with_analysis
from pgn_stats_core import load_games_from_text

GEORGINA_PGN = (Path(__file__).parent / "fixtures" / "analyzed-georgina-chin.pgn").read_text()


def _mistake(phase: str, mistake_type: str, *, move_number: int = 20,
             severity: str = "mistake") -> Mistake:
    return Mistake(
        ply=move_number * 2, move_number=move_number, san="x",
        severity=severity, phase=phase, mistake_type=mistake_type,
        win_pct_drop=25.0,
    )


def _row(date: str, rating, *, analyzed: bool = True, accuracy=None,
         mistakes=(), opponent: str = "Foe", url: str = "u") -> dict:
    """One Games-frame row carrying a hand-built GameAnalysis."""
    ga = GameAnalysis(
        chapter_url=url, analyzed=analyzed,
        error_profile=list(mistakes), accuracy=accuracy,
    )
    return {
        "Date": date,
        "Date_dt": pd.Timestamp(date) if date else pd.NaT,
        "PlayerRatingNum": rating,
        "Opponent": opponent,
        "ChapterURL": url,
        "Color": "White",
        "Analysis": ga,
        "Analyzed": analyzed,
    }


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Per-Game accuracy, trended over time with rating
# ---------------------------------------------------------------------------

class TestAccuracyTrend:
    def test_one_row_per_analyzed_game_oldest_first_with_rating(self):
        df = _df([
            _row("2026.03.01", 1500, accuracy=90.0, opponent="A", url="a"),
            _row("2026.01.01", 1450, accuracy=80.0, opponent="B", url="b"),
        ])
        out = accuracy_trend(df)
        assert list(out["Accuracy"]) == [80.0, 90.0]   # oldest → newest
        assert list(out["Rating"]) == [1450, 1500]
        assert list(out["ChapterURL"]) == ["b", "a"]

    def test_excludes_awaiting_analysis_games(self):
        df = _df([
            _row("2026.01.01", 1450, accuracy=80.0, url="a"),
            _row("2026.02.01", 1460, analyzed=False, accuracy=None, url="b"),
        ])
        out = accuracy_trend(df)
        assert list(out["ChapterURL"]) == ["a"]   # the unanalysed Game is gone

    def test_a_store_with_no_analyzed_games_is_empty(self):
        # Games loaded but none analysed yet — a populated frame, nothing to plot.
        df = _df([_row("2026.01.01", 1450, analyzed=False)])
        assert accuracy_trend(df).empty

    def test_empty_frame_yields_the_empty_shape(self):
        out = accuracy_trend(pd.DataFrame())
        assert out.empty
        assert list(out.columns) == [
            "Date_dt", "Date", "Accuracy", "Rating", "Opponent", "ChapterURL"]


# ---------------------------------------------------------------------------
# Mistake-type trend over time, with rating overlaid
# ---------------------------------------------------------------------------

class TestMistakeTypeTrend:
    def test_per_game_type_counts_over_time_with_rating(self):
        df = _df([
            _row("2026.02.01", 1500, url="late", mistakes=[
                _mistake("middlegame", "positional"),
            ]),
            _row("2026.01.01", 1450, url="early", mistakes=[
                _mistake("middlegame", "tactical"),
                _mistake("endgame", "tactical"),
            ]),
        ])
        out = mistake_type_trend(df)
        assert list(out["ChapterURL"]) == ["early", "late"]   # oldest first
        assert list(out["Tactical"]) == [2, 0]
        assert list(out["Positional"]) == [0, 1]
        assert list(out["Rating"]) == [1450, 1500]

    def test_a_clean_game_is_still_a_point_so_the_rating_line_is_continuous(self):
        df = _df([
            _row("2026.01.01", 1450, url="clean", mistakes=[]),
            _row("2026.02.01", 1460, analyzed=False, url="awaiting"),
        ])
        out = mistake_type_trend(df)
        assert list(out["ChapterURL"]) == ["clean"]   # awaiting one excluded
        assert list(out["Tactical"]) == [0]
        assert list(out["Positional"]) == [0]

    def test_a_store_with_no_analyzed_games_is_empty(self):
        df = _df([_row("2026.01.01", 1450, analyzed=False)])
        assert mistake_type_trend(df).empty

    def test_empty_frame_yields_the_empty_shape(self):
        out = mistake_type_trend(pd.DataFrame())
        assert out.empty
        assert "Tactical" in out.columns and "Positional" in out.columns


# ---------------------------------------------------------------------------
# Phase × type matrix (find the worst specific combination)
# ---------------------------------------------------------------------------

class TestPhaseTypeMatrix:
    def test_counts_mistakes_by_phase_and_type_across_games(self):
        df = _df([
            _row("2026.01.01", 1450, mistakes=[
                _mistake("middlegame", "tactical"),
                _mistake("middlegame", "tactical"),
                _mistake("endgame", "positional"),
            ]),
            _row("2026.02.01", 1460, mistakes=[
                _mistake("middlegame", "tactical"),
            ]),
        ])
        matrix = phase_type_matrix(df)
        # His worst specific combination is tactical-middlegame.
        assert matrix.loc["middlegame", "tactical"] == 3
        assert matrix.loc["endgame", "positional"] == 1
        assert matrix.loc["endgame", "tactical"] == 0   # absent cell → 0, not KeyError
        assert list(matrix.columns) == ["tactical", "positional"]

    def test_awaiting_games_contribute_nothing(self):
        df = _df([
            _row("2026.01.01", 1450, mistakes=[_mistake("opening", "tactical")]),
            _row("2026.02.01", 1460, analyzed=False),
        ])
        matrix = phase_type_matrix(df)
        assert int(matrix.to_numpy().sum()) == 1

    def test_empty_is_an_empty_matrix(self):
        matrix = phase_type_matrix(pd.DataFrame())
        assert matrix.empty
        assert list(matrix.columns) == ["tactical", "positional"]


# ---------------------------------------------------------------------------
# Critical-mistake move-number histogram (the time-trouble signal)
# ---------------------------------------------------------------------------

class TestMistakeMoveHistogram:
    def test_counts_mistakes_by_the_move_number_they_happened_on(self):
        df = _df([
            _row("2026.01.01", 1450, mistakes=[
                _mistake("middlegame", "tactical", move_number=18),
                _mistake("middlegame", "positional", move_number=18),
                _mistake("opening", "tactical", move_number=8),
            ]),
            _row("2026.02.01", 1460, mistakes=[
                _mistake("endgame", "positional", move_number=18),
            ]),
        ])
        hist = mistake_move_histogram(df)
        assert list(hist["MoveNumber"]) == [8, 18]   # ascending move number
        assert list(hist["Count"]) == [1, 3]

    def test_every_severity_counts_inaccuracies_included(self):
        df = _df([
            _row("2026.01.01", 1450, mistakes=[
                _mistake("middlegame", "positional", move_number=30,
                         severity="inaccuracy"),
            ]),
        ])
        hist = mistake_move_histogram(df)
        assert list(hist["MoveNumber"]) == [30]
        assert list(hist["Count"]) == [1]

    def test_empty_frame_yields_the_empty_shape(self):
        hist = mistake_move_histogram(pd.DataFrame())
        assert hist.empty
        assert list(hist.columns) == ["MoveNumber", "Count"]


# ---------------------------------------------------------------------------
# Against the real enrichment pipeline (column wiring, not hand-built rows)
# ---------------------------------------------------------------------------

class TestAgainstTheRealPipeline:
    """The hand-built rows above pin the aggregate logic; this pins that the
    column names the trends read (PlayerRatingNum, Date_dt, Analysis) are the
    ones ``enrich_games_with_analysis`` actually produces."""

    def _enriched(self) -> pd.DataFrame:
        df, _ = load_games_from_text(GEORGINA_PGN, player_name="Daniel Gentile")
        return enrich_games_with_analysis(df)

    def test_the_analyzed_georgina_game_flows_through_every_aggregate(self):
        enriched = self._enriched()
        acc = accuracy_trend(enriched)
        assert len(acc) == 1
        assert 0.0 < acc.iloc[0]["Accuracy"] < 100.0

        trend = mistake_type_trend(enriched)
        assert len(trend) == 1

        matrix = phase_type_matrix(enriched)
        hist = mistake_move_histogram(enriched)
        # Daniel's own profile in this Game is non-empty (his g5 inaccuracy …),
        # and the same mistakes feed both the matrix and the histogram.
        assert int(hist["Count"].sum()) >= 1
        assert int(matrix.to_numpy().sum()) == int(hist["Count"].sum())
