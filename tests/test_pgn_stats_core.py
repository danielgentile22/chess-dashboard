"""
tests/test_pgn_stats_core.py
============================
Unit tests for pgn_stats_core public functions.
Run with:  pytest tests/ -v
"""
from __future__ import annotations

import math

import pandas as pd

from pgn_stats_core import (
    activity_data,
    apply_filters,
    compute_milestones,
    event_summary,
    game_length_data,
    head_to_head,
    kpi_stats,
    load_games_from_text,
    opening_summary,
    opponent_rating_bucket_summary,
    opponent_summary,
    outcome_for_player,
    outcome_vs_rating_data,
    performance_rating_stats,
    player_rating_over_time,
    safe_int,
    streaks,
    termination_counts,
    win_draw_loss_counts,
    win_rate_over_time,
    winner_from_result,
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

class TestSafeInt:
    def test_plain_number(self):
        assert safe_int("1850") == 1850

    def test_provisional(self):
        assert safe_int("1850P") == 1850

    def test_question_mark(self):
        assert safe_int("?") is None

    def test_none(self):
        assert safe_int(None) is None

    def test_empty(self):
        assert safe_int("") is None

    def test_integer_input(self):
        assert safe_int(1800) == 1800


class TestOutcomeForPlayer:
    def test_white_wins(self):
        assert outcome_for_player("1-0", "White") == "Win"

    def test_white_loses(self):
        assert outcome_for_player("0-1", "White") == "Loss"

    def test_white_draws(self):
        assert outcome_for_player("1/2-1/2", "White") == "Draw"

    def test_black_wins(self):
        assert outcome_for_player("0-1", "Black") == "Win"

    def test_black_loses(self):
        assert outcome_for_player("1-0", "Black") == "Loss"

    def test_unknown_color(self):
        assert outcome_for_player("1-0", "Unknown") == "Unknown"


class TestWinnerFromResult:
    def test_white_wins(self):
        assert winner_from_result("1-0") == "White"

    def test_black_wins(self):
        assert winner_from_result("0-1") == "Black"

    def test_draw(self):
        assert winner_from_result("1/2-1/2") == "Draw"

    def test_unknown(self):
        assert winner_from_result("*") == "Unknown"


# ---------------------------------------------------------------------------
# load_games_df
# ---------------------------------------------------------------------------

class TestLoadGamesDf:
    def test_row_count(self, df):
        assert len(df) == 7

    def test_player_detected(self, player):
        assert player == "Test Player"

    def test_color_column(self, df):
        assert set(df["Color"].unique()).issubset({"White", "Black", "Unknown"})

    def test_outcome_column(self, df):
        assert set(df["Outcome"].unique()).issubset({"Win", "Draw", "Loss", "Unknown"})

    def test_opponent_not_self(self, df):
        """Opponent should never equal the player."""
        assert not (df["Opponent"] == "Test Player").any()

    def test_player_rating_num(self, df):
        """PlayerRatingNum should be numeric where available."""
        rated = df["PlayerRatingNum"].dropna()
        assert len(rated) > 0
        assert pd.api.types.is_numeric_dtype(rated)

    def test_rating_diff_computed(self, df):
        """RatingDiff = OpponentRatingNum - PlayerRatingNum."""
        rated = df[df["RatingDiff"].notna()]
        for _, row in rated.iterrows():
            expected = row["OpponentRatingNum"] - row["PlayerRatingNum"]
            assert math.isclose(row["RatingDiff"], expected, rel_tol=1e-6)

    def test_date_dt_parsed(self, df):
        """Date_dt should be datetime for all rows with a valid date."""
        assert pd.api.types.is_datetime64_any_dtype(df["Date_dt"])

    def test_eco_present(self, df):
        assert df["ECO"].str.strip().ne("").all()


# ---------------------------------------------------------------------------
# load_games_from_text
# ---------------------------------------------------------------------------

class TestLoadGamesFromText:
    def test_parses_pgn_text_same_as_file(self, sample_pgn_text, df):
        """PGN text (as returned by the Lichess client) parses identically to a file."""
        text_df, player = load_games_from_text(sample_pgn_text, player_name="Test Player")
        assert player == "Test Player"
        assert len(text_df) == len(df)
        assert list(text_df.columns) == list(df.columns)
        assert text_df["Outcome"].tolist() == df["Outcome"].tolist()

    def test_empty_text_gives_empty_df(self):
        text_df, player = load_games_from_text("", player_name="Someone")
        assert text_df.empty
        assert player == "Someone"


# ---------------------------------------------------------------------------
# Chapter metadata (Game identity — ADR 0001 / issue #3)
# ---------------------------------------------------------------------------

# A PGN that did not come from a Lichess Study (no chapter headers at all).
PGN_WITHOUT_CHAPTER_HEADERS = """\
[Event "Plain Old Tournament"]
[Date "2024.03.10"]
[White "Test Player"]
[Black "Opponent X"]
[Result "1-0"]

1. e4 e5 2. Nf3 1-0
"""


class TestChapterMetadata:
    def test_chapter_url_is_the_game_identity(self, df):
        """Every Game in a Study export carries its permanent ChapterURL."""
        assert (df["ChapterURL"].str.startswith("https://lichess.org/study/")).all()
        # ChapterURL is unique per Game — it is the identity key
        assert df["ChapterURL"].nunique() == len(df)

    def test_study_name_extracted(self, df):
        assert (df["StudyName"] == "Test Study").all()

    def test_chapter_name_extracted(self, df):
        assert df["ChapterName"].str.contains(" - ").all()

    def test_games_without_chapter_headers_get_empty_values(self):
        """Non-Study PGNs still parse; chapter fields are just empty."""
        plain_df, _ = load_games_from_text(
            PGN_WITHOUT_CHAPTER_HEADERS, player_name="Test Player"
        )
        assert len(plain_df) == 1
        assert (plain_df["ChapterURL"] == "").all()
        assert (plain_df["StudyName"] == "").all()
        assert (plain_df["ChapterName"] == "").all()


# ---------------------------------------------------------------------------
# apply_filters
# ---------------------------------------------------------------------------

class TestApplyFilters:
    def test_filter_color(self, df):
        out = apply_filters(df, ["White"], [], [], None, None)
        assert (out["Color"] == "White").all()

    def test_filter_outcome(self, df):
        out = apply_filters(df, [], ["Win"], [], None, None)
        assert (out["Outcome"] == "Win").all()

    def test_filter_both_colors_returns_all(self, df):
        out = apply_filters(df, ["White", "Black"], [], [], None, None)
        assert len(out) == len(df)

    def test_filter_empty_lists_no_change(self, df):
        out = apply_filters(df, [], [], [], None, None)
        assert len(out) == len(df)

    def test_filter_date(self, df):
        out = apply_filters(df, [], [], [], date_start="2024-06-01", date_end="2024-06-30")
        assert (out["Date_dt"].dt.month == 6).all()

    def test_filter_by_event(self, df):
        out = apply_filters(df, [], [], [], None, None, events=["Test Open"])
        assert (out["Event"] == "Test Open").all()

    def test_filter_moves(self, df):
        out = apply_filters(df, [], [], [], None, None, min_moves=5, max_moves=100)
        assert (out["FullMoves"] >= 5).all()


# ---------------------------------------------------------------------------
# Overview statistics
# ---------------------------------------------------------------------------

class TestWinDrawLossCounts:
    def test_returns_all_keys(self, df):
        c = win_draw_loss_counts(df)
        assert set(c.index) == {"Win", "Draw", "Loss", "Unknown"}

    def test_sums_to_total(self, df):
        c = win_draw_loss_counts(df)
        assert c.sum() == len(df)

    def test_empty_df(self):
        c = win_draw_loss_counts(pd.DataFrame())
        assert c.sum() == 0

    def test_known_wins(self, df):
        """Test Player wins: game 1 (White, 1-0), game 4 (Black, 0-1), game 5 (White, 1-0), game 6 (Black, 0-1)"""
        c = win_draw_loss_counts(df)
        assert c["Win"] == 4
        assert c["Draw"] == 2
        assert c["Loss"] == 1


class TestTerminationCounts:
    def test_columns(self, df):
        tc = termination_counts(df)
        assert list(tc.columns) == ["Termination", "Games"]

    def test_empty(self):
        tc = termination_counts(pd.DataFrame(columns=["Termination"]))
        assert tc.empty


class TestStreaks:
    def test_keys(self, df):
        s = streaks(df)
        assert "longest_streak_no_loss" in s
        assert "longest_streak_wins_only" in s
        assert "current_streak_same_outcome" in s
        assert "last_20" in s

    def test_last_20_length(self, df):
        s = streaks(df)
        assert len(s["last_20"]) <= 20

    def test_empty_df(self):
        s = streaks(pd.DataFrame())
        assert s["longest_streak_no_loss"] == 0

    def test_all_wins(self):
        d = pd.DataFrame({"Outcome": ["Win"] * 5, "Date_dt": pd.NaT, "Index": range(5)})
        s = streaks(d)
        assert s["longest_streak_wins_only"] == 5
        assert s["longest_streak_no_loss"] == 5


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

class TestWinRateOverTime:
    def test_columns(self, df):
        wr = win_rate_over_time(df)
        assert {"Date_dt", "WinRate", "CumGames", "CumWins"}.issubset(wr.columns)

    def test_win_rate_range(self, df):
        wr = win_rate_over_time(df)
        assert (wr["WinRate"] >= 0).all()
        assert (wr["WinRate"] <= 100).all()

    def test_empty_df(self):
        wr = win_rate_over_time(pd.DataFrame())
        assert wr.empty


class TestPlayerRatingOverTime:
    def test_columns(self, df):
        pr = player_rating_over_time(df)
        assert {"Date_dt", "PlayerRating"}.issubset(pr.columns)

    def test_one_row_per_date(self, df):
        pr = player_rating_over_time(df)
        assert pr["Date_dt"].nunique() == len(pr)


# ---------------------------------------------------------------------------
# Opponents
# ---------------------------------------------------------------------------

class TestOpponentSummary:
    def test_only_repeat_opponents(self, df):
        opp = opponent_summary(df)
        assert (opp["Games"] > 1).all()

    def test_columns(self, df):
        opp = opponent_summary(df)
        assert "WinRate" in opp.columns

    def test_opponent_a_appears(self, df):
        opp = opponent_summary(df)
        assert "Opponent A" in opp["Opponent"].values

    def test_opponent_b_appears(self, df):
        opp = opponent_summary(df)
        assert "Opponent B" in opp["Opponent"].values


class TestHeadToHead:
    def test_against_opponent_a(self, df):
        h = head_to_head(df, "Opponent A")
        assert h["total"] == 3
        assert h["win"] + h["draw"] + h["loss"] == 3

    def test_game_rows_carry_chapter_url(self, df):
        """Each head-to-head game row links back to its Lichess chapter."""
        h = head_to_head(df, "Opponent A")
        for row in h["game_rows"]:
            assert row["ChapterURL"].startswith("https://lichess.org/study/")

    def test_missing_opponent(self, df):
        h = head_to_head(df, "Nobody")
        assert h["total"] == 0

    def test_game_rows_present(self, df):
        h = head_to_head(df, "Opponent A")
        assert len(h["game_rows"]) == 3


# ---------------------------------------------------------------------------
# Openings
# ---------------------------------------------------------------------------

class TestOpeningSummary:
    def test_returns_tuple(self, df):
        result = opening_summary(df)
        assert isinstance(result, tuple) and len(result) == 2

    def test_family_df_columns(self, df):
        fam, _ = opening_summary(df)
        assert "ECO_Family" in fam.columns
        assert "WinRate" in fam.columns

    def test_opening_df_columns(self, df):
        _, opn = opening_summary(df)
        assert "ECO" in opn.columns
        assert "Opening" in opn.columns

    def test_win_rate_bounded(self, df):
        fam, opn = opening_summary(df)
        assert (fam["WinRate"] >= 0).all() and (fam["WinRate"] <= 100).all()

    def test_empty_df(self):
        fam, opn = opening_summary(pd.DataFrame())
        assert fam.empty and opn.empty


# ---------------------------------------------------------------------------
# Strength analysis
# ---------------------------------------------------------------------------

class TestOpponentRatingBuckets:
    def test_columns(self, df):
        b = opponent_rating_bucket_summary(df)
        assert "Bucket" in b.columns
        assert "WinRate" in b.columns

    def test_no_unknown_buckets(self, df):
        b = opponent_rating_bucket_summary(df)
        from pgn_stats_core import _BUCKET_LABELS
        assert b["Bucket"].isin(_BUCKET_LABELS).all()


class TestOutcomeVsRating:
    def test_columns(self, df):
        sc = outcome_vs_rating_data(df)
        assert {"OpponentRatingNum", "OutcomeNum", "Outcome"}.issubset(sc.columns)

    def test_outcome_num_values(self, df):
        sc = outcome_vs_rating_data(df)
        assert sc["OutcomeNum"].isin([0.0, 0.5, 1.0]).all()


# ---------------------------------------------------------------------------
# Game length
# ---------------------------------------------------------------------------

class TestGameLengthData:
    def test_returns_tuple(self, df):
        result = game_length_data(df)
        assert isinstance(result, tuple) and len(result) == 2

    def test_hist_df_has_moves(self, df):
        hist, avgs = game_length_data(df)
        assert "FullMoves" in hist.columns

    def test_averages_keys(self, df):
        _, avgs = game_length_data(df)
        assert set(avgs.keys()) == {"Win", "Draw", "Loss"}

    def test_empty(self):
        hist, avgs = game_length_data(pd.DataFrame())
        assert hist.empty and avgs == {}


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

class TestActivityData:
    def test_returns_tuple(self, df):
        result = activity_data(df)
        assert isinstance(result, tuple) and len(result) == 2

    def test_monthly_columns(self, df):
        m, _ = activity_data(df)
        assert "YearMonth" in m.columns
        assert "WinRate" in m.columns

    def test_dow_order(self, df):
        _, dw = activity_data(df)
        order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        present = dw["DayOfWeek"].tolist()
        indices = [order.index(d) for d in present if d in order]
        assert indices == sorted(indices)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class TestEventSummary:
    def test_two_events(self, df):
        ev = event_summary(df)
        assert len(ev) == 2

    def test_score_format(self, df):
        ev = event_summary(df)
        for _, row in ev.iterrows():
            assert "/" in row["Score"]

    def test_sorted_by_date(self, df):
        ev = event_summary(df)
        dates = pd.to_datetime(ev["FirstDate"], errors="coerce").tolist()
        assert dates == sorted(dates)


class TestPerformanceRatingStats:
    def test_keys(self, df):
        pr = performance_rating_stats(df)
        assert "performance_rating" in pr
        assert "avg_opp_rating" in pr
        assert "rated_games" in pr

    def test_pr_is_integer(self, df):
        pr = performance_rating_stats(df)
        if pr["performance_rating"] is not None:
            assert isinstance(pr["performance_rating"], int)

    def test_empty_df(self):
        pr = performance_rating_stats(pd.DataFrame())
        assert pr["performance_rating"] is None

    def test_all_wins(self):
        d = pd.DataFrame({
            "Outcome": ["Win", "Win", "Win"],
            "OpponentRatingNum": [1800.0, 1900.0, 2000.0],
        })
        pr = performance_rating_stats(d)
        # All wins → PR = avg + 800
        assert pr["performance_rating"] == round(1900.0 + 800)


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

class TestComputeMilestones:
    def test_returns_list(self, df):
        ms = compute_milestones(df)
        assert isinstance(ms, list)

    def test_has_first_game(self, df):
        ms = compute_milestones(df)
        kinds = [m["kind"] for m in ms]
        assert "first" in kinds

    def test_sorted_by_game_num(self, df):
        ms = compute_milestones(df)
        nums = [m["game_num"] for m in ms]
        assert nums == sorted(nums)

    def test_empty_df(self):
        ms = compute_milestones(pd.DataFrame())
        assert ms == []

    def test_each_item_has_required_keys(self, df):
        ms = compute_milestones(df)
        for m in ms:
            assert "date" in m
            assert "game_num" in m
            assert "description" in m
            assert "kind" in m


# ---------------------------------------------------------------------------
# KPI stats
# ---------------------------------------------------------------------------

class TestKpiStats:
    def test_keys(self, df):
        k = kpi_stats(df)
        expected = {
            "total_games", "win_pct", "draw_pct", "loss_pct",
            "current_rating", "peak_rating", "avg_opp_rating",
            "performance_rating", "longest_win_streak",
            "unique_opponents", "favorite_opening", "favorite_eco_family",
        }
        assert expected.issubset(k.keys())

    def test_total_games(self, df):
        k = kpi_stats(df)
        assert k["total_games"] == 7

    def test_pct_sum(self, df):
        k = kpi_stats(df)
        total = k["win_pct"] + k["draw_pct"] + k["loss_pct"]
        assert abs(total - 100.0) < 0.5

    def test_empty_df(self):
        k = kpi_stats(pd.DataFrame())
        assert k["total_games"] == 0
