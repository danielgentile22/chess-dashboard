"""
pgn_stats_core.py
=================
Core PGN parsing and statistics computation for the Chess Stats Dashboard.

All public functions are pure (DataFrame in → DataFrame / dict / list out) with
no dependency on Dash or Plotly, so they can be used from notebooks or scripts.

Public API
----------
Parsing
  load_games_df            Parse a PGN file → tidy DataFrame + player name.
  load_games_from_text     Parse PGN text (Lichess Study export) → same output.
  extract_lessons_and_tags Extract Lessons / Tags from a game's comments (ADR 0002).
  extract_mainline_san     A game's mainline moves as SAN strings (issue #16).
  extract_movetext         Movetext with comments / [%eval] / variations kept (issue #57).
  apply_filters            Apply UI filter selections to the DataFrame.

Overview
  win_draw_loss_counts     Raw W/D/L/Unknown counts.
  termination_counts       Count of games by termination type.
  streaks                  Longest / current streak stats + last-20 list.
  kpi_stats                All KPI card values in one call.

Timeline
  win_rate_over_time       Cumulative win-rate per date.
  player_rating_over_time  Player's own rating per date.

Opponents
  opponent_summary         W/D/L per opponent (played >1 game).
  head_to_head             Full breakdown for one opponent.

Openings
  opening_summary          W/D/L + win-rate by ECO family and opening name.

Strength analysis
  opponent_rating_bucket_summary  W/D/L by opponent-rating-difference bucket.
  outcome_vs_rating_data          Scatter data: opp rating vs outcome number.

Game length
  game_length_data         Move-count data for histograms + outcome averages.

Activity
  activity_data            Monthly and day-of-week counts + win rates.

Events / tournaments
  event_summary            Per-event W/D/L, score, and notable opponents.
  performance_rating_stats FIDE performance-rating approximation.

Milestones
  compute_milestones       Auto-detected career milestone games.
"""
from __future__ import annotations

import io
import math
import re
from collections import Counter

import chess.pgn
import pandas as pd

__all__ = [
    "CANONICAL_TAGS",
    "load_games_df",
    "load_games_from_text",
    "extract_lessons_and_tags",
    "extract_mainline_san",
    "extract_movetext",
    "mainline_movetext",
    "has_my_analysis",
    "apply_filters",
    "win_draw_loss_counts",
    "termination_counts",
    "streaks",
    "current_form",
    "kpi_stats",
    "lessons_table",
    "tag_counts",
    "recurring_weaknesses",
    "repertoire_tree",
    "review_queue",
    "round_performance",
    "time_control_summary",
    "upset_tracker",
    "win_rate_over_time",
    "player_rating_over_time",
    "opponent_summary",
    "head_to_head",
    "scouting_report",
    "opening_summary",
    "opponent_rating_bucket_summary",
    "outcome_vs_rating_data",
    "game_length_data",
    "activity_data",
    "daily_activity",
    "event_summary",
    "performance_rating_stats",
    "compute_milestones",
    "milestone_deltas",
]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _norm_headers(h) -> dict:
    """Normalise PGN header keys to lowercase; drop entries with None values."""
    return {k.lower(): v for k, v in h.items() if v is not None}


def _first_present(h: dict, keys: list[str], default: str = "") -> str:
    """Return the first non-empty, non-'?' value for any of *keys*."""
    for k in keys:
        v = h.get(k.lower())
        if v and v != "?":
            return v
    return default


def safe_int(x) -> int | None:
    """
    Parse a rating-like string to int; return None on any failure.
    Handles "1850", "1850P" (provisional), and "?" gracefully.
    """
    try:
        if x is None:
            return None
        s = str(x).strip()
        if not s or s == "?":
            return None
        m = re.match(r"^(\d+)", s)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def infer_player_name_from_rows(rows: list[dict]) -> str:
    """
    Infer the player name as the most-frequent name in White/Black headers.
    In a personal PGN the player's name dominates, so the modal name is correct.
    """
    names: list[str] = []
    for r in rows:
        if r.get("White"):
            names.append(r["White"])
        if r.get("Black"):
            names.append(r["Black"])
    if not names:
        return ""
    return Counter(names).most_common(1)[0][0]


def compute_move_counts(game) -> tuple[int, int]:
    """Return (plies, full_moves) for a parsed PGN game node."""
    plies = sum(1 for _ in game.mainline_moves())
    return plies, (plies + 1) // 2


def extract_mainline_san(game) -> list[str]:
    """
    The game's mainline moves as SAN strings (issue #16).

    Variations are excluded — the repertoire tree reflects what was actually
    played, not what was analysed afterwards.
    """
    board = game.board()
    sans: list[str] = []
    for move in game.mainline_moves():
        sans.append(board.san(move))
        board.push(move)
    return sans


def extract_movetext(game) -> str:
    """
    A Game's movetext with everything the mainline-SAN view throws away kept
    intact: per-move comments, Lichess ``[%eval]`` annotations, NAGs, and
    variations (issue #57 [F1]).

    ``extract_mainline_san`` deliberately discards all of that — the
    repertoire tree only wants what was played.  The engine-analysis pass
    needs the opposite: the engine evaluations Lichess embedded in an
    analysed Chapter survive into the core only if the parser stops dropping
    them.  Storing the movetext (headers excluded — those already live in
    their own columns) lets ``engine_analysis_core`` re-parse one Game in
    isolation, exactly the way the USCF enrichment pass works off already
    fetched data.

    Returns the movetext as a string; an empty string for a Game with no
    moves, so a caller can skip it without a presence check.
    """
    exporter = chess.pgn.StringExporter(
        headers=False, variations=True, comments=True
    )
    return game.accept(exporter).strip()


def mainline_movetext(movetext: str) -> str:
    """
    *movetext* reduced to the bare played line — comments, ``[%eval]``
    annotations, NAGs, and variations all stripped (issue #60 [F6]).

    This is the inverse of :func:`extract_movetext`: it feeds the Game-detail
    pgn-viewer its default "clean replay" view, where Daniel first sees the bare
    game before switching to his own annotated analysis.  A blank or unparseable
    movetext yields ``""`` so a caller can skip it without a presence check.
    """
    if not movetext or not movetext.strip():
        return ""
    game = chess.pgn.read_game(io.StringIO(movetext))
    if game is None:
        return ""
    exporter = chess.pgn.StringExporter(
        headers=False, variations=False, comments=False
    )
    return game.accept(exporter).strip()


# ---------------------------------------------------------------------------
# Lessons and Tags (ADR 0002 — Lichess comment conventions)
# ---------------------------------------------------------------------------

# A Lesson is any comment starting with "Lesson:" (case-insensitive).
_LESSON_RE = re.compile(r"^\s*lesson:\s*(.+)", re.IGNORECASE | re.DOTALL)

# A Tag is a hashtag: '#' followed by a letter, then letters/digits/hyphens.
# Requiring a leading letter keeps SAN checkmate suffixes (Qxf7#) and
# numbering ("#1") from becoming Tags.
_TAG_RE = re.compile(r"#([a-zA-Z][a-zA-Z0-9-]*)")

# Lichess embeds machine annotations in comments: [%clk 1:30:00], [%cal ...],
# [%csl ...]. Strip them before looking for Lessons/Tags.
_LICHESS_DIRECTIVE_RE = re.compile(r"\[%[^\]]*\]")

# The canonical Tag taxonomy (CONTEXT.md) — the default vocabulary for what a
# Game taught. Freeform tags are allowed; they sort after these.
CANONICAL_TAGS = [
    "opening", "tactics", "calculation", "endgame",
    "time-trouble", "blunder", "strategy",
]


def _all_comments(game) -> list[str]:
    """Every comment in a game tree (chapter-level, moves, variations), in document order."""
    comments: list[str] = []

    def _walk(node) -> None:
        if node.comment and node.comment.strip():
            comments.append(node.comment)
        for child in node.variations:
            _walk(child)

    _walk(game)
    return comments


def extract_lessons_and_tags(game) -> tuple[list[str], list[str]]:
    """
    Extract a Game's Lessons and Tags from its chapter comments (ADR 0002).

    Returns
    -------
    lessons : Comment texts that start with "Lesson:" (prefix stripped),
              in document order.
    tags    : Hashtags found in any comment, lowercase, deduplicated,
              in first-seen order.
    """
    lessons: list[str] = []
    tags: list[str] = []
    seen_tags: set[str] = set()

    for raw in _all_comments(game):
        text = _LICHESS_DIRECTIVE_RE.sub("", raw).strip()
        if not text:
            continue

        lesson_match = _LESSON_RE.match(text)
        if lesson_match:
            lessons.append(lesson_match.group(1).strip())

        for tag in _TAG_RE.findall(text):
            tag = tag.lower()
            if tag not in seen_tags:
                seen_tags.add(tag)
                tags.append(tag)

    return lessons, tags


def has_my_analysis(movetext: str) -> bool:
    """
    Whether *movetext* carries Daniel's own annotations — the signal the
    Game-detail view uses to offer a "My Analysis" board (issue #60 [F6]).

    True when he added something to the Chapter himself: a comment of his own.
    Lichess's machine annotations (``[%eval]`` / ``[%clk]``) are stripped first,
    so they never count.

    A blank or unparseable movetext is simply "no analysis" — never an error.
    """
    if not movetext or not movetext.strip():
        return False
    game = chess.pgn.read_game(io.StringIO(movetext))
    if game is None:
        return False

    if _has_variation(game):
        return True

    for raw in _all_comments(game):
        text = _LICHESS_DIRECTIVE_RE.sub("", raw).strip()
        # A Lesson is its own surface (ADR 0002); only other prose counts here.
        if text and not _LESSON_RE.match(text):
            return True
    return False


def _has_variation(game) -> bool:
    """True if the game tree branches anywhere — an alternative line he added."""
    node = game
    while node.variations:
        if len(node.variations) > 1:
            return True
        node = node.variations[0]
    return False


def outcome_for_player(result: str, color: str) -> str:
    """Map a PGN result string + piece colour to Win / Draw / Loss / Unknown."""
    mapping = {
        "White": {"1-0": "Win", "0-1": "Loss", "1/2-1/2": "Draw"},
        "Black": {"0-1": "Win", "1-0": "Loss", "1/2-1/2": "Draw"},
    }
    return mapping.get(color, {}).get(result, "Unknown")


def winner_from_result(result: str) -> str:
    """Map a PGN result string to White / Black / Draw / Unknown."""
    return {"1-0": "White", "0-1": "Black", "1/2-1/2": "Draw"}.get(result, "Unknown")


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def load_games_df(
    pgn_path: str,
    player_name: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """
    Parse a PGN file and return a tidy DataFrame + detected player name.

    Parameters
    ----------
    pgn_path    : Path to a UTF-8 encoded, multi-game PGN file.
    player_name : The player's name as it appears in headers (auto-detected
                  from most-frequent name when omitted).

    Returns
    -------
    df        : One row per game (see column docs in module docstring).
    detected  : The player name used for all perspective columns.
    """
    with open(pgn_path, encoding="utf-8", errors="ignore") as f:
        return load_games_from_text(f.read(), player_name=player_name)


def load_games_from_text(
    pgn_text: str,
    player_name: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """
    Parse PGN text (e.g. a Lichess Study export) and return a tidy DataFrame
    + detected player name.

    Same contract as :func:`load_games_df`, but takes the PGN content directly
    instead of a file path.
    """
    rows: list[dict] = []
    with io.StringIO(pgn_text) as f:
        idx = 0
        while True:
            game = chess.pgn.read_game(f)
            if game is None:
                break
            idx += 1
            h = _norm_headers(game.headers)

            white = _first_present(h, ["white"])
            black = _first_present(h, ["black"])
            white_rating = _first_present(h, ["whiteelo", "whiterating", "whiteuscf", "whiteuscfelo"])
            black_rating = _first_present(h, ["blackelo", "blackrating", "blackuscf", "blackuscfelo"])
            white_id = _first_present(h, ["whitefideid", "whiteuscfid", "whiteid", "whiteuscf_id"])
            black_id = _first_present(h, ["blackfideid", "blackuscfid", "blackid", "blackuscf_id"])

            result = _first_present(h, ["result"])
            termination = _first_present(h, ["termination"]) or "Unknown"
            event = _first_present(h, ["event"])
            site = _first_present(h, ["site"])
            round_tag = _first_present(h, ["round"])
            board_tag = _first_present(h, ["board"])
            date = _first_present(h, ["date", "utcdate"])
            time_tag = _first_present(h, ["utctime", "time"])
            eco = _first_present(h, ["eco"])
            opening = _first_present(h, ["opening"])
            timecontrol = _first_present(h, ["timecontrol"])
            # One mainline walk gives both the move list and the counts
            moves_san = extract_mainline_san(game)
            plies, fullmoves = len(moves_san), (len(moves_san) + 1) // 2
            # The rich movetext (comments, [%eval], variations) kept for the
            # engine-analysis pass — never disturbs the mainline-SAN view above.
            movetext = extract_movetext(game)

            # Lichess Study identity (ADR 0001): ChapterURL is the permanent
            # identity of a Game; empty for PGNs that aren't Study exports.
            study_name = _first_present(h, ["studyname"])
            chapter_name = _first_present(h, ["chaptername"])
            chapter_url = _first_present(h, ["chapterurl"])

            # Lessons and Tags from chapter comments (ADR 0002)
            lessons, tags = extract_lessons_and_tags(game)

            rows.append({
                "Index": idx, "Date": date, "Time": time_tag,
                "Event": event, "Site": site, "Round": round_tag,
                # Numeric round so round 10 sorts after round 2, not after round 1
                "RoundNum": safe_int(round_tag), "Board": board_tag,
                "ECO": eco, "Opening": opening, "TimeControl": timecontrol,
                "White": white, "WhiteRating": white_rating,
                "WhiteRatingNum": safe_int(white_rating), "WhiteID": white_id,
                "Black": black, "BlackRating": black_rating,
                "BlackRatingNum": safe_int(black_rating), "BlackID": black_id,
                "Result": result, "Winner": winner_from_result(result),
                "Termination": termination, "Plies": plies, "FullMoves": fullmoves,
                "Moves": moves_san, "Movetext": movetext,
                "StudyName": study_name, "ChapterName": chapter_name,
                "ChapterURL": chapter_url,
                "Lessons": lessons, "Tags": tags,
            })

    if not rows:
        return pd.DataFrame(), (player_name or "")

    df = pd.DataFrame(rows)
    df["Date_dt"] = pd.to_datetime(
        df["Date"].replace("????.??.??", None),
        errors="coerce", format="%Y.%m.%d",
    )

    detected = player_name or infer_player_name_from_rows(rows)

    is_white = df["White"] == detected
    is_black = df["Black"] == detected

    df["Color"] = "Unknown"
    df.loc[is_white, "Color"] = "White"
    df.loc[is_black, "Color"] = "Black"

    df["Opponent"] = ""
    df.loc[is_white, "Opponent"] = df.loc[is_white, "Black"]
    df.loc[is_black, "Opponent"] = df.loc[is_black, "White"]

    df["Outcome"] = df.apply(
        lambda r: outcome_for_player(r["Result"], r["Color"]), axis=1
    )

    # Perspective ratings
    df["PlayerRating"] = ""
    df.loc[is_white, "PlayerRating"] = df.loc[is_white, "WhiteRating"]
    df.loc[is_black, "PlayerRating"] = df.loc[is_black, "BlackRating"]

    df["PlayerRatingNum"] = pd.array([None] * len(df), dtype="object")
    df.loc[is_white, "PlayerRatingNum"] = df.loc[is_white, "WhiteRatingNum"]
    df.loc[is_black, "PlayerRatingNum"] = df.loc[is_black, "BlackRatingNum"]
    df["PlayerRatingNum"] = pd.to_numeric(df["PlayerRatingNum"], errors="coerce")

    df["OpponentRating"] = ""
    df.loc[is_white, "OpponentRating"] = df.loc[is_white, "BlackRating"]
    df.loc[is_black, "OpponentRating"] = df.loc[is_black, "WhiteRating"]

    df["OpponentRatingNum"] = pd.array([None] * len(df), dtype="object")
    df.loc[is_white, "OpponentRatingNum"] = df.loc[is_white, "BlackRatingNum"]
    df.loc[is_black, "OpponentRatingNum"] = df.loc[is_black, "WhiteRatingNum"]
    df["OpponentRatingNum"] = pd.to_numeric(df["OpponentRatingNum"], errors="coerce")

    # Rating difference: positive = opponent is higher-rated (harder game)
    df["RatingDiff"] = df["OpponentRatingNum"] - df["PlayerRatingNum"]

    return df, detected


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def apply_filters(
    df: pd.DataFrame,
    colors: list[str],
    outcomes: list[str],
    terminations: list[str],
    date_start: str | None,
    date_end: str | None,
    events: list[str] | None = None,
    min_moves: int | None = None,
    max_moves: int | None = None,
    min_opp_rating: int | None = None,
    max_opp_rating: int | None = None,
) -> pd.DataFrame:
    """
    Apply UI filter selections to *df* and return a filtered copy.

    All list parameters: empty list / None means keep all values.
    """
    out = df.copy()
    if colors:
        out = out[out["Color"].isin(colors)]
    if outcomes:
        out = out[out["Outcome"].isin(outcomes)]
    if terminations:
        out = out[out["Termination"].isin(terminations)]
    if events:
        out = out[out["Event"].isin(events)]
    if date_start or date_end:
        out = out[out["Date_dt"].notna()]
        if date_start:
            out = out[out["Date_dt"] >= pd.to_datetime(date_start)]
        if date_end:
            out = out[out["Date_dt"] <= pd.to_datetime(date_end)]
    if min_moves is not None:
        out = out[out["FullMoves"] >= min_moves]
    if max_moves is not None:
        out = out[out["FullMoves"] <= max_moves]
    if min_opp_rating is not None:
        out = out[out["OpponentRatingNum"].notna() & (out["OpponentRatingNum"] >= min_opp_rating)]
    if max_opp_rating is not None:
        out = out[out["OpponentRatingNum"].notna() & (out["OpponentRatingNum"] <= max_opp_rating)]
    return out


# ---------------------------------------------------------------------------
# Overview statistics
# ---------------------------------------------------------------------------

def _without_forfeits(df: pd.DataFrame) -> pd.DataFrame:
    """
    The Games that were actually played: Forfeits excluded (issue #29 — a
    forfeit win is not chess).

    The Forfeit column is enrichment (uscf_core.enrich_games); a DataFrame
    without it — raw parser output — is returned unchanged.
    """
    if df.empty or "Forfeit" not in df.columns:
        return df
    return df[~df["Forfeit"]]


def win_draw_loss_counts(df: pd.DataFrame) -> pd.Series:
    """Return a Series with counts for Win, Draw, Loss, Unknown outcomes.

    Forfeits are excluded — a forfeit win is not a win over the board."""
    outcomes = ["Win", "Draw", "Loss", "Unknown"]
    if df.empty or "Outcome" not in df.columns:
        return pd.Series(0, index=pd.Index(outcomes), name="count")
    df = _without_forfeits(df)
    return df["Outcome"].value_counts().reindex(outcomes, fill_value=0)


def termination_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Return a (Termination, Games) DataFrame, descending by count."""
    if df.empty:
        return pd.DataFrame(columns=["Termination", "Games"])
    c = df["Termination"].value_counts().reset_index()
    c.columns = ["Termination", "Games"]
    return c


def streaks(df: pd.DataFrame) -> dict:
    """
    Compute streak statistics over the filtered game set.

    Games are ordered by Date_dt ascending (undated games placed last),
    ties broken by Index.

    Returns dict keys:
      longest_streak_no_loss, longest_streak_wins_only,
      current_streak_same_outcome, current_streak_outcome, last_20
    """
    df = _without_forfeits(df)  # a no-show win never extends a Streak
    if df.empty:
        return {
            "longest_streak_no_loss": 0,
            "longest_streak_wins_only": 0,
            "current_streak_same_outcome": 0,
            "current_streak_outcome": "N/A",
            "last_20": [],
        }

    d = df.copy()
    d["_ds"] = d["Date_dt"].fillna(pd.Timestamp.max)
    d = d.sort_values(["_ds", "Index"])
    outcomes = d["Outcome"].tolist()

    longest_no_loss = cur_no_loss = 0
    longest_wins = cur_wins = 0
    for o in outcomes:
        if o in ("Win", "Draw"):
            cur_no_loss += 1
            longest_no_loss = max(longest_no_loss, cur_no_loss)
        else:
            cur_no_loss = 0
        if o == "Win":
            cur_wins += 1
            longest_wins = max(longest_wins, cur_wins)
        else:
            cur_wins = 0

    last_outcome = outcomes[-1] if outcomes else "N/A"
    current_same = 0
    for o in reversed(outcomes):
        if o == last_outcome:
            current_same += 1
        else:
            break

    return {
        "longest_streak_no_loss": longest_no_loss,
        "longest_streak_wins_only": longest_wins,
        "current_streak_same_outcome": current_same,
        "current_streak_outcome": last_outcome,
        "last_20": outcomes[-20:],
    }


def current_form(df: pd.DataFrame) -> dict:
    """
    Current form for the header indicators (issue #10): how the most recent
    Games have gone, ordered by date.

    Returns dict keys:
      win_streak  : consecutive Wins ending at the most recent Game (0 if the
                    last Game wasn't a Win)
      loss_streak : consecutive Losses ending at the most recent Game
      last_5      : outcomes of the last 5 Games, oldest → newest
    """
    s = streaks(df)
    outcome = s["current_streak_outcome"]
    count = s["current_streak_same_outcome"]
    return {
        "win_streak": count if outcome == "Win" else 0,
        "loss_streak": count if outcome == "Loss" else 0,
        "last_5": s["last_20"][-5:],
    }


def kpi_stats(df: pd.DataFrame) -> dict:
    """
    Compute all KPI card values for the (possibly filtered) DataFrame.

    Returns dict with keys: total_games, win_pct, draw_pct, loss_pct,
    current_rating, peak_rating, avg_opp_rating, performance_rating,
    longest_win_streak, unique_opponents, favorite_opening, favorite_eco_family.
    """
    empty = {
        "total_games": 0, "win_pct": 0.0, "draw_pct": 0.0, "loss_pct": 0.0,
        "current_rating": None, "peak_rating": None, "avg_opp_rating": None,
        "performance_rating": None, "longest_win_streak": 0,
        "unique_opponents": 0, "favorite_opening": "—", "favorite_eco_family": "—",
    }
    if df.empty:
        return empty

    counts = win_draw_loss_counts(df)
    decisive = counts["Win"] + counts["Draw"] + counts["Loss"]

    rated_player = df["PlayerRatingNum"].dropna()
    current_rating: int | None = None
    dated = df[df["Date_dt"].notna() & df["PlayerRatingNum"].notna()]
    if not dated.empty:
        current_rating = int(dated.sort_values("Date_dt").iloc[-1]["PlayerRatingNum"])
    peak_rating = int(rated_player.max()) if not rated_player.empty else None

    rated_opp = df["OpponentRatingNum"].dropna()
    avg_opp_rating = round(float(rated_opp.mean()), 0) if not rated_opp.empty else None

    pr = performance_rating_stats(df)
    s = streaks(df)

    # Favourite opening/family: opening stats, so Forfeits never count (#29)
    played = _without_forfeits(df)
    openings = played["Opening"].replace("", pd.NA).dropna()
    fav_opening = str(openings.value_counts().index[0]) if not openings.empty else "—"
    eco_vals = played["ECO"].replace("", pd.NA).dropna()
    fav_eco = str(eco_vals.value_counts().index[0])[0].upper() if not eco_vals.empty else "—"

    return {
        "total_games": len(df),
        "win_pct": round(counts["Win"] / decisive * 100, 1) if decisive else 0.0,
        "draw_pct": round(counts["Draw"] / decisive * 100, 1) if decisive else 0.0,
        "loss_pct": round(counts["Loss"] / decisive * 100, 1) if decisive else 0.0,
        "current_rating": current_rating,
        "peak_rating": peak_rating,
        "avg_opp_rating": avg_opp_rating,
        "performance_rating": pr.get("performance_rating"),
        "longest_win_streak": s["longest_streak_wins_only"],
        "unique_opponents": int(df["Opponent"].nunique()),
        "favorite_opening": fav_opening,
        "favorite_eco_family": fav_eco,
    }


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def win_rate_over_time(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cumulative win rate over time (one row per date, dated games only,
    excluding Unknown outcomes and Forfeits). Columns: Date_dt, CumGames,
    CumWins, WinRate.
    """
    if df.empty:
        return pd.DataFrame(columns=["Date_dt", "CumGames", "CumWins", "WinRate"])
    df = _without_forfeits(df)
    d = df[df["Date_dt"].notna() & df["Outcome"].isin(["Win", "Draw", "Loss"])].copy()
    d = d.sort_values("Date_dt")
    if d.empty:
        return pd.DataFrame(columns=["Date_dt", "CumGames", "CumWins", "WinRate"])
    d["IsWin"] = (d["Outcome"] == "Win").astype(int)
    d["CumGames"] = d["IsWin"].expanding().count().astype(int)
    d["CumWins"] = d["IsWin"].expanding().sum().astype(int)
    d["WinRate"] = (d["CumWins"] / d["CumGames"]) * 100.0
    return d.groupby("Date_dt").tail(1)[["Date_dt", "CumGames", "CumWins", "WinRate"]]


def player_rating_over_time(df: pd.DataFrame) -> pd.DataFrame:
    """
    Player's own rating per date (last game of each day).
    Columns: Date_dt, PlayerRating. Excludes games with missing date or rating.
    """
    if df.empty:
        return pd.DataFrame(columns=["Date_dt", "PlayerRating"])
    d = df[df["Date_dt"].notna() & df["PlayerRatingNum"].notna()].copy()
    if d.empty:
        return pd.DataFrame(columns=["Date_dt", "PlayerRating"])
    d = d.sort_values(["Date_dt", "Index"])
    return (
        d.groupby("Date_dt").tail(1)[["Date_dt", "PlayerRatingNum"]]
        .rename(columns={"PlayerRatingNum": "PlayerRating"})
    )


# ---------------------------------------------------------------------------
# Opponents
# ---------------------------------------------------------------------------

def opponent_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Opponents played more than once with W/D/L counts and win rate.
    Columns: Opponent, Games, Win, Draw, Loss, WinRate. Sorted by Games desc.
    """
    if df.empty:
        return pd.DataFrame(columns=["Opponent", "Games", "Win", "Draw", "Loss", "WinRate"])
    pivot = (
        df.groupby(["Opponent", "Outcome"])
        .size().unstack(fill_value=0)
        .reindex(columns=["Win", "Draw", "Loss"], fill_value=0)
    )
    pivot["Games"] = pivot.sum(axis=1)
    pivot["WinRate"] = (pivot["Win"] / pivot["Games"] * 100).round(1)
    pivot = pivot[pivot["Games"] > 1]
    out = pivot.reset_index()[["Opponent", "Games", "Win", "Draw", "Loss", "WinRate"]]
    return out.sort_values(["Games", "Win"], ascending=[False, False])


def head_to_head(df: pd.DataFrame, opponent: str) -> dict:
    """
    Full head-to-head breakdown for games against *opponent*.

    Returns dict with keys: total, win, draw, loss,
    as_white_(w/d/l), as_black_(w/d/l), avg_opp_rating, game_rows.
    """
    d = df[df["Opponent"] == opponent].copy()
    if d.empty:
        return {"total": 0, "win": 0, "draw": 0, "loss": 0, "game_rows": []}
    d["_ds"] = d["Date_dt"].fillna(pd.Timestamp.max)
    d = d.sort_values(["_ds", "Index"])

    def _n(mask) -> int:
        return int(mask.sum())

    w = d[d["Color"] == "White"]
    b = d[d["Color"] == "Black"]
    rated = d["OpponentRatingNum"].dropna()

    return {
        "total": len(d),
        "win": _n(d["Outcome"] == "Win"),
        "draw": _n(d["Outcome"] == "Draw"),
        "loss": _n(d["Outcome"] == "Loss"),
        "as_white_w": _n(w["Outcome"] == "Win"),
        "as_white_d": _n(w["Outcome"] == "Draw"),
        "as_white_l": _n(w["Outcome"] == "Loss"),
        "as_black_w": _n(b["Outcome"] == "Win"),
        "as_black_d": _n(b["Outcome"] == "Draw"),
        "as_black_l": _n(b["Outcome"] == "Loss"),
        "avg_opp_rating": round(float(rated.mean()), 0) if not rated.empty else None,
        "game_rows": d[[
            "Date", "Color", "Outcome", "Result",
            "PlayerRating", "OpponentRating", "Event", "Round", "Termination", "FullMoves",
            "ChapterURL",
        ]].rename(columns={"PlayerRating": "MyRating", "OpponentRating": "OppRating"})
        .to_dict("records"),
    }


# ---------------------------------------------------------------------------
# Scouting Report (issue #13)
# ---------------------------------------------------------------------------

def scouting_report(df: pd.DataFrame, opponent: str) -> dict:
    """
    The pre-game dossier on one opponent (CONTEXT.md: Scouting Report).

    Composes everything Daniel wants to know right before facing someone
    again: the head-to-head score and rating gap, the per-game results
    timeline, the openings they've played against him (split by his color),
    how those Games ended, and the Lessons he wrote after facing them.
    """
    report: dict = {
        "opponent": opponent,
        "total": 0, "win": 0, "draw": 0, "loss": 0, "score": "0/0",
        "their_rating": None, "my_rating": None, "rating_gap": None,
        "timeline": [],
        "openings_as_white": [], "openings_as_black": [],
        "terminations": [],
        "lessons": [],
    }
    if df.empty or "Opponent" not in df.columns:
        return report

    # The opponent's games, filtered once; everything below works on this slice
    games = df[df["Opponent"] == opponent]

    h2h = head_to_head(games, opponent)
    total = h2h["total"]
    report.update({
        "total": total,
        "win": h2h.get("win", 0),
        "draw": h2h.get("draw", 0),
        "loss": h2h.get("loss", 0),
        "score": f"{h2h.get('win', 0) + 0.5 * h2h.get('draw', 0):g}/{total}",
        # Per-game results, oldest → newest (head_to_head sorts by date)
        "timeline": h2h.get("game_rows", []),
    })
    if total == 0:
        return report

    def _latest_rating(frame: pd.DataFrame, column: str) -> int | None:
        """The rating in the most recent dated game (ties broken by Index)."""
        rated = frame[frame["Date_dt"].notna() & frame[column].notna()]
        if rated.empty:
            return None
        return int(rated.sort_values(["Date_dt", "Index"]).iloc[-1][column])

    # Rating gap: their latest known rating (vs Daniel) against Daniel's
    # latest rating anywhere — "where do I stand if we play tomorrow?"
    report["their_rating"] = _latest_rating(games, "OpponentRatingNum")
    report["my_rating"] = _latest_rating(df, "PlayerRatingNum")
    if report["their_rating"] is not None and report["my_rating"] is not None:
        report["rating_gap"] = report["their_rating"] - report["my_rating"]

    # Openings they've played against me, split by my color
    for color, key in (("White", "openings_as_white"), ("Black", "openings_as_black")):
        _, openings = opening_summary(games[games["Color"] == color])
        report[key] = openings.to_dict("records")

    # How our games ended
    report["terminations"] = termination_counts(games).to_dict("records")

    # The differentiator: every Lesson written after facing them
    report["lessons"] = lessons_table(games).to_dict("records")

    return report


# ---------------------------------------------------------------------------
# Openings
# ---------------------------------------------------------------------------

_ECO_FAMILY_NAMES = {
    "A": "A — Flank / Queen's Pawn",
    "B": "B — Semi-Open",
    "C": "C — Open",
    "D": "D — Closed / Semi-Closed",
    "E": "E — Indian Defences",
}


def opening_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    W/D/L and win-rate at two granularities: ECO family and specific opening.

    Returns (family_df, opening_df).
    family_df  columns: ECO_Family, FamilyName, Games, Win, Draw, Loss, WinRate
    opening_df columns: ECO, Opening, Games, Win, Draw, Loss, WinRate
    """
    _FC = ["ECO_Family", "FamilyName", "Games", "Win", "Draw", "Loss", "WinRate"]
    _OC = ["ECO", "Opening", "Games", "Win", "Draw", "Loss", "WinRate"]

    df = _without_forfeits(df)  # one forced move is not repertoire data (#29)
    if df.empty:
        return pd.DataFrame(columns=_FC), pd.DataFrame(columns=_OC)

    d = df.copy()
    d["ECO"] = d["ECO"].fillna("").astype(str).str.strip()
    d["Opening"] = d["Opening"].fillna("").astype(str).str.strip()
    d["ECO_Family"] = d["ECO"].apply(lambda x: x[0].upper() if x else "?")
    has_eco = d[d["ECO_Family"] != "?"].copy()
    if has_eco.empty:
        return pd.DataFrame(columns=_FC), pd.DataFrame(columns=_OC)

    def _build_pivot(frame, key):
        pv = (
            frame.groupby([key, "Outcome"]).size()
            .unstack(fill_value=0)
            .reindex(columns=["Win", "Draw", "Loss"], fill_value=0)
        )
        pv["Games"] = pv.sum(axis=1)
        pv["WinRate"] = (pv["Win"] / pv["Games"] * 100).round(1)
        return pv.reset_index()

    fam = _build_pivot(has_eco, "ECO_Family")
    fam.columns = ["ECO_Family", "Win", "Draw", "Loss", "Games", "WinRate"]
    fam["FamilyName"] = fam["ECO_Family"].map(
        lambda x: _ECO_FAMILY_NAMES.get(x, f"{x} — Other")
    )
    fam = fam.sort_values("Games", ascending=False)[_FC]

    opn = (
        has_eco.groupby(["ECO", "Opening", "Outcome"]).size()
        .unstack(fill_value=0)
        .reindex(columns=["Win", "Draw", "Loss"], fill_value=0)
        .reset_index()
    )
    opn["Games"] = opn[["Win", "Draw", "Loss"]].sum(axis=1)
    opn["WinRate"] = (opn["Win"] / opn["Games"] * 100).round(1)
    opn = opn.sort_values("Games", ascending=False)[_OC]

    return fam, opn


# ---------------------------------------------------------------------------
# Strength analysis
# ---------------------------------------------------------------------------

_BUCKET_LABELS = [
    "< −200", "−200 to −101", "−100 to −1",
    "0 to +99", "+100 to +199", "≥ +200",
]
_BUCKET_EDGES = [-math.inf, -200, -100, 0, 100, 200, math.inf]


def _rating_bucket(diff: float) -> str:
    for i in range(len(_BUCKET_EDGES) - 1):
        if _BUCKET_EDGES[i] <= diff < _BUCKET_EDGES[i + 1]:
            return _BUCKET_LABELS[i]
    return _BUCKET_LABELS[-1]


def opponent_rating_bucket_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    W/D/L by opponent-rating-difference bucket.
    RatingDiff = OpponentRatingNum − PlayerRatingNum (positive = stronger opp).
    Columns: Bucket, Games, Win, Draw, Loss, WinRate.
    """
    if df.empty:
        return pd.DataFrame(columns=["Bucket", "Games", "Win", "Draw", "Loss", "WinRate"])
    d = df[df["RatingDiff"].notna()].copy()
    if d.empty:
        return pd.DataFrame(columns=["Bucket", "Games", "Win", "Draw", "Loss", "WinRate"])
    d["Bucket"] = d["RatingDiff"].apply(_rating_bucket)
    pivot = (
        d.groupby(["Bucket", "Outcome"]).size()
        .unstack(fill_value=0)
        .reindex(columns=["Win", "Draw", "Loss"], fill_value=0)
    )
    pivot["Games"] = pivot.sum(axis=1)
    pivot["WinRate"] = (pivot["Win"] / pivot["Games"] * 100).round(1)
    out = pivot.reset_index()[["Bucket", "Games", "Win", "Draw", "Loss", "WinRate"]]
    order = {b: i for i, b in enumerate(_BUCKET_LABELS)}
    out["_o"] = out["Bucket"].map(order)
    return out.sort_values("_o").drop(columns=["_o"])


def outcome_vs_rating_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Scatter data: opponent rating vs outcome numeric value.
    Columns: OpponentRatingNum, OutcomeNum (1/0.5/0), Outcome, Opponent, Date.
    """
    if df.empty:
        return pd.DataFrame(columns=["OpponentRatingNum", "OutcomeNum", "Outcome", "Opponent", "Date"])
    d = df[
        df["OpponentRatingNum"].notna() & df["Outcome"].isin(["Win", "Draw", "Loss"])
    ].copy()
    d["OutcomeNum"] = d["Outcome"].map({"Win": 1.0, "Draw": 0.5, "Loss": 0.0})
    return d[["OpponentRatingNum", "OutcomeNum", "Outcome", "Opponent", "Date"]].copy()


# ---------------------------------------------------------------------------
# Game length
# ---------------------------------------------------------------------------

def game_length_data(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Move-count data for histogram charts plus per-outcome averages.

    Returns (hist_df, averages_dict).
    hist_df  columns: FullMoves, Outcome (decisive games only).
    averages keys: Win, Draw, Loss → float or None.
    """
    if df.empty:
        return pd.DataFrame(columns=["FullMoves", "Outcome"]), {}
    d = df[df["Outcome"].isin(["Win", "Draw", "Loss"]) & df["FullMoves"].notna()].copy()
    avgs = {
        o: round(float(d[d["Outcome"] == o]["FullMoves"].mean()), 1)
        if not d[d["Outcome"] == o].empty else None
        for o in ("Win", "Draw", "Loss")
    }
    return d[["FullMoves", "Outcome"]], avgs


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

def activity_data(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Monthly and day-of-week game counts + win rates.

    Returns (monthly_df, dow_df).
    monthly_df columns: YearMonth (YYYY-MM str), Games, Win, WinRate.
    dow_df     columns: DayOfWeek (Mon…Sun), Games, Win, WinRate. Mon→Sun order.
    """
    _EM = pd.DataFrame(columns=["YearMonth", "Games", "Win", "WinRate"])
    _ED = pd.DataFrame(columns=["DayOfWeek", "Games", "Win", "WinRate"])
    if df.empty:
        return _EM, _ED
    d = df[df["Date_dt"].notna() & df["Outcome"].isin(["Win", "Draw", "Loss"])].copy()
    if d.empty:
        return _EM, _ED
    d["IsWin"] = (d["Outcome"] == "Win").astype(int)
    d["YearMonth"] = d["Date_dt"].dt.strftime("%Y-%m")
    d["DayOfWeek"] = d["Date_dt"].dt.day_name().str[:3]

    m = d.groupby("YearMonth").agg(
        Games=("IsWin", "count"), Win=("IsWin", "sum")
    ).reset_index()
    m["WinRate"] = (m["Win"] / m["Games"] * 100).round(1)
    m = m.sort_values("YearMonth")

    dow_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dw = d.groupby("DayOfWeek").agg(
        Games=("IsWin", "count"), Win=("IsWin", "sum")
    ).reset_index()
    dw["WinRate"] = (dw["Win"] / dw["Games"] * 100).round(1)
    dw["_o"] = dw["DayOfWeek"].map({day: i for i, day in enumerate(dow_order)})
    dw = dw.sort_values("_o").drop(columns=["_o"])

    return m, dw


_DAILY_COLS = ["Date_dt", "Games", "Win", "Draw", "Loss", "Net", "Detail"]


def daily_activity(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-day Game results for the activity heatmap calendar (issue #14).

    One row per calendar day that has dated Games, sorted by day.
    Columns:
      Date_dt          : the day
      Games            : how many Games were played
      Win, Draw, Loss  : outcome counts
      Net              : Win − Loss (positive = winning day → green,
                         negative = losing day → red; drives the cell color)
      Detail           : that day's Games as text, one per line
                         ("Win vs Opponent A<br>Draw vs Opponent B") for hover
    Days without Games don't appear (the calendar shows them as empty cells).
    Unfinished games (Outcome "Unknown") are excluded — same convention as
    the monthly/day-of-week activity charts.
    """
    if df.empty or "Date_dt" not in df.columns:
        return pd.DataFrame(columns=_DAILY_COLS)

    d = df[df["Date_dt"].notna() & df["Outcome"].isin(["Win", "Draw", "Loss"])].copy()
    if d.empty:
        return pd.DataFrame(columns=_DAILY_COLS)

    d["Day"] = d["Date_dt"].dt.normalize()
    daily = (
        d.groupby("Day")["Outcome"].value_counts().unstack(fill_value=0)
        .reindex(columns=["Win", "Draw", "Loss"], fill_value=0)
    )
    daily["Games"] = d.groupby("Day").size()
    daily["Net"] = daily["Win"] - daily["Loss"]
    d["_line"] = d["Outcome"].astype(str) + " vs " + d["Opponent"].astype(str)
    daily["Detail"] = d.groupby("Day")["_line"].agg("<br>".join)
    out = daily.reset_index().rename(columns={"Day": "Date_dt"})
    return out.sort_values("Date_dt").reset_index(drop=True)[_DAILY_COLS]


# ---------------------------------------------------------------------------
# Events / tournaments
# ---------------------------------------------------------------------------

def event_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-event W/D/L, score, and notable opponents.
    Columns: Event, FirstDate, Games, Win, Draw, Loss, Score,
             HighestOpp, HighestOppRating, HighestOppOutcome,
             LowestOpp, LowestOppRating, LowestOppOutcome.
    """
    _COLS = [
        "Event", "FirstDate", "Games", "Win", "Draw", "Loss", "Score",
        "HighestOpp", "HighestOppRating", "HighestOppOutcome",
        "LowestOpp", "LowestOppRating", "LowestOppOutcome",
    ]
    if df.empty:
        return pd.DataFrame(columns=_COLS)
    d = df.copy()
    d["Event"] = d["Event"].fillna("").astype(str)
    d = d[d["Event"].str.strip() != ""]
    if d.empty:
        return pd.DataFrame(columns=_COLS)

    rows = []
    for event, g in d.groupby("Event", dropna=False):
        first_date = g["Date_dt"].min()
        win = int((g["Outcome"] == "Win").sum())
        draw = int((g["Outcome"] == "Draw").sum())
        loss = int((g["Outcome"] == "Loss").sum())
        games = int(len(g))
        rated = g[g["OpponentRatingNum"].notna()].copy()
        hi_n = hi_o = lo_n = lo_o = ""
        hi_r: int | str = ""
        lo_r: int | str = ""
        if not rated.empty:
            hr = rated.loc[rated["OpponentRatingNum"].idxmax()]
            lr = rated.loc[rated["OpponentRatingNum"].idxmin()]
            hi_n, hi_r, hi_o = str(hr["Opponent"]), int(hr["OpponentRatingNum"]), str(hr["Outcome"])
            lo_n, lo_r, lo_o = str(lr["Opponent"]), int(lr["OpponentRatingNum"]), str(lr["Outcome"])
        rows.append({
            "Event": event,
            "FirstDate": first_date.date().isoformat() if pd.notna(first_date) else "",
            "Games": games, "Win": win, "Draw": draw, "Loss": loss,
            "Score": f"{win + 0.5 * draw:g}/{games}",
            "HighestOpp": hi_n, "HighestOppRating": hi_r, "HighestOppOutcome": hi_o,
            "LowestOpp": lo_n, "LowestOppRating": lo_r, "LowestOppOutcome": lo_o,
        })

    out = pd.DataFrame(rows)
    out["_s"] = pd.to_datetime(out["FirstDate"], errors="coerce")
    return out.sort_values(["_s", "Event"]).drop(columns=["_s"])


def performance_rating_stats(df: pd.DataFrame) -> dict:
    """
    FIDE performance-rating approximation.
    Formula: PR = avg(opp_rating) + 400 * log10(p / (1-p))  where p = score%.
    Only uses games with rated opponents.

    Returns dict: performance_rating, avg_opp_rating, score, score_pct, rated_games.
    """
    empty = {"performance_rating": None, "avg_opp_rating": None,
             "score": 0.0, "score_pct": 0.0, "rated_games": 0}
    if df.empty or "OpponentRatingNum" not in df.columns:
        return empty
    rated = df[
        df["OpponentRatingNum"].notna() & df["Outcome"].isin(["Win", "Draw", "Loss"])
    ].copy()
    if rated.empty:
        return empty
    wins = int((rated["Outcome"] == "Win").sum())
    draws = int((rated["Outcome"] == "Draw").sum())
    total = len(rated)
    score = wins + 0.5 * draws
    score_pct = score / total
    avg_opp = float(rated["OpponentRatingNum"].mean())
    if score_pct >= 1.0:
        pr = avg_opp + 800
    elif score_pct <= 0.0:
        pr = avg_opp - 800
    else:
        pr = avg_opp + 400 * math.log10(score_pct / (1.0 - score_pct))
    return {
        "performance_rating": round(pr),
        "avg_opp_rating": round(avg_opp, 1),
        "score": score,
        "score_pct": round(score_pct * 100, 1),
        "rated_games": total,
    }


# ---------------------------------------------------------------------------
# Repertoire tree (issue #16)
# ---------------------------------------------------------------------------

def _score_pct(win: int, draw: int, total: int) -> float:
    """Chess score percentage: wins count 1, draws count ½."""
    return round((win + 0.5 * draw) / total * 100, 1) if total else 0.0


def _move_nodes(game_list: list[tuple], ply: int, *,
                baseline: float, min_games: int) -> list[dict]:
    """
    Group games by their move at *ply* (1-based) into scored tree nodes,
    recursing into each group for the next ply.

    *game_list* items are (moves, ref) tuples — plain Python so the recursion
    never re-filters a DataFrame.  A branch stops splitting once it holds a
    single game: drilling further would just replay that game move by move,
    which is what its detail view is for.

    A node is ``underperforming`` when it scores below *baseline* (Daniel's
    overall score% as this color) across at least *min_games* games — a thin
    branch proves nothing, so it never gets flagged.
    """
    groups: dict[str, list[tuple]] = {}
    for moves, ref in game_list:
        if len(moves) >= ply:
            groups.setdefault(moves[ply - 1], []).append((moves, ref))

    nodes = []
    for san, group in groups.items():
        counts = Counter(ref["Outcome"] for _, ref in group)
        win, draw, loss = counts["Win"], counts["Draw"], counts["Loss"]
        score = _score_pct(win, draw, len(group))
        children = (_move_nodes(group, ply + 1, baseline=baseline, min_games=min_games)
                    if len(group) > 1 else [])
        nodes.append({
            "san": san,
            "ply": ply,
            "games": len(group),
            "win": win, "draw": draw, "loss": loss,
            "score_pct": score,
            "underperforming": len(group) >= min_games and score < baseline,
            "game_refs": [ref for _, ref in group],
            # The games whose move sequence stops at this exact position —
            # decided here, where each game's move list is at hand, so no
            # consumer ever has to reconstruct it (or mis-key it by URL)
            "ended_here": [ref for moves, ref in group if len(moves) == ply],
            "moves": children,
        })
    # Most-played lines first; ties break alphabetically so output is stable
    return sorted(nodes, key=lambda node: (-node["games"], node["san"]))


def repertoire_tree(df: pd.DataFrame, color: str, *, min_games: int = 3) -> dict:
    """
    Daniel's personal opening explorer (issue #16): every Game as *color*,
    arranged move by move into a tree.

    Returns ``{"color", "games", "score_pct", "moves"}`` where ``moves`` is
    the list of first-move nodes.  Each node:
      san             : the move
      ply             : 1-based half-move number
      games           : how many Games continued this way
      win/draw/loss, score_pct : how those Games went (Daniel's perspective)
      underperforming : True when the branch scores below Daniel's overall
                        average as this color across >= *min_games* games —
                        "your anti-Sicilian is leaking points"
      game_refs       : the Games that reached this position (ChapterURL,
                        Opponent, Outcome, Date), for linking
      ended_here      : the subset of game_refs whose move sequence stops at
                        this exact position
      moves           : the next moves, most-played first

    The top level also carries ``min_games`` so UIs can explain the
    flagging rule they're showing.
    """
    empty = {"color": color, "games": 0, "score_pct": 0.0,
             "min_games": min_games, "moves": []}
    if df.empty or "Moves" not in df.columns:
        return empty

    df = _without_forfeits(df)  # one forced move is not a repertoire branch (#29)

    # NaN-proof: a merged/hand-built frame can hold NaN where the parser
    # would put a list — that's "no moves", not a crash
    has_moves = df["Moves"].map(lambda m: isinstance(m, list) and len(m) > 0)
    games = df[
        (df["Color"] == color)
        & df["Outcome"].isin(["Win", "Draw", "Loss"])
        & has_moves
    ]
    if games.empty:
        return empty

    game_list = [
        (
            moves,
            # What a node needs to render a meaningful link to this Game
            {"ChapterURL": url, "Opponent": opponent, "Outcome": outcome, "Date": date},
        )
        for moves, outcome, url, opponent, date in zip(
            games["Moves"], games["Outcome"],
            games.get("ChapterURL", pd.Series("", index=games.index)),
            games.get("Opponent", pd.Series("", index=games.index)),
            games.get("Date", pd.Series("", index=games.index)),
        )
    ]

    total = len(games)
    wins = int((games["Outcome"] == "Win").sum())
    draws = int((games["Outcome"] == "Draw").sum())
    baseline = _score_pct(wins, draws, total)
    return {
        "color": color,
        "games": total,
        "score_pct": baseline,
        "min_games": min_games,
        "moves": _move_nodes(game_list, ply=1, baseline=baseline, min_games=min_games),
    }


# ---------------------------------------------------------------------------
# Time control / fatigue / upset analytics (issue #17)
# ---------------------------------------------------------------------------

_TC_COLS = ["TimeControl", "Speed", "Minutes", "Games", "Win", "Draw", "Loss", "WinRate"]

# TimeControl header parsing.  OTB headers are written by hand, so the formats
# vary: "110+10" (minutes + increment seconds), "40/80, SD30; +30" (multi-stage
# USCF), "60+5d" (delay), "G/30;d5" (game-in-N).
_TC_STAGE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")          # "40/80" → 80 min stage
_TC_SD_RE = re.compile(r"SD\s*/?\s*(\d+)", re.IGNORECASE)  # "SD30" / "SD 30" / "SD/30"
_TC_GAME_RE = re.compile(r"G\s*/\s*(\d+)", re.IGNORECASE)  # "G/30" → 30 min game
_TC_BASE_RE = re.compile(r"^(\d+)")                       # "110+10" → 110 min base
_TC_EXTRA_RE = re.compile(r"[+;d]\s*(\d+)\s*d?", re.IGNORECASE)  # "+30" / "d5" / "+5d" seconds


def _time_control_minutes(tc: str) -> float | None:
    """
    Estimated total thinking time per player in minutes (None if unparseable).

    Uses the standard convention that an increment/delay adds one minute of
    total time per second (a game lasting ~60 moves).

    Assumes hand-written OTB headers (minutes), not the PGN-standard
    seconds format: this dashboard's Games are over-the-board (CONTEXT.md),
    where "30+5" means 30 minutes.  An online blitz export ("180+2" meaning
    seconds) would be misread as 182 minutes — out of scope by design.
    """
    s = str(tc or "").strip()
    if not s:
        return None

    # Stage minutes ("40/80", "SD30", "G/30") are summed, then removed so the
    # SD's "D" can't be mistaken for a delay marker below.
    base = 0.0
    base += sum(int(minutes) for _, minutes in _TC_STAGE_RE.findall(s))
    base += sum(int(m) for m in _TC_SD_RE.findall(s))
    base += sum(int(m) for m in _TC_GAME_RE.findall(s))
    remainder = _TC_GAME_RE.sub("", _TC_SD_RE.sub("", _TC_STAGE_RE.sub("", s)))

    if base == 0:
        head = _TC_BASE_RE.match(remainder)
        if not head:
            return None
        base = float(head.group(1))
        # The PGN standard writes seconds ("5400+30"); hand-written OTB headers
        # write minutes ("110+10").  No real game has a 300+ minute base.
        if base > 300:
            base /= 60.0
        remainder = remainder[head.end():]

    extras = _TC_EXTRA_RE.findall(remainder)
    increment = float(extras[0]) if extras else 0.0
    return base + increment


def _speed_class(minutes: float | None) -> str:
    """Classical / Rapid / Blitz / Unknown from estimated total minutes."""
    # NaN-aware: pandas turns None into NaN inside the numeric Minutes column
    if minutes is None or pd.isna(minutes):
        return "Unknown"
    if minutes >= 60:
        return "Classical"
    if minutes >= 10:
        return "Rapid"
    return "Blitz"


def _wdl_pivot(d: pd.DataFrame, key) -> pd.DataFrame:
    """
    Group *d* by *key* (column name or Series) and pivot Outcome into
    Win / Draw / Loss / Games / WinRate columns — the shape every
    per-category summary in this module shares.
    """
    pivot = (
        d.groupby(key)["Outcome"].value_counts()
        .unstack(fill_value=0)
        .reindex(columns=["Win", "Draw", "Loss"], fill_value=0)
    )
    pivot["Games"] = pivot.sum(axis=1)
    pivot["WinRate"] = (pivot["Win"] / pivot["Games"] * 100).round(1)
    return pivot.reset_index()


_ROUND_COLS = ["Round", "Games", "Win", "Draw", "Loss", "WinRate", "ScorePct", "Reliable"]


def round_performance(df: pd.DataFrame, *, min_games: int = 3) -> pd.DataFrame:
    """
    W/D/L per round number (issue #17): does Daniel fade in late rounds?

    One row per round number that has finished Games, sorted numerically
    (round 10 after round 9, not after round 1).
    Columns: Round, Games, Win, Draw, Loss, WinRate,
    ScorePct ((W + D/2) / Games — fatigue shows up as draws too),
    Reliable (Games >= *min_games* — rounds below the threshold can't support
    a fatigue conclusion and should render dimmed).

    When a Game carries its real round number from the USCF crosstable
    (UscfRound — issue #34), that takes precedence over the hand-typed Round
    header; Games without one fall back to the typed round per Game.
    """
    if df.empty or "RoundNum" not in df.columns:
        return pd.DataFrame(columns=_ROUND_COLS)

    d = df.copy()
    # Real round numbers (USCF crosstables) outrank hand-typed ones (issue #34)
    if "UscfRound" in d.columns:
        d["RoundNum"] = d["UscfRound"].fillna(d["RoundNum"])

    d = d[d["RoundNum"].notna() & d["Outcome"].isin(["Win", "Draw", "Loss"])]
    if d.empty:
        return pd.DataFrame(columns=_ROUND_COLS)

    out = _wdl_pivot(d, d["RoundNum"].astype(int)).rename(columns={"RoundNum": "Round"})
    out["ScorePct"] = ((out["Win"] + 0.5 * out["Draw"]) / out["Games"] * 100).round(1)
    out["Reliable"] = out["Games"] >= min_games
    return out.sort_values("Round").reset_index(drop=True)[_ROUND_COLS]


def time_control_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    W/D/L per time control (issue #17): does Daniel play better slow or fast?

    One row per distinct TimeControl header value, slowest first.
    Columns: TimeControl, Speed (Classical/Rapid/Blitz/Unknown),
    Minutes (estimated total thinking time), Games, Win, Draw, Loss, WinRate.
    """
    if df.empty or "TimeControl" not in df.columns:
        return pd.DataFrame(columns=_TC_COLS)

    d = df[df["Outcome"].isin(["Win", "Draw", "Loss"])].copy()
    if d.empty:
        return pd.DataFrame(columns=_TC_COLS)

    out = _wdl_pivot(d, "TimeControl")
    out["Minutes"] = out["TimeControl"].map(_time_control_minutes)
    out["Speed"] = out["Minutes"].map(_speed_class)
    # Slowest first; unparseable time controls go last
    out["_o"] = out["Minutes"].fillna(-1.0)
    return out.sort_values("_o", ascending=False).drop(columns="_o").reset_index(drop=True)[_TC_COLS]


# Columns each upset row carries (for tables that click through to the Game)
_UPSET_ROW_COLS = ["Date", "Opponent", "OpponentRating", "PlayerRating",
                   "Event", "Round", "ChapterURL"]


def upset_tracker(df: pd.DataFrame) -> dict:
    """
    Giant kills and upset losses (issue #17).

    Returns ``{"wins": [...], "losses": [...]}``:
      wins   : Wins against higher-rated opponents, biggest rating margin first.
      losses : Losses to lower-rated opponents, biggest rating margin first.

    Each row carries Date, Opponent, both ratings, Margin (rating points),
    Event, Round, and ChapterURL so tables can click through to the Game.
    Games where either rating is unknown can't be ranked and are skipped.
    Forfeits are never upsets (issue #35): a no-show win is not a giant kill,
    the same rule that keeps them out of win rate, Streaks, and openings.
    """
    empty: dict = {"wins": [], "losses": []}
    if df.empty or "RatingDiff" not in df.columns:
        return empty

    rated = _without_forfeits(df)
    rated = rated[rated["RatingDiff"].notna()]

    def _rows(games: pd.DataFrame) -> list[dict]:
        out = games[_UPSET_ROW_COLS].copy()
        out["Margin"] = games["RatingDiff"].abs().astype(int)
        return out.to_dict("records")

    wins = rated[(rated["Outcome"] == "Win") & (rated["RatingDiff"] > 0)]
    losses = rated[(rated["Outcome"] == "Loss") & (rated["RatingDiff"] < 0)]

    return {
        "wins": _rows(wins.sort_values("RatingDiff", ascending=False)),
        "losses": _rows(losses.sort_values("RatingDiff", ascending=True)),
    }


# ---------------------------------------------------------------------------
# Lessons + Tags insights (issue #12)
# ---------------------------------------------------------------------------

_LESSON_COLS = ["Lesson", "Tags", "Opponent", "Outcome", "Result", "Date",
                "Date_dt", "Event", "ChapterURL"]


def lessons_table(
    df: pd.DataFrame,
    tags: list[str] | None = None,
    opponent: str | None = None,
) -> pd.DataFrame:
    """
    One row per Lesson across all Games, newest first.

    A Game with two Lessons contributes two rows; Games without a Lesson are
    excluded.  Each row carries its source Game (Opponent, Outcome, Date,
    Event, ChapterURL) and that Game's Tags.

    Parameters
    ----------
    tags     : keep only Lessons whose Game carries *all* of these Tags.
    opponent : keep only Lessons from Games against this opponent.
    """
    if df.empty or "Lessons" not in df.columns:
        return pd.DataFrame(columns=_LESSON_COLS)

    games = df[df["Lessons"].map(bool)]
    if opponent:
        games = games[games["Opponent"] == opponent]
    if tags:
        games = games[games["Tags"].map(lambda game_tags: set(tags) <= set(game_tags))]

    rows = [
        {
            "Lesson": lesson,
            "Tags": game["Tags"],
            "Opponent": game["Opponent"],
            "Outcome": game["Outcome"],
            "Result": game["Result"],
            "Date": game["Date"],
            "Date_dt": game["Date_dt"],
            "Event": game["Event"],
            "ChapterURL": game["ChapterURL"],
        }
        for _, game in games.iterrows()
        for lesson in game["Lessons"]
    ]
    if not rows:
        return pd.DataFrame(columns=_LESSON_COLS)

    out = pd.DataFrame(rows)
    return out.sort_values("Date_dt", ascending=False, na_position="last").reset_index(drop=True)


def tag_counts(df: pd.DataFrame) -> list[dict]:
    """
    Every Tag in the archive with the number of Games carrying it.

    Canonical taxonomy Tags come first (in taxonomy order), then freeform
    Tags by descending count — so vocabulary fragmentation stays visible.

    Returns: [{"tag": str, "count": int, "canonical": bool}, ...]
    """
    if df.empty or "Tags" not in df.columns:
        return []

    counts: Counter[str] = Counter()
    for game_tags in df["Tags"]:
        counts.update(game_tags)

    canonical = [
        {"tag": t, "count": counts[t], "canonical": True}
        for t in CANONICAL_TAGS if counts[t] > 0
    ]
    freeform_pairs = sorted(
        ((t, n) for t, n in counts.items() if t not in CANONICAL_TAGS),
        key=lambda pair: (-pair[1], pair[0]),
    )
    freeform = [{"tag": t, "count": n, "canonical": False} for t, n in freeform_pairs]
    return canonical + freeform


# ---------------------------------------------------------------------------
# Recurring weakness detection (issue #18)
# ---------------------------------------------------------------------------

def recurring_weaknesses(
    df: pd.DataFrame,
    *,
    loss_window: int = 10,
    min_occurrences: int = 3,
) -> list[dict]:
    """
    Recurring weaknesses (issue #18): Tags that keep showing up in recent
    losses — the insight that makes Tags pay off.

    Looks at the last *loss_window* Losses and calls out every Tag that

      (a) appears in at least *min_occurrences* of them, and
      (b) is genuinely loss-associated: over the same period it shows up in
          losses more than in wins/draws (otherwise it's just a frequent
          Tag, not a weakness).

    Below those thresholds nothing is reported — silence over noise.

    Returns callouts ranked by severity (most severe first), each:
      tag           : the Tag (without '#')
      loss_count    : how many of the windowed losses carry it
      window_losses : how many losses the window holds
      stat          : the headline ("#time-trouble appears in 6 of your last
                      8 losses")
      window        : the period those games span ("Mar – Jun 2024")
      severity      : 0..1 ranking score
      chapter_urls  : the Games behind the callout, for linking
    """
    if df.empty or "Tags" not in df.columns:
        return []

    d = df.copy()
    d["_ds"] = d["Date_dt"].fillna(pd.Timestamp.max)
    d = d.sort_values(["_ds", "Index"]).reset_index(drop=True)

    loss_positions = d.index[d["Outcome"] == "Loss"]
    if len(loss_positions) == 0:
        return []
    windowed_positions = loss_positions[-loss_window:]
    window_losses = len(windowed_positions)

    # The comparison window: every game from the first windowed loss onward
    window_games = d.iloc[windowed_positions[0]:]
    recent_losses = d.loc[windowed_positions]
    non_losses = window_games[window_games["Outcome"] != "Loss"]

    loss_tag_counts: Counter[str] = Counter()
    loss_tag_games: dict[str, list[str]] = {}
    for _, game in recent_losses.iterrows():
        for tag in game["Tags"]:
            loss_tag_counts[tag] += 1
            loss_tag_games.setdefault(tag, []).append(game.get("ChapterURL", ""))

    non_loss_tag_counts: Counter[str] = Counter()
    for _, game in non_losses.iterrows():
        for tag in game["Tags"]:
            non_loss_tag_counts[tag] += 1

    # The period the callout covers, for display
    dated = window_games[window_games["Date_dt"].notna()]
    if dated.empty:
        window_label = ""
    else:
        start = f"{dated['Date_dt'].min():%b %Y}"
        end = f"{dated['Date_dt'].max():%b %Y}"
        window_label = start if start == end else f"{start} – {end}"

    callouts: list[dict] = []
    for tag, loss_count in loss_tag_counts.items():
        if loss_count < min_occurrences:
            continue
        association = loss_count / (loss_count + non_loss_tag_counts[tag])
        if association <= 0.5:
            continue  # shows up just as often when you don't lose
        severity = (loss_count / window_losses) * association
        callouts.append({
            "tag": tag,
            "loss_count": loss_count,
            "window_losses": window_losses,
            "stat": f"#{tag} appears in {loss_count} of your last {window_losses} losses",
            "window": window_label,
            "severity": round(severity, 3),
            "chapter_urls": loss_tag_games[tag],
        })

    callouts.sort(key=lambda c: (-c["severity"], c["tag"]))
    return callouts


# ---------------------------------------------------------------------------
# Pre-game review (issue #19)
# ---------------------------------------------------------------------------

def review_queue(df: pd.DataFrame, *, opponent: str | None = None) -> list[dict]:
    """
    The Lessons to re-read in the five minutes before a round (issue #19),
    most relevant first.

    Priority order:
      1. Lessons tagged with a detected recurring weakness — what's actually
         costing you games right now
      2. Lessons from Games against *opponent* (when one is given)
      3. Everything else

    Within each bucket, newest first.  Each card carries a ``reason`` saying
    why it made the queue.
    """
    lessons = lessons_table(df)  # already newest-first
    if lessons.empty:
        return []

    weakness_tags = {w["tag"] for w in recurring_weaknesses(df)}

    def _bucket(card: dict) -> tuple[int, str]:
        """(priority, reason) for one Lesson."""
        card_weak_tags = [t for t in card["Tags"] if t in weakness_tags]
        if card_weak_tags:
            return 0, "Recurring weakness: " + " ".join(f"#{t}" for t in card_weak_tags)
        if opponent and card["Opponent"] == opponent:
            return 1, f"You're facing {opponent}"
        return 2, "Recent lesson"

    cards = []
    for _, row in lessons.iterrows():
        card = row.to_dict()
        card["priority"], card["reason"] = _bucket(card)
        cards.append(card)

    # Stable sort: lessons_table is newest-first, so sorting by priority
    # alone keeps recency order inside each bucket.
    cards.sort(key=lambda c: c["priority"])
    return cards


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

def compute_milestones(df: pd.DataFrame) -> list[dict]:
    """
    Auto-detect career milestone games, returned as a chronologically sorted list.

    Each item: {date: str, game_num: int, description: str, kind: str}
    Kinds: 'first', 'win', 'draw', 'loss', 'milestone', 'peak', 'streak'
    """
    if df.empty:
        return []

    d = df.copy()
    d["_ds"] = d["Date_dt"].fillna(pd.Timestamp.max)
    d = d.sort_values(["_ds", "Index"]).reset_index(drop=True)
    d["_gn"] = range(1, len(d) + 1)

    def _date(row) -> str:
        dt = row.get("Date_dt")
        return str(dt.date()) if pd.notna(dt) else str(row.get("Date", ""))

    items: list[dict] = []

    def _add(row, desc: str, kind: str):
        items.append({"date": _date(row), "game_num": int(row["_gn"]),
                      "description": desc, "kind": kind})

    # First game
    _add(d.iloc[0], "First recorded game", "first")

    # First Win / Draw / Loss
    for outcome in ("Win", "Draw", "Loss"):
        sub = d[d["Outcome"] == outcome]
        if not sub.empty:
            r = sub.iloc[0]
            opp = r.get("Opponent", "")
            _add(r, f"First {outcome.lower()}" + (f" (vs {opp})" if opp else ""), outcome.lower())

    # Every 10th game
    for n in range(10, len(d) + 1, 10):
        r = d[d["_gn"] == n].iloc[0]
        _add(r, f"Game #{n}", "milestone")

    # Highest rated opponent beaten
    beaten = d[(d["Outcome"] == "Win") & d["OpponentRatingNum"].notna()]
    if not beaten.empty:
        r = beaten.loc[beaten["OpponentRatingNum"].idxmax()]
        _add(r, f"Beat highest-rated opponent: {r['Opponent']} ({int(r['OpponentRatingNum'])})", "peak")

    # Peak rating
    rated_g = d[d["PlayerRatingNum"].notna()]
    if not rated_g.empty:
        r = rated_g.loc[rated_g["PlayerRatingNum"].idxmax()]
        _add(r, f"Achieved peak rating: {int(r['PlayerRatingNum'])}", "peak")

    # Longest win streak
    outcomes = d["Outcome"].tolist()
    best_len = best_start = best_end = 0
    cur_len = cur_start = 0
    for i, o in enumerate(outcomes):
        if o == "Win":
            if cur_len == 0:
                cur_start = i
            cur_len += 1
            if cur_len > best_len:
                best_len, best_start, best_end = cur_len, cur_start, i
        else:
            cur_len = 0
    if best_len >= 3:
        r = d.iloc[best_start]
        _add(r, f"Start of longest win streak ({best_len} in a row, ended game #{int(d.iloc[best_end]['_gn'])})", "streak")

    items.sort(key=lambda x: x["game_num"])
    return items


def _peak_rating(df: pd.DataFrame) -> int | None:
    """The player's highest rating in *df*, or None if no rated games."""
    if df.empty or "PlayerRatingNum" not in df.columns:
        return None
    rated = df["PlayerRatingNum"].dropna()
    return int(rated.max()) if not rated.empty else None


def _best_win(df: pd.DataFrame) -> tuple[int, str] | None:
    """(rating, opponent) of the highest-rated opponent beaten, or None."""
    if df.empty or "OpponentRatingNum" not in df.columns:
        return None
    beaten = df[(df["Outcome"] == "Win") & df["OpponentRatingNum"].notna()]
    if beaten.empty:
        return None
    best = beaten.loc[beaten["OpponentRatingNum"].idxmax()]
    return int(best["OpponentRatingNum"]), str(best["Opponent"])


def milestone_deltas(old_df: pd.DataFrame, new_df: pd.DataFrame) -> list[dict]:
    """
    Personal bests set between two data snapshots (issue #15).

    Compares the pre-Sync and post-Sync Games and reports every record that
    the new Games broke.  Nothing is persisted: the comparison is the whole
    state (ADR 0002).

    Returns a list of deltas, each:
      {"kind": "peak_rating", "old": <previous best>, "new": <new best>,
       "description": <celebration text>}

    An empty *old_df* (or one with no baseline value for a record) produces
    no delta for it — there is nothing to beat, so nothing to celebrate.
    """
    deltas: list[dict] = []

    old_peak, new_peak = _peak_rating(old_df), _peak_rating(new_df)
    if old_peak is not None and new_peak is not None and new_peak > old_peak:
        deltas.append({
            "kind": "peak_rating", "old": old_peak, "new": new_peak,
            "description": f"New peak rating: {new_peak} (was {old_peak})",
        })

    # A baseline of 0 from real games still counts: someone whose archive has
    # no win streak yet deserves the banner for their first one.
    old_streak = streaks(old_df)["longest_streak_wins_only"]
    new_streak = streaks(new_df)["longest_streak_wins_only"]
    if not old_df.empty and new_streak > old_streak:
        deltas.append({
            "kind": "win_streak", "old": old_streak, "new": new_streak,
            "description": (f"New longest win streak: {new_streak} games in a row "
                            f"(was {old_streak})"),
        })

    old_best, new_best = _best_win(old_df), _best_win(new_df)
    if old_best is not None and new_best is not None and new_best[0] > old_best[0]:
        rating, opponent = new_best
        deltas.append({
            "kind": "giant_kill", "old": old_best[0], "new": rating,
            "description": (f"Beat your highest-rated opponent yet: "
                            f"{opponent} ({rating}, previous best {old_best[0]})"),
        })

    return deltas
