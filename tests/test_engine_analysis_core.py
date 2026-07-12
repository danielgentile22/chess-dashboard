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

import chess
import pandas as pd
import pytest

from engine_analysis_core import (
    GameAnalysis,
    Mistake,
    MoveEval,
    analyze_game,
    classify_phase,
    classify_severity,
    enrich_games_with_analysis,
    mistake_type_distribution,
    player_accuracy,
    tags_from_error_profile,
    win_pct_from_cp,
)
from pgn_stats_core import load_games_from_text

DATA_DIR = Path(__file__).parent / "data"
ALICE_PGN = (DATA_DIR / "analyzed-alice-anderson.pgn").read_text()
ALICE_URL = "https://lichess.org/study/abcdWXYZ/alic0001"


def _alice() -> GameAnalysis:
    """The analysed Alice Anderson Game, parsed the way a Sync produces it."""
    df, _player = load_games_from_text(ALICE_PGN, player_name="Daniel Gentile")
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
# Severity: inaccuracy | mistake | blunder from win%-drop thresholds (F2)
# ---------------------------------------------------------------------------

class TestSeverityThresholds:
    """``classify_severity`` is the 0.1 / 0.2 / 0.3 win-probability ladder,
    with a sub-inaccuracy swing being *no* mistake at all (None).

    Daniel's decision (issue #58): severity is recomputed from the win%-drop,
    never read from Lichess's text word.  So a move Lichess *labels* a mistake
    but whose recomputed drop is only ~15% is an *inaccuracy* here, and a ~5%
    swing Lichess calls an inaccuracy is not an error-profile entry at all.
    """

    def test_below_inaccuracy_is_not_a_mistake(self):
        assert classify_severity(0.0) is None
        assert classify_severity(9.99) is None
        assert classify_severity(-5.0) is None  # an improving move never qualifies

    def test_inaccuracy_band(self):
        assert classify_severity(10.0) == "inaccuracy"
        assert classify_severity(19.99) == "inaccuracy"

    def test_mistake_band(self):
        assert classify_severity(20.0) == "mistake"
        assert classify_severity(29.99) == "mistake"

    def test_blunder_band(self):
        assert classify_severity(30.0) == "blunder"
        assert classify_severity(95.0) == "blunder"

    def test_thresholds_on_the_three_known_alice_moves(self):
        # The priority "severity thresholds" check against real data: the three
        # annotated moves span the boundary exactly as their recomputed drops do.
        ga = _alice()

        def drop(move_number, side):
            return next(m.win_pct_drop for m in ga.moves
                        if m.move_number == move_number and m.side == side)

        assert classify_severity(drop(3, "White")) is None         # d3?! ~5% drop
        assert classify_severity(drop(15, "Black")) == "inaccuracy"  # g5? ~15% drop
        assert classify_severity(drop(16, "White")) == "blunder"     # Bd4?? ~38% drop


# ---------------------------------------------------------------------------
# Phase: a per-position port of Lichess's Divider (F2)
# ---------------------------------------------------------------------------

class TestPhaseDetection:
    """``classify_phase`` ports the Divider thresholds: endgame at majors+minors
    ≤ 6, middlegame at ≤ 10 (or a sparse home rank / high mixedness), else
    opening.  Checked against positions with known piece counts."""

    def test_starting_position_is_opening(self):
        # 14 majors+minors, full home ranks, near-zero mixedness.
        assert classify_phase(chess.Board()) == "opening"

    def test_six_or_fewer_majors_and_minors_is_endgame(self):
        # K+R vs K+N — two majors/minors total.
        board = chess.Board("8/8/4k3/8/8/4K3/4R3/5n2 w - - 0 1")
        assert classify_phase(board) == "endgame"

    def test_seven_to_ten_majors_and_minors_is_middlegame(self):
        # Q+R+B+N a side = 8 total: past the endgame line, under the midgame one.
        board = chess.Board(
            "rnbqk3/pppppppp/8/8/8/8/PPPPPPPP/RNBQK3 w - - 0 1"
        )
        assert classify_phase(board) == "middlegame"

    def test_sparse_home_rank_is_middlegame_despite_full_material(self):
        # 14 majors+minors (no endgame, no midgame by count), but White's back
        # rank holds only 3 pieces → the Divider's backrank-sparse trigger fires.
        board = chess.Board(
            "rnbqkbnr/pppppppp/8/8/2B5/2NQ1N2/1B6/4RRK1 w - - 0 1"
        )
        assert classify_phase(board) == "middlegame"


# ---------------------------------------------------------------------------
# The whole parser against the captured analysed Alice Anderson Game
# ---------------------------------------------------------------------------

class TestAliceAnderson:
    """The known fixture: d3?! inaccuracy, g5? mistake, Bd4?? blunder, the
    −4.38 swing as the critical moment (issue #57)."""

    def test_game_is_analyzed_with_one_eval_per_ply(self):
        ga = _alice()
        assert ga.analyzed is True
        assert ga.chapter_url == ALICE_URL
        assert len(ga.moves) == 48  # 24 full moves played out

    def test_critical_moment_is_white_move_16_Bd4(self):
        cm = _alice().critical_moment
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
        cm = _alice().critical_moment
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
        ga = _alice()
        d3 = self._move(ga, 3, "White")  # "Inaccuracy. d4 was best."
        assert d3.san == "d3"
        assert d3.best_move == "d4"
        assert d3.refutation_line[:3] == ["d4", "dxe4", "Nxe4"]
        assert d3.eval_before == pytest.approx(0.38)
        assert d3.eval_after == pytest.approx(-0.17)

    def test_mistake_is_blacks_and_win_pct_is_mover_relative(self):
        ga = _alice()
        g5 = self._move(ga, 15, "Black")  # "Mistake. h5 was best."
        assert g5.san == "g5"
        assert g5.best_move == "h5"
        # Win% is from the mover's side: Black went from ~60% to ~45%.
        assert g5.win_pct_before == pytest.approx(59.8, abs=0.3)
        assert g5.win_pct_after == pytest.approx(45.0, abs=0.3)
        assert g5.win_pct_drop == pytest.approx(g5.win_pct_before - g5.win_pct_after)
        assert g5.win_pct_drop == pytest.approx(14.8, abs=0.3)

    def test_blunder_move_has_the_recommended_line(self):
        ga = _alice()
        bd4 = self._move(ga, 16, "White")  # "Blunder. Nh5 was best."
        assert bd4.best_move == "Nh5"
        assert bd4.refutation_line[:2] == ["Nh5", "Bxh2+"]
        assert bd4.eval_after == pytest.approx(-4.38)

    def test_a_quiet_move_has_no_judgment_data(self):
        ga = _alice()
        e4 = self._move(ga, 1, "White")
        assert e4.san == "e4"
        assert e4.best_move is None
        assert e4.refutation_line == []
        assert e4.win_pct_drop == pytest.approx(0.0, abs=2.0)  # move 1 is no swing


# ---------------------------------------------------------------------------
# The error profile: the player's own non-none mistakes, regardless of result
# ---------------------------------------------------------------------------

class TestErrorProfile:
    """``GameAnalysis.error_profile`` is the player's own classified mistakes.

    On the analysed Alice Anderson Game, Daniel is Black and *won* (0-1); his one
    qualifying move is 15...g5, recorded even in a win so the improvement signal
    isn't polluted by the result (issue #58).  His opponent's 16. Bd4?? blunder
    and 3. d3?! inaccuracy are *not* his mistakes and stay out of his profile.
    """

    def test_records_only_the_players_own_mistakes_even_in_a_win(self):
        ga = _alice()
        assert all(isinstance(m, Mistake) for m in ga.error_profile)
        assert [m.san for m in ga.error_profile] == ["g5"]

    def test_the_recorded_mistake_carries_its_classification_and_move_number(self):
        g5 = _alice().error_profile[0]
        assert g5.move_number == 15
        assert g5.severity == "inaccuracy"   # ~15% drop, recomputed (not "Mistake")
        assert g5.phase == "middlegame"      # 10 majors+minors at move 15
        assert g5.mistake_type in {"tactical", "positional"}

    def test_excludes_the_opponents_blunder_and_inaccuracy(self):
        sans = [m.san for m in _alice().error_profile]
        assert "Bd4" not in sans  # the opponent's blunder is not Daniel's mistake
        assert "d3" not in sans   # nor the opponent's inaccuracy

    def test_unanalyzed_game_has_an_empty_profile(self):
        ga = analyze_game("1. e4 e5 2. Nf3 Nc6 1-0",
                          player_color="White", player_outcome="Win")
        assert ga.error_profile == []

    def test_without_a_player_color_there_is_no_profile(self):
        # Headless use can't tell whose mistakes are whose → empty, never a guess.
        ga = analyze_game(PLAYER_BLUNDER)
        assert ga.error_profile == []


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
# The mistake-type heuristic: tactical (forcing) vs positional (slow bleed)
# ---------------------------------------------------------------------------

# A slow positional bleed: White's 3. b3?! drifts the eval down with no forcing
# refutation, and the engine's best move (c4) is a quiet pawn push.
SLOW_BLEED = (
    "1. d4 { [%eval 0.2] } 1... d5 { [%eval 0.3] } "
    "2. Nf3 { [%eval 0.3] } 2... Nf6 { [%eval 0.3] } "
    "3. b3 $6 { [%eval -1.2] Inaccuracy. c4 was best. } ( 3. c4 e6 4. Nc3 ) "
    "3... e6 { [%eval -1.1] } 4. Bb2 { [%eval -1.0] } 1/2-1/2"
)

# A missed forcing tactic: White plays the quiet 4. h3?! when the engine's best
# move (Nxe5, a capture) was a forcing shot — even though the game drifts on
# quietly afterwards, so only the best-move-was-forcing signal can catch it.
MISSED_TACTIC = (
    "1. e4 { [%eval 0.3] } 1... e5 { [%eval 0.3] } "
    "2. Nf3 { [%eval 0.3] } 2... d6 { [%eval 0.4] } "
    "3. Bc4 { [%eval 0.4] } 3... Bg4 { [%eval 0.5] } "
    "4. h3 $6 { [%eval -0.9] Inaccuracy. Nxe5 was best. } "
    "( 4. Nxe5 dxe5 5. Qxg4 ) 4... Nf6 { [%eval -0.8] } 1/2-1/2"
)


class TestMistakeType:
    """The deterministic tactical/positional heuristic (issue #58)."""

    def test_hanging_material_to_a_forcing_capture_is_tactical(self):
        # White grabs a pawn and is punished by a forcing recapture of the queen.
        ga = analyze_game(PLAYER_BLUNDER, player_color="White", player_outcome="Loss")
        assert [m.san for m in ga.error_profile] == ["Qxe5+"]
        assert ga.error_profile[0].mistake_type == "tactical"

    def test_slow_eval_bleed_with_a_quiet_best_move_is_positional(self):
        ga = analyze_game(SLOW_BLEED, player_color="White", player_outcome="Draw")
        assert [m.san for m in ga.error_profile] == ["b3"]
        assert ga.error_profile[0].mistake_type == "positional"

    def test_missing_a_forcing_best_move_is_tactical(self):
        # The continuation is quiet; only "the best move was itself forcing" fires.
        ga = analyze_game(MISSED_TACTIC, player_color="White", player_outcome="Draw")
        assert [m.san for m in ga.error_profile] == ["h3"]
        assert ga.error_profile[0].mistake_type == "tactical"


# ---------------------------------------------------------------------------
# Per-Game accuracy (the published Lichess accuracy formula, issue #61 [F3])
# ---------------------------------------------------------------------------

def _move(side: str, win_pct_drop: float) -> MoveEval:
    """A MoveEval carrying just the side + win%-drop accuracy depends on."""
    return MoveEval(
        ply=1, move_number=1, side=side, san="x",
        eval_before=0.0, eval_after=0.0,
        win_pct_before=50.0, win_pct_after=50.0, win_pct_drop=win_pct_drop,
    )


class TestPlayerAccuracy:
    """``player_accuracy`` averages the per-move Lichess accuracy formula
    ``103.1668*exp(-0.04354*winLoss) - 3.1669`` over the player's own moves."""

    def test_is_the_mean_of_the_per_move_formula(self):
        moves = [_move("White", 0.0), _move("White", 20.0)]
        a0 = 103.1668 * math.exp(-0.04354 * 0.0) - 3.1669
        a1 = 103.1668 * math.exp(-0.04354 * 20.0) - 3.1669
        expected = (min(100.0, a0) + a1) / 2
        assert player_accuracy(moves, "White") == pytest.approx(expected)

    def test_only_the_players_own_moves_count(self):
        # The opponent's blunder must not flatter — or dent — the player's score.
        clean = [_move("White", 0.0), _move("White", 0.0)]
        with_opponent = clean + [_move("Black", 80.0)]
        assert player_accuracy(with_opponent, "White") == pytest.approx(
            player_accuracy(clean, "White")
        )

    def test_a_flawless_game_is_essentially_100(self):
        moves = [_move("White", 0.0), _move("White", -5.0)]  # one improving move
        assert player_accuracy(moves, "White") == pytest.approx(100.0, abs=0.001)

    def test_unknown_color_or_no_moves_is_none(self):
        assert player_accuracy([_move("White", 10.0)], "") is None
        assert player_accuracy([_move("Black", 10.0)], "White") is None
        assert player_accuracy([], "White") is None

    def test_analyze_game_carries_the_accuracy(self):
        # A holder of the GameAnalysis (the trend, a future Engine view) gets the
        # number ready — analysed Games carry it, unanalysed ones carry None.
        ga = _alice()
        assert ga.accuracy is not None
        assert 0.0 < ga.accuracy < 100.0
        plain = analyze_game("1. e4 e5 1-0", player_color="White")
        assert plain.accuracy is None

class TestMistakeTypeDistribution:
    """``mistake_type_distribution`` totals tactical vs positional mistakes across
    analysed Games — the "single biggest weakness at a glance" the page leads
    with.  Unanalysed Games contribute nothing (issue #58)."""

    def test_counts_each_type_across_analyzed_games(self):
        tactical = analyze_game(
            PLAYER_BLUNDER, player_color="White", player_outcome="Loss")
        positional = analyze_game(
            SLOW_BLEED, player_color="White", player_outcome="Draw")
        assert mistake_type_distribution([tactical, positional]) == {
            "tactical": 1, "positional": 1,
        }

    def test_unanalyzed_games_are_excluded_from_the_math(self):
        analyzed = analyze_game(
            PLAYER_BLUNDER, player_color="White", player_outcome="Loss")
        unanalyzed = analyze_game(
            "1. e4 e5 1-0", player_color="White", player_outcome="Win")
        assert mistake_type_distribution([analyzed, unanalyzed]) == {
            "tactical": 1, "positional": 0,
        }

    def test_no_analyzed_games_is_all_zero(self):
        assert mistake_type_distribution([]) == {"tactical": 0, "positional": 0}


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
# tags_from_error_profile (issue #62 [F4] — engine-emitted Tags)
# ---------------------------------------------------------------------------

def _mistake(severity="inaccuracy", phase="middlegame", mistake_type="positional"):
    """A Mistake with the three classifications that drive Tag emission; the ply
    / SAN / drop don't affect the mapping, so they get harmless defaults."""
    return Mistake(
        ply=7, move_number=4, san="Nf6", severity=severity, phase=phase,
        mistake_type=mistake_type, win_pct_drop=15.0,
    )


class TestTagsFromErrorProfile:
    def test_a_tactical_mistake_emits_the_tactics_tag(self):
        tags = tags_from_error_profile([_mistake(mistake_type="tactical")])
        assert "tactics" in tags

    def test_a_positional_mistake_emits_the_strategy_tag(self):
        tags = tags_from_error_profile([_mistake(mistake_type="positional")])
        assert "strategy" in tags
        assert "tactics" not in tags

    def test_a_blunder_severity_move_emits_the_blunder_tag(self):
        # Severity is its own axis: a blunder earns #blunder on top of its
        # tactical/positional Tag, while a mere inaccuracy never does.
        blunder = tags_from_error_profile(
            [_mistake(severity="blunder", mistake_type="tactical")]
        )
        assert "blunder" in blunder
        assert "tactics" in blunder
        inaccuracy = tags_from_error_profile([_mistake(severity="inaccuracy")])
        assert "blunder" not in inaccuracy

    def test_phase_maps_to_the_opening_and_endgame_tags(self):
        assert "opening" in tags_from_error_profile([_mistake(phase="opening")])
        assert "endgame" in tags_from_error_profile([_mistake(phase="endgame")])
        # The middlegame is the unmarked default — it earns no phase Tag.
        middle = tags_from_error_profile([_mistake(phase="middlegame")])
        assert "opening" not in middle and "endgame" not in middle

    def test_tags_are_deduplicated_and_in_taxonomy_order(self):
        # A profile that earns every emittable Tag, out of order and with a
        # duplicate, comes back unique and in the canonical taxonomy order.
        profile = [
            _mistake(phase="endgame", mistake_type="positional"),
            _mistake(phase="opening", severity="blunder", mistake_type="tactical"),
            _mistake(phase="opening", mistake_type="tactical"),  # duplicate signals
        ]
        assert tags_from_error_profile(profile) == [
            "opening", "tactics", "endgame", "blunder", "strategy",
        ]

    def test_an_empty_profile_earns_no_tags(self):
        assert tags_from_error_profile([]) == []


class TestEmittedTagsOnTheAnalysis:
    # analyzedA's movetext: Daniel (Black) plays the 3...b6 inaccuracy — an
    # opening-phase positional slip — so the Game tags itself #opening #strategy.
    _ANALYZED = (
        "1. d4 { [%eval 0.2] } 1... d5 { [%eval 0.2] } "
        "2. c4 { [%eval 0.2] } 2... Nf6 { [%eval 0.3] } "
        "3. Nc3 { [%eval 0.3] } 3... b6 $6 { [%eval 1.8] Inaccuracy. e6 was best. } "
        "( 3... e6 4. Nf3 ) 4. Bf4 { [%eval 1.7] } 0-1"
    )

    def test_analyze_game_carries_the_emitted_tags(self):
        analysis = analyze_game(self._ANALYZED, player_color="Black")
        # The Analysis carries exactly what its own error profile earns…
        assert analysis.emitted_tags == tags_from_error_profile(analysis.error_profile)
        # …which for his opening positional slip is #opening and #strategy.
        assert "opening" in analysis.emitted_tags
        assert "strategy" in analysis.emitted_tags

    def test_an_unanalysed_game_emits_no_tags(self):
        assert analyze_game("").emitted_tags == []


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
        # TagSources must exist on the empty store too — the non-empty path always
        # sets it, so the schema stays identical and accessors stay total (#5).
        assert list(out.columns) == ["Analysis", "Analyzed", "TagSources"]
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
# Engine Tags flow into the Tags column, source-tagged (issue #62 [F4])
# ---------------------------------------------------------------------------

# An analysed Game in which Daniel (Black) plays the 3...b6 inaccuracy — an
# opening-phase positional slip → the engine emits #opening and #strategy.
_ENGINE_TAGGED_PGN = """\
[Event "T"]
[White "Foe One"]
[Black "Daniel Gentile"]
[Result "0-1"]
[StudyName "S"]
[ChapterName "Foe One - Daniel Gentile"]
[ChapterURL "https://lichess.org/study/s/tagA"]

1. d4 { [%eval 0.2] } 1... d5 { [%eval 0.2] } 2. c4 { [%eval 0.2] } 2... Nf6 { [%eval 0.3] } 3. Nc3 { [%eval 0.3] } 3... b6 $6 { [%eval 1.8] Inaccuracy. e6 was best. } ( 3... e6 4. Nf3 ) 4. Bf4 { [%eval 1.7] } 0-1
"""

# The same analysed slip, but the Chapter also carries a hand-written Lesson
# with his own #endgame Tag — so we can prove his Tag survives and the engine's
# are added beside it (and that an overlap stays "his").
_HANDWRITTEN_PLUS_ENGINE_PGN = """\
[Event "T"]
[White "Foe One"]
[Black "Daniel Gentile"]
[Result "0-1"]
[StudyName "S"]
[ChapterName "Foe One - Daniel Gentile"]
[ChapterURL "https://lichess.org/study/s/tagA"]

{ Lesson: Don't loosen the queenside so early. #endgame #strategy }
{ Lesson: Don't loosen the queenside so early. #endgame #strategy }
1. d4 { [%eval 0.2] } 1... d5 { [%eval 0.2] } 2. c4 { [%eval 0.2] } 2... Nf6 { [%eval 0.3] } 3. Nc3 { [%eval 0.3] } 3... b6 $6 { [%eval 1.8] Inaccuracy. e6 was best. } ( 3... e6 4. Nf3 ) 4. Bf4 { [%eval 1.7] } 0-1
"""

# A Game with no requested analysis (no [%eval]) but a hand-written #endgame —
# the engine emits nothing, so his Tag must be left exactly as it was.
_UNANALYSED_TAGGED_PGN = """\
[Event "T"]
[White "Daniel Gentile"]
[Black "Foe Two"]
[Result "1-0"]
[StudyName "S"]
[ChapterName "Daniel Gentile - Foe Two"]
[ChapterURL "https://lichess.org/study/s/plainB"]

{ Lesson: Convert the extra pawn cleanly. #endgame }
1. e4 e5 2. Nf3 Nc6 1-0
"""


class TestEngineTagsIngestion:
    @staticmethod
    def _enriched(pgn):
        df, _ = load_games_from_text(pgn, player_name="Daniel Gentile")
        return enrich_games_with_analysis(df)

    def test_engine_tags_flow_into_the_tags_column_marked_engine(self):
        row = self._enriched(_ENGINE_TAGGED_PGN).iloc[0]
        assert "strategy" in row["Tags"]                  # the engine Tag is there
        assert row["TagSources"]["strategy"] == "engine"  # and sourced to the engine

    def test_hand_written_tags_survive_and_engine_tags_append_after(self):
        # His #endgame #strategy come first and stay "mine"; the engine's
        # opening-phase Tag is appended after, marked "engine".
        row = self._enriched(_HANDWRITTEN_PLUS_ENGINE_PGN).iloc[0]
        assert row["Tags"][:2] == ["endgame", "strategy"]   # his Tags, his order
        assert row["TagSources"]["endgame"] == "mine"
        assert "opening" in row["Tags"]
        assert row["Tags"].index("opening") >= 2            # appended after his
        assert row["TagSources"]["opening"] == "engine"

    def test_a_tag_he_also_wrote_stays_his(self):
        # The engine also earns #strategy here, but he wrote it — so it stays
        # "mine" (ADR 0002: nothing he wrote changes).
        row = self._enriched(_HANDWRITTEN_PLUS_ENGINE_PGN).iloc[0]
        assert row["Tags"].count("strategy") == 1           # not duplicated
        assert row["TagSources"]["strategy"] == "mine"

    def test_an_unanalysed_game_keeps_its_tags_untouched(self):
        # No evals → nothing emitted; his hand-written Tag is unchanged and
        # still sourced to him, never reframed as the engine's.
        out = self._enriched(_UNANALYSED_TAGGED_PGN).iloc[0]
        assert out["Tags"] == ["endgame"]
        assert out["TagSources"] == {"endgame": "mine"}


# Three analysed losses in which Daniel (White) blunders 2.Qg4?? — each earns
# the engine #blunder Tag, so a recurring weakness emerges with no hand-tagging.
def _three_blunder_losses() -> str:
    chapters = []
    for i, date in enumerate(("2024.01.01", "2024.02.01", "2024.03.01")):
        chapters.append(f"""\
[Event "T"]
[Date "{date}"]
[White "Daniel Gentile"]
[Black "Foe {i}"]
[Result "0-1"]
[StudyName "S"]
[ChapterName "Daniel Gentile - Foe {i}"]
[ChapterURL "https://lichess.org/study/s/blund{i}"]

1. e4 {{ [%eval 0.2] }} 1... e5 {{ [%eval 0.2] }} 2. Qg4 {{ [%eval -6.0] }} 2... d5 {{ [%eval -6.1] }} 0-1
""")
    return "\n".join(chapters)


class TestConsumersPickUpEngineTags:
    """The whole point of routing engine Tags into the Tags column: the Lessons
    page and recurring-weakness detection light up with no change to them."""

    @staticmethod
    def _enriched(pgn):
        df, _ = load_games_from_text(pgn, player_name="Daniel Gentile")
        return enrich_games_with_analysis(df)

    def test_lessons_can_be_filtered_by_an_engine_tag(self):
        # His Lesson Game earns #opening from the engine; filtering Lessons by
        # #opening — which he never typed — now finds it.
        from pgn_stats_core import lessons_table
        enriched = self._enriched(_HANDWRITTEN_PLUS_ENGINE_PGN)
        hit = lessons_table(enriched, tags=["opening"])
        assert len(hit) == 1
        assert hit.iloc[0]["ChapterURL"].endswith("/tagA")

    def test_recurring_weakness_detection_sees_engine_tags(self):
        # Three analysed losses, each an engine #blunder, surface as a recurring
        # weakness — the insight that was empty until games tagged themselves.
        from pgn_stats_core import recurring_weaknesses
        enriched = self._enriched(_three_blunder_losses())
        weak_tags = {w["tag"] for w in recurring_weaknesses(enriched)}
        assert "blunder" in weak_tags


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
        data = store_from_pgn(ALICE_PGN)
        assert data.is_loaded() is True
        assert "Analysis" in data.get_df().columns
        ga = data.get_game_analysis(ALICE_URL)
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
        assert data.get_game_analysis(ALICE_URL).analyzed is False
        assert data.get_awaiting_analysis().empty
        assert data.get_mistake_type_distribution() == {
            "tactical": 0, "positional": 0,
        }

    def test_mistake_type_distribution_comes_from_the_store(self, store_from_pgn):
        data = store_from_pgn(ALICE_PGN)
        # Daniel's only mistake in the analysed Alice Game is the positional g5.
        assert data.get_mistake_type_distribution() == {
            "tactical": 0, "positional": 1,
        }

    def test_trend_accessors_come_from_the_store(self, store_from_pgn):
        data = store_from_pgn(ALICE_PGN)
        # One analysed Game → one accuracy point, in range.
        acc = data.get_accuracy_trend()
        assert len(acc) == 1
        assert 0.0 < acc.iloc[0]["Accuracy"] < 100.0
        # Its single mistake (the positional g5) shows up across the aggregates.
        trend = data.get_mistake_type_trend()
        assert len(trend) == 1
        assert int(trend.iloc[0]["Positional"]) == 1
        assert int(data.get_phase_type_matrix().to_numpy().sum()) == 1
        assert int(data.get_mistake_move_histogram()["Count"].sum()) == 1

    def test_trend_accessors_exist_before_any_sync(self):
        import data
        data.reset()
        assert data.get_accuracy_trend().empty
        assert data.get_mistake_type_trend().empty
        assert data.get_phase_type_matrix().empty
        assert data.get_mistake_move_histogram().empty

    def test_refresh_keeps_the_analysis(self, store_from_pgn):
        import sync
        data = store_from_pgn(ALICE_PGN)
        with mock.patch.object(sync, "fetch_study_pgn", return_value=ALICE_PGN):
            outcome = data.refresh()
        assert outcome.status == "success"
        assert data.get_game_analysis(ALICE_URL).analyzed is True


# ---------------------------------------------------------------------------
# Mate-score parsing (issue #91): [%eval #N] must saturate the win% curve
# ---------------------------------------------------------------------------

# White is mating (2. Qh5 #4) then Black is mating (2... Nc6 #-3); both are
# annotations the parser must fold into a saturating centipawn magnitude.
MATE_PGN = (
    "1. e4 { [%eval 0.3] } 1... e5 { [%eval 0.2] } "
    "2. Qh5 { [%eval #4] } 2... Nc6 { [%eval #-3] } "
    "3. Bc4 { [%eval #2] } 0-1"
)


class TestMateScoreParsing:
    """A ``[%eval #N]`` mate score reads as a near-certain win for the mating
    side, from either colour's perspective (issue #91)."""

    def _move(self, ga, san):
        return next(m for m in ga.moves if m.san == san)

    def test_white_mate_saturates_to_a_full_white_advantage(self):
        ga = analyze_game(MATE_PGN, player_color="White", player_outcome="Win")
        qh5 = self._move(ga, "Qh5")            # 2. Qh5 { [%eval #4] }
        assert qh5.eval_after == pytest.approx(99.96, abs=0.05)  # Mate(+4) → +9996cp
        assert qh5.win_pct_after == pytest.approx(100.0, abs=0.5)  # White is mating

    def test_black_mate_saturates_to_a_full_mover_advantage(self):
        ga = analyze_game(MATE_PGN, player_color="Black", player_outcome="Win")
        nc6 = self._move(ga, "Nc6")            # 2... Nc6 { [%eval #-3] }
        assert nc6.eval_after == pytest.approx(-99.97, abs=0.05)  # Mate(-3) → -9997cp
        # Win% is the mover's: Black is the one mating, so ~100 from Black's side.
        assert nc6.win_pct_after == pytest.approx(100.0, abs=0.5)


# ---------------------------------------------------------------------------
# Missing-eval handling (issue #91): no carrying a stray/absent eval forward as
# a real, zero-drop, ~100%-accuracy move
# ---------------------------------------------------------------------------

# Only move 1 carries an [%eval]; a lone manual eval must not read as a fully
# analysed Game (coverage well below the threshold).
STRAY_EVAL_PGN = (
    "1. e4 { [%eval 0.2] } 1... e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O 1-0"
)

# 4 of 5 plies evaluated (0.8 coverage, just analysed); White's 2. Qh5 has no
# eval (a gap), and White's real blunder is 3. Nf3 { [%eval -8.0] }.
PARTIAL_COVERAGE_PGN = (
    "1. e4 { [%eval 0.3] } 1... e5 { [%eval 0.2] } "
    "2. Qh5 2... Nc6 { [%eval 0.2] } "
    "3. Nf3 { [%eval -8.0] } 0-1"
)


class TestMissingEvalHandling:
    def test_a_single_stray_eval_is_not_a_fully_analysed_game(self):
        ga = analyze_game(STRAY_EVAL_PGN, player_color="White", player_outcome="Win")
        assert ga.analyzed is False   # coverage below the threshold → awaiting analysis
        assert ga.moves == []
        assert ga.accuracy is None
        assert ga.error_profile == []

    def test_the_eval_less_move_is_marked_and_excluded_from_accuracy(self):
        ga = analyze_game(
            PARTIAL_COVERAGE_PGN, player_color="White", player_outcome="Loss"
        )
        assert ga.analyzed is True
        qh5 = next(m for m in ga.moves if m.san == "Qh5")
        assert qh5.has_eval is False              # no [%eval] on that move
        assert qh5.win_pct_drop == pytest.approx(0.0)  # carried forward → no swing
        # Accuracy averages only the two *evaluated* White moves (a ~100 and the
        # ~10 blunder → ~55). Had the carried-forward Qh5 counted as a phantom
        # 100, the mean would jump toward ~70 — so it must stay well under 62.
        assert 45.0 < ga.accuracy < 62.0

    def test_a_carried_forward_move_is_never_a_phantom_mistake(self):
        ga = analyze_game(
            PARTIAL_COVERAGE_PGN, player_color="White", player_outcome="Loss"
        )
        # The eval-less Qh5 must not appear; only the genuinely evaluated blunder.
        assert [m.san for m in ga.error_profile] == ["Nf3"]
        assert ga.error_profile[0].severity == "blunder"


# ---------------------------------------------------------------------------
# Refutation sibling selection (issue #91): the engine's line, not the player's
# hand-written sideline serialised first
# ---------------------------------------------------------------------------

# 3. b3?! has two siblings: the player's hand line (3. Nc3, no eval, FIRST) and
# the engine's line (3. c4, carrying [%eval]); the judgment names c4 as best.
REFUTATION_NAMED_PGN = (
    "1. d4 { [%eval 0.2] } 1... d5 { [%eval 0.2] } "
    "2. Nf3 { [%eval 0.2] } 2... Nf6 { [%eval 0.2] } "
    "3. b3 { [%eval -1.0] Inaccuracy. c4 was best. } "
    "( 3. Nc3 e6 ) "
    "( 3. c4 { [%eval 0.3] } dxc4 { [%eval 0.2] } ) "
    "3... e6 { [%eval -0.9] } 0-1"
)

# Same shape, but no "X was best" text — the eval on the second sibling is the
# only signal distinguishing the engine's line from the hand sideline.
REFUTATION_EVAL_PGN = (
    "1. d4 { [%eval 0.2] } 1... d5 { [%eval 0.2] } "
    "2. Nf3 { [%eval 0.2] } 2... Nf6 { [%eval 0.2] } "
    "3. b3 { [%eval -1.0] } "
    "( 3. Nc3 e6 ) "
    "( 3. c4 { [%eval 0.3] } ) "
    "3... e6 { [%eval -0.9] } 0-1"
)


class TestRefutationSiblingSelection:
    def test_judgment_named_best_move_picks_that_sibling(self):
        ga = analyze_game(REFUTATION_NAMED_PGN, player_color="White")
        b3 = next(m for m in ga.moves if m.san == "b3")
        assert b3.best_move == "c4"
        assert b3.refutation_line[0] == "c4"   # engine's line, not the hand line Nc3

    def test_eval_bearing_sibling_wins_when_no_best_move_is_named(self):
        ga = analyze_game(REFUTATION_EVAL_PGN, player_color="White")
        b3 = next(m for m in ga.moves if m.san == "b3")
        assert b3.refutation_line[0] == "c4"   # the sibling carrying [%eval]
        assert b3.best_move == "c4"            # falls back to the chosen line's head


# ---------------------------------------------------------------------------
# Castling-with-check best move (issue #91)
# ---------------------------------------------------------------------------

class TestCastlingBestMove:
    def test_castling_with_check_or_mate_is_a_parsable_best_move(self):
        from engine_analysis_core import _best_move_from_judgment
        assert _best_move_from_judgment("Mistake. O-O+ was best.") == "O-O+"
        assert _best_move_from_judgment("Blunder. O-O-O# was best.") == "O-O-O#"
        assert _best_move_from_judgment("Inaccuracy. O-O was best.") == "O-O"


# ---------------------------------------------------------------------------
# Custom start position (issue #91): a Chapter beginning from a FEN reparses
# from that position instead of truncating against the standard start
# ---------------------------------------------------------------------------

_CUSTOM_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"   # bare kings; Kd2 is illegal from start
_CUSTOM_MOVETEXT = (
    "1. Kd2 { [%eval 0.0] } Kd7 { [%eval 0.0] } "
    "2. Kd3 { [%eval 0.0] } Kd6 { [%eval 0.0] } *"
)


class TestCustomStartPosition:
    def test_without_the_fen_the_line_truncates_to_unanalysed(self):
        ga = analyze_game(_CUSTOM_MOVETEXT, player_color="White")
        assert ga.analyzed is False   # Kd2 illegal from the standard start → no moves

    def test_with_the_fen_the_full_line_is_restored(self):
        ga = analyze_game(_CUSTOM_MOVETEXT, player_color="White", setup_fen=_CUSTOM_FEN)
        assert ga.analyzed is True
        assert [m.san for m in ga.moves] == ["Kd2", "Kd7", "Kd3", "Kd6"]
