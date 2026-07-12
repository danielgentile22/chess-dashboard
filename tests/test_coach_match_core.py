"""
tests/test_coach_match_core.py
==============================
The coach-chapter matching engine (issue #73 [G3]).

Mirrors ``tests/test_uscf_core.py``: a pure module verified against captured
fixtures.  The coach study PGN (``tests/data/coach-study.pgn``) carries
three Chapters that mirror real Games in the captured Study snapshot — the
Alice Anderson win with the documented 7 prose comments + 10 variations — plus
three extras (an online blitz, a teaching endgame, a master game) that match no
Game and must be dropped.

A good test asserts external behavior: given a user's Games and a coach Study's
PGN, the right Games get the right coach notes and variations and the extras
fall out — never the internal call sequence.
"""
from __future__ import annotations

from pathlib import Path

from coach_match_core import (
    CoachChapter,
    CoachComment,
    CoachMatchResult,
    match_coach_chapters,
    match_coach_study,
    parse_coach_study,
)

COACH_PGN = (Path(__file__).parent / "data" / "coach-study.pgn").read_text()

# The three Chapters that mirror real Games in the snapshot, and the snapshot
# ChapterURL each should pair with (the user's Game identity — ADR 0001).
ALICE_URL = "https://lichess.org/study/abcdWXYZ/alic0001"
DIANA_URL = "https://lichess.org/study/abcdWXYZ/dian0001"
ETHAN_URL = "https://lichess.org/study/abcdWXYZ/ethn0001"

# The coach Study's own Chapter URLs (provenance — never a Game identity).
ALICE_COACH_URL = "https://lichess.org/study/coachAAAA/gc000001"


# ---------------------------------------------------------------------------
# Parsing a coach Study into Chapters
# ---------------------------------------------------------------------------

class TestParseCoachStudy:
    def test_parses_every_chapter(self):
        chapters = parse_coach_study(COACH_PGN)
        # 3 matched mirrors + 3 extras
        assert len(chapters) == 6

    def test_chapter_carries_its_move_prefix(self):
        chapters = parse_coach_study(COACH_PGN)
        alice = _by_name(chapters, "Alice")
        # The Caro-Kann the real Game opened with (ADR 0001 move identity)
        assert alice.move_prefix[:6] == ("e4", "c6", "Nc3", "d5", "d3", "dxe4")

    def test_chapter_preserves_annotated_movetext_for_the_board(self):
        alice = _by_name(parse_coach_study(COACH_PGN), "Alice")
        # The Coach board view replays the coach's annotated line, not a bare one
        assert "e4" in alice.movetext
        assert alice.movetext.strip()

    def test_empty_pgn_yields_no_chapters(self):
        assert parse_coach_study("") == []


# ---------------------------------------------------------------------------
# Prose-comment extraction (strip engine directives, keep the coach's words)
# ---------------------------------------------------------------------------

class TestProseExtraction:
    def test_alice_has_the_documented_seven_comments(self):
        alice = _by_name(parse_coach_study(COACH_PGN), "Alice")
        assert len(alice.comments) == 7

    def test_engine_eval_directives_are_stripped_but_prose_kept(self):
        alice = _by_name(parse_coach_study(COACH_PGN), "Alice")
        texts = [c.text for c in alice.comments]
        # A comment that began "{ [%eval -0.17] d3 is too quiet ... }"
        quiet = next(t for t in texts if "too quiet" in t)
        assert "%eval" not in quiet
        assert "[" not in quiet
        assert quiet.startswith("d3 is too quiet")

    def test_pure_engine_comments_are_not_prose(self):
        """A bare ``{ [%eval 0.09] }`` carries no coach words — it never
        becomes a comment."""
        alice = _by_name(parse_coach_study(COACH_PGN), "Alice")
        assert all(c.text.strip() for c in alice.comments)

    def test_chapter_level_comment_is_anchored_before_the_first_move(self):
        alice = _by_name(parse_coach_study(COACH_PGN), "Alice")
        first = alice.comments[0]
        assert first.move_number == 0
        assert "Caro-Kann again" in first.text

    def test_move_comment_carries_its_move_number(self):
        alice = _by_name(parse_coach_study(COACH_PGN), "Alice")
        quiet = next(c for c in alice.comments if "too quiet" in c.text)
        # The comment sits on White's 3rd move (ply 5)
        assert quiet.move_number == 3


# ---------------------------------------------------------------------------
# Variations are preserved
# ---------------------------------------------------------------------------

class TestVariations:
    def test_alice_preserves_its_ten_variations(self):
        alice = _by_name(parse_coach_study(COACH_PGN), "Alice")
        assert alice.variation_count == 10


# ---------------------------------------------------------------------------
# Move-prefix matching against the user's Games (the priority suite)
# ---------------------------------------------------------------------------

class TestMatching:
    def test_each_mirrored_chapter_pairs_its_game(self, study_snapshot_df):
        result = match_coach_study(study_snapshot_df, COACH_PGN)
        matched = {m.chapter_url for m in result.matches}
        assert ALICE_URL in matched
        assert DIANA_URL in matched
        assert ETHAN_URL in matched

    def test_matching_is_unambiguous_one_game_per_chapter(self, study_snapshot_df):
        result = match_coach_study(study_snapshot_df, COACH_PGN)
        urls = [m.chapter_url for m in result.matches]
        assert len(urls) == len(set(urls))  # no Game claimed twice

    def test_extras_are_left_unmatched_and_dropped(self, study_snapshot_df):
        result = match_coach_study(study_snapshot_df, COACH_PGN)
        dropped = {c.chapter_name for c in result.unmatched_chapters}
        assert any("blitz" in n.lower() for n in dropped)
        assert any("lucena" in n.lower() for n in dropped)
        assert any("immortal" in n.lower() for n in dropped)
        # exactly the three mirrors matched; the three extras dropped
        assert len(result.matches) == 3
        assert len(result.unmatched_chapters) == 3

    def test_an_unmatched_coach_chapter_never_creates_a_game(self, study_snapshot_df):
        before = set(study_snapshot_df["ChapterURL"])
        result = match_coach_study(study_snapshot_df, COACH_PGN)
        # every matched Game already existed; no extra is invented (ADR 0001)
        assert all(m.chapter_url in before for m in result.matches)

    def test_no_games_means_no_matches(self):
        import pandas as pd
        result = match_coach_chapters(pd.DataFrame(), parse_coach_study(COACH_PGN))
        assert result.matches == ()
        # …but the Chapters are still reported as unmatched, never lost
        assert len(result.unmatched_chapters) == 6


# ---------------------------------------------------------------------------
# The result is the lookup the store and pages read
# ---------------------------------------------------------------------------

class TestResultLookup:
    def test_chapter_for_returns_the_matched_chapter(self, study_snapshot_df):
        result = match_coach_study(study_snapshot_df, COACH_PGN)
        chapter = result.chapter_for(ALICE_URL)
        assert isinstance(chapter, CoachChapter)
        assert chapter.chapter_url == ALICE_COACH_URL

    def test_chapter_for_unknown_url_is_none(self, study_snapshot_df):
        result = match_coach_study(study_snapshot_df, COACH_PGN)
        assert result.chapter_for("https://lichess.org/study/x/nope") is None

    def test_comments_for_returns_the_coachs_prose(self, study_snapshot_df):
        result = match_coach_study(study_snapshot_df, COACH_PGN)
        comments = result.comments_for(ALICE_URL)
        assert len(comments) == 7
        assert all(isinstance(c, CoachComment) for c in comments)

    def test_comments_for_unmatched_game_is_empty(self, study_snapshot_df):
        result = match_coach_study(study_snapshot_df, COACH_PGN)
        assert result.comments_for("https://lichess.org/study/x/nope") == ()

    def test_result_is_empty_by_default(self):
        assert CoachMatchResult().matches == ()
        assert CoachMatchResult().unmatched_chapters == ()
        assert CoachMatchResult().ambiguous_chapters == ()


# ---------------------------------------------------------------------------
# Ambiguity vs teaching extras (issue #92): a review the coach wrote but the
# matcher couldn't place must surface, not vanish among the dropped extras.
# ---------------------------------------------------------------------------

# A 20-ply opening two Games can share (deep theory the user repeats).
_SHARED = ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6",
           "Be2", "e5", "Nb3", "Be7", "O-O", "O-O", "Be3", "Be6", "Nd5", "Nbd7"]


def _games(*rows):
    import pandas as pd
    return pd.DataFrame(
        [{"ChapterURL": url, "Moves": list(moves)} for url, moves in rows]
    )


def _chapter(name: str, prefix, url: str = "") -> CoachChapter:
    return CoachChapter(
        chapter_name=name, chapter_url=url, move_prefix=tuple(prefix),
        comments=(), variation_count=0, movetext="",
    )


class TestAmbiguitySurfaces:
    def test_a_chapter_fitting_two_games_is_ambiguous_not_dropped(self):
        """Both of the user's Games share the same 20-ply opening; the coach's
        one review of that line fits both → ambiguous, surfaced (not matched,
        not silently dropped among the extras)."""
        df = _games(("game-1", _SHARED), ("game-2", _SHARED))
        result = match_coach_chapters(df, [_chapter("Review", _SHARED, "coach-1")])

        assert result.matches == ()
        assert [c.chapter_name for c in result.ambiguous_chapters] == ["Review"]
        assert result.unmatched_chapters == ()

    def test_a_game_two_chapters_claim_makes_both_ambiguous(self):
        """Two coach Chapters both mirror the one Game → neither is a safe
        match; both surface as ambiguous."""
        df = _games(("game-1", _SHARED))
        result = match_coach_chapters(df, [
            _chapter("Review A", _SHARED, "coach-a"),
            _chapter("Review B", _SHARED, "coach-b"),
        ])

        assert result.matches == ()
        assert {c.chapter_name for c in result.ambiguous_chapters} == {
            "Review A", "Review B"}

    def test_zero_candidate_extras_stay_unmatched_not_ambiguous(self):
        """A teaching extra that fits no Game keeps being dropped silently — only
        reviews with a real candidate Game are the ambiguous ones."""
        extra = ["d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "O-O"]
        df = _games(("game-1", _SHARED))
        result = match_coach_chapters(df, [
            _chapter("Real review", _SHARED, "coach-1"),
            _chapter("Teaching QGD", extra, "coach-2"),
        ])

        assert [m.chapter_url for m in result.matches] == ["game-1"]
        assert [c.chapter_name for c in result.unmatched_chapters] == ["Teaching QGD"]
        assert result.ambiguous_chapters == ()


class TestPrefixOverlapGate:
    def test_short_opening_fragment_does_not_false_match_a_full_game(self):
        """A 10-ply opening-lesson Chapter shares only the opening of a full
        Game — below _PREFIX_PLIES and one side keeps going, so no match (#92)."""
        full_game = _SHARED + ["Rc1", "Qc7", "f4", "exf4", "Bxf4", "Ne5"]  # 26 plies
        df = _games(("game-1", full_game))
        result = match_coach_chapters(df, [_chapter("Najdorf intro", _SHARED[:10])])

        assert result.matches == ()

    def test_two_short_games_that_both_ended_still_match(self):
        """A genuine short miniature the coach reviewed: both the Chapter's
        mainline and the Game's movelist end at the same 12 plies → a real
        match, not a fragment."""
        short = _SHARED[:12]
        df = _games(("game-1", short))
        result = match_coach_chapters(df, [_chapter("Miniature", short, "coach-1")])

        assert [m.chapter_url for m in result.matches] == ["game-1"]


class TestDocumentOrderAndResultToken:
    _DOC_PGN = (
        '[Event "x"]\n[ChapterURL "https://lichess.org/study/c/doc1"]\n\n'
        "1. e4 e5 2. Nf3 { MAIN2 } ( 2. f4 { SIDE2 } ) 2... Nc6 { MAIN2b } *\n"
    )

    def test_comments_are_in_pgn_document_order(self):
        """A sideline's comment reads right after its branch point, before the
        mainline continues — not after every later mainline comment (#92)."""
        chapter = parse_coach_study(self._DOC_PGN)[0]
        assert [c.text for c in chapter.comments] == ["MAIN2", "SIDE2", "MAIN2b"]

    def test_movetext_has_no_trailing_result_token(self):
        """The Coach board wraps the user's authoritative Result header around
        this movetext; a stray '*'/'1-0' terminator would contradict it (#92)."""
        for result_token in ("*", "1-0", "1/2-1/2"):
            pgn = (
                f'[Event "x"]\n[Result "{result_token}"]\n'
                '[ChapterURL "https://lichess.org/study/c/r"]\n\n'
                f"1. e4 e5 2. Nf3 Nc6 {result_token}\n"
            )
            chapter = parse_coach_study(pgn)[0]
            assert chapter.movetext.split()[-1] not in {"*", "1-0", "0-1", "1/2-1/2"}
            assert "Nc6" in chapter.movetext


def _by_name(chapters: list[CoachChapter], needle: str) -> CoachChapter:
    return next(c for c in chapters if needle.lower() in c.chapter_name.lower())
