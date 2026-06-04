"""
tests/test_engine_analysis_core.py
==================================
The priority suite for the engine-analysis spine (issue #57 [F1]).

External behaviour only: given a real analysed Game's PGN, the right win%
values, swings, critical moment, and best moves come out — never the internal
arrangement of helper calls.  Fixtures are captured real PGN (the analysed
Alice Anderson Game) and small synthetic analysed movetext for the attribution
cases.  Network is blocked by the autouse ``no_network`` fixture; the
data-integration tests stub ``sync.fetch_study_pgn`` exactly like the rest of
the suite.
"""
from __future__ import annotations

import math
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from engine_analysis_core import (
    GameAnalysis,
    analyze_game,
    enrich_games_with_analysis,
    win_pct_from_cp,
)
from pgn_stats_core import load_games_from_text

FIXTURES = Path(__file__).parent / "fixtures"
GEORGINA_PGN = (FIXTURES / "analyzed-alice-anderson.pgn").read_text()
GEORGINA_URL = "https://lichess.org/study/abcdWXYZ/alic0001"


def _georgina() -> GameAnalysis:
    """The analysed Alice Anderson Game, parsed the way a Sync produces it."""
    df, _player = load_games_from_text(GEORGINA_PGN, player_name="Daniel Gentile")
    row = df.iloc[0]
    return analyze_game(
        row["Movetext"],
        player_color=row["Color"],
        player_outcome=row["Outcome"],
        chapter_url=row["ChapterURL"],
    )


# ---------------------------------------------------------------------------
# The win-probability formula (against published Lichess values)
# ---------------------------------------------------------------------------

class TestWinPctFormula:
    """``win_pct_from_cp`` is the canonical Lichess curve."""

    def test_equal_position_is_fifty(self):
        assert win_pct_from_cp(0) == pytest.approx(50.0)

    @pytest.mark.parametrize("cp, expected", [
        (100, 59.10),
        (200, 67.62),
        (300, 75.11),
        (-100, 40.90),
        (-300, 24.89),
        (54, 54.95),    # the position before Alice's 16. Bd4
        (-438, 16.62),  # the position after it
        (1000, 97.55),
    ])
    def test_published_reference_values(self, cp, expected):
        assert win_pct_from_cp(cp) == pytest.approx(expected, abs=0.05)

    def test_is_symmetric_about_fifty(self):
        for cp in (37, 120, 455, 999):
            assert win_pct_from_cp(cp) + win_pct_from_cp(-cp) == pytest.approx(100.0)

    def test_is_monotonic_and_bounded(self):
        vals = [win_pct_from_cp(cp) for cp in range(-2000, 2001, 100)]
        assert vals == sorted(vals)
        assert 0.0 < vals[0] < 1.0           # deep negative → ~0
        assert 99.0 < vals[-1] < 100.0       # deep positive → ~100

    def test_matches_the_raw_formula(self):
        # Guards the exact constant: 50 + 50*(2/(1+e^(-0.00368208*cp)) - 1).
        cp = 173
        expected = 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)
        assert win_pct_from_cp(cp) == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# The whole parser against the real analysed Alice Anderson Game
# ---------------------------------------------------------------------------

class TestGeorginaChin:
    """The known fixture: d3?! inaccuracy, g5? mistake, Bd4?? blunder, the
    −4.38 swing as the critical moment (issue #57)."""

    def test_game_is_analyzed_with_one_eval_per_ply(self):
        ga = _georgina()
        assert ga.analyzed is True
        assert ga.chapter_url == GEORGINA_URL
        assert len(ga.moves) == 48  # 24 full moves played out

    def test_critical_moment_is_white_move_16_Bd4(self):
        cm = _georgina().critical_moment
        assert cm is not None
        assert cm.move_number == 16
        assert cm.side == "White"
        assert cm.san == "Bd4"
        # The −4.38 swing the issue calls out: eval crashed from +0.54 to −4.38
        assert cm.eval_before == pytest.approx(0.54)
        assert cm.eval_after == pytest.approx(-4.38)
        # ...a ~38-point win-probability swing
        assert cm.win_pct_swing == pytest.approx(38.3, abs=0.3)

    def test_critical_moment_attributed_to_the_opponent(self):
        # Daniel was Black and won; the blunder was the opponent's.
        cm = _georgina().critical_moment
        assert cm.by_player is False
        assert "opponent" in cm.headline.lower()
        assert "blunder" in cm.headline.lower()
        assert "move 16" in cm.headline
        assert "Bd4" in cm.headline
        assert cm.headline.startswith("Won")

    def _move(self, ga, move_number, side):
        return next(m for m in ga.moves
                    if m.move_number == move_number and m.side == side)

    def test_inaccuracy_carries_best_move_and_refutation(self):
        ga = _georgina()
        d3 = self._move(ga, 3, "White")  # "Inaccuracy. d4 was best."
        assert d3.san == "d3"
        assert d3.best_move == "d4"
        assert d3.refutation_line[:3] == ["d4", "dxe4", "Nxe4"]
        assert d3.eval_before == pytest.approx(0.38)
        assert d3.eval_after == pytest.approx(-0.17)

    def test_mistake_is_blacks_and_win_pct_is_mover_relative(self):
        ga = _georgina()
        g5 = self._move(ga, 15, "Black")  # "Mistake. h5 was best."
        assert g5.san == "g5"
        assert g5.best_move == "h5"
        # Win% is from the mover's side: Black went from ~60% to ~45%.
        assert g5.win_pct_before == pytest.approx(59.8, abs=0.3)
        assert g5.win_pct_after == pytest.approx(45.0, abs=0.3)
        assert g5.win_pct_drop == pytest.approx(g5.win_pct_before - g5.win_pct_after)
        assert g5.win_pct_drop == pytest.approx(14.8, abs=0.3)

    def test_blunder_move_has_the_recommended_line(self):
        ga = _georgina()
        bd4 = self._move(ga, 16, "White")  # "Blunder. Nh5 was best."
        assert bd4.best_move == "Nh5"
        assert bd4.refutation_line[:2] == ["Nh5", "Bxh2+"]
        assert bd4.eval_after == pytest.approx(-4.38)

    def test_a_quiet_move_has_no_judgment_data(self):
        ga = _georgina()
        e4 = self._move(ga, 1, "White")
        assert e4.san == "e4"
        assert e4.best_move is None
        assert e4.refutation_line == []
        assert e4.win_pct_drop == pytest.approx(0.0, abs=2.0)  # move 1 is no swing


# ---------------------------------------------------------------------------
# Attribution: the player's own blunder
# ---------------------------------------------------------------------------

# Synthetic analysed movetext: White (the player) hangs the queen and loses.
# The biggest swing is White's 3. Qxe5??.
PLAYER_BLUNDER = (
    "1. e4 { [%eval 0.2] } 1... e5 { [%eval 0.1] } "
    "2. Qh5 { [%eval 0.2] } 2... Nc6 { [%eval 0.1] } "
    "3. Qxe5?? { [%eval -8.5] } { Blunder. Nf3 was best. } (3. Nf3 Nf6 4. Bc4) "
    "3... Nxe5 { [%eval -8.6] } 0-1"
)


class TestAttribution:
    def test_player_blunder_when_they_lost(self):
        cm = analyze_game(
            PLAYER_BLUNDER, player_color="White", player_outcome="Loss"
        ).critical_moment
        assert cm is not None
        assert cm.side == "White"
        assert cm.san == "Qxe5+"  # the queen grab is check, then it's lost
        assert cm.move_number == 3
        assert cm.by_player is True
        assert cm.headline.startswith("Lost")
        assert "your blunder" in cm.headline.lower()

    def test_perspective_unknown_still_yields_a_neutral_headline(self):
        # No player_color/outcome (headless use) → never raises, still framed.
        cm = analyze_game(PLAYER_BLUNDER).critical_moment
        assert cm is not None
        assert cm.by_player is False  # can't attribute without a color
        assert "critical moment" in cm.headline.lower()


# ---------------------------------------------------------------------------
# Degrade-to-analyzed=False (no requested analysis is never an error)
# ---------------------------------------------------------------------------

class TestDegradesCleanly:
    def test_plain_game_with_no_evals_is_unanalyzed(self):
        ga = analyze_game("1. e4 e5 2. Nf3 Nc6 1-0",
                           player_color="White", player_outcome="Win")
        assert ga.analyzed is False
        assert ga.moves == []
        assert ga.critical_moment is None

    def test_comments_without_evals_are_still_unanalyzed(self):
        ga = analyze_game("1. e4 { a note, no eval } e5 { #tactics } 1-0")
        assert ga.analyzed is False

    def test_empty_and_blank_movetext(self):
        assert analyze_game("").analyzed is False
        assert analyze_game("   \n  ").analyzed is False

    def test_garbage_movetext_never_raises(self):
        ga = analyze_game("this is not pgn at all {{{", chapter_url="u")
        assert ga.analyzed is False
        assert ga.chapter_url == "u"

    def test_unanalyzed_keeps_its_chapter_url(self):
        ga = analyze_game("1. e4 e5 1-0", chapter_url="https://x/y")
        assert ga.chapter_url == "https://x/y"


# ---------------------------------------------------------------------------
# enrich_games_with_analysis (mirrors uscf_core.enrich_games)
# ---------------------------------------------------------------------------

# A 2-game Study export: one analysed (Daniel White, opponent blunders),
# one with no requested analysis.
MIXED_PGN = """\
[Event "Test"]
[White "Daniel Gentile"]
[Black "Foe One"]
[Result "1-0"]
[StudyName "S"]
[ChapterName "Daniel Gentile - Foe One"]
[ChapterURL "https://lichess.org/study/s/analyzedA"]

1. e4 { [%eval 0.2] } 1... e5 { [%eval 0.1] } 2. Qh5 { [%eval 0.0] } 2... Nf6?? { [%eval 9.9] } { Blunder. Nc6 was best. } (2... Nc6 3. Bc4) 3. Qxe5+ { [%eval 9.8] } 1-0

[Event "Test"]
[White "Foe Two"]
[Black "Daniel Gentile"]
[Result "0-1"]
[StudyName "S"]
[ChapterName "Foe Two - Daniel Gentile"]
[ChapterURL "https://lichess.org/study/s/plainB"]

1. d4 d5 2. c4 e6 0-1
"""


class TestEnrich:
    def _mixed(self):
        df, _player = load_games_from_text(MIXED_PGN, player_name="Daniel Gentile")
        return enrich_games_with_analysis(df)

    def test_adds_columns_that_always_exist(self):
        out = self._mixed()
        assert "Analysis" in out.columns
        assert "Analyzed" in out.columns
        assert all(isinstance(a, GameAnalysis) for a in out["Analysis"])

    def test_does_not_mutate_the_input(self):
        df, _ = load_games_from_text(MIXED_PGN, player_name="Daniel Gentile")
        before = list(df.columns)
        enrich_games_with_analysis(df)
        assert list(df.columns) == before  # the copy carried the new columns

    def test_flags_the_analyzed_and_unanalyzed_games(self):
        out = self._mixed()
        by_url = {r["ChapterURL"]: r for _, r in out.iterrows()}
        a = by_url["https://lichess.org/study/s/analyzedA"]
        b = by_url["https://lichess.org/study/s/plainB"]
        assert bool(a["Analyzed"]) is True
        assert bool(b["Analyzed"]) is False
        assert a["Analysis"].critical_moment is not None
        assert a["Analysis"].critical_moment.san == "Nf6"  # opponent's blunder
        assert b["Analysis"].analyzed is False

    def test_empty_df_still_gets_the_columns(self):
        out = enrich_games_with_analysis(pd.DataFrame())
        assert list(out.columns) == ["Analysis", "Analyzed"]
        assert out.empty

    def test_a_bad_movetext_row_degrades_instead_of_failing(self):
        df = pd.DataFrame([
            {"ChapterURL": "u", "Color": "White", "Outcome": "Win",
             "Movetext": None},  # None movetext must not blow up the pass
        ])
        out = enrich_games_with_analysis(df)
        assert bool(out.iloc[0]["Analyzed"]) is False
        assert isinstance(out.iloc[0]["Analysis"], GameAnalysis)


# ---------------------------------------------------------------------------
# Sync / data integration: the ingestion pass and the accessors
# ---------------------------------------------------------------------------

@pytest.fixture
def store_from_pgn():
    """Initialise the real data store from a PGN, USCF off, Lichess stubbed."""
    import data
    import sync

    def _init(pgn_text: str):
        data.reset()
        with mock.patch.object(sync, "fetch_study_pgn", return_value=pgn_text):
            data.initialize(["analyzed-study"], player_name="Daniel Gentile")
        return data

    yield _init
    data.reset()


class TestSyncIntegration:
    def test_sync_attaches_analysis_and_exposes_it(self, store_from_pgn):
        data = store_from_pgn(GEORGINA_PGN)
        assert data.is_loaded() is True
        assert "Analysis" in data.get_df().columns
        ga = data.get_game_analysis(GEORGINA_URL)
        assert ga.analyzed is True
        assert ga.critical_moment.san == "Bd4"

    def test_sync_succeeds_when_nothing_is_analyzed(self, store_from_pgn):
        # A Sync that reaches Lichess succeeds regardless of analysis state.
        data = store_from_pgn(MIXED_PGN)
        assert data.is_loaded() is True
        # plainB has no analysis; the Sync still loaded every Game.
        assert len(data.get_df()) == 2

    def test_awaiting_analysis_lists_only_unanalyzed_chapters(self, store_from_pgn):
        data = store_from_pgn(MIXED_PGN)
        awaiting = data.get_awaiting_analysis()
        urls = set(awaiting["ChapterURL"])
        assert urls == {"https://lichess.org/study/s/plainB"}

    def test_get_game_analysis_is_total(self, store_from_pgn):
        data = store_from_pgn(MIXED_PGN)
        # Unknown URL → an empty analysis, never a KeyError.
        miss = data.get_game_analysis("https://lichess.org/study/s/nope")
        assert isinstance(miss, GameAnalysis)
        assert miss.analyzed is False
        # Blank URL likewise.
        assert data.get_game_analysis("").analyzed is False

    def test_accessors_exist_before_any_sync(self):
        import data
        data.reset()
        assert data.get_game_analysis(GEORGINA_URL).analyzed is False
        assert data.get_awaiting_analysis().empty

    def test_refresh_keeps_the_analysis(self, store_from_pgn):
        import sync
        data = store_from_pgn(GEORGINA_PGN)
        with mock.patch.object(sync, "fetch_study_pgn", return_value=GEORGINA_PGN):
            outcome = data.refresh()
        assert outcome.status == "success"
        assert data.get_game_analysis(GEORGINA_URL).analyzed is True
