"""
engine_analysis_core.py
=======================
Pure interpretation of the engine analysis Lichess embeds in an analysed
Chapter's Study export (PRD #54, issue #57 [F1] — the spine).

When you request computer analysis on a Chapter on Lichess, the next Sync's
Study export carries, per move, an ``[%eval]`` evaluation, a natural-language
judgment ("Blunder. Nh5 was best."), and the recommended line as a variation.
This module reads that already-fetched movetext and turns one Game into a
structured :class:`GameAnalysis` whose headline — the **critical moment** —
is the single largest win-probability swing of the Game.

Like ``uscf_core`` it is framework-agnostic and deeply testable: movetext in →
dataclasses out, zero Dash imports, importable from a notebook.  And like USCF
data it is *enrichment, never a dependency* (ADR 0004): a Game with no requested
analysis degrades cleanly to ``analyzed=False`` and an empty analysis — it never
raises and never blanks a page.

Public API
----------
win_pct_from_cp           Win% from centipawns (the canonical Lichess formula).
analyze_game              One Game's movetext → GameAnalysis.
enrich_games_with_analysis  Attach a GameAnalysis to every row of a Games df.
MoveEval / CriticalMoment / GameAnalysis  The result shapes.

Verified facts (issue #57)
--------------------------
* Win% from centipawns uses Lichess's canonical
  ``50 + 50 * (2/(1+exp(-0.00368208*cp)) - 1)``; checked against published
  values in the tests.
* The critical moment on the real analysed Georgina Chin Game is White's
  ``16. Bd4??`` — the −4.38 / ~38-point swing — attributed to the opponent.
* OTB time-trouble cannot be auto-detected: the Study export carries no clock
  data, so the manual ``#time-trouble`` Tag stays the only signal for it
  (recorded in ADR 0004).
"""
from __future__ import annotations

import io
import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import chess
import chess.pgn
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "MoveEval",
    "CriticalMoment",
    "GameAnalysis",
    "Mistake",
    "win_pct_from_cp",
    "classify_severity",
    "classify_phase",
    "analyze_game",
    "enrich_games_with_analysis",
    "mistake_type_distribution",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The starting-position eval, in centipawns, used as the "before" of move 1.
# The export carries no pre-first-move eval; a small White edge matches
# Lichess's opening baseline and keeps move 1 from ever showing a swing.
_START_CP = 15

# Forced mate is folded into a large centipawn magnitude so the win% formula
# saturates to ~100/0 — the exact value past a few hundred cp is irrelevant.
_MATE_CP = 10_000

# Win-probability-drop thresholds (percentage points) for the headline's
# severity word — the same 0.1 / 0.2 / 0.3 boundaries Lichess uses, ×100.
_BLUNDER_DROP = 30.0
_MISTAKE_DROP = 20.0
_INACCURACY_DROP = 10.0


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------

@dataclass
class MoveEval:
    """One played move with the engine data that survived into the core.

    ``eval_before`` / ``eval_after`` are in pawns from White's perspective
    (positive = White better), exactly as the ``[%eval]`` export records them.
    ``win_pct_*`` are from the *mover's* perspective (0–100), so
    ``win_pct_drop = win_pct_before - win_pct_after`` is how much the side that
    moved hurt its own winning chances — positive is a mistake, the bigger the
    worse.
    """

    ply: int                      # 1-based half-move number
    move_number: int              # full move number ((ply + 1) // 2)
    side: str                     # "White" | "Black" — who made this move
    san: str
    eval_before: float            # pawns, White's perspective
    eval_after: float
    win_pct_before: float         # mover's perspective, 0–100
    win_pct_after: float
    win_pct_drop: float           # mover's win% lost on this move
    best_move: str | None = None  # the engine's recommended move, if judged
    refutation_line: list[str] = field(default_factory=list)


@dataclass
class CriticalMoment:
    """The Game's headline: its single largest win-probability swing.

    Attributed to whichever side made it (``side``), and framed for the player
    in ``headline`` ("Won after your opponent's blunder…" / "Lost to your
    blunder…").  ``by_player`` is True when the player is the one who made the
    swing.
    """

    ply: int
    move_number: int
    side: str            # the side that made the swing
    san: str
    win_pct_swing: float  # the mover's win% drop on this move
    eval_before: float    # pawns, White's perspective
    eval_after: float
    by_player: bool
    headline: str


@dataclass
class Mistake:
    """One classified entry in the player's error profile (issue #58).

    A single non-``none`` move the *player* made, tagged with how bad it was
    (``severity``), where in the Game it happened (``phase`` and ``move_number``),
    and what kind of error it was (``mistake_type``).  Recorded for every Game
    regardless of result, so the improvement signal isn't biased by whether the
    Game was won.
    """

    ply: int
    move_number: int
    san: str
    severity: str        # "inaccuracy" | "mistake" | "blunder"
    phase: str           # "opening" | "middlegame" | "endgame"
    mistake_type: str    # "tactical" | "positional"
    win_pct_drop: float  # the player's win% lost on this move


@dataclass
class GameAnalysis:
    """One Game's structured engine analysis.

    An unanalysed Game (no requested computer analysis) degrades to
    ``analyzed=False`` with no moves, no critical moment, and an empty error
    profile — never an error.
    """

    chapter_url: str = ""
    analyzed: bool = False
    moves: list[MoveEval] = field(default_factory=list)
    critical_moment: CriticalMoment | None = None
    error_profile: list[Mistake] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The win-probability formula (the canonical Lichess reference)
# ---------------------------------------------------------------------------

def win_pct_from_cp(cp: float) -> float:
    """Win probability (0–100) from a centipawn evaluation.

    The canonical Lichess formula ``50 + 50 * (2/(1+exp(-0.00368208*cp)) - 1)``.
    ``cp`` is signed from a fixed perspective; the result is the win% for that
    perspective (cp = 0 → 50, large positive → ~100, large negative → ~0).
    """
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-0.00368208 * cp)) - 1.0)


# ---------------------------------------------------------------------------
# Movetext parsing
# ---------------------------------------------------------------------------

# Lichess machine annotations inside a comment: [%eval 0.18], [%clk ...], …
_DIRECTIVE_RE = re.compile(r"\[%[^\]]*\]")

# "d4 was best." / "Nh5 was best." / "O-O was best." — the recommended move
# named in an engine judgment.  Requiring the "was best" tail keeps it from
# grabbing SAN out of the surrounding prose.
_BEST_MOVE_RE = re.compile(
    r"\b([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O(?:-O)?)\s+was\s+best",
)


def _node_cp(node: chess.pgn.GameNode) -> int | None:
    """The node's ``[%eval]`` as centipawns from White's perspective, or None."""
    try:
        pov = node.eval()
    except (ValueError, KeyError):
        return None
    if pov is None:
        return None
    return pov.white().score(mate_score=_MATE_CP)


def _judgment_text(node: chess.pgn.GameNode) -> str:
    """The node's comment with the ``[%…]`` machine annotations stripped out."""
    return _DIRECTIVE_RE.sub("", node.comment or "").strip()


def _best_move_from_judgment(judgment: str) -> str | None:
    """The move named as best in an engine judgment ("d4 was best." → "d4")."""
    match = _BEST_MOVE_RE.search(judgment)
    return match.group(1) if match else None


def _refutation_line(
    node: chess.pgn.GameNode, board_before: chess.Board, *, max_plies: int = 8
) -> list[str]:
    """The engine's recommended line for this move, as SAN.

    Lichess exports the better continuation as a sibling variation — an
    alternative to the played move from the same position — so it hangs off
    the *parent* node.  We follow that variation's mainline from the
    pre-move position and read it back as SAN.
    """
    parent = node.parent
    if parent is None:
        return []
    siblings = [v for v in parent.variations if v is not node]
    if not siblings:
        return []

    board = board_before.copy()
    sans: list[str] = []
    cursor: chess.pgn.GameNode | None = siblings[0]
    while cursor is not None and cursor.move is not None and len(sans) < max_plies:
        try:
            sans.append(board.san(cursor.move))
            board.push(cursor.move)
        except (ValueError, AssertionError):
            break
        cursor = cursor.variations[0] if cursor.variations else None
    return sans


def _parse_move_evals(game: chess.pgn.Game) -> list[MoveEval]:
    """Walk the mainline and build a MoveEval per move.

    Returns ``[]`` when the Game carries no ``[%eval]`` at all — that is what
    "no requested analysis" looks like, and the caller turns it into
    ``analyzed=False``.
    """
    board = game.board()
    prev_cp: int = _START_CP
    any_eval = False
    moves: list[MoveEval] = []
    ply = 0

    for node in game.mainline():
        move = node.move
        if move is None:  # defensive: a mainline node always has a move
            continue
        ply += 1
        side = "White" if board.turn == chess.WHITE else "Black"
        san = board.san(move)

        cp_after = _node_cp(node)
        if cp_after is None:
            cp_after = prev_cp  # carry the prior eval forward → no swing
        else:
            any_eval = True

        white_before = win_pct_from_cp(prev_cp)
        white_after = win_pct_from_cp(cp_after)
        if side == "White":
            win_before, win_after = white_before, white_after
        else:
            win_before, win_after = 100.0 - white_before, 100.0 - white_after

        judgment = _judgment_text(node)
        refutation = _refutation_line(node, board)
        best_move = _best_move_from_judgment(judgment)
        if best_move is None and refutation:
            best_move = refutation[0]

        moves.append(MoveEval(
            ply=ply,
            move_number=(ply + 1) // 2,
            side=side,
            san=san,
            eval_before=prev_cp / 100.0,
            eval_after=cp_after / 100.0,
            win_pct_before=win_before,
            win_pct_after=win_after,
            win_pct_drop=win_before - win_after,
            best_move=best_move,
            refutation_line=refutation,
        ))

        board.push(move)
        prev_cp = cp_after

    return moves if any_eval else []


# ---------------------------------------------------------------------------
# The critical moment
# ---------------------------------------------------------------------------

def _severity_word(drop: float) -> str:
    """The headline's word for a win%-drop magnitude (Lichess's boundaries)."""
    if drop >= _BLUNDER_DROP:
        return "blunder"
    if drop >= _MISTAKE_DROP:
        return "mistake"
    if drop >= _INACCURACY_DROP:
        return "inaccuracy"
    return "slip"


def classify_severity(win_pct_drop: float) -> str | None:
    """The error-profile severity for a move's win%-drop, or None if the move
    is not a mistake.

    The same 0.1 / 0.2 / 0.3 win-probability ladder the headline uses, but a
    swing below the inaccuracy line is *not* an entry in the error profile — it
    returns None rather than the headline's soft "slip".  Severity is recomputed
    from the swing, never read from Lichess's text word (issue #58), so a move
    Lichess labels a "Mistake" whose recomputed drop is only ~15% is an
    *inaccuracy* here — the profile stays internally consistent across moves.
    """
    if win_pct_drop >= _BLUNDER_DROP:
        return "blunder"
    if win_pct_drop >= _MISTAKE_DROP:
        return "mistake"
    if win_pct_drop >= _INACCURACY_DROP:
        return "inaccuracy"
    return None


# ---------------------------------------------------------------------------
# Game phase — a per-position port of Lichess's open-source Divider
# ---------------------------------------------------------------------------
# The Divider (scalachess `Divider.scala`) decides where a game crosses from
# opening into middlegame and from middlegame into endgame.  We need the phase
# of a *single* position (the one a mistake was made in), so this ports the same
# three signals and classifies a board on its own terms: endgame once majors +
# minors fall to ≤ 6 (which also implies the ≤ 10 midgame line), middlegame once
# they fall to ≤ 10 — or a home rank goes sparse, or "mixedness" exceeds 150 —
# and opening before any of that.  Majors+minors decreases monotonically with
# captures, so a position at ≤ 6 is unambiguously past midgame too.

# The 2×2 region (a1,b1,a2,b2) the Divider slides across the board for mixedness.
_MIXEDNESS_SMALL_SQUARE = 0x0303
# Each region with its 1-indexed bottom rank (y), for y=1..7 and x shifted 0..6.
_MIXEDNESS_REGIONS: list[tuple[int, int]] = [
    (_MIXEDNESS_SMALL_SQUARE << (x + 8 * y), y + 1)
    for y in range(7)
    for x in range(7)
]


def _majors_and_minors(board: chess.Board) -> int:
    """Queens, rooks, bishops, knights on the board — everything but kings and
    pawns (the Divider's ``occupied & ~(kings | pawns)``)."""
    return chess.popcount(board.occupied & ~(board.kings | board.pawns))


def _backrank_sparse(board: chess.Board) -> bool:
    """True when either side's home rank holds fewer than four of its pieces —
    the Divider's signal that pieces have left the back rank for the middlegame."""
    white = board.occupied_co[chess.WHITE]
    black = board.occupied_co[chess.BLACK]
    return (chess.popcount(white & chess.BB_RANK_1) < 4
            or chess.popcount(black & chess.BB_RANK_8) < 4)


def _mixedness_score(y: int, white: int, black: int) -> int:
    """The Divider's per-region score table for (white, black) piece counts in a
    2×2 region whose bottom rank is ``y`` (1–7)."""
    key = (white, black)
    if key == (0, 0):
        return 0
    if key == (1, 0):
        return 1 + (8 - y)
    if key == (2, 0):
        return 2 + (y - 2) if y > 2 else 0
    if key == (3, 0):
        return 3 + (y - 1) if y > 1 else 0
    if key == (4, 0):
        return 3 + (y - 1) if y > 1 else 0
    if key == (0, 1):
        return 1 + y
    if key == (1, 1):
        return 5 + abs(4 - y)
    if key == (2, 1):
        return 4 + (y - 1)
    if key == (3, 1):
        return 5 + (y - 1)
    if key == (0, 2):
        return 2 + (6 - y) if y < 6 else 0
    if key == (1, 2):
        return 4 + (7 - y)
    if key == (2, 2):
        return 7
    if key == (0, 3):
        return 3 + (7 - y) if y < 7 else 0
    if key == (1, 3):
        return 5 + (7 - y)
    if key == (0, 4):
        return 3 + (7 - y) if y < 7 else 0
    return 0


def _mixedness(board: chess.Board) -> int:
    """The Divider's "mixedness": how interleaved the two armies are, summed over
    every 2×2 region.  High when the position is a tangled middlegame melee."""
    white = board.occupied_co[chess.WHITE]
    black = board.occupied_co[chess.BLACK]
    total = 0
    for region, y in _MIXEDNESS_REGIONS:
        total += _mixedness_score(
            y, chess.popcount(white & region), chess.popcount(black & region)
        )
    return total


def classify_phase(board: chess.Board) -> str:
    """The game phase of *board*: ``"opening"`` | ``"middlegame"`` | ``"endgame"``.

    A per-position read of Lichess's Divider (see the section note): endgame once
    majors+minors ≤ 6, else middlegame once ≤ 10 or a home rank is sparse or
    mixedness > 150, else opening.
    """
    majors_minors = _majors_and_minors(board)
    if majors_minors <= 6:
        return "endgame"
    if (majors_minors <= 10
            or _backrank_sparse(board)
            or _mixedness(board) > 150):
        return "middlegame"
    return "opening"


def _headline(move: MoveEval, by_player: bool, player_outcome: str) -> str:
    """A one-line, player-perspective verdict for the critical moment.

    Frames the swing by who made it and how the Game ended, without claiming
    anything the engine data does not support (no "hung a rook" — that needs
    material analysis that lands with a later slice; no "tactical" until the
    move-type classifier exists).
    """
    word = _severity_word(move.win_pct_drop)
    where = f"move {move.move_number} ({move.san})"
    outcome = (player_outcome or "").lower()

    if by_player:
        if outcome == "loss":
            return f"Lost to your {word} on {where}."
        if outcome == "win":
            return f"Won despite your {word} on {where}."
        return f"Your {word} on {where} was the critical moment."
    # the opponent made the swing
    if outcome == "win":
        return f"Won after your opponent's {word} on {where}."
    if outcome == "loss":
        return f"Lost despite your opponent's {word} on {where}."
    return f"Your opponent's {word} on {where} was the critical moment."


def _critical_moment(
    moves: list[MoveEval], player_color: str, player_outcome: str
) -> CriticalMoment | None:
    """The single largest win-probability swing of the Game, attributed.

    The biggest mistake by *either* side: a move's ``win_pct_drop`` is the
    mover's own loss, so the maximum across all moves is the game's defining
    swing — whether the player made it or the opponent handed it over.
    """
    worst = max(moves, key=lambda m: m.win_pct_drop, default=None)
    if worst is None or worst.win_pct_drop <= 0:
        return None

    by_player = bool(player_color) and worst.side == player_color
    return CriticalMoment(
        ply=worst.ply,
        move_number=worst.move_number,
        side=worst.side,
        san=worst.san,
        win_pct_swing=worst.win_pct_drop,
        eval_before=worst.eval_before,
        eval_after=worst.eval_after,
        by_player=by_player,
        headline=_headline(worst, by_player, player_outcome),
    )


# ---------------------------------------------------------------------------
# The error profile (the player's own classified mistakes)
# ---------------------------------------------------------------------------

# Standard material values, in pawns, for the forcing-sequence material check.
_PIECE_VALUE: dict[int, int] = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9,
}
# How far the punishment may run and still count as "a forcing sequence".
_FORCING_LOOKAHEAD = 3  # plies
# Material loss (in pawns) that marks a forcing sequence as a real material drop —
# a minor piece or more, so even recaptures inside the window never false-fire.
_MATERIAL_DROP = 2


def _is_forcing(board: chess.Board, move: chess.Move) -> bool:
    """A move is forcing when it captures or gives check — the moves that drive
    a tactic and leave the opponent little choice."""
    return board.is_capture(move) or board.gives_check(move)


def _material_balance(board: chess.Board, color: chess.Color) -> int:
    """*color*'s material minus the opponent's, in pawns."""
    total = 0
    for piece_type, value in _PIECE_VALUE.items():
        total += value * (len(board.pieces(piece_type, color))
                          - len(board.pieces(piece_type, not color)))
    return total


def _best_move_is_forcing(move_eval: MoveEval, board_before: chess.Board) -> bool:
    """True when the engine's recommended move was itself a forcing tactic — the
    player missed a check or capture the position called for."""
    if not move_eval.best_move:
        return False
    try:
        best = board_before.parse_san(move_eval.best_move)
    except ValueError:
        return False
    return _is_forcing(board_before, best)


def _drops_material_to_forcing_sequence(
    board_before: chess.Board, played: list[chess.Move], index: int, color: chess.Color
) -> bool:
    """True when the played move bleeds material to a forcing run within ~3 plies.

    Replays the actual continuation from the mistake: as long as each reply is
    forcing (a capture or check), if the player ends up down a minor piece or
    more versus the position they moved from, the move dropped material to a
    tactic.  A non-forcing reply ends the sequence — a slow loss is positional.
    """
    baseline = _material_balance(board_before, color)
    board = board_before.copy(stack=False)
    board.push(played[index])
    for offset in range(1, _FORCING_LOOKAHEAD + 1):
        nxt = index + offset
        if nxt >= len(played):
            break
        move = played[nxt]
        forcing = _is_forcing(board, move)
        board.push(move)
        if not forcing:
            break
        if _material_balance(board, color) <= baseline - _MATERIAL_DROP:
            return True
    return False


def _mistake_type(
    move_eval: MoveEval,
    board_before: chess.Board,
    played: list[chess.Move],
    index: int,
) -> str:
    """Whether a mistake was ``"tactical"`` or ``"positional"`` (issue #58).

    Tactical when the engine's best move was itself a forcing tactic, or when the
    played move dropped material to a forcing sequence within ~3 plies; otherwise
    a slow eval bleed with no forcing refutation — positional.
    """
    color = chess.WHITE if move_eval.side == "White" else chess.BLACK
    if _best_move_is_forcing(move_eval, board_before):
        return "tactical"
    if _drops_material_to_forcing_sequence(board_before, played, index, color):
        return "tactical"
    return "positional"


def _build_error_profile(
    game: chess.pgn.Game, moves: list[MoveEval], player_color: str
) -> list[Mistake]:
    """The player's own non-``none`` moves, each classified (issue #58).

    Empty when the player's colour is unknown (headless use): attribution needs a
    colour, and the profile never guesses whose mistakes are whose.  Recorded for
    every Game regardless of result — a mistake in a won Game still counts.
    """
    if not player_color:
        return []

    # Pre-move boards and the move played, aligned 1:1 with ``moves`` (same
    # mainline, same order) so each mistake is classified against the position it
    # was made in and the forcing continuation that followed it.
    boards_before: list[chess.Board] = []
    played: list[chess.Move] = []
    board = game.board()
    for node in game.mainline():
        if node.move is None:  # defensive: a mainline node always has a move
            continue
        boards_before.append(board.copy(stack=False))
        played.append(node.move)
        board.push(node.move)

    profile: list[Mistake] = []
    for index, move_eval in enumerate(moves):
        if move_eval.side != player_color:
            continue
        severity = classify_severity(move_eval.win_pct_drop)
        if severity is None:
            continue
        pre = boards_before[index]
        profile.append(Mistake(
            ply=move_eval.ply,
            move_number=move_eval.move_number,
            san=move_eval.san,
            severity=severity,
            phase=classify_phase(pre),  # the position the player faced
            mistake_type=_mistake_type(move_eval, pre, played, index),
            win_pct_drop=move_eval.win_pct_drop,
        ))
    return profile


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def analyze_game(
    movetext: str,
    *,
    player_color: str = "",
    player_outcome: str = "",
    chapter_url: str = "",
) -> GameAnalysis:
    """Parse one Game's movetext into a :class:`GameAnalysis`.

    *movetext* is the rich movetext kept by ``pgn_stats_core.extract_movetext``
    (comments, ``[%eval]``, variations).  *player_color* / *player_outcome*
    ("White"/"Black", "Win"/"Loss"/"Draw") frame the critical moment for the
    player; both optional so the function works headless.

    A Game with no requested analysis — or unparseable movetext — degrades to
    ``analyzed=False`` with an empty analysis.  This never raises: engine
    analysis is enrichment, never a dependency (ADR 0004).
    """
    if not movetext or not movetext.strip():
        return GameAnalysis(chapter_url=chapter_url)

    try:
        game = chess.pgn.read_game(io.StringIO(movetext))
    except (ValueError, KeyError):
        game = None
    if game is None:
        return GameAnalysis(chapter_url=chapter_url)

    moves = _parse_move_evals(game)
    if not moves:
        return GameAnalysis(chapter_url=chapter_url)  # analysed=False, no data

    return GameAnalysis(
        chapter_url=chapter_url,
        analyzed=True,
        moves=moves,
        critical_moment=_critical_moment(moves, player_color, player_outcome),
        error_profile=_build_error_profile(game, moves, player_color),
    )


def enrich_games_with_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of *df* with the engine-analysis enrichment columns.

    Mirrors ``uscf_core.enrich_games``: every Game gets an ``Analysis``
    (:class:`GameAnalysis`) and an ``Analyzed`` flag, so the columns *always*
    exist and pages never check for their presence.  An unanalysed Game simply
    carries ``analyzed=False`` — enrichment never filters, hides, or
    restructures Games (ADR 0004).

    A single Game that fails to parse degrades to an empty analysis rather than
    failing the whole pass — one bad Chapter never costs the Sync.
    """
    enriched = df.copy()
    if enriched.empty:
        # The columns exist even on an empty store, so accessors are total.
        enriched["Analysis"] = pd.Series(dtype=object)
        enriched["Analyzed"] = pd.Series(dtype=bool)
        return enriched

    analyses: list[GameAnalysis] = []
    for _, row in enriched.iterrows():
        chapter_url = str(row.get("ChapterURL") or "")
        try:
            analysis = analyze_game(
                str(row.get("Movetext") or ""),
                player_color=str(row.get("Color") or ""),
                player_outcome=str(row.get("Outcome") or ""),
                chapter_url=chapter_url,
            )
        except Exception:  # enrichment must never break a Sync (ADR 0004)
            logger.exception("Engine analysis failed for %s — degrading", chapter_url)
            analysis = GameAnalysis(chapter_url=chapter_url)
        analyses.append(analysis)

    enriched["Analysis"] = analyses
    enriched["Analyzed"] = [a.analyzed for a in analyses]
    return enriched


# ---------------------------------------------------------------------------
# Aggregates across Games (the Analysis page's first view)
# ---------------------------------------------------------------------------

def mistake_type_distribution(analyses: Iterable[GameAnalysis]) -> dict[str, int]:
    """The tactical-vs-positional split of the player's mistakes across *analyses*.

    The Analysis page's first aggregate — "the single biggest weakness at a
    glance" (issue #58).  Only analysed Games contribute; a Game still awaiting
    its computer analysis carries an empty profile and adds nothing, so it is
    excluded from the distribution math without any special-casing.
    """
    counts = {"tactical": 0, "positional": 0}
    for analysis in analyses:
        if not isinstance(analysis, GameAnalysis) or not analysis.analyzed:
            continue
        for mistake in analysis.error_profile:
            if mistake.mistake_type in counts:
                counts[mistake.mistake_type] += 1
    return counts
