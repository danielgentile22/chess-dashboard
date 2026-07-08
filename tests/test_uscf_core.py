"""
tests/test_uscf_core.py
=======================
Tests for the pure USCF interpretation layer (uscf_core.py): raw MUIR API
responses in → typed records, rating series, and Game matches out.

No HTTP, no Dash — everything runs on real captured response shapes
(tests/data/uscf/) plus inline variants for edge cases.  Matching tests
build the Games side through the real PGN parser (load_games_from_text) so
the engine always sees exactly what a Sync produces.
"""
from __future__ import annotations

import itertools
from datetime import date
from types import SimpleNamespace

import pytest

import uscf_core
from pgn_stats_core import load_games_from_text

# ---------------------------------------------------------------------------
# Matching-test builders: compact specs → real parsed Games / raw USCF items
# ---------------------------------------------------------------------------

_CHAPTER_IDS = itertools.count(1)


def chapter(opponent="Bob Baker", opponent_id="20000056", color="White",
            result="1-0", date="2026.05.01", event="ACC Friday Ladder",
            moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6", player_rating="1470",
            opponent_rating="1465"):
    """One Game as Study-export PGN: Test Player vs *opponent*."""
    n = next(_CHAPTER_IDS)
    me, my_id = "Test Player", "99999999"
    if color == "White":
        white, black, white_id, black_id = me, opponent, my_id, opponent_id
        white_elo, black_elo = player_rating, opponent_rating
    else:
        white, black, white_id, black_id = opponent, me, opponent_id, my_id
        white_elo, black_elo = opponent_rating, player_rating

    headers = [
        f'[Event "{event}"]', f'[Date "{date}"]',
        f'[White "{white}"]', f'[Black "{black}"]', f'[Result "{result}"]',
        f'[WhiteElo "{white_elo}"]', f'[BlackElo "{black_elo}"]',
        f'[ChapterURL "https://lichess.org/study/matchtest/chap{n:04d}"]',
    ]
    # A chapter missing the opponent's FideId omits the header entirely
    # (exactly what Lichess exports when Daniel didn't type one).
    if white_id:
        headers.append(f'[WhiteFideId "{white_id}"]')
    if black_id:
        headers.append(f'[BlackFideId "{black_id}"]')
    return "\n".join(headers) + f"\n\n{moves} {result}\n"


def games_df(*chapters):
    """Parse chapter() texts into a Games DataFrame via the real PGN parser."""
    df, _ = load_games_from_text("\n\n".join(chapters), player_name="Test Player")
    return df


def uscf_game(opponent_id="20000056", opponent_first="BOB", opponent_last="BAKER",
              player_color="White", player_outcome="Win",
              event="ACC MAY 2026", event_id="202605290393",
              start="2026-05-01", end="2026-05-29",
              section="LADDER", rating_system="R"):
    """One raw USCF Game Record item (the real games-endpoint shape)."""
    opponent_color = "Black" if player_color == "White" else "White"
    opponent_outcome = {"Win": "Loss", "Loss": "Win", "Draw": "Draw"}[player_outcome]
    return {
        "section": {"id": "x", "number": 1, "name": section},
        "event": {"id": event_id, "name": event,
                  "startDate": start, "endDate": end, "stateCode": "VA"},
        "ratingSystem": rating_system,
        "player": {"color": player_color, "outcome": player_outcome},
        "opponent": {"id": opponent_id, "firstName": opponent_first,
                     "lastName": opponent_last, "stateRep": "VA",
                     "color": opponent_color, "outcome": opponent_outcome},
    }

# ---------------------------------------------------------------------------
# Member profile parsing (issue #25)
# ---------------------------------------------------------------------------

class TestParseMemberProfile:
    def test_parses_the_real_profile(self, uscf_profile_json):
        profile = uscf_core.parse_member_profile(uscf_profile_json)

        assert profile.member_id == "12345678"
        assert profile.name == "Daniel Gentile"
        assert profile.state == "VA"
        assert profile.national_rank == 11719
        assert profile.state_rank == 356
        assert profile.membership_status == "Active"
        assert profile.membership_expires == date(2028, 7, 31)

    def test_established_rating_with_floor(self, uscf_profile_json):
        """Daniel's Regular rating: established at 1545 with a 1300 floor."""
        profile = uscf_core.parse_member_profile(uscf_profile_json)
        regular = profile.rating("R")

        assert regular is not None
        assert regular.rating == 1545
        assert regular.is_provisional is False
        assert regular.floor == 1300

    def test_provisional_ratings_carry_game_counts(self, uscf_profile_json):
        """Quick (17 games) and Online-Regular (11 games) are provisional."""
        profile = uscf_core.parse_member_profile(uscf_profile_json)

        quick = profile.rating("Q")
        assert quick.rating == 1092
        assert quick.is_provisional is True
        assert quick.games_played == 17

        online_regular = profile.rating("OR")
        assert online_regular.rating == 1336
        assert online_regular.is_provisional is True
        assert online_regular.games_played == 11

    def test_unrated_systems_are_tolerated(self, uscf_profile_json):
        """Blitz / Online-Quick / Online-Blitz have no `rating` key at all —
        the parser must treat them as unrated, not crash (handoff API note)."""
        profile = uscf_core.parse_member_profile(uscf_profile_json)

        for system in ("B", "OQ", "OB"):
            entry = profile.rating(system)
            assert entry is None or entry.rating is None

    def test_unknown_system_means_unrated(self, uscf_profile_json):
        """Asking for a system that isn't in the response is 'unrated', not an error."""
        profile = uscf_core.parse_member_profile(uscf_profile_json)
        assert profile.rating("NOSUCH") is None


# ---------------------------------------------------------------------------
# Membership expiration warning (issue #25)
#
# Exercised with fixture variants: Daniel renewed during planning (now
# 2028-07-31), so live data never triggers the warning.
# ---------------------------------------------------------------------------

def _profile_expiring(uscf_profile_json, expiration: str | None, status: str = "Active"):
    """The real profile with a different membership expiration."""
    raw = dict(uscf_profile_json, status=status)
    if expiration is None:
        raw.pop("expirationDate", None)
    else:
        raw["expirationDate"] = expiration
    return uscf_core.parse_member_profile(raw)


class TestMembershipAlert:
    TODAY = date(2026, 6, 2)

    def test_no_alert_when_expiration_is_far_away(self, uscf_profile_json):
        """The real profile (expires 2028-07-31) needs no warning."""
        profile = uscf_core.parse_member_profile(uscf_profile_json)
        assert uscf_core.membership_alert(profile, today=self.TODAY) is None

    def test_alert_when_expiring_within_90_days(self, uscf_profile_json):
        """The exact situation planning caught: weeks away from a tournament-day surprise."""
        profile = _profile_expiring(uscf_profile_json, "2026-06-30")
        alert = uscf_core.membership_alert(profile, today=self.TODAY)

        assert alert is not None
        assert "2026-06-30" in alert    # says when
        assert "28" in alert            # and how soon (days left)

    def test_no_alert_just_outside_90_days(self, uscf_profile_json):
        """91 days out is not yet a warning — the boundary is 90."""
        profile = _profile_expiring(uscf_profile_json, "2026-09-01")  # 91 days
        assert uscf_core.membership_alert(profile, today=self.TODAY) is None

    def test_alert_when_lapsed(self, uscf_profile_json):
        """An expired membership reads 'lapsed', not 'expires in -30 days'."""
        profile = _profile_expiring(uscf_profile_json, "2026-05-01")
        alert = uscf_core.membership_alert(profile, today=self.TODAY)

        assert alert is not None
        assert "lapsed" in alert.lower()
        assert "2026-05-01" in alert

    def test_no_expiration_date_means_no_alert(self, uscf_profile_json):
        """Can't warn about a date USCF didn't provide."""
        profile = _profile_expiring(uscf_profile_json, None)
        assert uscf_core.membership_alert(profile, today=self.TODAY) is None


# ---------------------------------------------------------------------------
# The Official Rating series (issue #27)
#
# One integer per supplement month, starting at the first supplement —
# earlier months have no official value and must never be invented.
# ---------------------------------------------------------------------------

class TestBuildOfficialSeries:
    def test_one_point_per_supplement_month(self, uscf_supplements_json):
        series = uscf_core.build_official_series(uscf_supplements_json["items"])
        assert len(series) == 10  # Sept 2025 through June 2026

    def test_chronological_starting_at_the_first_supplement(self, uscf_supplements_json):
        series = uscf_core.build_official_series(uscf_supplements_json["items"])

        months = [p.month for p in series]
        assert months == sorted(months)            # oldest first
        assert months[0] == date(2025, 9, 1)       # the first supplement, nothing before
        assert months[-1] == date(2026, 6, 1)

    def test_values_are_the_published_integers(self, uscf_supplements_json):
        """The Official Rating is the supplement's integer, exactly as published."""
        series = uscf_core.build_official_series(uscf_supplements_json["items"])
        by_month = {p.month: p.rating for p in series}

        assert by_month[date(2025, 9, 1)] == 1038   # the first supplement
        assert by_month[date(2026, 5, 1)] == 1470   # the lag the PRD talks about...
        assert by_month[date(2026, 6, 1)] == 1545   # ...still visible in June

    def test_gap_months_are_not_invented(self, uscf_supplements_json):
        """A month with no supplement published has no point — no interpolation."""
        items = [
            item for item in uscf_supplements_json["items"]
            if item["ratingSupplementDate"] != "2026-03-01"
        ]
        series = uscf_core.build_official_series(items)

        months = [p.month for p in series]
        assert date(2026, 3, 1) not in months
        assert len(series) == 9

    def test_supplement_without_regular_rating_contributes_no_point(self):
        """A member rated only in Quick that month: no Regular point to plot."""
        items = [
            {"ratingSupplementDate": "2025-08-01",
             "ratings": [{"source": "Q", "rating": 1014, "provisionalGameCount": 4}]},
            {"ratingSupplementDate": "2025-09-01",
             "ratings": [{"source": "R", "rating": 1038}]},
        ]
        series = uscf_core.build_official_series(items)

        assert len(series) == 1
        assert series[0].month == date(2025, 9, 1)
        assert series[0].rating == 1038


# ---------------------------------------------------------------------------
# USCF Game Records (issue #28)
#
# Raw /members/{id}/games items → typed records the matching engine consumes.
# ---------------------------------------------------------------------------

class TestBuildGameRecords:
    def test_parses_the_real_games_response(self, uscf_games_json):
        records = uscf_core.build_game_records(uscf_games_json["items"])

        assert len(records) == 63
        # The most recent game: a win with White against BOB BAKER
        first = records[0]
        assert first.opponent_id == "20000056"
        assert first.opponent_name == "BOB BAKER"
        assert first.player_color == "White"
        assert first.player_outcome == "Win"
        assert first.event_name == "ACC MAY 2026"
        assert first.section_name == "LADDER"
        assert first.rating_system == "R"
        assert first.event_start == date(2026, 5, 1)
        assert first.event_end == date(2026, 5, 29)


# ---------------------------------------------------------------------------
# The Live Rating series (issue #27)
#
# One pre→post pair per Regular-rated Section, decimals preserved,
# chronological. The chain is continuous: each Section's post-rating IS the
# next Section's pre-rating.
# ---------------------------------------------------------------------------

class TestBuildLiveSeries:
    def test_the_chain_is_continuous(self, uscf_sections_json):
        """THE property of the Live series: every Section starts exactly where
        the previous one ended. Asserted across Daniel's entire real career."""
        series = uscf_core.build_live_series(uscf_sections_json["items"])

        assert len(series) > 1
        for prev, curr in zip(series, series[1:]):
            assert curr.pre == prev.post, (
                f"chain broken at {curr.event_name} ({curr.end_date}): "
                f"pre {curr.pre} != previous post {prev.post}"
            )

    def test_chronological_by_event_end_date(self, uscf_sections_json):
        series = uscf_core.build_live_series(uscf_sections_json["items"])
        end_dates = [p.end_date for p in series]
        assert end_dates == sorted(end_dates)

    def test_only_the_regular_chain_is_included(self, uscf_sections_json):
        """24 real Sections: 23 count toward Regular (22 R + 1 OR excluded + 3 dual-rated)."""
        series = uscf_core.build_live_series(uscf_sections_json["items"])

        # The one Online-Regular Section (DMVCHESS WEDNESDAY CLASS) is excluded
        assert len(series) == 23
        assert not any("DMVCHESS.COM" in p.event_name for p in series)

    def test_decimals_are_preserved(self, uscf_sections_json):
        """The Live Rating carries decimals — 1570.72, never rounded to 1571."""
        series = uscf_core.build_live_series(uscf_sections_json["items"])

        current = series[-1]
        assert current.event_name == "ACC MAY 2026"
        assert current.pre == 1544.47
        assert current.post == 1570.72

    def test_dual_rated_sections_count_toward_the_regular_chain(self, uscf_sections_json):
        """A dual-rated (D) Section moves the Regular chain with its R record;
        its Quick record is ignored entirely."""
        series = uscf_core.build_live_series(uscf_sections_json["items"])
        thanksgiving = next(p for p in series if "Thankgiving" in p.event_name)

        assert thanksgiving.pre == 1155.4      # the R record...
        assert thanksgiving.post == 1229.8
        # ...the Q record's values (1013.59 → 1091.53) appear nowhere in the chain
        all_values = {p.pre for p in series} | {p.post for p in series}
        assert 1013.59 not in all_values
        assert 1091.53 not in all_values

    def test_first_ever_section_has_no_pre_rating(self, uscf_sections_json):
        """Daniel's first rated event: there was no rating before it."""
        series = uscf_core.build_live_series(uscf_sections_json["items"])
        first = series[0]

        assert first.event_name == "ACC JUNE 2025"
        assert first.pre is None
        assert first.post == 695.23

    def test_zero_change_section_is_included(self, uscf_sections_json):
        """Entered but played no rated games (Rockville): the Section still
        exists, with pre == post, and must not break the chain."""
        series = uscf_core.build_live_series(uscf_sections_json["items"])
        rockville = next(p for p in series if "ROCKVILLE" in p.event_name)

        assert rockville.pre == rockville.post == 1015.16

    def test_two_sections_of_one_event_are_ordered_by_the_chain(self, uscf_sections_json):
        """DMV All Ages: Daniel played two Sections ending the same day. The
        pre/post values say which was rated first — the order must honor that,
        not the API's response order (which has them backwards)."""
        series = uscf_core.build_live_series(uscf_sections_json["items"])
        dmv = [p for p in series if "DMV Chess Second Annual" in p.event_name]

        assert len(dmv) == 2
        assert dmv[0].section_name == "Under 1800"               # rated first
        assert dmv[1].section_name == "Extra games - Classical"  # rated second
        assert dmv[1].pre == dmv[0].post                          # and they chain


# ---------------------------------------------------------------------------
# The dual-line rating trend (issue #31)
#
# rating_trend_series feeds the Trends chart: both rating series, trimmed to
# the global date-range filter.  The chart is the one place the lens hides
# nothing — both lines always render.
# ---------------------------------------------------------------------------

class TestRatingTrendSeries:
    def test_without_a_date_range_both_series_pass_through_whole(
        self, uscf_supplements_json, uscf_sections_json
    ):
        """The tracer bullet: no date filter → the chart gets Daniel's whole
        career, both series untouched."""
        official = uscf_core.build_official_series(uscf_supplements_json["items"])
        live = uscf_core.build_live_series(uscf_sections_json["items"])

        trend_official, trend_live = uscf_core.rating_trend_series(official, live)

        assert trend_official == official    # all 10 supplement points
        assert trend_live == live            # all 23 chain points

    def test_the_date_filter_trims_both_series(
        self, uscf_supplements_json, uscf_sections_json
    ):
        """The chart respects the global date filter (issue #31).  Dash sends
        the range as ISO strings; Q1 2026 keeps 3 supplements and 7 Sections."""
        official = uscf_core.build_official_series(uscf_supplements_json["items"])
        live = uscf_core.build_live_series(uscf_sections_json["items"])

        trend_official, trend_live = uscf_core.rating_trend_series(
            official, live, date_start="2026-01-01", date_end="2026-03-31",
        )

        assert [p.month for p in trend_official] == [
            date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
        ]
        # Newcomb, ACC Jan, ACC Feb, Army-Navy, ACC March, both DMV Sections
        assert len(trend_live) == 7
        assert all(date(2026, 1, 1) <= p.end_date <= date(2026, 3, 31)
                   for p in trend_live)

    def test_a_one_sided_range_trims_only_that_side(
        self, uscf_supplements_json, uscf_sections_json
    ):
        """The date picker often has only one side set — the other side stays open."""
        official = uscf_core.build_official_series(uscf_supplements_json["items"])
        live = uscf_core.build_live_series(uscf_sections_json["items"])

        trend_official, trend_live = uscf_core.rating_trend_series(
            official, live, date_start="2026-04-01",
        )

        # April, May, June supplements; ACC Aprril + ACC MAY Sections
        assert [p.rating for p in trend_official] == [1440, 1470, 1545]
        assert [p.event_name for p in trend_live] == ["ACC Aprril 2026", "ACC MAY 2026"]

    def test_datetime_range_bounds_work_like_dates(
        self, uscf_supplements_json, uscf_sections_json
    ):
        """A datetime is-a date in Python, so it must be accepted — and not
        blow up when compared against the series' plain dates."""
        from datetime import datetime

        official = uscf_core.build_official_series(uscf_supplements_json["items"])
        live = uscf_core.build_live_series(uscf_sections_json["items"])

        trend_official, _trend_live = uscf_core.rating_trend_series(
            official, live, date_start=datetime(2026, 4, 1, 10, 30),
        )

        assert [p.rating for p in trend_official] == [1440, 1470, 1545]

    def test_the_divergence_the_chart_exists_to_show(
        self, uscf_supplements_json, uscf_sections_json
    ):
        """The payoff (PRD #24): the June supplement (1545) reflects ACC April;
        ACC May missed its cutoff — so Official and Live visibly diverge today."""
        trend_official, trend_live = uscf_core.rating_trend_series(
            uscf_core.build_official_series(uscf_supplements_json["items"]),
            uscf_core.build_live_series(uscf_sections_json["items"]),
        )

        assert trend_official[-1].rating == 1545
        assert trend_live[-1].post == 1570.72


# ---------------------------------------------------------------------------
# The supplement ↔ chain property (issue #31)
#
# The two series describe one career, so they must agree: every published
# supplement value is (within USCF's own rounding) the post-rating of the
# last Section rated before that supplement's cutoff.  A free cross-series
# regression guard — it breaks loudly if either builder picks wrong fields
# or orders the chain wrong.
# ---------------------------------------------------------------------------

class TestSupplementChainProperty:
    def test_every_supplement_matches_an_earlier_live_post_rating(
        self, uscf_supplements_json, uscf_sections_json
    ):
        official = uscf_core.build_official_series(uscf_supplements_json["items"])
        live = uscf_core.build_live_series(uscf_sections_json["items"])

        for supplement in official:
            earlier_posts = [p.post for p in live if p.end_date < supplement.month]
            assert earlier_posts, f"no Sections rated before {supplement.month}"
            closest = min(abs(supplement.rating - post) for post in earlier_posts)
            assert closest <= 1.0, (
                f"the {supplement.month} supplement ({supplement.rating}) matches "
                f"no earlier Section's post-rating (closest is {closest} away)"
            )


# ---------------------------------------------------------------------------
# The matching engine — primary pass: opponent ID + result (issue #28)
# ---------------------------------------------------------------------------

class TestMatchGamesById:
    def test_a_game_record_matches_its_game_by_opponent_id_and_result(self):
        """The tracer bullet: one Game, one USCF Game Record, same opponent
        member ID, same result → matched."""
        df = games_df(chapter(opponent="Bob Baker", opponent_id="20000056",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1
        match = result.matches[0]
        assert match.chapter_url == df.iloc[0]["ChapterURL"]
        assert match.record.event_name == "ACC MAY 2026"
        assert match.matched_by == "id"
        # Nothing left over on either side
        assert result.unmatched_chapter_urls == ()
        assert result.unmatched_records == ()

    def test_result_disagreement_prevents_a_match(self):
        """Same opponent, but the chapter says Win and USCF says Loss — these
        cannot be the same game.  Both sides stay unmatched (Reconciliation's
        problem, not the matcher's)."""
        df = games_df(chapter(opponent_id="20000056", color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_outcome="Loss"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()
        assert result.unmatched_chapter_urls == (df.iloc[0]["ChapterURL"],)
        assert len(result.unmatched_records) == 1

    def test_chapter_without_fide_id_never_matches_by_id(self):
        """A chapter where Daniel never typed the opponent's member ID cannot
        match by ID — only the name-fallback pass (issue #29) can claim it,
        and it says so."""
        df = games_df(chapter(opponent="Vera Clark", opponent_id="",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000009", opponent_first="VERA",
                      opponent_last="CLARK", player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert all(m.matched_by == "name" for m in result.matches)

    def test_two_missing_ids_never_match_each_other(self):
        """'' == '' is not an ID match — absence of data is not a key.
        (Different names, so the name fallback can't claim them either.)"""
        df = games_df(chapter(opponent="Bob Baker", opponent_id="",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="", opponent_first="MARY",
                      opponent_last="DIFFERENT", player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()


class TestRepeatOpponentDisambiguation:
    """
    Issue #28: repeat opponents with identical results disambiguate via color
    and the Rated Event date window — tiebreakers, never match requirements.
    """

    def test_color_disambiguates_two_wins_against_the_same_opponent(self):
        """The real Foster case: Daniel beat the same opponent twice in the same
        monthly Rated Event — once as Black, once as White.  Only color says
        which USCF record belongs to which chapter."""
        df = games_df(
            chapter(opponent="Alice Baker", opponent_id="20000133",
                    color="Black", result="0-1", date="2025.12.05"),   # Win as Black
            chapter(opponent="Alice Baker", opponent_id="20000133",
                    color="White", result="1-0", date="2025.12.26"),   # Win as White
        )
        december = dict(opponent_id="20000133", opponent_first="Wyatt",
                        opponent_last="Baker", player_outcome="Win",
                        event="ACC DECEMBER 2025", event_id="202512260263",
                        start="2025-12-05", end="2025-12-26")
        records = uscf_core.build_game_records([
            uscf_game(**december, player_color="White"),
            uscf_game(**december, player_color="Black"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 2
        record_for = {m.chapter_url: m.record for m in result.matches}
        black_game, white_game = df.iloc[0]["ChapterURL"], df.iloc[1]["ChapterURL"]
        assert record_for[black_game].player_color == "Black"
        assert record_for[white_game].player_color == "White"

    def test_date_window_disambiguates_when_color_cannot(self):
        """Two wins against the same opponent with the same color, months apart
        in different Rated Events — the chapter date falls inside exactly one
        event's window."""
        df = games_df(
            chapter(opponent_id="20000035", color="White", result="1-0",
                    date="2025.10.04"),
            chapter(opponent_id="20000035", color="White", result="1-0",
                    date="2025.12.14"),
        )
        hiban = dict(opponent_id="20000035", opponent_first="Michael Thomas",
                     opponent_last="Hiban", player_color="White", player_outcome="Win")
        records = uscf_core.build_game_records([
            uscf_game(**hiban, event="SECOND ANNUAL FEDERAL OPEN",
                      event_id="202510054832", start="2025-10-03", end="2025-10-05"),
            uscf_game(**hiban, event="First Annual Oak Grove Open",
                      event_id="202512140213", start="2025-12-12", end="2025-12-14"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 2
        event_for = {m.chapter_url: m.record.event_name for m in result.matches}
        assert event_for[df.iloc[0]["ChapterURL"]] == "SECOND ANNUAL FEDERAL OPEN"
        assert event_for[df.iloc[1]["ChapterURL"]] == "First Annual Oak Grove Open"

    def test_one_record_for_two_chapters_goes_to_the_better_fit(self):
        """Daniel played the opponent twice (same result) but USCF rated only
        one of the games: the record attaches to the chapter whose color and
        date agree; the other chapter stays unmatched."""
        df = games_df(
            chapter(opponent_id="20000133", color="Black", result="0-1",
                    date="2025.12.05"),
            chapter(opponent_id="20000133", color="White", result="1-0",
                    date="2026.02.06"),
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000133", player_color="White",
                      player_outcome="Win", event="ACC FEBRUARY 2026",
                      start="2026-02-06", end="2026-02-27"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1
        assert result.matches[0].chapter_url == df.iloc[1]["ChapterURL"]
        assert result.unmatched_chapter_urls == (df.iloc[0]["ChapterURL"],)


class TestMatchingPolicies:
    def test_color_disagreement_does_not_prevent_a_match(self):
        """The synthetic Davis case: the chapter says Daniel played Black, USCF
        says White.  Color is itself a fact that can conflict between sources —
        it is never a match requirement (PRD #24)."""
        df = games_df(chapter(opponent="Liam Davis", opponent_id="20000164",
                              color="Black", result="1/2-1/2", date="2026.02.20"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000164", opponent_first="Liam",
                      opponent_last="Davis", player_color="White",
                      player_outcome="Draw", event="ACC FEBRUARY 2026",
                      start="2026-02-20", end="2026-02-27"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1
        match = result.matches[0]
        # The match carries both colors so Reconciliation can flag the conflict
        assert match.record.player_color == "White"
        assert df.iloc[0]["Color"] == "Black"

    def test_online_rated_records_never_match_chapters(self):
        """The Study is OTB-only by design (PRD #24): an online-rated (OR)
        record never becomes a Game, even when opponent and result line up.
        It surfaces in Reconciliation as a skippable USCF-only item."""
        df = games_df(chapter(opponent="Carter Harris", opponent_id="20000166",
                              color="Black", result="0-1"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000166", opponent_first="Carter",
                      opponent_last="Harris", player_color="Black",
                      player_outcome="Win", rating_system="OR"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()
        assert len(result.unmatched_records) == 1
        assert result.unmatched_records[0].rating_system == "OR"

    def test_dual_rated_records_match_like_regular_ones(self):
        """Dual-rated (D) Sections are over-the-board games — they match
        exactly like Regular (R) ones (the real Thanksgiving Open case)."""
        df = games_df(chapter(opponent="Wyatt Garcia", opponent_id="20000042",
                              color="Black", result="0-1", date="2025.11.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000042", opponent_first="Vignesh",
                      opponent_last="Garcia", player_color="Black",
                      player_outcome="Win", rating_system="D",
                      event="2nd Annual Thankgiving Day Open",
                      start="2025-10-31", end="2025-11-02"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1


# ---------------------------------------------------------------------------
# The matching engine — fallback pass: normalized name + result + date window
# (issue #29).  Only for chapters without a typed opponent FideId.
# ---------------------------------------------------------------------------

class TestMatchGamesByName:
    def test_a_chapter_without_fide_id_matches_by_name_result_and_window(self):
        """The fallback pass: same opponent name, same result, the chapter's
        date inside the Rated Event's window → matched (marked as a name
        match so it can be eyeballed)."""
        df = games_df(chapter(opponent="Jonah Baker", opponent_id="",
                              color="White", result="1-0", date="2026.04.17"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000051", opponent_first="Jonah",
                      opponent_last="Baker", player_color="Black",
                      player_outcome="Win", event="ACC Aprril 2026",
                      start="2026-04-03", end="2026-04-24"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1
        assert result.matches[0].matched_by == "name"
        assert result.matches[0].record.opponent_id == "20000051"

    def test_name_matching_ignores_case(self):
        """The synthetic Baker case: USCF registers 'BOB BAKER', the chapter
        says 'Bob Baker'."""
        df = games_df(chapter(opponent="Bob Baker", opponent_id="",
                              color="White", result="1-0", date="2026.05.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", opponent_first="BOB",
                      opponent_last="BAKER", player_color="White",
                      player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1

    def test_name_matching_ignores_punctuation(self):
        """The real Williams case: 'Vera Clark' (chapter) vs
        'VERA' + 'CLARK' (USCF) — the middle-initial dot must not matter."""
        df = games_df(chapter(opponent="Vera Clark", opponent_id="",
                              color="White", result="1-0", date="2026.04.03"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000009", opponent_first="VERA",
                      opponent_last="CLARK", player_color="White",
                      player_outcome="Win", event="ACC Aprril 2026",
                      start="2026-04-03", end="2026-04-24"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1

    def test_first_name_spelling_variant_with_exact_last_name(self):
        """The synthetic Clark case: Daniel typed 'Carter', USCF has 'Carver'.
        Last name matches exactly + same first initial → still a match."""
        df = games_df(chapter(opponent="Carter Clark", opponent_id="",
                              color="Black", result="1-0", date="2026.05.22"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000144", opponent_first="Carver",
                      opponent_last="Clark", player_color="Black",
                      player_outcome="Loss"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1
        assert result.matches[0].matched_by == "name"

    def test_a_different_last_name_never_matches(self):
        """Spelling tolerance never crosses last names: 'Carter Clark'
        is not 'Carter Kaplan'."""
        df = games_df(chapter(opponent="Carter Clark", opponent_id="",
                              color="Black", result="1-0", date="2026.05.22"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="11111111", opponent_first="Carter",
                      opponent_last="Kaplan", player_color="Black",
                      player_outcome="Loss"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()

    def test_name_match_requires_the_date_window(self):
        """The same opponent name + result in an event months away is a
        different game — the window is part of the fallback key, so that the
        weaker name key can never reach across events."""
        df = games_df(chapter(opponent="Bob Baker", opponent_id="",
                              color="White", result="1-0", date="2026.05.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", opponent_first="BOB",
                      opponent_last="BAKER", player_color="White",
                      player_outcome="Win", event="ACC JANUARY 2026",
                      start="2026-01-02", end="2026-01-30"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()

    def test_ambiguous_name_candidates_match_nothing(self):
        """Two records with the same name, result, and window: a guess could
        attach the wrong Rated Event to the Game — no match, not a guess
        (both go to Reconciliation instead)."""
        df = games_df(chapter(opponent="John Smith", opponent_id="",
                              color="White", result="1-0", date="2026.05.10"))
        smith = dict(opponent_first="John", opponent_last="Smith",
                     player_color="White", player_outcome="Win",
                     start="2026-05-01", end="2026-05-29")
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="22222221", event="EVENT ONE",
                      event_id="1", **smith),
            uscf_game(opponent_id="22222222", event="EVENT TWO",
                      event_id="2", **smith),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()
        assert len(result.unmatched_records) == 2

    def test_chapters_with_typed_ids_never_fall_back_to_names(self):
        """A chapter whose typed FideId matched nothing stays unmatched — the
        wrong ID is a discrepancy to surface (Reconciliation), not to paper
        over with a name guess."""
        df = games_df(chapter(opponent="Bob Baker", opponent_id="99999990",
                              color="White", result="1-0", date="2026.05.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", opponent_first="BOB",
                      opponent_last="BAKER", player_color="White",
                      player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()


# ---------------------------------------------------------------------------
# The matching engine against the real fixture pair: Daniel's full Study
# (63 chapters) ↔ his full USCF record (63 USCF Game Records), captured the
# same day (2026-06-02).  This is the engine's ground truth.
# ---------------------------------------------------------------------------

class TestMatchingAgainstRealData:
    def test_the_id_pass_matches_55_of_63_games(
        self, study_snapshot_df, uscf_games_json
    ):
        """Every chapter with a typed FideId whose USCF record exists matches:
        54 with full agreement + 1 with a color conflict (Davis)."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        id_matches = [m for m in result.matches if m.matched_by == "id"]
        assert len(id_matches) == 55

    def test_the_name_pass_matches_the_seven_id_less_chapters(
        self, study_snapshot_df, uscf_games_json
    ):
        """The 7 chapters Daniel never typed FideIds into (Apr–May 2026) all
        match by name — including the Williams punctuation case and the
        Carter/Carver spelling variant."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        name_matches = [m for m in result.matches if m.matched_by == "name"]
        assert len(name_matches) == 7
        matched_opponents = {m.record.opponent_name for m in name_matches}
        assert "VERA CLARK" in matched_opponents     # punctuation + case
        assert "Carver Clark" in matched_opponents    # spelling variant

    def test_both_passes_together_match_62_of_63_games(
        self, study_snapshot_df, uscf_games_json
    ):
        """The full engine on the full real career: 62 of 63 chapters match.
        The only unmatched chapter is the Forfeit (Uma Baker no-show — USCF
        correctly never rated it)."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        assert len(result.matches) == 62
        unmatched = study_snapshot_df[
            study_snapshot_df["ChapterURL"].isin(result.unmatched_chapter_urls)
        ]
        assert list(unmatched["Opponent"]) == ["Uma Baker"]

    def test_unmatched_records_are_exactly_the_online_game(
        self, study_snapshot_df, uscf_games_json
    ):
        """The online-rated (OR) game Daniel deliberately keeps out of his OTB
        Study is the only USCF Game Record with no Game — exposed, never
        silently dropped."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        assert len(result.unmatched_records) == 1
        assert result.unmatched_records[0].rating_system == "OR"
        assert result.unmatched_records[0].opponent_name == "Carter Harris"

    def test_every_match_agrees_on_opponent_and_result(
        self, study_snapshot_df, uscf_games_json
    ):
        """No false matches by construction: every matched pair agrees on the
        opponent's member ID and the result, across the entire real career."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)
        games_by_url = {
            game["ChapterURL"]: game for _, game in study_snapshot_df.iterrows()
        }

        for match in result.matches:
            game = games_by_url[match.chapter_url]
            if match.matched_by == "id":
                opponent_id = (game["BlackID"] if game["Color"] == "White"
                               else game["WhiteID"])
                assert match.record.opponent_id == opponent_id
            assert match.record.player_outcome == game["Outcome"]


# ---------------------------------------------------------------------------
# Enrichment: matched Games gain their USCF Game Record facts as columns
# (issue #28) — "match & enrich", the Game stays the central entity (ADR 0003)
# ---------------------------------------------------------------------------

class TestEnrichGames:
    def _enriched_pair(self):
        """One matched Game + one unmatched Game, enriched."""
        df = games_df(
            chapter(opponent="Bob Baker", opponent_id="20000056",
                    color="White", result="1-0"),
            chapter(opponent="Nobody USCF Knows", opponent_id="11111111",
                    color="Black", result="0-1"),
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win"),
        ])
        return uscf_core.enrich_games(df, uscf_core.match_games(df, records)), df

    def test_matched_games_gain_their_uscf_facts(self):
        enriched, _ = self._enriched_pair()
        game = enriched.iloc[0]

        assert bool(game["UscfMatched"]) is True
        assert game["UscfMatchedBy"] == "id"
        assert game["UscfEventName"] == "ACC MAY 2026"
        assert game["UscfSection"] == "LADDER"
        assert game["UscfRatingSystem"] == "R"
        assert game["UscfOpponentName"] == "BOB BAKER"
        assert game["UscfOpponentId"] == "20000056"

    def test_unmatched_games_carry_empty_enrichment(self):
        """Unmatched Games keep working everywhere — enrichment is additive,
        never a filter (ADR 0003)."""
        enriched, _ = self._enriched_pair()
        game = enriched.iloc[1]

        assert bool(game["UscfMatched"]) is False
        assert game["UscfMatchedBy"] == ""
        assert game["UscfEventName"] == ""
        assert game["UscfOpponentId"] == ""

    def test_matched_games_carry_their_rated_event_id(self):
        """The Rated Event ID (issue #33): names are ambiguous and carry USCF's
        own typos — grouping and standings URLs need the ID."""
        enriched, _ = self._enriched_pair()

        assert enriched.iloc[0]["UscfEventId"] == "202605290393"   # matched
        assert enriched.iloc[1]["UscfEventId"] == ""               # unmatched

    def test_the_input_df_is_never_mutated(self):
        """Pages read the store concurrently — enrichment returns a copy."""
        _, original = self._enriched_pair()
        assert "UscfMatched" not in original.columns

    def test_an_empty_match_result_still_adds_the_columns(self):
        """USCF down / not configured: the columns exist (all unmatched) so
        pages never need to check for their presence."""
        df = games_df(chapter())
        empty = uscf_core.match_games(df, [])

        enriched = uscf_core.enrich_games(df, empty)

        assert "UscfMatched" in enriched.columns
        assert not enriched["UscfMatched"].any()

    def test_an_empty_df_is_enriched_harmlessly(self):
        """A Lichess-cache boot with zero games must not crash enrichment."""
        import pandas as pd

        enriched = uscf_core.enrich_games(
            pd.DataFrame(), uscf_core.match_games(pd.DataFrame(), [])
        )
        assert enriched.empty

    def test_color_conflicts_are_flagged_on_the_matched_game(self):
        """The synthetic Davis case (issue #30): chapter says Black, USCF says
        White.  The Game stays matched and displays the Lichess version — the
        disagreement is flagged, never hidden."""
        df = games_df(
            chapter(opponent="Liam Davis", opponent_id="20000164",
                    color="Black", result="1/2-1/2"),       # conflicted
            chapter(opponent="Bob Baker", opponent_id="20000056",
                    color="White", result="1-0"),           # clean
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000164", opponent_first="Liam",
                      opponent_last="Davis", player_color="White",
                      player_outcome="Draw"),
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win"),
        ])

        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, records))

        assert bool(enriched.iloc[0]["UscfColorConflict"]) is True
        assert bool(enriched.iloc[1]["UscfColorConflict"]) is False
        # Lichess displays: the Color column itself is untouched
        assert enriched.iloc[0]["Color"] == "Black"


# ---------------------------------------------------------------------------
# Forfeit detection (issue #29)
#
# A Game with no USCF Game Record after both passes AND at most one move is a
# Forfeit: the opponent never showed, so USCF correctly never rated it
# (CONTEXT.md).
# ---------------------------------------------------------------------------

class TestForfeitDetection:
    def test_unmatched_one_move_game_is_a_forfeit(self):
        """The real Uma Baker case: the chapter is literally '1. e4 1-0'."""
        df = games_df(chapter(opponent="Uma Baker", opponent_id="20000071",
                              color="White", result="1-0", moves="1. e4"))
        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, []))

        assert bool(enriched.iloc[0]["Forfeit"]) is True

    def test_unmatched_full_game_is_not_a_forfeit(self):
        """A real game USCF just hasn't rated yet is unmatched, not a Forfeit
        — it belongs in Reconciliation, not excluded from stats."""
        df = games_df(chapter(opponent_id="20000056", color="White", result="1-0",
                              moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6"))
        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, []))

        assert bool(enriched.iloc[0]["Forfeit"]) is False

    def test_matched_game_is_never_a_forfeit(self):
        """If USCF rated it, a game was played — however short the chapter."""
        df = games_df(chapter(opponent_id="20000056", color="White",
                              result="1-0", moves="1. e4"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win"),
        ])
        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, records))

        assert bool(enriched.iloc[0]["Forfeit"]) is False

    def test_the_real_career_has_exactly_one_forfeit(
        self, study_snapshot_df, uscf_games_json
    ):
        """Against the full fixture pair: only the Thanksgiving Open no-show."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        enriched = uscf_core.enrich_games(
            study_snapshot_df, uscf_core.match_games(study_snapshot_df, records)
        )

        forfeits = enriched[enriched["Forfeit"]]
        assert list(forfeits["Opponent"]) == ["Uma Baker"]


# ---------------------------------------------------------------------------
# Reconciliation (issue #30): every disagreement between the Studies and USCF
# becomes a visible, actionable entry.
# ---------------------------------------------------------------------------

def _reconcile(df, records, official_series=None, dismissed=frozenset()):
    """Run the full pipeline the way data.py does: match → enrich → reconcile."""
    result = uscf_core.match_games(df, records)
    enriched = uscf_core.enrich_games(df, result)
    return uscf_core.reconcile(
        enriched, result, official_series or [], dismissed=dismissed
    )


class TestReconcileConflicts:
    def test_a_color_conflict_becomes_an_entry_showing_both_versions(self):
        """The synthetic Davis case: matched, but the chapter says Black and
        USCF says White.  Both versions appear side by side."""
        df = games_df(chapter(opponent="Liam Davis", opponent_id="20000164",
                              color="Black", result="1/2-1/2", date="2026.02.20"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000164", opponent_first="Liam",
                      opponent_last="Davis", player_color="White",
                      player_outcome="Draw", event="ACC FEBRUARY 2026",
                      start="2026-02-20", end="2026-02-27"),
        ])

        entries = _reconcile(df, records)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.kind == "conflict"
        assert entry.opponent == "Liam Davis"
        assert "Black" in entry.lichess_says
        assert "White" in entry.uscf_says
        # The fix-on-Lichess action knows which chapter to open
        assert entry.chapter_url == df.iloc[0]["ChapterURL"]

    def test_a_clean_match_produces_no_entry(self):
        """Agreement is silence — Reconciliation only lists disagreements."""
        df = games_df(chapter(opponent="Bob Baker", opponent_id="20000056",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win"),
        ])

        assert _reconcile(df, records) == []


class TestReconcileUnmatched:
    def test_a_uscf_only_record_becomes_an_entry(self):
        """The real online-game case: USCF rated it, but Daniel deliberately
        never added a Chapter.  The entry offers Skip (dismiss)."""
        df = games_df(chapter(opponent="Bob Baker", opponent_id="20000056",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win"),
            uscf_game(opponent_id="20000166", opponent_first="Carter",
                      opponent_last="Harris", player_color="Black",
                      player_outcome="Win", rating_system="OR",
                      event="DMVCHESS.COM JANUARY CLIMB", event_id="202601300323",
                      start="2026-01-01", end="2026-01-30"),
        ])

        entries = _reconcile(df, records)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.kind == "uscf_only"
        assert entry.opponent == "Carter Harris"
        assert entry.lichess_says == ""             # there is no chapter
        assert "DMVCHESS.COM" in entry.uscf_says
        assert entry.chapter_url == ""              # nothing to link to

    def test_an_unmatched_real_game_becomes_a_lichess_only_entry(self):
        """A Game USCF hasn't rated (or rated under a different account):
        visible here so it isn't silently un-enriched forever."""
        df = games_df(chapter(opponent="Jane Newplayer", opponent_id="77777777",
                              color="White", result="1-0", date="2026.05.20",
                              moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6"))

        entries = _reconcile(df, [])

        assert len(entries) == 1
        entry = entries[0]
        assert entry.kind == "lichess_only"
        assert entry.opponent == "Jane Newplayer"
        assert entry.uscf_says == ""                # USCF has no record
        assert entry.chapter_url == df.iloc[0]["ChapterURL"]

    def test_forfeits_are_not_lichess_only_entries(self):
        """The Forfeit is already explained (opponent no-show, never rated) —
        listing it as a discrepancy would be noise."""
        df = games_df(chapter(opponent="Uma Baker", opponent_id="20000071",
                              color="White", result="1-0", moves="1. e4"))

        entries = _reconcile(df, [])

        assert entries == []


class TestReconcileMissingFideIds:
    def test_a_chapter_without_an_opponent_id_becomes_an_entry(self):
        """Even when the name fallback matched it, the chapter is listed so
        Daniel can type the FideId in and make the match robust."""
        df = games_df(chapter(opponent="Bob Baker", opponent_id="",
                              color="White", result="1-0", date="2026.05.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", opponent_first="BOB",
                      opponent_last="BAKER", player_color="White",
                      player_outcome="Win"),
        ])

        entries = _reconcile(df, records)

        missing = [e for e in entries if e.kind == "missing_fide_id"]
        assert len(missing) == 1
        assert missing[0].opponent == "Bob Baker"
        assert missing[0].chapter_url == df.iloc[0]["ChapterURL"]
        # The matched record tells Daniel exactly which ID to type in
        assert "20000056" in missing[0].uscf_says

    def test_chapters_with_ids_are_not_listed(self):
        df = games_df(chapter(opponent_id="20000056", color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win"),
        ])

        entries = _reconcile(df, records)

        assert [e for e in entries if e.kind == "missing_fide_id"] == []


class TestReconcileRatingMismatches:
    def _records(self):
        return uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win", event="ACC MAY 2026",
                      start="2026-05-01", end="2026-05-29"),
        ])

    def _official(self):
        """An Official series where May 2026 = 1470."""
        return uscf_core.build_official_series([
            {"ratingSupplementDate": "2026-05-01",
             "ratings": [{"source": "R", "rating": 1470}]},
        ])

    def test_typed_rating_disagreeing_with_official_becomes_an_entry(self):
        """The real case: Daniel typed 1440 on a May chapter; the May
        supplement says 1470."""
        df = games_df(chapter(opponent_id="20000056", color="White",
                              result="1-0", date="2026.05.01",
                              player_rating="1440"))

        entries = uscf_core.reconcile(
            uscf_core.enrich_games(df, uscf_core.match_games(df, self._records())),
            uscf_core.match_games(df, self._records()),
            self._official(),
        )

        mismatches = [e for e in entries if e.kind == "rating_mismatch"]
        assert len(mismatches) == 1
        assert "1440" in mismatches[0].lichess_says
        assert "1470" in mismatches[0].uscf_says

    def test_agreeing_typed_rating_produces_no_entry(self):
        df = games_df(chapter(opponent_id="20000056", color="White",
                              result="1-0", date="2026.05.01",
                              player_rating="1470"))
        result = uscf_core.match_games(df, self._records())

        entries = uscf_core.reconcile(
            uscf_core.enrich_games(df, result), result, self._official(),
        )

        assert [e for e in entries if e.kind == "rating_mismatch"] == []

    def test_no_official_rating_for_that_month_means_no_check(self):
        """Months before the first supplement have no official value — typed
        ratings there are unverifiable, not wrong."""
        df = games_df(chapter(opponent_id="20000056", color="White",
                              result="1-0", date="2026.05.01",
                              player_rating="1440"))
        result = uscf_core.match_games(df, self._records())

        entries = uscf_core.reconcile(
            uscf_core.enrich_games(df, result), result, [],  # no supplements at all
        )

        assert [e for e in entries if e.kind == "rating_mismatch"] == []

    def test_unmatched_games_have_no_rating_check(self):
        """Without a match there is no Rated Event, hence no month to check
        the typed rating against."""
        df = games_df(chapter(opponent_id="20000056", color="White",
                              result="1-0", date="2026.05.01",
                              player_rating="1440",
                              moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6"))

        entries = uscf_core.reconcile(
            uscf_core.enrich_games(df, uscf_core.match_games(df, [])),
            uscf_core.match_games(df, []),
            self._official(),
        )

        assert [e for e in entries if e.kind == "rating_mismatch"] == []


class TestReconcileDismissals:
    def test_dismissed_entries_disappear(self):
        """A dismissal ('USCF is wrong' / 'intentionally skipped') removes the
        entry; everything else stays."""
        df = games_df(
            chapter(opponent="Liam Davis", opponent_id="20000164",
                    color="Black", result="1/2-1/2"),
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000164", opponent_first="Liam",
                      opponent_last="Davis", player_color="White",
                      player_outcome="Draw"),
            uscf_game(opponent_id="20000166", opponent_first="Carter",
                      opponent_last="Harris", player_color="Black",
                      player_outcome="Win", rating_system="OR",
                      event="ONLINE LADDER", event_id="999"),
        ])
        open_entries = _reconcile(df, records)
        assert {e.kind for e in open_entries} == {"conflict", "uscf_only"}

        conflict_id = next(e.entry_id for e in open_entries if e.kind == "conflict")
        remaining = _reconcile(df, records, dismissed={conflict_id})

        assert {e.kind for e in remaining} == {"uscf_only"}

    def test_entry_ids_are_stable_across_syncs(self):
        """Dismissals persist by entry_id — the same disagreement must produce
        the same id on every Sync, or dismissals would resurrect."""
        df = games_df(chapter(opponent="Liam Davis", opponent_id="20000164",
                              color="Black", result="1/2-1/2"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000164", opponent_first="Liam",
                      opponent_last="Davis", player_color="White",
                      player_outcome="Draw"),
        ])

        first = _reconcile(df, records)
        second = _reconcile(df, records)

        assert [e.entry_id for e in first] == [e.entry_id for e in second]


class TestReconcileAgainstRealData:
    """The full Reconciliation ground truth for the captured fixture pair.

    Note: planning (PRD #24 / issue #30) predicted 2 color conflicts; the
    captured data actually contains 3 — the Baker April chapter (chapter says
    White, USCF says Black) was missed by the planning experiment.  The data
    is the authority; the discrepancy is flagged for Daniel in the phase PR.
    """

    def _entries(self, study_snapshot_df, uscf_games_json, uscf_supplements_json):
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)
        enriched = uscf_core.enrich_games(study_snapshot_df, result)
        official = uscf_core.build_official_series(uscf_supplements_json["items"])
        return uscf_core.reconcile(enriched, result, official)

    def test_three_color_conflicts(
        self, study_snapshot_df, uscf_games_json, uscf_supplements_json
    ):
        entries = self._entries(study_snapshot_df, uscf_games_json,
                                uscf_supplements_json)
        conflicts = [e for e in entries if e.kind == "conflict"]

        assert {e.opponent for e in conflicts} == {
            "Liam Davis",     # Feb 2026 — known at planning time
            "Jonah Baker",     # Apr 2026 — found by this engine
            "Bob Harris",      # May 2026 — known at planning time
        }

    def test_one_uscf_only_entry_the_online_game(
        self, study_snapshot_df, uscf_games_json, uscf_supplements_json
    ):
        entries = self._entries(study_snapshot_df, uscf_games_json,
                                uscf_supplements_json)
        uscf_only = [e for e in entries if e.kind == "uscf_only"]

        assert len(uscf_only) == 1
        assert uscf_only[0].opponent == "Carter Harris"

    def test_no_lichess_only_entries(
        self, study_snapshot_df, uscf_games_json, uscf_supplements_json
    ):
        """Every real game is matched; the only unmatched chapter is the
        Forfeit, which is not a discrepancy."""
        entries = self._entries(study_snapshot_df, uscf_games_json,
                                uscf_supplements_json)

        assert [e for e in entries if e.kind == "lichess_only"] == []

    def test_seven_missing_fide_id_entries(
        self, study_snapshot_df, uscf_games_json, uscf_supplements_json
    ):
        entries = self._entries(study_snapshot_df, uscf_games_json,
                                uscf_supplements_json)
        missing = [e for e in entries if e.kind == "missing_fide_id"]

        assert len(missing) == 7
        # Every one tells Daniel the exact ID to type in (they all matched by name)
        assert all("type that ID" in e.uscf_says for e in missing)

    def test_one_rating_mismatch_typed_1440_official_1470(
        self, study_snapshot_df, uscf_games_json, uscf_supplements_json
    ):
        entries = self._entries(study_snapshot_df, uscf_games_json,
                                uscf_supplements_json)
        mismatches = [e for e in entries if e.kind == "rating_mismatch"]

        assert len(mismatches) == 1
        assert "1440" in mismatches[0].lichess_says
        assert "1470" in mismatches[0].uscf_says
        assert mismatches[0].opponent == "Bob Baker"


# ---------------------------------------------------------------------------
# Edge cases found by the Phase B self-review
# ---------------------------------------------------------------------------

class TestMatchingEdgeCases:
    def test_games_without_chapter_urls_are_invisible_to_matching(self):
        """A Game with no ChapterURL has no identity (ADR 0001): it can never
        be matched, and it is not reported as an unmatched leftover either —
        there is nothing to link a Reconciliation entry to."""
        no_url_chapter = (
            '[Event "Test"]\n[Date "2026.05.01"]\n'
            '[White "Test Player"]\n[Black "Bob Baker"]\n[Result "1-0"]\n'
            '[WhiteFideId "99999999"]\n[BlackFideId "20000056"]\n'
            "\n1. e4 e5 2. Nf3 1-0\n"
        )
        df = games_df(no_url_chapter)
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()
        assert result.unmatched_chapter_urls == ()        # no identity → not listed
        assert len(result.unmatched_records) == 1          # the record IS listed

    def test_record_without_a_color_never_flags_a_conflict(self):
        """A USCF record missing player.color (API quirk) is not a color
        disagreement — '' is absence of data, not a color."""
        df = games_df(chapter(opponent_id="20000056", color="White", result="1-0"))
        raw = uscf_game(opponent_id="20000056", player_outcome="Win")
        raw["player"].pop("color")

        records = uscf_core.build_game_records([raw])
        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, records))

        assert bool(enriched.iloc[0]["UscfMatched"]) is True
        assert bool(enriched.iloc[0]["UscfColorConflict"]) is False


class TestReconcileEdgeCases:
    def test_identical_uscf_only_records_get_distinct_entry_ids(self):
        """Two unmatched records against the same opponent in the same event
        with the same color and result (a double round-robin) must get
        distinct entry_ids — dismissing one never dismisses the other, and
        the page never renders duplicate component ids."""

        same = dict(opponent_id="22222222", opponent_first="Round", opponent_last="Robin",
                    player_color="White", player_outcome="Win",
                    event="DOUBLE RR", event_id="777", start="2026-05-01",
                    end="2026-05-29")
        records = uscf_core.build_game_records([uscf_game(**same), uscf_game(**same)])
        empty_df = games_df(chapter(opponent_id="11111111", color="White",
                                    result="1-0"))

        result = uscf_core.match_games(empty_df, records)
        enriched = uscf_core.enrich_games(empty_df, result)
        entries = uscf_core.reconcile(enriched, result, [])

        uscf_only_ids = [e.entry_id for e in entries if e.kind == "uscf_only"]
        assert len(uscf_only_ids) == 2
        assert len(set(uscf_only_ids)) == 2, "entry_ids must be unique"

    def test_rating_mismatch_found_even_when_supplement_is_not_dated_the_first(self):
        """The Official series is keyed by month, not by exact date — a
        supplement dated mid-month still covers its month."""
        df = games_df(chapter(opponent_id="20000056", color="White",
                              result="1-0", date="2026.05.01",
                              player_rating="1440"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win", event="ACC MAY 2026",
                      start="2026-05-01", end="2026-05-29"),
        ])
        official = uscf_core.build_official_series([
            {"ratingSupplementDate": "2026-05-15",   # not the 1st
             "ratings": [{"source": "R", "rating": 1470}]},
        ])
        result = uscf_core.match_games(df, records)

        entries = uscf_core.reconcile(uscf_core.enrich_games(df, result), result, official)

        mismatches = [e for e in entries if e.kind == "rating_mismatch"]
        assert len(mismatches) == 1



# ---------------------------------------------------------------------------
# Member events → typed Rated Events (issue #33)
# ---------------------------------------------------------------------------

class TestBuildMemberEvents:
    def test_parses_the_real_events_chronologically(self, uscf_events_json):
        """All 23 Rated Events Daniel entered, oldest first (the API sends
        newest first — the timeline wants chronological)."""
        events = uscf_core.build_member_events(uscf_events_json["items"])

        assert len(events) == 23
        assert events[0].name == "ACC JUNE 2025"
        assert events[0].start_date == date(2025, 6, 28)
        assert events[-1].name == "ACC MAY 2026"
        assert events[-1].event_id == "202605290393"
        assert events[-1].end_date == date(2026, 5, 29)

    def test_events_carry_field_size_and_section_count(self, uscf_events_json):
        """The DMV All Ages event: 8 sections, 89 players — what the Events
        page shows about the field."""
        events = uscf_core.build_member_events(uscf_events_json["items"])
        dmv = next(e for e in events if e.event_id == "202603290543")

        assert dmv.section_count == 8
        assert dmv.player_count == 89
        assert dmv.city == "ARLINGTON" or dmv.city  # city is carried

    def test_tolerates_missing_fields(self):
        """The MUIR API is undocumented (ADR 0003): events with missing dates
        or counts still parse."""
        events = uscf_core.build_member_events([{"id": "x", "name": "Mystery Open"}])

        assert len(events) == 1
        assert events[0].name == "Mystery Open"
        assert events[0].start_date is None
        assert events[0].player_count is None

    def test_no_events_is_an_empty_list(self):
        assert uscf_core.build_member_events([]) == []


# ---------------------------------------------------------------------------
# Series → Rated Event grouping (issue #33): the Events page's data
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_series_inputs(study_snapshot_df, uscf_games_json, uscf_sections_json,
                       uscf_events_json):
    """The real fixture pair, enriched — series_summary's inputs."""
    records = uscf_core.build_game_records(uscf_games_json["items"])
    match = uscf_core.match_games(study_snapshot_df, records)
    return SimpleNamespace(
        df=uscf_core.enrich_games(study_snapshot_df, match),
        live=uscf_core.build_live_series(uscf_sections_json["items"]),
        events=uscf_core.build_member_events(uscf_events_json["items"]),
    )


def _series_named(summary, name):
    return next(s for s in summary if s["series"] == name)


class TestSeriesSummary:
    def test_the_club_ladder_is_one_series_with_twelve_rated_events(
        self, real_series_inputs
    ):
        """The tracer bullet (issue #33's own example): 'ACC Friday Ladder' is
        one Series containing its 12 monthly Rated Events, chronological."""
        summary = uscf_core.series_summary(
            real_series_inputs.df, real_series_inputs.live, real_series_inputs.events,
        )

        ladder = _series_named(summary, "ACC Friday Ladder")
        assert ladder["games"] == 27
        rated_events = ladder["rated_events"]
        assert len(rated_events) == 12
        assert rated_events[0]["name"] == "ACC JUNE 2025"
        assert rated_events[-1]["name"] == "ACC MAY 2026"
        assert rated_events[-1]["games"] == 4

    @pytest.fixture()
    def summary(self, real_series_inputs):
        return uscf_core.series_summary(
            real_series_inputs.df, real_series_inputs.live, real_series_inputs.events,
        )

    def test_a_weekend_tournament_is_a_single_rated_event_series(self, summary):
        """The Oak Grove Open: one Series containing exactly one Rated Event."""
        oak_grove = _series_named(summary, "1st Annual Oak Grove Open (U1400)")

        assert len(oak_grove["rated_events"]) == 1
        assert oak_grove["rated_events"][0]["name"] == "First Annual Oak Grove Open"

    def test_rated_events_carry_official_identity_and_live_rating_change(
        self, summary
    ):
        """Each Rated Event shows USCF's official name (typos included), dates,
        Section(s), score, game count, and the live pre → post (issue #33)."""
        thanksgiving = _series_named(summary, "2nd Annual Thanksgiving Open (U1600)")
        event = thanksgiving["rated_events"][0]

        assert event["name"] == "2nd Annual Thankgiving Day Open"  # USCF's own typo
        assert event["start"] == "2025-10-31"
        assert event["end"] == "2025-11-02"
        assert event["sections"] == ["U1600"]
        assert event["games"] == 4          # the forfeit is not one of its games
        assert event["win"] == 2 and event["draw"] == 1 and event["loss"] == 1
        assert event["score"] == 2.5
        assert event["pre"] == 1155.4       # raw decimals — display rounds
        assert event["post"] == 1229.8
        assert event["player_count"] == 73  # the field, from the events endpoint

    def test_the_first_ever_event_has_no_pre_rating(self, summary):
        """ACC JUNE 2025: Daniel walked in unrated — pre is None, never invented."""
        ladder = _series_named(summary, "ACC Friday Ladder")
        first = ladder["rated_events"][0]

        assert first["pre"] is None
        assert first["post"] == 695.23

    def test_a_rated_event_with_two_played_sections_shows_both(self, summary):
        """The DMV All Ages case (issue #33): one Rated Event, two Sections
        played — both listed, the rating change spanning both in chain order."""
        adults_only = _series_named(summary, "2nd Annual Adults Only (U1800)")
        event = adults_only["rated_events"][0]

        assert event["sections"] == ["Extra games - Classical", "Under 1800"]
        assert event["pre"] == 1465.03      # walking into the Under 1800 Section
        assert event["post"] == 1470.23     # after the Extra-games Section

    def test_forfeits_stay_under_their_series_and_count_in_the_score(self, summary):
        """The Uma Baker no-show: not matched to any Rated Event, but it stays
        under its Series and its point counts toward the tournament score."""
        thanksgiving = _series_named(summary, "2nd Annual Thanksgiving Open (U1600)")

        assert thanksgiving["forfeits"] == 1
        assert len(thanksgiving["unmatched"]) == 1
        assert thanksgiving["unmatched"][0]["Opponent"] == "Uma Baker"
        # Series score: 2 wins + 0.5 draw + 1 forfeit win = 3.5 (the crosstable agrees)
        assert thanksgiving["score"] == 3.5
        # ...but the forfeit is never a "game" or a win
        assert thanksgiving["games"] == 4
        assert thanksgiving["win"] == 2

    def test_the_same_rated_event_can_appear_under_two_series(self, summary):
        """A real data quirk: Daniel typed two PGN Event names for the DMV All
        Ages tournament.  The Rated Event appears under both Series, each with
        its own games — the model tolerates it instead of merging or crashing."""
        a = _series_named(summary, "2nd Annual Adults Only (U1800)")
        b = _series_named(summary, "2nd Annual DMV Adults-Only (U1800)")

        assert a["rated_events"][0]["event_id"] == "202603290543"
        assert b["rated_events"][0]["event_id"] == "202603290543"
        assert a["rated_events"][0]["games"] == 3
        assert b["rated_events"][0]["games"] == 1

    def test_series_level_stats_roll_up_and_are_chronological(self, summary):
        """Series-level W/D/L, score, and Streaks roll up across Rated Events;
        Series are ordered by their first game."""
        ladder = _series_named(summary, "ACC Friday Ladder")

        assert ladder["games"] == sum(e["games"] for e in ladder["rated_events"])
        assert ladder["score"] == sum(e["score"] for e in ladder["rated_events"])
        assert ladder["win_streak"] >= 1
        # Chronological by first game date
        dates = [s["first_date"] for s in summary if s["first_date"]]
        assert dates == sorted(dates)

    def test_each_rated_event_carries_clickable_game_rows(self, summary):
        """Game rows carry ChapterURLs so the page can click through (issue #11)."""
        ladder = _series_named(summary, "ACC Friday Ladder")
        may = ladder["rated_events"][-1]

        assert len(may["rows"]) == 4
        assert all(row["ChapterURL"] for row in may["rows"])

    def test_game_rows_sort_by_round_numerically(self):
        """Round 10 belongs after round 2, not between 1 and 2 — the invariant
        the old Events detail panel enforced (issue #17's lexical-sort fix)."""
        df = games_df(
            chapter(opponent_id="20000056", result="1-0", date="2026.05.01"),
            chapter(opponent_id="20000061", result="1-0", date="2026.05.01"),
            chapter(opponent_id="20000082", result="1-0", date="2026.05.01"),
        )
        # All same-day games; hand-typed rounds 2, 10, 1 in chapter order
        df["Round"] = ["2", "10", "1"]
        df["RoundNum"] = [2.0, 10.0, 1.0]
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056"),
            uscf_game(opponent_id="20000061", opponent_first="WADE",
                      opponent_last="HARRIS"),
            uscf_game(opponent_id="20000082", opponent_first="ETHAN",
                      opponent_last="EDWARDS"),
        ])
        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, records))

        summary = uscf_core.series_summary(enriched, [], [])

        rows = summary[0]["rated_events"][0]["rows"]
        assert [row["RoundNum"] for row in rows] == [1.0, 2.0, 10.0]

    def test_works_on_a_df_without_enrichment_columns(self, study_snapshot_df):
        """The mid-Sync race: a filter callback can fire between the Lichess
        swap and USCF enrichment, handing series_summary a raw df with no
        enrichment columns.  Every Game is simply unmatched — never a KeyError
        (the same guard get_reconciliation has)."""
        summary = uscf_core.series_summary(study_snapshot_df, [], [])

        ladder = _series_named(summary, "ACC Friday Ladder")
        assert ladder["rated_events"] == []
        assert ladder["games"] == 27

    def test_a_filtered_df_filters_the_summary(self, real_series_inputs):
        """The summary is built from whatever Games it's given — global filters
        flow through by filtering the input df."""
        df_2026 = real_series_inputs.df[
            real_series_inputs.df["Date_dt"] >= "2026-01-01"
        ]
        summary = uscf_core.series_summary(
            df_2026, real_series_inputs.live, real_series_inputs.events,
        )

        ladder = _series_named(summary, "ACC Friday Ladder")
        assert {e["name"] for e in ladder["rated_events"]} == {
            "ACC January 2026", "ACC FEBRUARY 2026", "ACC March 2026",
            "ACC Aprril 2026", "ACC MAY 2026",
        }

    def test_an_empty_df_is_an_empty_summary(self, real_series_inputs):
        import pandas as pd

        assert uscf_core.series_summary(
            pd.DataFrame(), real_series_inputs.live, real_series_inputs.events,
        ) == []

    def test_works_without_uscf_data_at_all(self, study_snapshot_df):
        """ADR 0003: with USCF down/off, every Series still appears — just with
        no Rated Events inside (all Games unmatched)."""
        bare = uscf_core.enrich_games(
            study_snapshot_df, uscf_core.match_games(study_snapshot_df, []),
        )
        summary = uscf_core.series_summary(bare, [], [])

        ladder = _series_named(summary, "ACC Friday Ladder")
        assert ladder["rated_events"] == []
        assert ladder["games"] == 27          # the Games themselves are all there
        assert len(ladder["unmatched"]) == 27


class TestUnplayedEvents:
    def test_the_rockville_case(self, real_series_inputs):
        """Entered, never played (issue #33): Rockville has a Section record but
        zero Games.  The online-only DMVCHESS ladder also has no Games — its
        games are online-rated and never become Chapters by design."""
        unplayed = uscf_core.unplayed_events(
            real_series_inputs.df, real_series_inputs.events,
        )

        assert {e.name for e in unplayed} == {
            "ROCKVILLE ACTION TOURNAMENT",
            "DMVCHESS.COM JANUARY CLIMB THE RATING LADDER",
        }

    def test_a_date_filter_never_invents_unplayed_events(self, real_series_inputs):
        """Filtering to 2026 must NOT turn 2025's played events into 'never
        played' — unplayed is a fact about the full career."""
        unplayed = uscf_core.unplayed_events(
            real_series_inputs.df, real_series_inputs.events,
            date_start="2026-01-01", date_end="2026-12-31",
        )

        # Rockville (Aug 2025) falls outside the range → hidden; the DMVCHESS
        # ladder (Jan 2026) stays.  No 2025 played event sneaks in.
        assert {e.name for e in unplayed} == {
            "DMVCHESS.COM JANUARY CLIMB THE RATING LADDER",
        }

    def test_with_no_uscf_events_there_is_nothing_unplayed(self, study_snapshot_df):
        assert uscf_core.unplayed_events(study_snapshot_df, []) == []


# ---------------------------------------------------------------------------
# Standings → typed crosstables (issue #34)
# ---------------------------------------------------------------------------

class TestBuildStandings:
    def test_the_real_crosstable_parses_in_placement_order(self, uscf_standings_json):
        """ACC MAY 2026: 116 players, ordered by final placement."""
        standings = uscf_core.build_standings(
            uscf_standings_json[("202605290393", 1)]["items"])

        assert len(standings) == 116
        assert [s.ordinal for s in standings] == sorted(s.ordinal for s in standings)
        assert standings[0].ordinal == 1

    def test_daniels_row_carries_placement_score_and_ratings(
        self, uscf_standings_json
    ):
        """Daniel finished 5th of 116 with 3 points, 1544.47 → 1570.72."""
        standings = uscf_core.build_standings(
            uscf_standings_json[("202605290393", 1)]["items"])
        daniel = next(s for s in standings if s.member_id == "12345678")

        assert daniel.ordinal == 5
        assert daniel.name == "Daniel Gentile"
        assert daniel.score == 3
        assert daniel.pre_rating == 1544.47     # decimals kept in the data
        assert daniel.post_rating == 1570.72

    def test_round_outcomes_carry_real_round_numbers_and_opponents(
        self, uscf_standings_json
    ):
        """Daniel's ACC MAY rounds: played 1, 3, 4, 5 (unpaired in 2) — the
        real round numbers issue #34 attaches to Games."""
        standings = uscf_core.build_standings(
            uscf_standings_json[("202605290393", 1)]["items"])
        daniel = next(s for s in standings if s.member_id == "12345678")

        by_round = {r.round_number: r for r in daniel.rounds}
        assert by_round[1].outcome == "Win"
        assert by_round[1].opponent_member_id == "20000056"   # Baker
        assert by_round[2].outcome == "Unpaired"
        assert by_round[5].outcome == "Loss"
        assert by_round[5].color == "Black"
        assert by_round[5].opponent_member_id == "20000144"   # Clark

    def test_dual_rated_sections_use_the_regular_rating(self, uscf_standings_json):
        """The Thanksgiving Open was dual-rated: every player has Q and R
        records — Regular is the backbone (PRD #24).  Daniel's R record there
        (1155.4 → 1229.8) is exactly what the Live series chain says; his Q
        record (1013.59 → 1091.53) must never leak in."""
        standings = uscf_core.build_standings(
            uscf_standings_json[("202511020583", 3)]["items"])
        daniel = next(s for s in standings if s.member_id == "12345678")

        assert daniel.pre_rating == 1155.4      # the R record...
        assert daniel.post_rating == 1229.8
        assert daniel.pre_rating != 1013.59     # ...never the Q one

    def test_forfeit_and_bye_outcomes_parse(self, uscf_standings_json):
        """The crosstable encodes no-shows: Daniel's WinForfeit vs Uma Baker
        (Thanksgiving) and his own Forfeit at Rockville."""
        thanksgiving = uscf_core.build_standings(
            uscf_standings_json[("202511020583", 3)]["items"])
        daniel = next(s for s in thanksgiving if s.member_id == "12345678")
        forfeit_round = next(r for r in daniel.rounds if r.outcome == "WinForfeit")
        assert forfeit_round.round_number == 4
        assert forfeit_round.opponent_member_id == "20000071"   # Uma Baker

        rockville = uscf_core.build_standings(
            uscf_standings_json[("202508311982", 2)]["items"])
        daniel_rockville = next(s for s in rockville if s.member_id == "12345678")
        assert daniel_rockville.rounds[0].outcome == "Forfeit"   # he never showed
        assert daniel_rockville.score == 0

    def test_players_unrated_walking_in_have_no_pre_rating(
        self, uscf_standings_json
    ):
        """5 provisional players in ACC MAY had no pre-rating — None, never 0."""
        standings = uscf_core.build_standings(
            uscf_standings_json[("202605290393", 1)]["items"])
        unrated = [s for s in standings if s.pre_rating is None]

        assert len(unrated) == 5
        assert all(s.post_rating is not None for s in unrated)

    def test_an_empty_crosstable_is_an_empty_list(self):
        assert uscf_core.build_standings([]) == []


# ---------------------------------------------------------------------------
# Real round numbers from crosstables (issue #34)
# ---------------------------------------------------------------------------

# (event_id, section_number) → section name, for keying standings the way the
# Games know them (the UscfSection column carries names, not numbers).
_SECTION_NAMES = {
    ("202605290393", 1): "LADDER",
    ("202603290543", 2): "Under 1800",
    ("202603290543", 7): "Extra games - Classical",
    ("202508311982", 2): "UNDER 1500",
    ("202511020583", 3): "U1600",
}


@pytest.fixture(scope="module")
def real_standings(uscf_standings_json):
    """The captured crosstables as typed standings, keyed by (event_id, section name)."""
    return {
        (event_id, _SECTION_NAMES[(event_id, number)]):
            uscf_core.build_standings(raw["items"])
        for (event_id, number), raw in uscf_standings_json.items()
    }


@pytest.fixture(scope="module")
def real_enriched(study_snapshot_df, uscf_games_json):
    """The real career, matched and enriched."""
    records = uscf_core.build_game_records(uscf_games_json["items"])
    match = uscf_core.match_games(study_snapshot_df, records)
    return uscf_core.enrich_games(study_snapshot_df, match)


class TestAttachRoundNumbers:
    def test_ladder_games_get_their_real_rounds_not_the_typed_ones(
        self, real_enriched, real_standings
    ):
        """The whole point of issue #34: Daniel hand-types continuous ladder
        rounds (24, 25, 26, 27 for ACC MAY); USCF's crosstable says those were
        really rounds 1, 3, 4, 5 of that Rated Event."""
        attached = uscf_core.attach_round_numbers(
            real_enriched, real_standings, "12345678")

        may = attached[attached["UscfEventId"] == "202605290393"]
        assert list(may["RoundNum"]) == [24, 25, 26, 27]       # what Daniel typed
        assert list(may["UscfRound"]) == [1, 3, 4, 5]          # what USCF says

    def test_the_forfeit_gets_its_round_from_the_crosstable(
        self, real_enriched, real_standings
    ):
        """The Uma Baker no-show has no USCF Game Record, but the crosstable's
        WinForfeit round carries his member ID — so even the Forfeit gets its
        real round number."""
        attached = uscf_core.attach_round_numbers(
            real_enriched, real_standings, "12345678")

        forfeit = attached[attached["Forfeit"]]
        assert list(forfeit["UscfRound"]) == [4]

    def test_two_sections_of_one_event_attach_independently(
        self, real_enriched, real_standings
    ):
        """The DMV case: the Under 1800 games and the Extra-games game each get
        rounds from their own Section's crosstable."""
        attached = uscf_core.attach_round_numbers(
            real_enriched, real_standings, "12345678")

        dmv = attached[attached["UscfEventId"] == "202603290543"]
        by_opponent = dict(zip(dmv["Opponent"], dmv["UscfRound"], strict=True))
        assert by_opponent["Olivia Clark"] == 1       # Under 1800, round 1
        assert by_opponent["Vera Edwards"] == 2            # Under 1800, round 2
        assert by_opponent["Alice Anderson"] == 3            # Extra games, round 3
        assert by_opponent["Bob Baker"] == 4            # Under 1800, round 4

    def test_games_without_a_crosstable_have_no_real_round(
        self, real_enriched, real_standings
    ):
        """Only 5 crosstables are cached in the fixtures — every other Game
        keeps NaN, falling back to the typed round downstream."""
        import pandas as pd

        attached = uscf_core.attach_round_numbers(
            real_enriched, real_standings, "12345678")

        june = attached[attached["UscfEventId"] == "202506284842"]  # no crosstable
        assert pd.isna(june["UscfRound"]).all()

    def test_no_standings_at_all_still_adds_the_column(self, real_enriched):
        """ADR 0003: USCF down → the column exists (all NaN) so downstream
        code never checks for its presence."""
        import pandas as pd

        attached = uscf_core.attach_round_numbers(real_enriched, {}, "12345678")

        assert "UscfRound" in attached.columns
        assert pd.isna(attached["UscfRound"]).all()

    def test_the_input_df_is_never_mutated(self, real_enriched, real_standings):
        uscf_core.attach_round_numbers(real_enriched, real_standings, "12345678")
        assert "UscfRound" not in real_enriched.columns

    def test_repeat_opponent_same_outcome_games_get_distinct_rounds(self):
        """Beating the same opponent twice in one Section (both Wins): each
        Game gets its own round, never the same round twice."""
        df = games_df(
            chapter(opponent_id="20000056", result="1-0", date="2026.05.01"),
            chapter(opponent_id="20000056", result="1-0", date="2026.05.22"),
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_outcome="Win"),
            uscf_game(opponent_id="20000056", player_outcome="Win"),
        ])
        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, records))
        standings = {("202605290393", "LADDER"): uscf_core.build_standings([{
            "ordinal": 1, "memberId": "99999999",
            "firstName": "Test", "lastName": "Player", "score": 2,
            "roundOutcomes": [
                {"roundNumber": 2, "outcome": "Win", "color": "White",
                 "opponentMemberId": "20000056", "opponentLastName": "BAKER"},
                {"roundNumber": 5, "outcome": "Win", "color": "Black",
                 "opponentMemberId": "20000056", "opponentLastName": "BAKER"},
            ],
            "ratings": [{"ratingSystem": "R", "preRatingDecimal": 1500.0,
                         "postRatingDecimal": 1520.0}],
        }])}

        attached = uscf_core.attach_round_numbers(enriched, standings, "99999999")

        # Both rounds assigned, each exactly once
        assert sorted(attached["UscfRound"]) == [2, 5]


# ---------------------------------------------------------------------------
# Norms and awards → achievements (issue #36)
#
# build_achievements turns raw /norms and /awards responses into one
# chronological list of typed UscfAchievement records — what the Milestones
# timeline and the celebration check consume.
# ---------------------------------------------------------------------------

class TestBuildAchievements:
    def test_the_real_norm_becomes_an_achievement(self, uscf_norms_json):
        """Daniel's FourthCategory norm from the Oak Grove Open (Dec 2025)."""
        achievements = uscf_core.build_achievements(uscf_norms_json["items"], [])

        assert len(achievements) == 1
        norm = achievements[0]
        assert norm.kind == "norm"
        assert norm.title == "Fourth Category norm"
        assert norm.date == date(2025, 12, 14)  # the Rated Event's end date
        assert norm.event_id == "202512140213"
        assert norm.event_name == "First Annual Oak Grove Open"
        assert "4.5" in norm.detail

    def test_the_real_award_becomes_an_achievement(self, uscf_awards_json):
        """Daniel's 25th-career-win WinMilestone (Newcomb Memorial, Jan 2026)."""
        achievements = uscf_core.build_achievements([], uscf_awards_json["items"])

        assert len(achievements) == 1
        award = achievements[0]
        assert award.kind == "award"
        assert award.title == "25th career win"
        assert award.date == date(2026, 1, 25)  # the award's own date
        assert award.event_name == "Fourth Annual Newcomb Memorial Tournament"

    def test_norms_and_awards_merge_chronologically(self, uscf_norms_json,
                                                    uscf_awards_json):
        """One timeline: the Dec 2025 norm comes before the Jan 2026 award."""
        achievements = uscf_core.build_achievements(
            uscf_norms_json["items"], uscf_awards_json["items"],
        )

        assert [a.kind for a in achievements] == ["norm", "award"]
        assert achievements[0].date < achievements[1].date

    def test_each_achievement_has_a_stable_identity(self, uscf_norms_json,
                                                    uscf_awards_json):
        """New-vs-seen detection across Syncs needs identities that never change
        between two fetches of the same data."""
        first = uscf_core.build_achievements(
            uscf_norms_json["items"], uscf_awards_json["items"])
        second = uscf_core.build_achievements(
            uscf_norms_json["items"], uscf_awards_json["items"])

        assert [a.achievement_id for a in first] == [a.achievement_id for a in second]
        # And distinct from each other — dismissing one can never hide another
        assert len({a.achievement_id for a in first}) == len(first)

    def test_tolerates_missing_event_and_unknown_category(self):
        """The MUIR API is undocumented (ADR 0003): an award of a category we've
        never seen, with no event attached, still becomes a sensible entry."""
        achievements = uscf_core.build_achievements(
            [{"level": "ThirdCategory"}],   # norm with no event at all
            [{"category": "GamesPlayedMilestone", "date": "2026-03-01"}],
        )

        norm, award = achievements[-1], achievements[0]  # undated norm sorts last
        assert norm.title == "Third Category norm"
        assert norm.date is None
        assert norm.event_name == ""
        assert award.title == "Games Played Milestone award"
        assert award.date == date(2026, 3, 1)

    def test_tolerates_explicit_null_event_and_zero_score(self):
        """More API quirks (ADR 0003): an explicit "event": null (not just a
        missing key) and a legitimate score of 0 both parse — never crash,
        never silently dropped."""
        achievements = uscf_core.build_achievements(
            [{"level": "FourthCategory", "score": 0, "playedGames": 5,
              "event": None}],
            [{"category": "WinMilestone", "winCount": 25, "event": None,
              "date": "2026-01-25"}],
        )

        norm = next(a for a in achievements if a.kind == "norm")
        award = next(a for a in achievements if a.kind == "award")
        assert "0" in norm.detail            # a real score of 0 is still shown
        assert award.title == "25th career win"

    def test_no_norms_or_awards_is_an_empty_list(self):
        """Most members have neither — the common case is empty, never an error."""
        assert uscf_core.build_achievements([], []) == []


class TestAchievementMilestones:
    """Achievements as Milestone-timeline entries (issue #36): what the
    Overview page renders, gold-flagged and date-filterable."""

    @pytest.fixture()
    def achievements(self, uscf_norms_json, uscf_awards_json):
        return uscf_core.build_achievements(
            uscf_norms_json["items"], uscf_awards_json["items"])

    def test_each_achievement_becomes_a_gold_timeline_entry(self, achievements):
        entries = uscf_core.achievement_milestones(achievements)

        assert len(entries) == 2
        norm, award = entries
        # The shape compute_milestones() entries have, so the page renders both
        # through one code path — but flagged as official USCF achievements
        assert norm["kind"] == "uscf"
        assert norm["date"] == "2025-12-14"
        assert "Fourth Category norm" in norm["description"]
        assert "First Annual Oak Grove Open" in norm["description"]
        assert award["kind"] == "uscf"
        assert "25th career win" in award["description"]

    def test_entries_respect_the_date_range_filter(self, achievements):
        """Achievements are member-level facts: only the date-range filter
        applies (the same rule as rating_trend_series — they aren't Games)."""
        only_2025 = uscf_core.achievement_milestones(
            achievements, date_start="2025-01-01", date_end="2025-12-31")

        assert len(only_2025) == 1
        assert "Fourth Category norm" in only_2025[0]["description"]

    def test_no_achievements_is_an_empty_list(self):
        assert uscf_core.achievement_milestones([]) == []


# ---------------------------------------------------------------------------
# The rating lens (issue #32)
#
# apply_rating_lens rewrites a Games DataFrame's player-rating columns per
# the chosen basis, so that every rating-derived stat downstream follows the
# lens without knowing it exists.
#
#   Official — the supplement in effect at the matched Rated Event's start
#              date (Daniel's long-standing convention); unmatched Games use
#              the supplement at their own date; Games before the first
#              supplement have no value — never invented.
#   Live     — the matched Section's pre-rating, decimals preserved;
#              unmatched Games fall back to the Official basis.  Opponent
#              ratings come from crosstable pre-ratings where cached
#              (issue #35), falling back to typed values.
#
# The lens never hides Games.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_career(study_snapshot_df, uscf_games_json, uscf_supplements_json,
                uscf_sections_json):
    """Daniel's real matched career: the Games df, both series, the matches."""
    records = uscf_core.build_game_records(uscf_games_json["items"])
    return SimpleNamespace(
        df=study_snapshot_df,
        official=uscf_core.build_official_series(uscf_supplements_json["items"]),
        live=uscf_core.build_live_series(uscf_sections_json["items"]),
        matches=uscf_core.match_games(study_snapshot_df, records),
    )


def _lensed(career, lens: str):
    return uscf_core.apply_rating_lens(
        career.df, lens, career.official, career.live, career.matches,
    )


def _games_of_event(career, lensed, event_name: str):
    """The lensed rows of every Game matched to *event_name*."""
    urls = {m.chapter_url for m in career.matches.matches
            if m.record.event_name == event_name}
    return lensed[lensed["ChapterURL"].isin(urls)]


class TestOfficialLens:
    def test_matched_games_use_the_supplement_at_event_start(self, real_career):
        """The tracer bullet, on the real career: every ACC MAY 2026 Game gets
        the May supplement (1470) — including the chapter Daniel typo'd 1440."""
        lensed = _lensed(real_career, "official")

        may_games = _games_of_event(real_career, lensed, "ACC MAY 2026")
        assert len(may_games) == 4
        assert (may_games["PlayerRatingNum"] == 1470).all()
        assert (may_games["PlayerRating"] == "1470").all()

    def test_the_event_start_date_convention(self, real_career):
        """Issue #32's own example: the Thanksgiving Open started Oct 31, so
        even its games played Nov 1–2 use the October supplement (1005) —
        never November's (1133)."""
        lensed = _lensed(real_career, "official")

        thanksgiving = _games_of_event(
            real_career, lensed, "2nd Annual Thankgiving Day Open",
        )
        assert len(thanksgiving) == 4
        assert (thanksgiving["PlayerRatingNum"] == 1005).all()

    def test_the_pre_supplement_era_has_no_official_value(self, real_career):
        """Daniel played 14 games before the first supplement (2025-09-01).
        Under the Official lens they have no rating — '—', never a fake number."""
        lensed = _lensed(real_career, "official")

        pre_supplement_urls = {
            m.chapter_url for m in real_career.matches.matches
            if m.record.event_start < date(2025, 9, 1)
        }
        era = lensed[lensed["ChapterURL"].isin(pre_supplement_urls)]
        assert len(era) == 14
        assert era["PlayerRatingNum"].isna().all()
        assert (era["PlayerRating"] == "").all()

    def test_unmatched_games_use_the_supplement_at_their_own_date(self, real_career):
        """A Game with no USCF Game Record still gets an Official value — from
        the supplement in effect on the day it was played.  The real career's
        one unmatched Game (the Forfeit, 2025-11-02) → November's 1133."""
        lensed = _lensed(real_career, "official")

        forfeit_url = real_career.matches.unmatched_chapter_urls[0]
        forfeit = lensed[lensed["ChapterURL"] == forfeit_url].iloc[0]
        assert forfeit["PlayerRatingNum"] == 1133
        assert forfeit["PlayerRating"] == "1133"

    def test_a_gap_month_uses_the_latest_supplement_before_it(self):
        """USCF skips a month → the previous supplement stays in effect (this
        is how USCF ratings actually work).  Real data has no gaps, so this is
        pinned synthetically: no October supplement → an October event uses
        September's value."""
        df = games_df(chapter(opponent_id="20000056", color="White",
                              result="1-0", date="2025.10.15"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="20000056", player_color="White",
                      player_outcome="Win", event="OCTOBER OPEN",
                      event_id="202510150001",
                      start="2025-10-15", end="2025-10-15"),
        ])
        official = uscf_core.build_official_series([
            {"ratingSupplementDate": "2025-09-01",
             "ratings": [{"source": "R", "rating": 1038}]},
            {"ratingSupplementDate": "2025-11-01",
             "ratings": [{"source": "R", "rating": 1133}]},
        ])

        lensed = uscf_core.apply_rating_lens(
            df, "official", official, [], uscf_core.match_games(df, records),
        )

        assert lensed.iloc[0]["PlayerRatingNum"] == 1038  # September, still in effect


class TestLiveLens:
    def test_matched_games_use_their_sections_pre_rating(self, real_career):
        """The Live basis: the matched Section's pre-rating, rounded to a
        whole number for display (Daniel's preference — the chain itself keeps
        its decimals).  He entered ACC MAY 2026 at 1544.47 → his Games show
        1544, whatever he typed."""
        lensed = _lensed(real_career, "live")

        may_games = _games_of_event(real_career, lensed, "ACC MAY 2026")
        assert len(may_games) == 4
        assert (may_games["PlayerRatingNum"] == 1544).all()
        assert (may_games["PlayerRating"] == "1544").all()

    def test_two_sections_of_one_event_get_their_own_pre_ratings(self, real_career):
        """The join is per Section, not per Rated Event: DMV's Under 1800
        Games entered at 1465.03 → 1465, its Extra-games Game at 1468.80 → 1469."""
        lensed = _lensed(real_career, "live")

        by_section: dict = {}
        for m in real_career.matches.matches:
            if "DMV Chess Second Annual" in m.record.event_name:
                row = lensed[lensed["ChapterURL"] == m.chapter_url].iloc[0]
                by_section.setdefault(m.record.section_name, set()).add(
                    row["PlayerRatingNum"]
                )
        assert by_section["Under 1800"] == {1465}
        assert by_section["Extra games - Classical"] == {1469}

    def test_the_first_ever_event_has_no_live_value(self, real_career):
        """Daniel was unrated entering his first Rated Event (pre is None) —
        under the Live lens those Games honestly have no rating."""
        lensed = _lensed(real_career, "live")

        first_event = _games_of_event(real_career, lensed, "ACC JUNE 2025")
        assert len(first_event) == 2
        assert first_event["PlayerRatingNum"].isna().all()
        assert (first_event["PlayerRating"] == "").all()

    def test_unmatched_games_fall_back_to_the_official_basis(self, real_career):
        """A Game with no USCF Game Record has no Section to take a pre-rating
        from → it falls back to the Official basis (issue #32).  The Forfeit
        (2025-11-02) → November's supplement, 1133."""
        lensed = _lensed(real_career, "live")

        forfeit_url = real_career.matches.unmatched_chapter_urls[0]
        forfeit = lensed[lensed["ChapterURL"] == forfeit_url].iloc[0]
        assert forfeit["PlayerRatingNum"] == 1133
        assert forfeit["PlayerRating"] == "1133"


# The crosstable-backed opponent ratings (issue #35): the Live lens uses
# what opponents were really rated walking into the Section, not what Daniel
# typed on the pairing sheet.
class TestOpponentRatingsUnderTheLens:
    def _lensed_with_standings(self, career, standings, lens):
        return uscf_core.apply_rating_lens(
            career.df, lens, career.official, career.live, career.matches,
            standings=standings,
        )

    def test_live_lens_uses_crosstable_opponent_ratings(
        self, real_career, real_standings
    ):
        """Issue #35 closes the Phase C limitation: Baker walked into ACC
        MAY 2026 rated 1432.59 — under the Live lens his rating reads 1433,
        not the 1465 Daniel typed."""
        lensed = self._lensed_with_standings(real_career, real_standings, "live")

        may = _games_of_event(real_career, lensed, "ACC MAY 2026")
        by_opponent = dict(zip(may["Opponent"], may["OpponentRatingNum"], strict=True))
        assert by_opponent["Bob Baker"] == 1433        # typed 1465
        assert by_opponent["Carter Clark"] == 1366    # typed 1446
        assert by_opponent["Ethan Edwards"] == 1467     # typed 1436

    def test_live_lens_fills_in_opponents_daniel_never_rated(
        self, real_career, real_standings
    ):
        """Daniel typed no rating at all for Garcia (Thanksgiving R2) — the
        crosstable knows he was 245.  Official data fills the gap."""
        lensed = self._lensed_with_standings(real_career, real_standings, "live")

        thanksgiving = _games_of_event(
            real_career, lensed, "2nd Annual Thankgiving Day Open")
        garcia = thanksgiving[thanksgiving["Opponent"] == "Xena Garcia"].iloc[0]
        assert garcia["OpponentRatingNum"] == 245
        assert garcia["OpponentRating"] == "245"

    def test_official_lens_keeps_the_typed_pairing_sheet_values(
        self, real_career, real_standings
    ):
        """The Official world view is the pairing sheet (PRD #24): typed
        opponent ratings stay, even with crosstables available."""
        lensed = self._lensed_with_standings(real_career, real_standings, "official")

        may = _games_of_event(real_career, lensed, "ACC MAY 2026")
        by_opponent = dict(zip(may["Opponent"], may["OpponentRatingNum"], strict=True))
        assert by_opponent["Bob Baker"] == 1465        # as typed

    def test_opponents_without_a_cached_crosstable_keep_typed_values(
        self, real_career, real_standings
    ):
        """ACC JUNE 2025 has no cached crosstable → its opponents keep their
        typed ratings under the Live lens (fallback, never a wipe)."""
        typed = real_career.df
        lensed = self._lensed_with_standings(real_career, real_standings, "live")

        june_urls = {m.chapter_url for m in real_career.matches.matches
                     if m.record.event_name == "ACC JUNE 2025"}
        for url in june_urls:
            before = typed[typed["ChapterURL"] == url].iloc[0]
            after = lensed[lensed["ChapterURL"] == url].iloc[0]
            assert after["OpponentRatingNum"] == before["OpponentRatingNum"]

    def test_rating_diff_agrees_with_both_displayed_ratings(
        self, real_career, real_standings
    ):
        """The acceptance criterion: under the Live lens, rating-diff equals
        displayed opponent minus displayed player — fully consistent, no
        typed values mixed in."""
        lensed = self._lensed_with_standings(real_career, real_standings, "live")

        may = _games_of_event(real_career, lensed, "ACC MAY 2026")
        for _, game in may.iterrows():
            assert game["RatingDiff"] == (
                game["OpponentRatingNum"] - game["PlayerRatingNum"])
        # Concretely: Clark (1366) beat Daniel (1544) → diff −178
        clark = may[may["Opponent"] == "Carter Clark"].iloc[0]
        assert clark["RatingDiff"] == 1366 - 1544

    def test_without_standings_opponents_stay_typed(self, real_career):
        """No crosstables passed (USCF down, nothing cached) → exactly the
        Phase C behavior: opponent ratings stay typed, the diff rebuilt
        against the lensed player rating."""
        typed = real_career.df
        lensed = _lensed(real_career, "live")

        may_urls = {m.chapter_url for m in real_career.matches.matches
                    if m.record.event_name == "ACC MAY 2026"}
        for url in may_urls:
            before = typed[typed["ChapterURL"] == url].iloc[0]
            after = lensed[lensed["ChapterURL"] == url].iloc[0]
            assert after["OpponentRatingNum"] == before["OpponentRatingNum"]
            assert after["RatingDiff"] == before["OpponentRatingNum"] - 1544


class TestRatingLensInvariants:

    def test_the_lens_never_hides_games(self, real_career):
        """A lens, not a filter (PRD #24): every Game stays, in the same order,
        with everything except the player-rating columns untouched."""
        lensed = _lensed(real_career, "official")

        assert list(lensed["ChapterURL"]) == list(real_career.df["ChapterURL"])
        for column in ("Opponent", "Outcome", "Date", "Event", "FullMoves"):
            assert list(lensed[column]) == list(real_career.df[column])

    def test_the_input_df_is_never_mutated(self, real_career):
        """The store's df is shared by every callback — the lens works on a copy."""
        typed_before = list(real_career.df["PlayerRatingNum"])

        _lensed(real_career, "official")
        _lensed(real_career, "live")

        assert list(real_career.df["PlayerRatingNum"]) == typed_before

    def test_with_no_uscf_data_typed_values_stay(self, real_career):
        """ADR 0003: USCF never reached → both series empty → the lens changes
        nothing.  Typed values are all there is; wiping them would turn an
        outage into data loss."""
        lensed = uscf_core.apply_rating_lens(
            real_career.df, "official", [], [], uscf_core.MatchResult(),
        )

        assert (list(lensed["PlayerRatingNum"])
                == list(real_career.df["PlayerRatingNum"]))
        assert list(lensed["PlayerRating"]) == list(real_career.df["PlayerRating"])

    def test_an_empty_df_passes_through(self):
        """No Games at all → no crash, nothing invented."""
        import pandas as pd

        result = uscf_core.apply_rating_lens(
            pd.DataFrame(), "official", [], [], uscf_core.MatchResult(),
        )
        assert result.empty

