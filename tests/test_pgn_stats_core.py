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
    repertoire_tree,
    review_queue,
    round_performance,
    safe_int,
    scouting_report,
    streaks,
    tag_counts,
    termination_counts,
    time_control_summary,
    upset_tracker,
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

    def test_round_num_is_numeric(self, df):
        """RoundNum parses the Round header as a number, so round 10 sorts
        after round 2 instead of between 1 and 2 (lexical-sort bug)."""
        assert sorted(df["RoundNum"].dropna().unique()) == [1, 2, 3, 4]

    def test_mainline_moves_extracted_as_san(self, df):
        """Each Game stores its mainline move sequence (issue #16)."""
        game1 = df[df["ChapterURL"].str.endswith("chap0001")].iloc[0]
        assert game1["Moves"][:6] == ["d4", "Nf6", "c4", "e6", "g3", "d5"]
        assert len(game1["Moves"]) == game1["Plies"]

    def test_variations_are_excluded_from_moves(self, df):
        """Game 4 has a (3... d5) variation — only the mainline is stored."""
        game4 = df[df["ChapterURL"].str.endswith("chap0004")].iloc[0]
        assert game4["Moves"] == ["d4", "Nf6", "c4", "g6", "Nc3", "Bg7", "e4", "d6"]

    def test_game_with_no_moves_gets_empty_list(self):
        pgn = """\
[Event "T"]
[Site "S"]
[Date "2024.03.01"]
[White "Me"]
[Black "Other"]
[Result "*"]

*
"""
        games, _ = load_games_from_text(pgn, player_name="Me")
        assert games.iloc[0]["Moves"] == []
        assert games.iloc[0]["Plies"] == 0

    def test_unparseable_round_gives_no_round_num(self):
        pgn = """\
[Event "T"]
[Site "S"]
[Date "2024.03.01"]
[Round "?"]
[White "Me"]
[Black "Other"]
[Result "1-0"]

1. e4 1-0
"""
        games, _ = load_games_from_text(pgn, player_name="Me")
        assert games["RoundNum"].isna().all()


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
# Repertoire tree (issue #16)
# ---------------------------------------------------------------------------

class TestRepertoireTree:
    """Daniel's personal opening explorer: what he plays, and what it scores."""

    def test_first_moves_group_games_as_white(self, df):
        # As White the fixture opens 1.d4 three times (W/D/L) and 1.e4 once (W)
        tree = repertoire_tree(df, "White")
        moves = {node["san"]: node for node in tree["moves"]}
        assert moves["d4"]["games"] == 3
        assert moves["e4"]["games"] == 1

    def test_each_node_scores_from_daniels_perspective(self, df):
        tree = repertoire_tree(df, "White")
        moves = {node["san"]: node for node in tree["moves"]}
        d4 = moves["d4"]   # games 1 (Win), 3 (Loss), 7 (Draw)
        assert (d4["win"], d4["draw"], d4["loss"]) == (1, 1, 1)
        assert d4["score_pct"] == 50.0
        e4 = moves["e4"]   # game 5 (Win)
        assert e4["score_pct"] == 100.0
        # The baseline these are judged against: 2.5/4 as White
        assert tree["score_pct"] == 62.5

    def test_expanding_a_branch_drills_one_move_deeper(self, df):
        tree = repertoire_tree(df, "White")
        d4 = next(node for node in tree["moves"] if node["san"] == "d4")
        # All three 1.d4 games continued 1...Nf6
        assert [child["san"] for child in d4["moves"]] == ["Nf6"]
        nf6 = d4["moves"][0]
        assert nf6["games"] == 3
        assert nf6["ply"] == 2
        # Daniel then chose 2.c4 twice, 2.e4 once — most-played line first
        assert [child["san"] for child in nf6["moves"]] == ["c4", "e4"]
        c4 = nf6["moves"][0]
        assert (c4["games"], c4["win"], c4["draw"], c4["loss"]) == (2, 1, 1, 0)

    def test_underperforming_branches_are_flagged(self, df):
        """1.d4 scores 50% against Daniel's 62.5% White average over 3 games →
        that's the leak.  Thin branches stay unflagged no matter how bad:
        one game proves nothing."""
        tree = repertoire_tree(df, "White", min_games=3)
        moves = {node["san"]: node for node in tree["moves"]}
        assert moves["d4"]["underperforming"] is True
        assert moves["e4"]["underperforming"] is False   # 100% — above average

        # 2.e4 (game 3) was a loss — 0% — but it's a single game, so no flag
        nf6 = moves["d4"]["moves"][0]
        thin_loss = next(child for child in nf6["moves"] if child["san"] == "e4")
        assert thin_loss["score_pct"] == 0.0
        assert thin_loss["underperforming"] is False

    def test_nodes_link_to_the_games_that_reached_them(self, df):
        tree = repertoire_tree(df, "White")
        d4 = next(node for node in tree["moves"] if node["san"] == "d4")
        refs = d4["game_refs"]
        assert len(refs) == 3
        assert all(r["ChapterURL"].startswith("https://lichess.org/study/") for r in refs)
        # Each ref says who/when/how, so the UI link means something
        assert sorted(r["Outcome"] for r in refs) == ["Draw", "Loss", "Win"]
        assert all(r["Opponent"] for r in refs)

        # Deeper down, the single-game 2.e4 node points at exactly game 3
        nf6 = d4["moves"][0]
        thin_loss = next(child for child in nf6["moves"] if child["san"] == "e4")
        assert [r["ChapterURL"][-8:] for r in thin_loss["game_refs"]] == ["chap0003"]

    def test_black_tree_starts_with_the_opponents_move(self, df):
        """As Black the tree branches on what opponents throw at Daniel:
        1.e4 twice (he answers with the Caro-Kann), 1.d4 once."""
        tree = repertoire_tree(df, "Black")
        assert tree["games"] == 3
        assert tree["score_pct"] == 83.3   # 2.5/3 as Black
        moves = {node["san"]: node for node in tree["moves"]}
        assert moves["e4"]["games"] == 2
        assert moves["d4"]["games"] == 1
        # Both 1.e4 games answered 1...c6
        assert [child["san"] for child in moves["e4"]["moves"]] == ["c6"]

    def test_a_game_can_end_mid_branch(self, df):
        """Game 6 stops at move 3 while game 2 continues — the parent node
        keeps both games, the deeper node only the one that kept going."""
        tree = repertoire_tree(df, "Black")
        e4 = next(node for node in tree["moves"] if node["san"] == "e4")
        # Walk down the shared Caro-Kann line: c6, d4, d5, e5, Bf5
        node = e4
        for expected_san in ("c6", "d4", "d5", "e5", "Bf5"):
            node = node["moves"][0]
            assert node["san"] == expected_san
            assert node["games"] == 2
        # Past 3...Bf5 only game 2 continues (game 6 ended there)
        assert len(node["moves"]) == 1
        assert node["moves"][0]["san"] == "c3"
        assert node["moves"][0]["games"] == 1

    def test_nodes_separate_games_that_ended_from_games_that_continued(self, df):
        """Each node says which of its games stopped right there (ended_here)
        vs continued deeper — even when games have no ChapterURL to tell
        them apart."""
        tree = repertoire_tree(df, "Black")
        # Walk to 3...Bf5: game 6 ended there, game 2 continued with 4.c3
        node = next(n for n in tree["moves"] if n["san"] == "e4")
        for san in ("c6", "d4", "d5", "e5", "Bf5"):
            node = next(child for child in node["moves"] if child["san"] == san)
        assert [r["ChapterURL"][-8:] for r in node["ended_here"]] == ["chap0006"]
        assert [child["san"] for child in node["moves"]] == ["c3"]

    def test_games_without_chapter_urls_are_never_conflated(self):
        """Two URL-less games sharing a prefix: the one that ended mid-line
        must still appear at its final position, not vanish."""
        pgn = """\
[Event "T"]
[Site "S"]
[Date "2024.03.01"]
[White "Me"]
[Black "Opponent X"]
[Result "1-0"]

1. d4 d5 2. c4 1-0

[Event "T"]
[Site "S"]
[Date "2024.03.08"]
[White "Me"]
[Black "Opponent Y"]
[Result "0-1"]

1. d4 d5 0-1
"""
        games, _ = load_games_from_text(pgn, player_name="Me")
        tree = repertoire_tree(games, "White")
        d5 = tree["moves"][0]["moves"][0]   # the shared 1...d5 node
        assert d5["games"] == 2
        # Game Y ended at 1...d5; game X continued with 2.c4
        assert [r["Opponent"] for r in d5["ended_here"]] == ["Opponent Y"]
        assert [child["san"] for child in d5["moves"]] == ["c4"]

    def test_above_average_branch_with_enough_games_is_not_flagged(self):
        """The flag needs BOTH conditions: enough games AND a below-average
        score.  A 3-game branch scoring above the baseline stays unflagged."""
        pgn = "\n".join(
            f'[Event "T"]\n[Site "S"]\n[Date "2024.0{i}.01"]\n[White "Me"]\n'
            f'[Black "Opp {i}"]\n[Result "{result}"]\n\n1. {move} e6 {result}\n'
            for i, (move, result) in enumerate([
                ("e4", "1-0"), ("e4", "1-0"), ("e4", "1-0"),   # 3 wins with e4
                ("d4", "0-1"), ("d4", "0-1"), ("d4", "0-1"),   # 3 losses with d4
            ], start=1)
        )
        games, _ = load_games_from_text(pgn, player_name="Me")
        tree = repertoire_tree(games, "White", min_games=3)
        moves = {node["san"]: node for node in tree["moves"]}
        assert tree["score_pct"] == 50.0
        assert moves["e4"]["underperforming"] is False   # 100% over 3 games
        assert moves["d4"]["underperforming"] is True    # 0% over 3 games

    def test_color_without_games_gives_an_empty_tree(self, df):
        tree = repertoire_tree(df[df["Color"] == "White"], "Black")
        assert tree["games"] == 0
        assert tree["moves"] == []

    def test_a_nan_moves_cell_is_treated_as_no_moves(self, df):
        """A hand-built or merged DataFrame can hold NaN where the parser
        would put a list — that's 'no moves', not a crash."""
        broken = df.copy()
        broken.loc[broken.index[0], "Moves"] = float("nan")
        tree = repertoire_tree(broken, "White")
        assert tree["games"] == 3   # the NaN game is simply excluded

    def test_min_games_threshold_is_part_of_the_tree(self, df):
        """The UI explains the flagging rule, so the tree must say what
        threshold it actually used."""
        assert repertoire_tree(df, "White")["min_games"] == 3
        assert repertoire_tree(df, "White", min_games=5)["min_games"] == 5

    def test_empty_data(self):
        tree = repertoire_tree(pd.DataFrame(), "White")
        assert tree["games"] == 0
        assert tree["moves"] == []

def _pgn_with_headers(games: list[dict]) -> str:
    """
    Inline PGN built from per-game header dicts (player always 'Me', White
    unless headers say otherwise).  Keys: result, timecontrol, round, event,
    date, my_elo, opp_elo, opponent.
    """
    chunks = []
    for i, g in enumerate(games, start=1):
        result = g.get("result", "1-0")
        chunks.append("\n".join(filter(None, [
            f'[Event "{g.get("event", "Test Event")}"]',
            '[Site "S"]',
            f'[Date "{g.get("date", "2024.03.01")}"]',
            f'[Round "{g.get("round", str(i))}"]',
            '[White "Me"]',
            f'[Black "{g.get("opponent", f"Opp {i}")}"]',
            f'[WhiteElo "{g.get("my_elo", 1500)}"]',
            f'[BlackElo "{g.get("opp_elo", 1500)}"]',
            f'[Result "{result}"]',
            f'[TimeControl "{g["timecontrol"]}"]' if g.get("timecontrol") else None,
            f'[ChapterURL "https://lichess.org/study/x/ch{i:04d}"]',
            "",
            f"1. e4 e5 {result}",
        ])))
    return "\n\n".join(chunks) + "\n"


class TestTimeControlSummary:
    """Performance by time control (issue #17): does Daniel play better slow or fast?"""

    def test_groups_results_by_time_control(self):
        games, _ = load_games_from_text(_pgn_with_headers([
            {"timecontrol": "110+10", "result": "1-0"},
            {"timecontrol": "110+10", "result": "0-1"},
            {"timecontrol": "110+10", "result": "1/2-1/2"},
            {"timecontrol": "30+5", "result": "1-0"},
        ]), player_name="Me")
        tc = time_control_summary(games).set_index("TimeControl")
        slow = tc.loc["110+10"]
        assert (slow["Games"], slow["Win"], slow["Draw"], slow["Loss"]) == (3, 1, 1, 1)
        fast = tc.loc["30+5"]
        assert (fast["Games"], fast["Win"]) == (1, 1)

    def test_time_controls_classified_by_speed(self):
        """Real USCF header formats are read into a speed class: the multi-stage
        '40/80, SD30; +30' is Classical, 'G/30;d5'-style action chess is Rapid."""
        games, _ = load_games_from_text(_pgn_with_headers([
            {"timecontrol": "40/80, SD30; +30"},   # 80+30 min + 30s inc
            {"timecontrol": "110+10"},             # 110 min + 10s inc
            {"timecontrol": "60+5d"},              # 60 min + 5s delay
            {"timecontrol": "30+5"},               # 30 min + 5s inc
            {"timecontrol": "G/5;d0"},             # 5 min blitz
        ]), player_name="Me")
        tc = time_control_summary(games).set_index("TimeControl")
        assert tc.loc["40/80, SD30; +30", "Speed"] == "Classical"
        assert tc.loc["110+10", "Speed"] == "Classical"
        assert tc.loc["60+5d", "Speed"] == "Classical"
        assert tc.loc["30+5", "Speed"] == "Rapid"
        assert tc.loc["G/5;d0", "Speed"] == "Blitz"

    def test_sorted_slowest_first(self):
        games, _ = load_games_from_text(_pgn_with_headers([
            {"timecontrol": "30+5"},
            {"timecontrol": "40/80, SD30; +30"},
            {"timecontrol": "110+10"},
        ]), player_name="Me")
        tc = time_control_summary(games)
        assert list(tc["TimeControl"]) == ["40/80, SD30; +30", "110+10", "30+5"]

    def test_sudden_death_written_with_a_slash_still_parses(self):
        """USCF TLAs write sudden death both ways: 'SD30' and 'SD/30'."""
        games, _ = load_games_from_text(_pgn_with_headers([
            {"timecontrol": "40/100, SD/30"},   # slash form
            {"timecontrol": "SD/90"},           # sudden death only
        ]), player_name="Me")
        tc = time_control_summary(games).set_index("TimeControl")
        assert tc.loc["40/100, SD/30", "Speed"] == "Classical"   # 130 min
        assert tc.loc["SD/90", "Speed"] == "Classical"           # 90 min

    def test_games_without_a_time_control_header_sort_last_as_unknown(self):
        """The fixture games (no TimeControl header) still count — grouped under
        an empty label, classified Unknown, after every real time control."""
        games, _ = load_games_from_text(_pgn_with_headers([
            {"timecontrol": "30+5"},
            {},                         # no TimeControl header at all
        ]), player_name="Me")
        tc = time_control_summary(games)
        assert len(tc) == 2
        assert list(tc["Speed"]) == ["Rapid", "Unknown"]
        assert tc["Games"].sum() == 2

    def test_empty_data(self):
        tc = time_control_summary(pd.DataFrame())
        assert tc.empty
        assert "Speed" in tc.columns  # chart code can rely on the shape


class TestRoundPerformance:
    """Performance by round number (issue #17): late-round fatigue detection."""

    def test_results_grouped_by_round_number(self, df):
        # Fixture rounds: R1 = W+W, R2 = D+W, R3 = L+W, R4 = D
        rounds = round_performance(df).set_index("Round")
        r1 = rounds.loc[1]
        assert (r1["Games"], r1["Win"], r1["Draw"], r1["Loss"]) == (2, 2, 0, 0)
        r3 = rounds.loc[3]
        assert (r3["Games"], r3["Win"], r3["Loss"]) == (2, 1, 1)
        r4 = rounds.loc[4]
        assert (r4["Games"], r4["Draw"]) == (1, 1)

    def test_rounds_sort_numerically_not_lexically(self):
        """Round 10 comes after round 2 — the pre-existing lexical-sort bug."""
        games, _ = load_games_from_text(_pgn_with_headers([
            {"round": "10"}, {"round": "1"}, {"round": "2"},
        ]), player_name="Me")
        rounds = round_performance(games)
        assert list(rounds["Round"]) == [1, 2, 10]

    def test_thin_rounds_are_marked_unreliable(self, df):
        """One game in round 4 proves nothing — the chart needs to know which
        rounds have enough data to support a conclusion."""
        rounds = round_performance(df, min_games=2).set_index("Round")
        assert bool(rounds.loc[1, "Reliable"]) is True   # 2 games
        assert bool(rounds.loc[4, "Reliable"]) is False  # 1 game

    def test_score_pct_counts_draws_as_half(self, df):
        """Fatigue shows up as draws too, so the metric is score%, not just wins."""
        rounds = round_performance(df).set_index("Round")
        assert rounds.loc[1, "ScorePct"] == 100.0  # W + W
        assert rounds.loc[2, "ScorePct"] == 75.0   # D + W
        assert rounds.loc[3, "ScorePct"] == 50.0   # L + W
        assert rounds.loc[4, "ScorePct"] == 50.0   # D

    def test_games_without_a_round_are_excluded(self):
        games, _ = load_games_from_text(_pgn_with_headers([
            {"round": "?"}, {"round": "1"},
        ]), player_name="Me")
        rounds = round_performance(games)
        assert list(rounds["Round"]) == [1]

    def test_real_uscf_rounds_take_precedence_over_typed_rounds(self):
        """Issue #34: Daniel hand-types continuous ladder rounds (24, 25, …);
        the crosstable knows they were really rounds 1, 3, … of the Rated
        Event.  When the real round is attached, fatigue analytics use it."""
        games, _ = load_games_from_text(_pgn_with_headers([
            {"round": "24", "result": "1-0"},
            {"round": "25", "result": "0-1"},
        ]), player_name="Me")
        games["UscfRound"] = [1.0, 3.0]      # what the crosstable says

        rounds = round_performance(games)

        assert list(rounds["Round"]) == [1, 3]    # real rounds, not 24/25

    def test_games_without_a_real_round_fall_back_to_the_typed_one(self):
        """Mixed data degrades per Game (ADR 0003): a Game whose crosstable
        isn't cached keeps its typed round."""
        games, _ = load_games_from_text(_pgn_with_headers([
            {"round": "24", "result": "1-0"},
            {"round": "2", "result": "0-1"},
        ]), player_name="Me")
        games["UscfRound"] = [1.0, float("nan")]

        rounds = round_performance(games)

        assert list(rounds["Round"]) == [1, 2]    # real where known, typed where not

    def test_empty_data(self):
        rounds = round_performance(pd.DataFrame())
        assert rounds.empty
        assert "Reliable" in rounds.columns


class TestUpsetTracker:
    """Giant kills and upset losses (issue #17), ranked by rating margin."""

    def test_wins_against_higher_rated_opponents_are_upsets(self, df):
        # Fixture upset wins: game 1 (1800 beats 1920) and game 4 (1810 beats 1930)
        upsets = upset_tracker(df)
        assert len(upsets["wins"]) == 2
        for win in upsets["wins"]:
            assert win["Opponent"] == "Opponent A"
            assert win["Margin"] == 120

    def test_losses_to_lower_rated_opponents_are_upsets(self):
        games, _ = load_games_from_text(_pgn_with_headers([
            {"result": "0-1", "my_elo": 1800, "opp_elo": 1650, "opponent": "Lucky"},
            {"result": "0-1", "my_elo": 1800, "opp_elo": 1900, "opponent": "Stronger"},
        ]), player_name="Me")
        upsets = upset_tracker(games)
        # Losing to a 1900 as an 1800 is expected — only the 1650 loss stings
        assert [loss["Opponent"] for loss in upsets["losses"]] == ["Lucky"]
        assert upsets["losses"][0]["Margin"] == 150

    def test_expected_results_are_not_upsets(self, df):
        """Beating lower-rated players and losing to higher-rated ones is normal;
        draws never count."""
        upsets = upset_tracker(df)
        all_rows = upsets["wins"] + upsets["losses"]
        # Fixture games 3 (loss to 2050), 5 (win vs 1600), 6 (win vs 1760),
        # and both draws must be absent
        opponents = {row["Opponent"] for row in all_rows}
        assert opponents == {"Opponent A"}
        assert len(all_rows) == 2

    def test_ranked_by_rating_margin(self):
        games, _ = load_games_from_text(_pgn_with_headers([
            {"result": "1-0", "my_elo": 1500, "opp_elo": 1600, "opponent": "Small"},
            {"result": "1-0", "my_elo": 1500, "opp_elo": 1900, "opponent": "Giant"},
            {"result": "1-0", "my_elo": 1500, "opp_elo": 1700, "opponent": "Medium"},
            {"result": "0-1", "my_elo": 1500, "opp_elo": 1400, "opponent": "Ouch"},
            {"result": "0-1", "my_elo": 1500, "opp_elo": 1200, "opponent": "Disaster"},
        ]), player_name="Me")
        upsets = upset_tracker(games)
        assert [w["Opponent"] for w in upsets["wins"]] == ["Giant", "Medium", "Small"]
        assert [w["Margin"] for w in upsets["wins"]] == [400, 200, 100]
        assert [loss["Opponent"] for loss in upsets["losses"]] == ["Disaster", "Ouch"]

    def test_rows_link_to_their_games(self, df):
        upsets = upset_tracker(df)
        for row in upsets["wins"]:
            assert row["ChapterURL"].startswith("https://lichess.org/study/")

    def test_unrated_games_are_ignored(self):
        pgn = """\
[Event "T"]
[Site "S"]
[Date "2024.03.01"]
[White "Me"]
[Black "Other"]
[Result "1-0"]

1. e4 1-0
"""
        games, _ = load_games_from_text(pgn, player_name="Me")
        upsets = upset_tracker(games)
        assert upsets == {"wins": [], "losses": []}

    def test_empty_data(self):
        assert upset_tracker(pd.DataFrame()) == {"wins": [], "losses": []}

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


# ---------------------------------------------------------------------------
# Forfeit exclusion (issue #29)
#
# A forfeit win is not chess: Games whose Forfeit column is True are excluded
# from win rate, Streak math, and opening/repertoire stats — but they stay in
# the Games list and count toward event scores.  The Forfeit column is set by
# uscf_core.enrich_games; these tests exercise the contract that pgn stats
# honor it (and keep working for dataframes that don't have it).
# ---------------------------------------------------------------------------

FORFEIT_PGN = """\
[Event "Club Ladder"]
[Site "Springfield"]
[Date "2024.03.01"]
[Round "1"]
[White "Test Player"]
[Black "Opp One"]
[WhiteElo "1500"]
[BlackElo "1480"]
[ECO "C50"]
[Opening "Italian Game"]
[Result "1-0"]
[Termination "win by resignation"]
[ChapterURL "https://lichess.org/study/forfstudy/real0001"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 1-0

[Event "Club Ladder"]
[Site "Springfield"]
[Date "2024.03.08"]
[Round "2"]
[White "Opp Two"]
[Black "Test Player"]
[WhiteElo "1520"]
[BlackElo "1500"]
[ECO "B12"]
[Opening "Caro-Kann Defense"]
[Result "1-0"]
[Termination "loss by resignation"]
[ChapterURL "https://lichess.org/study/forfstudy/real0002"]

1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nf3 e6 1-0

[Event "Club Ladder"]
[Site "Springfield"]
[Date "2024.03.15"]
[Round "3"]
[White "Test Player"]
[Black "Opp Three"]
[WhiteElo "1500"]
[BlackElo "1510"]
[ECO "B00"]
[Opening "King's Pawn Game"]
[Result "1-0"]
[Termination "win by forfeit"]
[ChapterURL "https://lichess.org/study/forfstudy/forf0003"]

1. e4 1-0
"""


def _df_with_forfeit() -> pd.DataFrame:
    """A real Win, a real Loss, and a 1-move forfeit Win — with the Forfeit
    column set the way uscf_core.enrich_games sets it."""
    frame, _ = load_games_from_text(FORFEIT_PGN, player_name="Test Player")
    frame["Forfeit"] = frame["FullMoves"] <= 1
    return frame


class TestForfeitExclusion:
    def test_win_rate_excludes_forfeits(self):
        """The forfeit 'Win' is not a win: 1 real win, 1 real loss → 50%."""
        counts = win_draw_loss_counts(_df_with_forfeit())

        assert counts["Win"] == 1
        assert counts["Loss"] == 1

    def test_streaks_exclude_forfeits(self):
        """The games run Win, Loss, forfeit-Win: without the forfeit the most
        recent real game is the Loss — there is no current 'win streak'."""
        s = streaks(_df_with_forfeit())

        assert s["current_streak_outcome"] == "Loss"
        assert s["longest_streak_wins_only"] == 1
        assert s["last_20"] == ["Win", "Loss"]

    def test_current_form_excludes_forfeits(self):
        """The header form dots show real games only — a no-show win never
        lights the streak fire."""
        form = current_form(_df_with_forfeit())

        assert form["win_streak"] == 0
        assert form["last_5"] == ["Win", "Loss"]

    def test_kpi_win_pct_excludes_forfeits_but_total_games_keeps_them(self):
        """Win % is about chess played (1 win / 2 real games = 50%), while the
        game count reflects the archive (3 Games — Forfeits stay visible)."""
        k = kpi_stats(_df_with_forfeit())

        assert k["total_games"] == 3
        assert k["win_pct"] == 50.0
        assert k["longest_win_streak"] == 1

    def test_kpi_favorite_opening_ignores_forfeits(self):
        """Even when forfeits outnumber real games of an opening, that opening
        never becomes the 'favourite' — one forced move is not repertoire."""
        frame = _df_with_forfeit()
        # A second forfeit with the same opening: now 'King's Pawn Game' has
        # the most rows but zero real games
        frame = pd.concat([frame, frame[frame["Forfeit"]]], ignore_index=True)

        k = kpi_stats(frame)

        assert k["favorite_opening"] != "King's Pawn Game"

    def test_win_rate_over_time_excludes_forfeits(self):
        """The cumulative win-rate line is about real games: 2 points, ending
        at 50%, not 3 points ending at 66.7%."""
        timeline = win_rate_over_time(_df_with_forfeit())

        assert int(timeline["CumGames"].iloc[-1]) == 2
        assert float(timeline["WinRate"].iloc[-1]) == 50.0

    def test_opening_stats_exclude_forfeits(self):
        """The forfeit's opening (one forced move) is not repertoire data."""
        _families, openings = opening_summary(_df_with_forfeit())

        assert "King's Pawn Game" not in set(openings["Opening"])
        assert int(openings["Games"].sum()) == 2

    def test_repertoire_tree_excludes_forfeits(self):
        """As White, only the real Italian Game appears in the tree — the
        forfeit's lone 1. e4 is not a repertoire branch."""
        tree = repertoire_tree(_df_with_forfeit(), "White", min_games=1)

        assert tree["games"] == 1
        assert len(tree["moves"]) == 1

    def test_event_scores_keep_forfeits(self):
        """A forfeit win is a real tournament point: the Club Ladder score is
        2/3, exactly as the wallchart says."""
        events = event_summary(_df_with_forfeit())
        ladder = events[events["Event"] == "Club Ladder"].iloc[0]

        assert ladder["Games"] == 3
        assert ladder["Score"] == "2/3"

    def test_a_df_without_the_forfeit_column_is_unchanged(self):
        """Raw parser output (no enrichment) behaves exactly as before —
        nothing is excluded when there is nothing marking Forfeits."""
        frame, _ = load_games_from_text(FORFEIT_PGN, player_name="Test Player")

        counts = win_draw_loss_counts(frame)

        assert counts["Win"] == 2  # the forfeit counts: nothing says it's one
