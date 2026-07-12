"""
coach_match_core.py
===================
The coach-chapter matching engine (issue #73 [G3]) — pure and
framework-agnostic, mirroring ``uscf_core``'s ``match_games`` / ``enrich_games``
/ leftovers shape.

A coach reviews some of the user's Games in his own private Lichess Studies
(GM Midnight_Chess).  Those Studies also hold material that is *not* one of the
user's over-the-board Games: online blitz, teaching positions, master games.
This module pairs each coach Chapter to one of the user's Games **by the moves
played** — the move prefix of the mainline — and extracts the coach's content
(his prose comments, with engine ``[%eval]`` directives stripped, and his
variations) for the Games that match.  The extras fall out naturally as
unmatched and are dropped.

Why move-prefix matching?  The same Game is typed differently in two Studies —
names spelled differently, dates off by a day, ratings absent — but the moves
are the moves.  During design, comparing the first ~20 plies paired 58 of 63
Games with zero ambiguous and zero false matches.

Principles this module honours:

* **The user's main Study is the source of truth (ADR 0001).**  An unmatched
  coach Chapter never creates a Game; matching can only ever *enrich* a Game
  that already exists.
* **Ambiguity is never a guess.**  A coach Chapter matches a Game only when
  exactly one Game fits it and that Game is claimed by exactly one Chapter —
  mirroring the USCF name-fallback rule.  Everything else stays unmatched.
* **The coach's words are read, never rewritten (ADR 0002).**  Prose extraction
  strips Lichess's machine directives and keeps the coach's text verbatim.

Public API
----------
parse_coach_study     Parse a coach Study PGN → list[CoachChapter].
match_coach_chapters  Match parsed Chapters to a Games DataFrame → CoachMatchResult.
match_coach_study     Convenience: parse + match in one call.
CoachChapter          One parsed coach Chapter: move prefix + extracted content.
CoachComment          One prose comment the coach wrote, with where it sits.
CoachMatchResult      The matches, the dropped extras, and the lookups pages read.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import chess.pgn
import pandas as pd

__all__ = [
    "CoachChapter",
    "CoachComment",
    "CoachMatch",
    "CoachMatchResult",
    "match_coach_chapters",
    "match_coach_study",
    "parse_coach_study",
]

# How many plies of the mainline define a Game's move identity for matching.
# ~20 (10 moves) was verified during design to separate every real Game.
_PREFIX_PLIES = 20

# A coach Chapter must share at least this many plies with a Game to match it —
# below this it is a fragment (a teaching position, a few-move stub), not one of
# the user's Games.
_MIN_OVERLAP = 8

# Lichess embeds machine annotations in comments: [%eval 0.18], [%clk 1:30:00],
# [%cal ...], [%csl ...].  They are not the coach's words — strip them and keep
# whatever prose remains (mirrors pgn_stats_core's directive handling).
_DIRECTIVE_RE = re.compile(r"\[%[^\]]*\]")


@dataclass(frozen=True)
class CoachComment:
    """One prose comment the coach wrote, and where in the Game it sits."""

    text: str          # the coach's words, engine/clock directives stripped
    ply: int           # the ply the comment annotates; 0 = chapter-level
    move_number: int   # 1-based move number; 0 for a chapter-level comment


@dataclass(frozen=True)
class CoachChapter:
    """A parsed coach Study Chapter: its move identity and extracted content."""

    chapter_name: str
    chapter_url: str                       # the coach Study's URL (provenance)
    move_prefix: tuple[str, ...]           # first _PREFIX_PLIES SAN of the mainline
    comments: tuple[CoachComment, ...]     # the coach's prose, in document order
    variation_count: int                   # how many sideline branches he kept
    movetext: str                          # full annotated movetext (Coach board view)
    result: str = ""


@dataclass(frozen=True)
class CoachMatch:
    """One coach Chapter ↔ user Game pairing."""

    chapter_url: str          # the user's Game identity (ADR 0001)
    chapter: CoachChapter
    matched_by: str = "moves"


@dataclass(frozen=True)
class CoachMatchResult:
    """
    Everything the coach matcher produced.

    Chapters that matched no Game split two ways.  ``ambiguous_chapters`` are
    real coach reviews rejected only because of ambiguity — a Chapter that fit
    more than one Game, or a Game two Chapters both claimed; these surface in
    Reconciliation so a review the user paid for never silently vanishes (issue
    #92).  ``unmatched_chapters`` are the coach's teaching extras that fit no
    Game at all — deliberately dropped from Games-scoped surfaces.
    """

    matches: tuple[CoachMatch, ...] = ()
    unmatched_chapters: tuple[CoachChapter, ...] = ()
    ambiguous_chapters: tuple[CoachChapter, ...] = ()

    def chapter_for(self, chapter_url: str) -> CoachChapter | None:
        """The coach Chapter matched to *chapter_url*, or None."""
        return self._by_url().get(chapter_url)

    def comments_for(self, chapter_url: str) -> tuple[CoachComment, ...]:
        """The coach's prose comments for *chapter_url* ((), never None)."""
        chapter = self._by_url().get(chapter_url)
        return chapter.comments if chapter is not None else ()

    def _by_url(self) -> dict[str, CoachChapter]:
        return {m.chapter_url: m.chapter for m in self.matches}


# ---------------------------------------------------------------------------
# Parsing a coach Study PGN into Chapters
# ---------------------------------------------------------------------------

def parse_coach_study(pgn_text: str) -> list[CoachChapter]:
    """
    Parse a coach Study export (one game per Chapter) into ``CoachChapter``s,
    capturing each Chapter's move prefix, prose comments, and variation count.

    A blank or unparseable export yields ``[]``.
    """
    if not pgn_text or not pgn_text.strip():
        return []

    chapters: list[CoachChapter] = []
    stream = io.StringIO(pgn_text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        chapters.append(_chapter_from_game(game))
    return chapters


def _chapter_from_game(game: chess.pgn.Game) -> CoachChapter:
    headers = game.headers
    name = headers.get("ChapterName") or (
        f"{headers.get('White', '?')} - {headers.get('Black', '?')}"
    )
    exporter = chess.pgn.StringExporter(headers=False, variations=True, comments=True)
    return CoachChapter(
        chapter_name=name,
        chapter_url=headers.get("ChapterURL", ""),
        move_prefix=_mainline_prefix(game),
        comments=_extract_comments(game),
        variation_count=_count_variations(game),
        movetext=_strip_result(game.accept(exporter).strip()),
        result=headers.get("Result", ""),
    )


# The four PGN game-termination markers a StringExporter appends to the movetext.
_RESULT_TOKENS = frozenset(("1-0", "0-1", "1/2-1/2", "*"))


def _strip_result(movetext: str) -> str:
    """Drop the trailing result token an exporter appends (often ``*`` — coach
    Chapters are analysis boards that rarely set a result).  The Coach board view
    wraps this movetext in the user's own headers, whose Result is authoritative
    (ADR 0001); leaving the token in makes the move list terminate on a result
    that can contradict that header (issue #92)."""
    parts = movetext.rsplit(maxsplit=1)  # split on any whitespace (exporter wraps)
    if len(parts) == 2 and parts[1] in _RESULT_TOKENS:
        return parts[0]
    return "" if movetext in _RESULT_TOKENS else movetext


def _mainline_prefix(game: chess.pgn.Game) -> tuple[str, ...]:
    """The first _PREFIX_PLIES mainline moves as SAN, generated off the board so
    spelling matches the user's Games (which were exported the same way)."""
    board = game.board()
    sans: list[str] = []
    for move in game.mainline_moves():
        sans.append(board.san(move))
        board.push(move)
        if len(sans) >= _PREFIX_PLIES:
            break
    return tuple(sans)


def _extract_comments(game: chess.pgn.Game) -> tuple[CoachComment, ...]:
    """Every prose comment in the Chapter tree, engine directives stripped, in
    PGN document order: a move's comment, then that branch's sidelines, then the
    mainline continues (how python-chess writes the movetext).  A comment that is
    only directives carries no coach words and is dropped."""
    comments: list[CoachComment] = []

    def emit(node: chess.pgn.GameNode, ply: int) -> None:
        if not node.comment:
            return
        text = _DIRECTIVE_RE.sub("", node.comment).strip()
        if text:
            comments.append(CoachComment(text=text, ply=ply, move_number=(ply + 1) // 2))

    def order_children(node: chess.pgn.GameNode, ply: int) -> None:
        # node's own comment was emitted by its caller.  Its children are moves
        # at ply+1: the mainline move's comment is written first, then its
        # sidelines (each a full subtree), then the mainline continues.
        if not node.variations:
            return
        main, *sidelines = node.variations
        emit(main, ply + 1)
        for sideline in sidelines:
            emit(sideline, ply + 1)
            order_children(sideline, ply + 1)
        order_children(main, ply + 1)

    emit(game, 0)            # the chapter-level comment (ply 0), if any
    order_children(game, 0)
    return tuple(comments)


def _count_variations(game: chess.pgn.Game) -> int:
    """How many sideline branches the Chapter holds — one per alternative the
    coach added beyond the move actually played."""
    total = 0

    def walk(node: chess.pgn.GameNode) -> None:
        nonlocal total
        if len(node.variations) > 1:
            total += len(node.variations) - 1
        for child in node.variations:
            walk(child)

    walk(game)
    return total


# ---------------------------------------------------------------------------
# Matching Chapters to the user's Games by move prefix
# ---------------------------------------------------------------------------

def match_coach_study(df: pd.DataFrame, pgn_text: str) -> CoachMatchResult:
    """Parse *pgn_text* and match its Chapters to the Games in *df*."""
    return match_coach_chapters(df, parse_coach_study(pgn_text))


def match_coach_chapters(
    df: pd.DataFrame, chapters: list[CoachChapter]
) -> CoachMatchResult:
    """
    Pair each coach Chapter to one of the user's Games by move prefix.

    A Chapter matches a Game only when exactly one Game fits it *and* that Game
    is claimed by exactly one Chapter — any ambiguity in either direction means
    no match (a guess could attach the wrong review).  The user's main Study is
    the source of truth (ADR 0001): an unmatched Chapter never creates a Game,
    it simply lands in ``unmatched_chapters`` and is dropped from Games-scoped
    surfaces.
    """
    game_prefixes = _game_prefixes(df)

    # Which Games each Chapter could mean, and which Chapters claim each Game.
    chapter_candidates: dict[int, list[str]] = {}
    game_claimants: dict[str, list[int]] = {}
    for ci, chapter in enumerate(chapters):
        for url, game_prefix in game_prefixes.items():
            if _prefixes_match(chapter.move_prefix, game_prefix):
                chapter_candidates.setdefault(ci, []).append(url)
                game_claimants.setdefault(url, []).append(ci)

    matches: list[CoachMatch] = []
    matched: set[int] = set()
    ambiguous: set[int] = set()
    for ci, urls in chapter_candidates.items():
        if len(urls) == 1 and len(game_claimants[urls[0]]) == 1:
            matches.append(CoachMatch(chapter_url=urls[0], chapter=chapters[ci]))
            matched.add(ci)
        else:
            # Had a candidate Game but ambiguity in either direction → no match,
            # not a guess.  Distinct from the zero-candidate extras so it can
            # surface in Reconciliation rather than vanish silently (issue #92).
            ambiguous.add(ci)

    unmatched = tuple(
        c for i, c in enumerate(chapters) if i not in matched and i not in ambiguous
    )
    return CoachMatchResult(
        matches=tuple(matches),
        unmatched_chapters=unmatched,
        ambiguous_chapters=tuple(chapters[i] for i in sorted(ambiguous)),
    )


def _game_prefixes(df: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    """The move-prefix of every Game with a ChapterURL (its identity) and enough
    moves to match on, keyed by ChapterURL."""
    if df.empty or "Moves" not in df.columns or "ChapterURL" not in df.columns:
        return {}
    prefixes: dict[str, tuple[str, ...]] = {}
    for _, row in df.iterrows():
        url = row["ChapterURL"]
        moves = row["Moves"]
        if not url or not isinstance(moves, list) or len(moves) < _MIN_OVERLAP:
            continue
        prefixes[url] = tuple(moves[:_PREFIX_PLIES])
    return prefixes


def _prefixes_match(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Two move prefixes are the same Game when they agree over their overlap and
    that overlap is deep enough to be an identity rather than a shared opening.

    Deep enough means: the overlap reaches _PREFIX_PLIES, OR both sides ended
    within it (equal length, both below _PREFIX_PLIES so neither was truncated —
    both games actually finished on the same move).  A short overlap where only
    one side ended is an opening fragment (a teaching Chapter, or a coach's blitz
    game sharing our opening) and must not false-match (issue #92)."""
    overlap = min(len(a), len(b))
    if overlap < _MIN_OVERLAP or a[:overlap] != b[:overlap]:
        return False
    return overlap >= _PREFIX_PLIES or len(a) == len(b)
