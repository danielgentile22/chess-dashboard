# pgn_stats_core.py
from __future__ import annotations

import re
from collections import Counter
from typing import Optional, Tuple

import pandas as pd
import chess.pgn


# ----------------------------
# Parsing helpers
# ----------------------------
def _norm_headers(h) -> dict:
    return {k.lower(): v for k, v in h.items() if v is not None}


def _first_present(h: dict, keys: list[str], default: str = "") -> str:
    for k in keys:
        v = h.get(k.lower(), None)
        if v and v != "?":
            return v
    return default


def safe_int(x) -> Optional[int]:
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
    names = []
    for r in rows:
        if r.get("White"):
            names.append(r["White"])
        if r.get("Black"):
            names.append(r["Black"])
    if not names:
        return ""
    return Counter(names).most_common(1)[0][0]


def compute_move_counts(game) -> Tuple[int, int]:
    plies = 0
    board = game.board()
    for mv in game.mainline_moves():
        board.push(mv)
        plies += 1
    if plies == 0:
        return 0, 0
    fullmoves = (plies + 1) // 2
    return plies, fullmoves


def outcome_for_player(result: str, color: str) -> str:
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
    if result == "1-0":
        return "White"
    if result == "0-1":
        return "Black"
    if result == "1/2-1/2":
        return "Draw"
    return "Unknown"


# ----------------------------
# Main loader
# ----------------------------
def load_games_df(pgn_path: str, player_name: Optional[str] = None) -> tuple[pd.DataFrame, str]:
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

    # Parse date robustly. Coerce invalid to NaT.
    df["Date_dt"] = pd.to_datetime(df["Date"].replace("????.??.??", None), errors="coerce", format="%Y.%m.%d")

    detected = player_name or infer_player_name_from_rows(rows)

    def color_fn(r):
        if r["White"] == detected:
            return "White"
        if r["Black"] == detected:
            return "Black"
        return "Unknown"

    df["Color"] = df.apply(color_fn, axis=1)
    df["Opponent"] = df.apply(
        lambda r: r["Black"] if r["Color"] == "White" else (r["White"] if r["Color"] == "Black" else ""), axis=1
    )
    df["Outcome"] = df.apply(lambda r: outcome_for_player(r["Result"], r["Color"]), axis=1)

    df["OpponentRatingNum"] = df.apply(
        lambda r: r["BlackRatingNum"]
        if r["Color"] == "White"
        else (r["WhiteRatingNum"] if r["Color"] == "Black" else None),
        axis=1,
    )

    return df, detected


# ----------------------------
# Filtering + derived stats
# ----------------------------
def apply_filters(
    df: pd.DataFrame,
    colors: list[str],
    outcomes: list[str],
    terminations: list[str],
    date_start: Optional[str],
    date_end: Optional[str],
) -> pd.DataFrame:
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


def opponent_summary(df_filtered: pd.DataFrame) -> pd.DataFrame:
    """
    Opponents played more than once, with W/D/L counts.
    Returns columns: Opponent, Games, Win, Draw, Loss
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
    out = out.sort_values(["Games", "Win"], ascending=[False, False])
    return out


def win_draw_loss_counts(df_filtered: pd.DataFrame) -> pd.Series:
    return df_filtered["Outcome"].value_counts().reindex(["Win", "Draw", "Loss", "Unknown"], fill_value=0)


def win_rate_over_time(df_filtered: pd.DataFrame) -> pd.DataFrame:
    """
    Cumulative win rate over time.
    Uses dated games only. Excludes Unknown outcomes.
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

    out = d.groupby("Date_dt").tail(1)[["Date_dt", "CumGames", "CumWins", "WinRate"]]
    return out


def termination_counts(df_filtered: pd.DataFrame) -> pd.DataFrame:
    if df_filtered.empty:
        return pd.DataFrame(columns=["Termination", "Games"])
    c = df_filtered["Termination"].value_counts().reset_index()
    c.columns = ["Termination", "Games"]
    return c


def streaks(df_filtered: pd.DataFrame) -> dict:
    """
    - longest_streak_no_loss: longest consecutive games without a Loss (Win/Draw)
    - longest_streak_wins_only: longest consecutive Wins
    - current_streak_same_outcome: count of consecutive most-recent games matching the most-recent Outcome
    Ordering: Date_dt asc; undated games last; stable by Index.
    """
    if df_filtered.empty:
        return {
            "longest_streak_no_loss": 0,
            "longest_streak_wins_only": 0,
            "current_streak_same_outcome": 0,
            "current_streak_outcome": "N/A",
        }

    d = df_filtered.copy()
    d["Date_sort"] = d["Date_dt"].fillna(pd.Timestamp.max)
    d = d.sort_values(["Date_sort", "Index"])
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


def event_summary(df_filtered: pd.DataFrame) -> pd.DataFrame:
    """
    For each Event (sorted by date of first game in that event):
      - Win/Draw/Loss counts
      - Score like "3/5"
      - Highest-rated opponent faced + your outcome
      - Lowest-rated opponent faced + your outcome
    """
    if df_filtered.empty:
        return pd.DataFrame(
            columns=[
                "Event",
                "FirstDate",
                "Games",
                "Win",
                "Draw",
                "Loss",
                "Score",
                "HighestOpp",
                "HighestOppRating",
                "HighestOppOutcome",
                "LowestOpp",
                "LowestOppRating",
                "LowestOppOutcome",
            ]
        )

    d = df_filtered.copy()
    d["Event"] = d["Event"].fillna("").astype(str)
    d = d[d["Event"].str.strip() != ""]
    if d.empty:
        return pd.DataFrame(
            columns=[
                "Event",
                "FirstDate",
                "Games",
                "Win",
                "Draw",
                "Loss",
                "Score",
                "HighestOpp",
                "HighestOppRating",
                "HighestOppOutcome",
                "LowestOpp",
                "LowestOppRating",
                "LowestOppOutcome",
            ]
        )

    rows = []
    for event, g in d.groupby("Event", dropna=False):
        g2 = g.copy()

        first_date = g2["Date_dt"].min()
        first_date_str = first_date.date().isoformat() if pd.notna(first_date) else ""

        win = int((g2["Outcome"] == "Win").sum())
        draw = int((g2["Outcome"] == "Draw").sum())
        loss = int((g2["Outcome"] == "Loss").sum())
        games = int(len(g2))

        points = win + 0.5 * draw
        score = f"{points:g}/{games}"


        rated = g2[g2["OpponentRatingNum"].notna()].copy()

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
    out["_FirstDateSort"] = pd.to_datetime(out["FirstDate"], errors="coerce")
    out = out.sort_values(["_FirstDateSort", "Event"], ascending=[True, True]).drop(columns=["_FirstDateSort"])
    return out