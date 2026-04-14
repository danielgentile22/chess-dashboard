"""
pgn_stats_core.py
=================
Core PGN parsing and statistics computation for the Chess Stats Dashboard.

This module handles:
  - Loading and parsing PGN files into a structured Pandas DataFrame
  - Player detection from game headers
  - Derived statistics: win/draw/loss counts, streaks, opponent summaries,
    event summaries, rating progression, and cumulative win-rate over time

Public API
----------
load_games_df        Parse a PGN file and return a tidy DataFrame + player name.
apply_filters        Apply UI filter selections to the DataFrame.
opponent_summary     W/D/L breakdown per opponent (opponents played >1 game).
win_draw_loss_counts Raw W/D/L/Unknown counts for a filtered DataFrame.
win_rate_over_time   Cumulative win-rate per date.
termination_counts   Count of games by termination type.
streaks              Longest / current streak stats.
event_summary        Per-tournament W/D/L, score, and notable opponents.
player_rating_over_time  Player's own rating per date (last game of each day).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional, Tuple

import pandas as pd
import chess.pgn

__all__ = [
    "load_games_df",
    "apply_filters",
    "opponent_summary",
    "win_draw_loss_counts",
    "win_rate_over_time",
    "termination_counts",
    "streaks",
    "event_summary",
    "player_rating_over_time",
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


def safe_int(x) -> Optional[int]:
    """
    Parse a rating-like string to int; return None on any failure.

    Handles values like "1850", "1850P" (provisional), and "?" gracefully.
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
    Infer the player's name as the most-frequent name appearing in any
    White or Black header across all parsed games.

    In a personal PGN the player's name dominates, so the modal name is
    almost always correct.
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


def compute_move_counts(game) -> Tuple[int, int]:
    """
    Return ``(plies, full_moves)`` for a parsed PGN game node.

    *plies*      – total half-moves (each individual move = 1 ply).
    *full_moves* – standard move-number count (ceiling of plies / 2).
    """
    plies = sum(1 for _ in game.mainline_moves())
    fullmoves = (plies + 1) // 2
    return plies, fullmoves


def outcome_for_player(result: str, color: str) -> str:
    """Map a PGN result string and piece colour to Win / Draw / Loss / Unknown."""
    if color == "White":
        if result == "1-0":
            return "Win"
        if result == "0-1":
            return "Loss"
        if result == "1/2-1/2":
            return "Draw"
    elif color == "Black":
        if result == "0-1":
            return "Win"
        if result == "1-0":
            return "Loss"
        if result == "1/2-1/2":
            return "Draw"
    return "Unknown"


def winner_from_result(result: str) -> str:
    """Map a PGN result string to White / Black / Draw / Unknown."""
    if result == "1-0":
        return "White"
    if result == "0-1":
        return "Black"
    if result == "1/2-1/2":
        return "Draw"
    return "Unknown"


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def load_games_df(
    pgn_path: str,
    player_name: Optional[str] = None,
) -> tuple[pd.DataFrame, str]:
    """
    Parse a PGN file and return a tidy DataFrame of games together with the
    detected (or supplied) player name.

    Parameters
    ----------
    pgn_path : str
        Filesystem path to a UTF-8 encoded, multi-game PGN file.
    player_name : str, optional
        The player's name as it appears in White / Black headers.
        When omitted the most-frequent name across all games is used.

    Returns
    -------
    df : pd.DataFrame
        One row per game.  Key columns:

        Raw headers
          Index, Date, Time, Event, Site, Round, Board, ECO, Opening,
          TimeControl, White, WhiteRating, WhiteRatingNum, WhiteID,
          Black, BlackRating, BlackRatingNum, BlackID,
          Result, Winner, Termination, Plies, FullMoves

        Perspective columns (relative to the detected player)
          Color            – "White", "Black", or "Unknown"
          Opponent         – opponent's name
          Outcome          – "Win", "Draw", "Loss", or "Unknown"
          PlayerRating     – player's rating string for that game
          PlayerRatingNum  – player's rating as int (None if unavailable)
          OpponentRating   – opponent's rating string
          OpponentRatingNum – opponent's rating as int (None if unavailable)

    detected : str
        The player name used to compute perspective columns.
    """
    rows = []
    with open(pgn_path, "r", encoding="utf-8", errors="ignore") as f:
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
            time = _first_present(h, ["utctime", "time"])

            eco = _first_present(h, ["eco"])
            opening = _first_present(h, ["opening"])
            timecontrol = _first_present(h, ["timecontrol"])

            plies, fullmoves = compute_move_counts(game)

            rows.append(
                {
                    "Index": idx,
                    "Date": date,
                    "Time": time,
                    "Event": event,
                    "Site": site,
                    "Round": round_tag,
                    "Board": board_tag,
                    "ECO": eco,
                    "Opening": opening,
                    "TimeControl": timecontrol,
                    "White": white,
                    "WhiteRating": white_rating,
                    "WhiteRatingNum": safe_int(white_rating),
                    "WhiteID": white_id,
                    "Black": black,
                    "BlackRating": black_rating,
                    "BlackRatingNum": safe_int(black_rating),
                    "BlackID": black_id,
                    "Result": result,
                    "Winner": winner_from_result(result),
                    "Termination": termination,
                    "Plies": plies,
                    "FullMoves": fullmoves,
                }
            )

    if not rows:
        return pd.DataFrame(), (player_name or "")

    df = pd.DataFrame(rows)

    # Parse date robustly; coerce unknown patterns (e.g. "????.??.??") to NaT.
    df["Date_dt"] = pd.to_datetime(
        df["Date"].replace("????.??.??", None),
        errors="coerce",
        format="%Y.%m.%d",
    )

    detected = player_name or infer_player_name_from_rows(rows)

    # ------------------------------------------------------------------
    # Perspective columns (relative to the detected player)
    # ------------------------------------------------------------------
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

    # Player's own rating (string + numeric) and opponent's rating
    df["PlayerRating"] = ""
    df.loc[is_white, "PlayerRating"] = df.loc[is_white, "WhiteRating"]
    df.loc[is_black, "PlayerRating"] = df.loc[is_black, "BlackRating"]

    df["PlayerRatingNum"] = None
    df.loc[is_white, "PlayerRatingNum"] = df.loc[is_white, "WhiteRatingNum"]
    df.loc[is_black, "PlayerRatingNum"] = df.loc[is_black, "BlackRatingNum"]
    df["PlayerRatingNum"] = pd.to_numeric(df["PlayerRatingNum"], errors="coerce")

    df["OpponentRating"] = ""
    df.loc[is_white, "OpponentRating"] = df.loc[is_white, "BlackRating"]
    df.loc[is_black, "OpponentRating"] = df.loc[is_black, "WhiteRating"]

    df["OpponentRatingNum"] = None
    df.loc[is_white, "OpponentRatingNum"] = df.loc[is_white, "BlackRatingNum"]
    df.loc[is_black, "OpponentRatingNum"] = df.loc[is_black, "WhiteRatingNum"]
    df["OpponentRatingNum"] = pd.to_numeric(df["OpponentRatingNum"], errors="coerce")

    return df, detected


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def apply_filters(
    df: pd.DataFrame,
    colors: list[str],
    outcomes: list[str],
    terminations: list[str],
    date_start: Optional[str],
    date_end: Optional[str],
) -> pd.DataFrame:
    """
    Apply UI filter selections to *df* and return the filtered copy.

    Parameters
    ----------
    colors       : list of "White" / "Black" to keep (empty = keep all).
    outcomes     : list of "Win" / "Draw" / "Loss" to keep (empty = keep all).
    terminations : list of termination strings to keep (empty = keep all).
    date_start   : ISO date string for the lower bound (inclusive), or None.
    date_end     : ISO date string for the upper bound (inclusive), or None.
    """
    out = df.copy()

    if colors:
        out = out[out["Color"].isin(colors)]

    if outcomes:
        out = out[out["Outcome"].isin(outcomes)]

    if terminations:
        out = out[out["Termination"].isin(terminations)]

    if date_start or date_end:
        out = out[out["Date_dt"].notna()]
        if date_start:
            out = out[out["Date_dt"] >= pd.to_datetime(date_start)]
        if date_end:
            out = out[out["Date_dt"] <= pd.to_datetime(date_end)]

    return out


# ---------------------------------------------------------------------------
# Derived statistics
# ---------------------------------------------------------------------------

def opponent_summary(df_filtered: pd.DataFrame) -> pd.DataFrame:
    """
    Return a DataFrame of opponents played more than once, with W/D/L counts.

    Columns: Opponent, Games, Win, Draw, Loss
    Sorted by Games desc, then Win desc.
    """
    if df_filtered.empty:
        return pd.DataFrame(columns=["Opponent", "Games", "Win", "Draw", "Loss"])

    pivot = (
        df_filtered.groupby(["Opponent", "Outcome"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["Win", "Draw", "Loss"], fill_value=0)
    )

    pivot["Games"] = pivot.sum(axis=1)
    pivot = pivot[pivot["Games"] > 1]

    out = pivot.reset_index()[["Opponent", "Games", "Win", "Draw", "Loss"]]
    return out.sort_values(["Games", "Win"], ascending=[False, False])


def win_draw_loss_counts(df_filtered: pd.DataFrame) -> pd.Series:
    """Return a Series with counts for Win, Draw, Loss, and Unknown outcomes."""
    return df_filtered["Outcome"].value_counts().reindex(
        ["Win", "Draw", "Loss", "Unknown"], fill_value=0
    )


def win_rate_over_time(df_filtered: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the cumulative win rate over time for dated games.

    Excludes games with Unknown outcome. Returns one row per date (the
    last game of that day sets the cumulative rate).

    Columns: Date_dt, CumGames, CumWins, WinRate
    """
    if df_filtered.empty:
        return pd.DataFrame(columns=["Date_dt", "CumGames", "CumWins", "WinRate"])

    d = df_filtered[df_filtered["Date_dt"].notna()].copy()
    d = d[d["Outcome"].isin(["Win", "Draw", "Loss"])].sort_values("Date_dt")
    if d.empty:
        return pd.DataFrame(columns=["Date_dt", "CumGames", "CumWins", "WinRate"])

    d["IsWin"] = (d["Outcome"] == "Win").astype(int)
    d["CumGames"] = d["IsWin"].expanding().count().astype(int)
    d["CumWins"] = d["IsWin"].expanding().sum().astype(int)
    d["WinRate"] = (d["CumWins"] / d["CumGames"]) * 100.0

    return d.groupby("Date_dt").tail(1)[["Date_dt", "CumGames", "CumWins", "WinRate"]]


def termination_counts(df_filtered: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame of (Termination, Games) counts, descending by count."""
    if df_filtered.empty:
        return pd.DataFrame(columns=["Termination", "Games"])
    c = df_filtered["Termination"].value_counts().reset_index()
    c.columns = ["Termination", "Games"]
    return c


def streaks(df_filtered: pd.DataFrame) -> dict:
    """
    Compute streak statistics over the filtered game set.

    Games are ordered by Date_dt ascending (undated games placed last),
    with ties broken by Index.

    Returns a dict with:
      longest_streak_no_loss      – longest run of consecutive Win/Draw games
      longest_streak_wins_only    – longest run of consecutive Win games
      current_streak_same_outcome – consecutive games at the end matching the
                                    most-recent outcome
      current_streak_outcome      – the outcome label for the current streak
    """
    if df_filtered.empty:
        return {
            "longest_streak_no_loss": 0,
            "longest_streak_wins_only": 0,
            "current_streak_same_outcome": 0,
            "current_streak_outcome": "N/A",
        }

    d = df_filtered.copy()
    d["_date_sort"] = d["Date_dt"].fillna(pd.Timestamp.max)
    d = d.sort_values(["_date_sort", "Index"])
    outcomes = d["Outcome"].tolist()

    longest_no_loss = 0
    cur_no_loss = 0
    longest_wins = 0
    cur_wins = 0

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
    }


def player_rating_over_time(df_filtered: pd.DataFrame) -> pd.DataFrame:
    """
    Return the player's own rating per date, using the last game of each day.

    Columns: Date_dt, PlayerRating
    Excludes games with missing Date_dt or missing PlayerRatingNum.
    """
    if df_filtered.empty:
        return pd.DataFrame(columns=["Date_dt", "PlayerRating"])

    d = df_filtered[df_filtered["Date_dt"].notna()].copy()
    d = d[d["PlayerRatingNum"].notna()].copy()

    if d.empty:
        return pd.DataFrame(columns=["Date_dt", "PlayerRating"])

    d = d.sort_values(["Date_dt", "Index"])
    return d.groupby("Date_dt").tail(1)[["Date_dt", "PlayerRatingNum"]].rename(
        columns={"PlayerRatingNum": "PlayerRating"}
    )


def event_summary(df_filtered: pd.DataFrame) -> pd.DataFrame:
    """
    Summarise performance per tournament / event.

    For each Event (sorted by first game date):
      - Win / Draw / Loss counts and score ("3/5")
      - Highest-rated opponent faced and the outcome of that game
      - Lowest-rated opponent faced and the outcome of that game

    Columns: Event, FirstDate, Games, Win, Draw, Loss, Score,
             HighestOpp, HighestOppRating, HighestOppOutcome,
             LowestOpp, LowestOppRating, LowestOppOutcome
    """
    _COLS = [
        "Event", "FirstDate", "Games", "Win", "Draw", "Loss", "Score",
        "HighestOpp", "HighestOppRating", "HighestOppOutcome",
        "LowestOpp", "LowestOppRating", "LowestOppOutcome",
    ]

    if df_filtered.empty:
        return pd.DataFrame(columns=_COLS)

    d = df_filtered.copy()
    d["Event"] = d["Event"].fillna("").astype(str)
    d = d[d["Event"].str.strip() != ""]
    if d.empty:
        return pd.DataFrame(columns=_COLS)

    rows = []
    for event, g in d.groupby("Event", dropna=False):
        first_date = g["Date_dt"].min()
        first_date_str = first_date.date().isoformat() if pd.notna(first_date) else ""

        win = int((g["Outcome"] == "Win").sum())
        draw = int((g["Outcome"] == "Draw").sum())
        loss = int((g["Outcome"] == "Loss").sum())
        games = int(len(g))
        score = f"{win + 0.5 * draw:g}/{games}"

        rated = g[g["OpponentRatingNum"].notna()].copy()

        high_name = high_rating = high_outcome = ""
        low_name = low_rating = low_outcome = ""

        if not rated.empty:
            high_row = rated.loc[rated["OpponentRatingNum"].idxmax()]
            low_row = rated.loc[rated["OpponentRatingNum"].idxmin()]

            high_name = str(high_row.get("Opponent", ""))
            high_rating = int(high_row.get("OpponentRatingNum"))
            high_outcome = str(high_row.get("Outcome", ""))

            low_name = str(low_row.get("Opponent", ""))
            low_rating = int(low_row.get("OpponentRatingNum"))
            low_outcome = str(low_row.get("Outcome", ""))

        rows.append(
            {
                "Event": event,
                "FirstDate": first_date_str,
                "Games": games,
                "Win": win,
                "Draw": draw,
                "Loss": loss,
                "Score": score,
                "HighestOpp": high_name,
                "HighestOppRating": high_rating,
                "HighestOppOutcome": high_outcome,
                "LowestOpp": low_name,
                "LowestOppRating": low_rating,
                "LowestOppOutcome": low_outcome,
            }
        )

    out = pd.DataFrame(rows)
    out["_sort"] = pd.to_datetime(out["FirstDate"], errors="coerce")
    out = out.sort_values(["_sort", "Event"]).drop(columns=["_sort"])
    return out
