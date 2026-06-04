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
* The critical moment on the real analysed Alice Anderson Game is White's
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
from dataclasses import dataclass, field

import chess
import chess.pgn
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "MoveEval",
    "CriticalMoment",
    "GameAnalysis",
    "win_pct_from_cp",
    "analyze_game",
    "enrich_games_with_analysis",
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
class GameAnalysis:
    """One Game's structured engine analysis.

    An unanalysed Game (no requested computer analysis) degrades to
    ``analyzed=False`` with no moves and no critical moment — never an error.
    """

    chapter_url: str = ""
    analyzed: bool = False
    moves: list[MoveEval] = field(default_factory=list)
    critical_moment: CriticalMoment | None = None


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
