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
    CANONICAL_TAGS,
    activity_data,
    apply_filters,
    compute_milestones,
    current_form,
    daily_activity,
    event_summary,
    game_length_data,
    head_to_head,
    kpi_stats,
    lessons_table,
    load_games_from_text,
    milestone_deltas,
    opening_summary,
    opponent_rating_bucket_summary,
    opponent_summary,
    outcome_for_player,
    outcome_vs_rating_data,
    performance_rating_stats,
    player_rating_over_time,
    recurring_weaknesses,
    review_queue,
    safe_int,
    scouting_report,
    streaks,
    tag_counts,
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
# Lessons and Tags (ADR 0002 — comment conventions / issue #4)
# ---------------------------------------------------------------------------

class TestLessons:
    """A Lesson is a chapter comment starting with 'Lesson:' (case-insensitive)."""

    def test_chapter_comment_with_lesson_prefix_becomes_a_lesson(self, df):
        game1 = df[df["ChapterURL"].str.endswith("chap0001")].iloc[0]
        assert game1["Lessons"] == [
            "Keep the tension in the center instead of releasing it early. #strategy"
        ]

    def test_game_with_no_comments_has_no_lessons(self, df):
        game2 = df[df["ChapterURL"].str.endswith("chap0002")].iloc[0]
        assert game2["Lessons"] == []

    def test_comments_without_prefix_are_not_lessons(self, df):
        """Ordinary annotations (game 3 has two) never become Lessons."""
        game3 = df[df["ChapterURL"].str.endswith("chap0003")].iloc[0]
        assert game3["Lessons"] == []

    def test_multiple_lessons_with_mixed_case_prefixes(self, df):
        """Game 4 has 'LESSON:' at chapter level and 'lesson:' inside a variation."""
        game4 = df[df["ChapterURL"].str.endswith("chap0004")].iloc[0]
        assert len(game4["Lessons"]) == 2
        assert "Don't grab pawns while behind in development." in game4["Lessons"]
        assert "Castle before starting an attack. #opening" in game4["Lessons"]


class TestTags:
    """A Tag is a hashtag in any chapter comment, normalized to lowercase."""

    def test_tag_extracted_from_lesson_comment(self, df):
        game1 = df[df["ChapterURL"].str.endswith("chap0001")].iloc[0]
        assert game1["Tags"] == ["strategy"]

    def test_game_with_no_comments_has_no_tags(self, df):
        game2 = df[df["ChapterURL"].str.endswith("chap0002")].iloc[0]
        assert game2["Tags"] == []

    def test_mixed_case_tags_normalized_to_lowercase(self, df):
        """Game 3 has '#blunder #Tactics' → both lowercase."""
        game3 = df[df["ChapterURL"].str.endswith("chap0003")].iloc[0]
        assert sorted(game3["Tags"]) == ["blunder", "tactics"]

    def test_tags_spread_across_comments_are_merged_and_deduplicated(self, df):
        """Game 5 has #tactics in two comments and #endgame in one."""
        game5 = df[df["ChapterURL"].str.endswith("chap0005")].iloc[0]
        assert sorted(game5["Tags"]) == ["endgame", "tactics"]

    def test_lichess_clock_annotations_are_not_tags(self, df):
        """[%clk 1:30:00] style noise must never produce Tags."""
        for _, game in df.iterrows():
            for tag in game["Tags"]:
                assert "clk" not in tag
                assert ":" not in tag

    def test_checkmate_symbol_in_moves_is_not_a_tag(self):
        """The '#' checkmate suffix in SAN (e.g. Qe3#) must not produce Tags."""
        pgn = (
            '[Event "T"]\n[White "Test Player"]\n[Black "X"]\n[Result "1-0"]\n\n'
            "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# { mated with Qxf7# } 1-0\n"
        )
        one_df, _ = load_games_from_text(pgn, player_name="Test Player")
        assert one_df.iloc[0]["Tags"] == []


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


def _games_with_outcomes(outcomes: list[str]) -> pd.DataFrame:
    """A minimal date-ordered Games DataFrame with the given outcomes."""
    return pd.DataFrame({
        "Outcome": outcomes,
        "Date_dt": pd.date_range("2024-01-01", periods=len(outcomes)),
        "Index": range(1, len(outcomes) + 1),
    })


class TestCurrentForm:
    """Streak fire / form dots computation (issue #10)."""

    def test_empty_data(self):
        form = current_form(pd.DataFrame())
        assert form == {"win_streak": 0, "loss_streak": 0, "last_5": []}

    def test_all_wins(self):
        form = current_form(_games_with_outcomes(["Win"] * 6))
        assert form["win_streak"] == 6
        assert form["loss_streak"] == 0
        assert form["last_5"] == ["Win"] * 5

    def test_alternating_results(self):
        form = current_form(_games_with_outcomes(["Win", "Loss", "Win", "Loss", "Win"]))
        assert form["win_streak"] == 1
        assert form["loss_streak"] == 0
        assert form["last_5"] == ["Win", "Loss", "Win", "Loss", "Win"]

    def test_loss_streak(self):
        form = current_form(_games_with_outcomes(["Win", "Win", "Loss", "Loss", "Loss"]))
        assert form["win_streak"] == 0
        assert form["loss_streak"] == 3

    def test_draw_breaks_both_streaks(self):
        form = current_form(_games_with_outcomes(["Win", "Win", "Draw"]))
        assert form["win_streak"] == 0
        assert form["loss_streak"] == 0

    def test_last_5_is_oldest_to_newest(self):
        outcomes = ["Loss", "Loss", "Draw", "Win", "Win", "Win"]
        form = current_form(_games_with_outcomes(outcomes))
        assert form["last_5"] == ["Loss", "Draw", "Win", "Win", "Win"]
        assert form["win_streak"] == 3

    def test_fewer_than_5_games(self):
        form = current_form(_games_with_outcomes(["Win", "Draw"]))
        assert form["last_5"] == ["Win", "Draw"]

    def test_fixture_games(self, df):
        # Fixture order by date: Win, Draw, Loss, Win, Win, Win, Draw
        form = current_form(df)
        assert form["last_5"] == ["Loss", "Win", "Win", "Win", "Draw"]
        assert form["win_streak"] == 0  # the final Draw broke the 3-game run
        assert form["loss_streak"] == 0


# ---------------------------------------------------------------------------
# Lessons aggregation + Tag counting (issue #12)
# ---------------------------------------------------------------------------

class TestLessonsTable:
    def test_one_row_per_lesson_newest_first(self, df):
        """A Game with two Lessons contributes two rows; ordering is newest first."""
        lessons = lessons_table(df)
        # Fixture: game 1 (2024.01.06) has 1 Lesson, game 4 (2024.06.15) has 2
        assert len(lessons) == 3
        assert list(lessons["Date"])[:2] == ["2024.06.15", "2024.06.15"]
        assert list(lessons["Date"])[2] == "2024.01.06"

    def test_each_lesson_carries_its_source_game(self, df):
        lessons = lessons_table(df)
        oldest = lessons.iloc[-1]
        assert oldest["Lesson"].startswith("Keep the tension")
        assert oldest["Opponent"] == "Opponent A"
        assert oldest["Outcome"] == "Win"
        assert oldest["ChapterURL"].endswith("chap0001")
        assert list(oldest["Tags"]) == ["strategy"]

    def test_games_without_lessons_are_excluded(self, df):
        lessons = lessons_table(df)
        urls = set(lessons["ChapterURL"])
        assert not any(u.endswith("chap0002") for u in urls)  # no comments at all
        assert not any(u.endswith("chap0003") for u in urls)  # tags but no Lesson

    def test_filter_by_tag(self, df):
        lessons = lessons_table(df, tags=["opening"])
        # Only game 4's lessons carry #opening
        assert len(lessons) == 2
        assert all(u.endswith("chap0004") for u in lessons["ChapterURL"])

    def test_filter_by_opponent(self, df):
        lessons = lessons_table(df, opponent="Opponent A")
        # Game 1 (vs A) and game 4 (vs A) → all 3 lessons
        assert len(lessons) == 3
        lessons_d = lessons_table(df, opponent="Opponent D")
        assert len(lessons_d) == 0

    def test_empty_data(self):
        lessons = lessons_table(pd.DataFrame())
        assert lessons.empty


def _tagged_games(games: list[tuple[str, list[str]]]) -> pd.DataFrame:
    """A minimal Games DataFrame from (outcome, tags) pairs, date-ordered."""
    dates = pd.date_range("2024-01-01", periods=len(games), freq="W")
    return pd.DataFrame({
        "Outcome": [outcome for outcome, _ in games],
        "Tags": [tags for _, tags in games],
        "Lessons": [[] for _ in games],
        "Date_dt": dates,
        "Date": [d.strftime("%Y.%m.%d") for d in dates],
        "Index": range(1, len(games) + 1),
        "ChapterURL": [f"https://lichess.org/study/x/ch{i:04d}"
                       for i in range(1, len(games) + 1)],
        "Opponent": ["Someone"] * len(games),
    })


class TestRecurringWeaknesses:
    """Tag ↔ loss correlation (issue #18): the insight that makes Tags pay off."""

    def test_clear_recurring_pattern_is_called_out(self):
        """#time-trouble on most recent losses → a callout naming the stat."""
        games = [("Win", [])] * 4 + [
            ("Loss", ["time-trouble"]),
            ("Loss", ["time-trouble", "endgame"]),
            ("Win", ["tactics"]),
            ("Loss", ["time-trouble"]),
            ("Loss", []),
            ("Loss", ["time-trouble"]),
        ]
        callouts = recurring_weaknesses(_tagged_games(games))
        assert len(callouts) >= 1
        top = callouts[0]
        assert top["tag"] == "time-trouble"
        assert top["loss_count"] == 4
        assert top["window_losses"] == 5
        assert "4 of your last 5 losses" in top["stat"]
        assert "#time-trouble" in top["stat"]

    def test_callout_links_to_the_games_behind_it(self):
        """Each callout carries the Games it's based on (clickable in the UI)."""
        games = [("Loss", ["blunder"]), ("Loss", ["blunder"]), ("Win", []),
                 ("Loss", ["blunder"])]
        callouts = recurring_weaknesses(_tagged_games(games))
        urls = callouts[0]["chapter_urls"]
        assert len(urls) == 3
        assert all("lichess.org" in u for u in urls)
        assert callouts[0]["window"]   # the period is named

    def test_no_pattern_when_tag_is_not_loss_associated(self):
        """A Tag on every game (wins included) is a habit, not a weakness."""
        games = [("Win", ["tactics"]), ("Loss", ["tactics"]), ("Win", ["tactics"]),
                 ("Loss", ["tactics"]), ("Win", ["tactics"]), ("Loss", ["tactics"]),
                 ("Win", ["tactics"])]
        assert recurring_weaknesses(_tagged_games(games)) == []

    def test_sparse_tags_stay_silent(self):
        """One or two occurrences are an anecdote, not a pattern."""
        games = [("Loss", ["endgame"]), ("Loss", ["time-trouble"]),
                 ("Loss", ["endgame"]), ("Loss", []), ("Loss", ["opening"])]
        assert recurring_weaknesses(_tagged_games(games)) == []

    def test_all_wins_means_no_weaknesses(self):
        games = [("Win", ["tactics"]), ("Win", ["tactics"]), ("Win", ["tactics"]),
                 ("Win", ["tactics"])]
        assert recurring_weaknesses(_tagged_games(games)) == []

    def test_callouts_ranked_by_severity(self):
        """The tag in more losses outranks the tag in fewer."""
        games = [
            ("Loss", ["time-trouble", "blunder"]),
            ("Loss", ["time-trouble", "blunder"]),
            ("Loss", ["time-trouble", "blunder"]),
            ("Loss", ["time-trouble"]),
            ("Loss", ["time-trouble"]),
        ]
        callouts = recurring_weaknesses(_tagged_games(games))
        assert [c["tag"] for c in callouts] == ["time-trouble", "blunder"]
        assert callouts[0]["severity"] >= callouts[1]["severity"]

    def test_fixture_data_is_below_threshold(self, df):
        """The 7-game fixture has one Loss → silence, not noise."""
        assert recurring_weaknesses(df) == []

    def test_empty_data(self):
        assert recurring_weaknesses(pd.DataFrame()) == []


def _games_with_lessons(games: list[dict]) -> pd.DataFrame:
    """A minimal Games DataFrame where each game can carry Lessons and Tags."""
    dates = pd.date_range("2024-01-01", periods=len(games), freq="W")
    return pd.DataFrame({
        "Outcome": [g.get("outcome", "Win") for g in games],
        "Tags": [g.get("tags", []) for g in games],
        "Lessons": [g.get("lessons", []) for g in games],
        "Opponent": [g.get("opponent", "Someone") for g in games],
        "Event": ["Test Event"] * len(games),
        "Result": ["1-0"] * len(games),
        "Date_dt": dates,
        "Date": [d.strftime("%Y.%m.%d") for d in dates],
        "Index": range(1, len(games) + 1),
        "ChapterURL": [f"https://lichess.org/study/x/rch{i:04d}"
                       for i in range(1, len(games) + 1)],
    })


class TestReviewQueue:
    """Pre-game review prioritization (issue #19)."""

    def test_weakness_tagged_lessons_come_first(self):
        """Lessons tagged with a detected recurring weakness lead the queue."""
        games = [
            {"outcome": "Win",  "lessons": ["Oldest, no weakness"], "tags": ["opening"]},
            {"outcome": "Loss", "lessons": ["Weakness lesson 1"], "tags": ["time-trouble"]},
            {"outcome": "Loss", "lessons": ["Weakness lesson 2"], "tags": ["time-trouble"]},
            {"outcome": "Loss", "lessons": ["Weakness lesson 3"], "tags": ["time-trouble"]},
            {"outcome": "Win",  "lessons": ["Very newest lesson"], "tags": []},
        ]
        queue = review_queue(_games_with_lessons(games))
        # The weakness bucket leads, newest first within it…
        assert [c["Lesson"] for c in queue[:3]] == [
            "Weakness lesson 3", "Weakness lesson 2", "Weakness lesson 1",
        ]
        # …then everything else by recency
        assert [c["Lesson"] for c in queue[3:]] == [
            "Very newest lesson", "Oldest, no weakness",
        ]

    def test_opponent_lessons_outrank_generic_recent_ones(self):
        """Scouting context: lessons from facing them beat other recent lessons."""
        games = [
            {"lessons": ["From facing Shao"], "opponent": "Shao"},
            {"lessons": ["From facing someone else"], "opponent": "Lopez"},
            {"lessons": ["Newest, also someone else"], "opponent": "Lopez"},
        ]
        queue = review_queue(_games_with_lessons(games), opponent="Shao")
        assert queue[0]["Lesson"] == "From facing Shao"
        assert queue[0]["reason"] == "You're facing Shao"
        # The rest stay in recency order
        assert [c["Lesson"] for c in queue[1:]] == [
            "Newest, also someone else", "From facing someone else",
        ]

    def test_weakness_outranks_opponent(self):
        """What's costing you games beats opponent history."""
        games = [
            {"lessons": ["Opponent lesson"], "opponent": "Shao"},
            {"outcome": "Loss", "lessons": ["W1"], "tags": ["blunder"]},
            {"outcome": "Loss", "lessons": ["W2"], "tags": ["blunder"]},
            {"outcome": "Loss", "lessons": ["W3"], "tags": ["blunder"]},
        ]
        queue = review_queue(_games_with_lessons(games), opponent="Shao")
        assert [c["Lesson"] for c in queue] == ["W3", "W2", "W1", "Opponent lesson"]
        assert queue[0]["reason"] == "Recurring weakness: #blunder"

    def test_every_card_explains_why_it_is_there(self):
        games = [{"lessons": ["Some lesson"]}, {"lessons": ["Another"]}]
        queue = review_queue(_games_with_lessons(games))
        assert all(c["reason"] for c in queue)
        assert all(c["ChapterURL"] for c in queue)

    def test_no_weaknesses_means_recency_order(self):
        """Below the weakness threshold the queue is simply newest-first."""
        games = [{"lessons": [f"Lesson {i}"]} for i in range(1, 5)]
        queue = review_queue(_games_with_lessons(games))
        assert [c["Lesson"] for c in queue] == [
            "Lesson 4", "Lesson 3", "Lesson 2", "Lesson 1",
        ]

    def test_no_lessons_means_empty_queue(self, df):
        assert review_queue(pd.DataFrame()) == []
        no_lessons = df.copy()
        no_lessons["Lessons"] = [[] for _ in range(len(no_lessons))]
        assert review_queue(no_lessons) == []


class TestTagCounts:
    def test_counts_reflect_per_game_tags(self, df):
        counts = {t["tag"]: t["count"] for t in tag_counts(df)}
        # Fixture: tactics appears in games 3 and 5; the rest once each
        assert counts["tactics"] == 2
        assert counts["strategy"] == 1
        assert counts["blunder"] == 1
        assert counts["opening"] == 1
        assert counts["endgame"] == 1

    def test_canonical_taxonomy_tags_come_first(self, df):
        result = tag_counts(df)
        # All fixture tags are canonical → ordered by taxonomy position
        tags_in_order = [t["tag"] for t in result]
        canonical_positions = [CANONICAL_TAGS.index(t) for t in tags_in_order]
        assert canonical_positions == sorted(canonical_positions)
        assert all(t["canonical"] for t in result)

    def test_freeform_tags_come_after_canonical_sorted_by_count(self):
        pgn = """\
[Event "T"]
[Site "S"]
[Date "2024.01.01"]
[White "Me"]
[Black "Other"]
[Result "1-0"]
[ChapterURL "https://lichess.org/study/x/y1"]

{ Lesson: a. #zugzwang #endgame #zugzwang } 1. e4 1-0

[Event "T"]
[Site "S"]
[Date "2024.01.02"]
[White "Me"]
[Black "Other"]
[Result "1-0"]
[ChapterURL "https://lichess.org/study/x/y2"]

{ Lesson: b. #zugzwang #fortress } 1. e4 1-0
"""
        df, _ = load_games_from_text(pgn, player_name="Me")
        result = tag_counts(df)
        tags = [t["tag"] for t in result]
        # Canonical (#endgame) first, then freeform by count: zugzwang (2) > fortress (1)
        assert tags == ["endgame", "zugzwang", "fortress"]
        assert [t["canonical"] for t in result] == [True, False, False]
        assert [t["count"] for t in result] == [1, 2, 1]

    def test_empty_data(self):
        assert tag_counts(pd.DataFrame()) == []


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


class TestScoutingReport:
    """The pre-game dossier on one opponent (issue #13, CONTEXT.md glossary)."""

    def test_head_to_head_score(self, df):
        # Fixture vs Opponent A: Win (G1), Win (G4), Draw (G7)
        report = scouting_report(df, "Opponent A")
        assert report["total"] == 3
        assert (report["win"], report["draw"], report["loss"]) == (2, 1, 0)
        assert report["score"] == "2.5/3"

    def test_rating_gap_uses_latest_known_ratings(self, df):
        """Gap = their latest rating vs them − my latest rating anywhere."""
        report = scouting_report(df, "Opponent A")
        assert report["their_rating"] == 1925   # G7 (2024-06-16), their latest
        assert report["my_rating"] == 1810      # Daniel's latest rated game
        assert report["rating_gap"] == 115      # positive = they're stronger

    def test_timeline_lists_every_game_with_dates(self, df):
        """Per-game results, oldest → newest, each linkable to its Game."""
        report = scouting_report(df, "Opponent A")
        timeline = report["timeline"]
        assert [g["Date"] for g in timeline] == ["2024.01.06", "2024.06.15", "2024.06.16"]
        assert [g["Outcome"] for g in timeline] == ["Win", "Win", "Draw"]
        assert all(g["ChapterURL"] for g in timeline)

    def test_openings_split_by_my_color(self, df):
        """What they play against me, separately for my White and Black games."""
        report = scouting_report(df, "Opponent A")
        as_white = report["openings_as_white"]
        as_black = report["openings_as_black"]
        # As White I've had two Catalans (E04) against them
        assert {o["ECO"] for o in as_white} == {"E04"}
        assert sum(o["Games"] for o in as_white) == 2
        # As Black they opened into a King's Indian — and I won it
        assert [o["Opening"] for o in as_black] == ["King's Indian Defense"]
        assert as_black[0]["Win"] == 1

    def test_how_our_games_ended(self, df):
        """Termination breakdown for the games against this opponent only."""
        report = scouting_report(df, "Opponent A")
        terminations = {t["Termination"]: t["Games"] for t in report["terminations"]}
        assert terminations == {"win by resignation": 1,
                                "loss by checkmate": 1,
                                "Normal": 1}

    def test_lessons_from_facing_them(self, df):
        """Every Lesson written after a game vs this opponent, with Game links."""
        report = scouting_report(df, "Opponent A")
        lessons = report["lessons"]
        assert len(lessons) == 3   # 1 from G1 + 2 from G4
        assert all(lesson["ChapterURL"] for lesson in lessons)
        texts = " ".join(lesson["Lesson"] for lesson in lessons)
        assert "Keep the tension" in texts
        assert "Don't grab pawns" in texts

    def test_unknown_opponent_gives_empty_dossier(self, df):
        report = scouting_report(df, "Nobody Iknow")
        assert report["total"] == 0
        assert report["timeline"] == []
        assert report["lessons"] == []

    def test_empty_data(self):
        report = scouting_report(pd.DataFrame(), "Anyone")
        assert report["total"] == 0


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


class TestDailyActivity:
    """Per-day aggregation behind the activity heatmap calendar (issue #14)."""

    def test_one_row_per_day_with_games(self, df):
        # Fixture days: 2024-01-06 (2 games), 01-07 (1), 06-15 (2), 06-16 (2)
        daily = daily_activity(df)
        assert len(daily) == 4
        assert list(daily["Date_dt"]) == sorted(daily["Date_dt"])
        assert daily["Games"].sum() == 7

    def test_day_results_color_the_cell(self, df):
        """Net = Win − Loss decides whether a day reads green or red."""
        daily = daily_activity(df).set_index("Date_dt")
        jan6 = daily.loc[pd.Timestamp("2024-01-06")]   # Win + Draw
        assert (jan6["Win"], jan6["Draw"], jan6["Loss"]) == (1, 1, 0)
        assert jan6["Net"] == 1

        jan7 = daily.loc[pd.Timestamp("2024-01-07")]   # the only Loss
        assert jan7["Net"] == -1

        jun15 = daily.loc[pd.Timestamp("2024-06-15")]  # two Wins → strongest green
        assert jun15["Net"] == 2

    def test_each_day_lists_its_games_for_hover(self, df):
        """Hovering/tapping a cell shows that day's Games: opponent + result."""
        daily = daily_activity(df).set_index("Date_dt")
        jan6 = daily.loc[pd.Timestamp("2024-01-06")]
        assert "Win vs Opponent A" in jan6["Detail"]
        assert "Draw vs Opponent B" in jan6["Detail"]

        jun16 = daily.loc[pd.Timestamp("2024-06-16")]
        assert "Win vs Opponent B" in jun16["Detail"]
        assert "Draw vs Opponent A" in jun16["Detail"]

    def test_empty_data(self):
        daily = daily_activity(pd.DataFrame())
        assert daily.empty
        assert "Net" in daily.columns  # chart code can rely on the shape

    def test_undated_games_are_excluded(self):
        """A Game without a date can't sit on a calendar."""
        pgn = """\
[Event "T"]
[Site "S"]
[Date "????.??.??"]
[White "Me"]
[Black "Other"]
[Result "1-0"]

1. e4 1-0

[Event "T"]
[Site "S"]
[Date "2024.03.01"]
[White "Me"]
[Black "Other"]
[Result "1-0"]

1. e4 1-0
"""
        games, _ = load_games_from_text(pgn, player_name="Me")
        daily = daily_activity(games)
        assert len(daily) == 1
        assert daily["Games"].sum() == 1

    def test_unfinished_games_are_excluded(self):
        """A Game with no result yet (Outcome 'Unknown') isn't a calendar day —
        same convention as the monthly/day-of-week activity charts."""
        pgn = """\
[Event "T"]
[Site "S"]
[Date "2024.03.01"]
[White "Me"]
[Black "Other"]
[Result "*"]

1. e4 e5 *

[Event "T"]
[Site "S"]
[Date "2024.03.08"]
[White "Me"]
[Black "Other"]
[Result "1-0"]

1. e4 1-0
"""
        games, _ = load_games_from_text(pgn, player_name="Me")
        daily = daily_activity(games)
        assert len(daily) == 1                  # only the finished game's day
        assert daily["Games"].sum() == daily[["Win", "Draw", "Loss"]].sum().sum()


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
# Milestone celebrations (issue #15) — comparing two data snapshots
# ---------------------------------------------------------------------------

def _snapshot(games: list[dict]) -> pd.DataFrame:
    """A minimal Games DataFrame snapshot for milestone-delta tests."""
    return pd.DataFrame({
        "Outcome": [g.get("outcome", "Win") for g in games],
        "Opponent": [g.get("opponent", "Someone") for g in games],
        "PlayerRatingNum": [float(g["my_rating"]) if g.get("my_rating") else None
                            for g in games],
        "OpponentRatingNum": [float(g["opp_rating"]) if g.get("opp_rating") else None
                              for g in games],
        "Date_dt": pd.date_range("2024-01-01", periods=len(games)),
        "Index": range(1, len(games) + 1),
    })


class TestMilestoneDeltas:
    def test_new_peak_rating_detected(self):
        old = _snapshot([{"my_rating": 1800}, {"my_rating": 1810}])
        new = _snapshot([{"my_rating": 1800}, {"my_rating": 1810},
                         {"my_rating": 1850}])
        deltas = milestone_deltas(old, new)
        peak = next(d for d in deltas if d["kind"] == "peak_rating")
        assert peak["old"] == 1810
        assert peak["new"] == 1850
        assert "1850" in peak["description"]

    def test_new_longest_win_streak_detected(self):
        old = _snapshot([{"outcome": "Win"}, {"outcome": "Win"}, {"outcome": "Loss"}])
        new = _snapshot([{"outcome": "Win"}, {"outcome": "Win"}, {"outcome": "Loss"},
                         {"outcome": "Win"}, {"outcome": "Win"}, {"outcome": "Win"}])
        deltas = milestone_deltas(old, new)
        streak = next(d for d in deltas if d["kind"] == "win_streak")
        assert streak["old"] == 2
        assert streak["new"] == 3

    def test_win_against_new_highest_rated_opponent_detected(self):
        old = _snapshot([{"outcome": "Win", "opp_rating": 1900, "opponent": "Old Best"},
                         {"outcome": "Loss", "opp_rating": 2100, "opponent": "Lost To"}])
        new = _snapshot([{"outcome": "Win", "opp_rating": 1900, "opponent": "Old Best"},
                         {"outcome": "Loss", "opp_rating": 2100, "opponent": "Lost To"},
                         {"outcome": "Win", "opp_rating": 2050, "opponent": "Giant"}])
        deltas = milestone_deltas(old, new)
        kill = next(d for d in deltas if d["kind"] == "giant_kill")
        # Losses against strong players don't count — only beaten opponents
        assert kill["old"] == 1900
        assert kill["new"] == 2050
        assert "Giant" in kill["description"]

    def test_no_celebration_when_nothing_improved(self):
        """A Sync that brings a loss (or nothing) sets no records."""
        old = _snapshot([{"outcome": "Win", "my_rating": 1810, "opp_rating": 1900}])
        new = _snapshot([{"outcome": "Win", "my_rating": 1810, "opp_rating": 1900},
                         {"outcome": "Loss", "my_rating": 1805, "opp_rating": 2000}])
        assert milestone_deltas(old, new) == []
        assert milestone_deltas(old, old) == []

    def test_no_baseline_means_no_celebration(self):
        """An empty pre-Sync snapshot: nothing to beat → nothing to celebrate."""
        new = _snapshot([{"outcome": "Win", "my_rating": 1850, "opp_rating": 1900}])
        assert milestone_deltas(pd.DataFrame(), new) == []

    def test_first_ever_win_streak_is_celebrated(self):
        """Going from no streak at all (real games, zero wins in a row) to a
        streak IS a personal best — a baseline of 0 from real games counts."""
        old = _snapshot([{"outcome": "Loss"}, {"outcome": "Draw"}])
        new = _snapshot([{"outcome": "Loss"}, {"outcome": "Draw"},
                         {"outcome": "Win"}, {"outcome": "Win"}])
        deltas = milestone_deltas(old, new)
        streak = next(d for d in deltas if d["kind"] == "win_streak")
        assert streak["old"] == 0
        assert streak["new"] == 2

    def test_one_sync_can_break_several_records(self, sample_pgn_text,
                                                 sample_pgn_study2_text):
        """The real fixture flow: Study 2's games set a new peak AND a new streak."""
        old, _ = load_games_from_text(sample_pgn_text, player_name="Test Player")
        new, _ = load_games_from_text(
            sample_pgn_text + "\n\n" + sample_pgn_study2_text,
            player_name="Test Player",
        )
        kinds = {d["kind"] for d in milestone_deltas(old, new)}
        # Study 2 brings: a 1815 rating (old peak 1810) and a 4-game win
        # streak (old longest 3); its only win is vs a 1700 (old best 1930).
        assert kinds == {"peak_rating", "win_streak"}


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
