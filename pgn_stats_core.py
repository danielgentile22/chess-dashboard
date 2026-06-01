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
    "apply_filters",
    "win_draw_loss_counts",
    "termination_counts",
    "streaks",
    "current_form",
    "kpi_stats",
    "lessons_table",
    "tag_counts",
    "win_rate_over_time",
    "player_rating_over_time",
    "opponent_summary",
    "head_to_head",
    "opening_summary",
    "opponent_rating_bucket_summary",
    "outcome_vs_rating_data",
    "game_length_data",
    "activity_data",
    "daily_activity",
    "event_summary",
    "performance_rating_stats",
    "compute_milestones",
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
            plies, fullmoves = compute_move_counts(game)

            # Lichess Study identity (ADR 0001): ChapterURL is the permanent
            # identity of a Game; empty for PGNs that aren't Study exports.
            study_name = _first_present(h, ["studyname"])
            chapter_name = _first_present(h, ["chaptername"])
            chapter_url = _first_present(h, ["chapterurl"])

            # Lessons and Tags from chapter comments (ADR 0002)
            lessons, tags = extract_lessons_and_tags(game)

            rows.append({
                "Index": idx, "Date": date, "Time": time_tag,
                "Event": event, "Site": site, "Round": round_tag, "Board": board_tag,
                "ECO": eco, "Opening": opening, "TimeControl": timecontrol,
                "White": white, "WhiteRating": white_rating,
                "WhiteRatingNum": safe_int(white_rating), "WhiteID": white_id,
                "Black": black, "BlackRating": black_rating,
                "BlackRatingNum": safe_int(black_rating), "BlackID": black_id,
                "Result": result, "Winner": winner_from_result(result),
                "Termination": termination, "Plies": plies, "FullMoves": fullmoves,
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

def win_draw_loss_counts(df: pd.DataFrame) -> pd.Series:
    """Return a Series with counts for Win, Draw, Loss, Unknown outcomes."""
    outcomes = ["Win", "Draw", "Loss", "Unknown"]
    if df.empty or "Outcome" not in df.columns:
        return pd.Series(0, index=pd.Index(outcomes), name="count")
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

    openings = df["Opening"].replace("", pd.NA).dropna()
    fav_opening = str(openings.value_counts().index[0]) if not openings.empty else "—"
    eco_vals = df["ECO"].replace("", pd.NA).dropna()
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
    excluding Unknown outcomes). Columns: Date_dt, CumGames, CumWins, WinRate.
    """
    if df.empty:
        return pd.DataFrame(columns=["Date_dt", "CumGames", "CumWins", "WinRate"])
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
    """
    if df.empty or "Date_dt" not in df.columns:
        return pd.DataFrame(columns=_DAILY_COLS)

    d = df[df["Date_dt"].notna()].copy()
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
