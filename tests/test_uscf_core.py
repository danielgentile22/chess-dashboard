"""
tests/test_uscf_core.py
=======================
Tests for the pure USCF interpretation layer (uscf_core.py): raw MUIR API
responses in → typed records, rating series, and Game matches out.

No HTTP, no Dash — everything runs on real captured response shapes
(tests/fixtures/uscf/) plus inline variants for edge cases.  Matching tests
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


def chapter(opponent="John Fontaine", opponent_id="16441708", color="White",
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


def uscf_game(opponent_id="16441708", opponent_first="JOHN", opponent_last="FONTAINE",
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

        assert profile.member_id == "32487228"
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
        # The most recent game: a win with White against JOHN FONTAINE
        first = records[0]
        assert first.opponent_id == "16441708"
        assert first.opponent_name == "JOHN FONTAINE"
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
        df = games_df(chapter(opponent="John Fontaine", opponent_id="16441708",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
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
        df = games_df(chapter(opponent_id="16441708", color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_outcome="Loss"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()
        assert result.unmatched_chapter_urls == (df.iloc[0]["ChapterURL"],)
        assert len(result.unmatched_records) == 1

    def test_chapter_without_fide_id_never_matches_by_id(self):
        """A chapter where Daniel never typed the opponent's member ID cannot
        match by ID — only the name-fallback pass (issue #29) can claim it,
        and it says so."""
        df = games_df(chapter(opponent="James K. Williams", opponent_id="",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="12424913", opponent_first="JAMES K",
                      opponent_last="WILLIAMS", player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert all(m.matched_by == "name" for m in result.matches)

    def test_two_missing_ids_never_match_each_other(self):
        """'' == '' is not an ID match — absence of data is not a key.
        (Different names, so the name fallback can't claim them either.)"""
        df = games_df(chapter(opponent="John Fontaine", opponent_id="",
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
        """The real Baru case: Daniel beat the same opponent twice in the same
        monthly Rated Event — once as Black, once as White.  Only color says
        which USCF record belongs to which chapter."""
        df = games_df(
            chapter(opponent="Baru Dharmesh", opponent_id="32018453",
                    color="Black", result="0-1", date="2025.12.05"),   # Win as Black
            chapter(opponent="Baru Dharmesh", opponent_id="32018453",
                    color="White", result="1-0", date="2025.12.26"),   # Win as White
        )
        december = dict(opponent_id="32018453", opponent_first="Dharmesh",
                        opponent_last="Baru", player_outcome="Win",
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
            chapter(opponent_id="13419518", color="White", result="1-0",
                    date="2025.10.04"),
            chapter(opponent_id="13419518", color="White", result="1-0",
                    date="2025.12.14"),
        )
        hiban = dict(opponent_id="13419518", opponent_first="Michael Thomas",
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
            chapter(opponent_id="32018453", color="Black", result="0-1",
                    date="2025.12.05"),
            chapter(opponent_id="32018453", color="White", result="1-0",
                    date="2026.02.06"),
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="32018453", player_color="White",
                      player_outcome="Win", event="ACC FEBRUARY 2026",
                      start="2026-02-06", end="2026-02-27"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1
        assert result.matches[0].chapter_url == df.iloc[1]["ChapterURL"]
        assert result.unmatched_chapter_urls == (df.iloc[0]["ChapterURL"],)


class TestMatchingPolicies:
    def test_color_disagreement_does_not_prevent_a_match(self):
        """The real Nordberg case: the chapter says Daniel played Black, USCF
        says White.  Color is itself a fact that can conflict between sources —
        it is never a match requirement (PRD #24)."""
        df = games_df(chapter(opponent="Justin Nordberg", opponent_id="32668352",
                              color="Black", result="1/2-1/2", date="2026.02.20"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="32668352", opponent_first="Justin",
                      opponent_last="Nordberg", player_color="White",
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
        df = games_df(chapter(opponent="Will Soublo", opponent_id="32697429",
                              color="Black", result="0-1"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="32697429", opponent_first="Will",
                      opponent_last="Soublo", player_color="Black",
                      player_outcome="Win", rating_system="OR"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()
        assert len(result.unmatched_records) == 1
        assert result.unmatched_records[0].rating_system == "OR"

    def test_dual_rated_records_match_like_regular_ones(self):
        """Dual-rated (D) Sections are over-the-board games — they match
        exactly like Regular (R) ones (the real Thanksgiving Open case)."""
        df = games_df(chapter(opponent="Vignesh Srinivasan", opponent_id="14822404",
                              color="Black", result="0-1", date="2025.11.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="14822404", opponent_first="Vignesh",
                      opponent_last="Srinivasan", player_color="Black",
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
        df = games_df(chapter(opponent="Christian Miles", opponent_id="",
                              color="White", result="1-0", date="2026.04.17"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="15569691", opponent_first="Christian",
                      opponent_last="Miles", player_color="Black",
                      player_outcome="Win", event="ACC Aprril 2026",
                      start="2026-04-03", end="2026-04-24"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1
        assert result.matches[0].matched_by == "name"
        assert result.matches[0].record.opponent_id == "15569691"

    def test_name_matching_ignores_case(self):
        """The real Fontaine case: USCF registers 'JOHN FONTAINE', the chapter
        says 'John Fontaine'."""
        df = games_df(chapter(opponent="John Fontaine", opponent_id="",
                              color="White", result="1-0", date="2026.05.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", opponent_first="JOHN",
                      opponent_last="FONTAINE", player_color="White",
                      player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1

    def test_name_matching_ignores_punctuation(self):
        """The real Williams case: 'James K. Williams' (chapter) vs
        'JAMES K' + 'WILLIAMS' (USCF) — the middle-initial dot must not matter."""
        df = games_df(chapter(opponent="James K. Williams", opponent_id="",
                              color="White", result="1-0", date="2026.04.03"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="12424913", opponent_first="JAMES K",
                      opponent_last="WILLIAMS", player_color="White",
                      player_outcome="Win", event="ACC Aprril 2026",
                      start="2026-04-03", end="2026-04-24"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1

    def test_first_name_spelling_variant_with_exact_last_name(self):
        """The real Kaiyrberli case: Daniel typed 'Kaiser', USCF has 'Kaisar'.
        Last name matches exactly + same first initial → still a match."""
        df = games_df(chapter(opponent="Kaiser Kaiyrberli", opponent_id="",
                              color="Black", result="1-0", date="2026.05.22"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="32235302", opponent_first="Kaisar",
                      opponent_last="Kaiyrberli", player_color="Black",
                      player_outcome="Loss"),
        ])

        result = uscf_core.match_games(df, records)

        assert len(result.matches) == 1
        assert result.matches[0].matched_by == "name"

    def test_a_different_last_name_never_matches(self):
        """Spelling tolerance never crosses last names: 'Kaiser Kaiyrberli'
        is not 'Kaiser Kaplan'."""
        df = games_df(chapter(opponent="Kaiser Kaiyrberli", opponent_id="",
                              color="Black", result="1-0", date="2026.05.22"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="11111111", opponent_first="Kaiser",
                      opponent_last="Kaplan", player_color="Black",
                      player_outcome="Loss"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()

    def test_name_match_requires_the_date_window(self):
        """The same opponent name + result in an event months away is a
        different game — the window is part of the fallback key, so that the
        weaker name key can never reach across events."""
        df = games_df(chapter(opponent="John Fontaine", opponent_id="",
                              color="White", result="1-0", date="2026.05.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", opponent_first="JOHN",
                      opponent_last="FONTAINE", player_color="White",
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
        df = games_df(chapter(opponent="John Fontaine", opponent_id="99999990",
                              color="White", result="1-0", date="2026.05.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", opponent_first="JOHN",
                      opponent_last="FONTAINE", player_color="White",
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
        54 with full agreement + 1 with a color conflict (Nordberg)."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        id_matches = [m for m in result.matches if m.matched_by == "id"]
        assert len(id_matches) == 55

    def test_the_name_pass_matches_the_seven_id_less_chapters(
        self, study_snapshot_df, uscf_games_json
    ):
        """The 7 chapters Daniel never typed FideIds into (Apr–May 2026) all
        match by name — including the Williams punctuation case and the
        Kaiser/Kaisar spelling variant."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        name_matches = [m for m in result.matches if m.matched_by == "name"]
        assert len(name_matches) == 7
        matched_opponents = {m.record.opponent_name for m in name_matches}
        assert "JAMES K WILLIAMS" in matched_opponents     # punctuation + case
        assert "Kaisar Kaiyrberli" in matched_opponents    # spelling variant

    def test_both_passes_together_match_62_of_63_games(
        self, study_snapshot_df, uscf_games_json
    ):
        """The full engine on the full real career: 62 of 63 chapters match.
        The only unmatched chapter is the Forfeit (Feketekuty no-show — USCF
        correctly never rated it)."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        assert len(result.matches) == 62
        unmatched = study_snapshot_df[
            study_snapshot_df["ChapterURL"].isin(result.unmatched_chapter_urls)
        ]
        assert list(unmatched["Opponent"]) == ["Dennis Feketekuty"]

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
        assert result.unmatched_records[0].opponent_name == "Will Soublo"

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
            chapter(opponent="John Fontaine", opponent_id="16441708",
                    color="White", result="1-0"),
            chapter(opponent="Nobody USCF Knows", opponent_id="11111111",
                    color="Black", result="0-1"),
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
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
        assert game["UscfOpponentName"] == "JOHN FONTAINE"
        assert game["UscfOpponentId"] == "16441708"

    def test_unmatched_games_carry_empty_enrichment(self):
        """Unmatched Games keep working everywhere — enrichment is additive,
        never a filter (ADR 0003)."""
        enriched, _ = self._enriched_pair()
        game = enriched.iloc[1]

        assert bool(game["UscfMatched"]) is False
        assert game["UscfMatchedBy"] == ""
        assert game["UscfEventName"] == ""
        assert game["UscfOpponentId"] == ""

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
        """The real Nordberg case (issue #30): chapter says Black, USCF says
        White.  The Game stays matched and displays the Lichess version — the
        disagreement is flagged, never hidden."""
        df = games_df(
            chapter(opponent="Justin Nordberg", opponent_id="32668352",
                    color="Black", result="1/2-1/2"),       # conflicted
            chapter(opponent="John Fontaine", opponent_id="16441708",
                    color="White", result="1-0"),           # clean
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="32668352", opponent_first="Justin",
                      opponent_last="Nordberg", player_color="White",
                      player_outcome="Draw"),
            uscf_game(opponent_id="16441708", player_color="White",
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
        """The real Feketekuty case: the chapter is literally '1. e4 1-0'."""
        df = games_df(chapter(opponent="Dennis Feketekuty", opponent_id="30077997",
                              color="White", result="1-0", moves="1. e4"))
        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, []))

        assert bool(enriched.iloc[0]["Forfeit"]) is True

    def test_unmatched_full_game_is_not_a_forfeit(self):
        """A real game USCF just hasn't rated yet is unmatched, not a Forfeit
        — it belongs in Reconciliation, not excluded from stats."""
        df = games_df(chapter(opponent_id="16441708", color="White", result="1-0",
                              moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6"))
        enriched = uscf_core.enrich_games(df, uscf_core.match_games(df, []))

        assert bool(enriched.iloc[0]["Forfeit"]) is False

    def test_matched_game_is_never_a_forfeit(self):
        """If USCF rated it, a game was played — however short the chapter."""
        df = games_df(chapter(opponent_id="16441708", color="White",
                              result="1-0", moves="1. e4"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
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
        assert list(forfeits["Opponent"]) == ["Dennis Feketekuty"]


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
        """The real Nordberg case: matched, but the chapter says Black and
        USCF says White.  Both versions appear side by side."""
        df = games_df(chapter(opponent="Justin Nordberg", opponent_id="32668352",
                              color="Black", result="1/2-1/2", date="2026.02.20"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="32668352", opponent_first="Justin",
                      opponent_last="Nordberg", player_color="White",
                      player_outcome="Draw", event="ACC FEBRUARY 2026",
                      start="2026-02-20", end="2026-02-27"),
        ])

        entries = _reconcile(df, records)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.kind == "conflict"
        assert entry.opponent == "Justin Nordberg"
        assert "Black" in entry.lichess_says
        assert "White" in entry.uscf_says
        # The fix-on-Lichess action knows which chapter to open
        assert entry.chapter_url == df.iloc[0]["ChapterURL"]

    def test_a_clean_match_produces_no_entry(self):
        """Agreement is silence — Reconciliation only lists disagreements."""
        df = games_df(chapter(opponent="John Fontaine", opponent_id="16441708",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
                      player_outcome="Win"),
        ])

        assert _reconcile(df, records) == []


class TestReconcileUnmatched:
    def test_a_uscf_only_record_becomes_an_entry(self):
        """The real online-game case: USCF rated it, but Daniel deliberately
        never added a Chapter.  The entry offers Skip (dismiss)."""
        df = games_df(chapter(opponent="John Fontaine", opponent_id="16441708",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
                      player_outcome="Win"),
            uscf_game(opponent_id="32697429", opponent_first="Will",
                      opponent_last="Soublo", player_color="Black",
                      player_outcome="Win", rating_system="OR",
                      event="DMVCHESS.COM JANUARY CLIMB", event_id="202601300323",
                      start="2026-01-01", end="2026-01-30"),
        ])

        entries = _reconcile(df, records)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.kind == "uscf_only"
        assert entry.opponent == "Will Soublo"
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
        df = games_df(chapter(opponent="Dennis Feketekuty", opponent_id="30077997",
                              color="White", result="1-0", moves="1. e4"))

        entries = _reconcile(df, [])

        assert entries == []


class TestReconcileMissingFideIds:
    def test_a_chapter_without_an_opponent_id_becomes_an_entry(self):
        """Even when the name fallback matched it, the chapter is listed so
        Daniel can type the FideId in and make the match robust."""
        df = games_df(chapter(opponent="John Fontaine", opponent_id="",
                              color="White", result="1-0", date="2026.05.01"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", opponent_first="JOHN",
                      opponent_last="FONTAINE", player_color="White",
                      player_outcome="Win"),
        ])

        entries = _reconcile(df, records)

        missing = [e for e in entries if e.kind == "missing_fide_id"]
        assert len(missing) == 1
        assert missing[0].opponent == "John Fontaine"
        assert missing[0].chapter_url == df.iloc[0]["ChapterURL"]
        # The matched record tells Daniel exactly which ID to type in
        assert "16441708" in missing[0].uscf_says

    def test_chapters_with_ids_are_not_listed(self):
        df = games_df(chapter(opponent_id="16441708", color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
                      player_outcome="Win"),
        ])

        entries = _reconcile(df, records)

        assert [e for e in entries if e.kind == "missing_fide_id"] == []


class TestReconcileRatingMismatches:
    def _records(self):
        return uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
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
        df = games_df(chapter(opponent_id="16441708", color="White",
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
        df = games_df(chapter(opponent_id="16441708", color="White",
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
        df = games_df(chapter(opponent_id="16441708", color="White",
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
        df = games_df(chapter(opponent_id="16441708", color="White",
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
            chapter(opponent="Justin Nordberg", opponent_id="32668352",
                    color="Black", result="1/2-1/2"),
        )
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="32668352", opponent_first="Justin",
                      opponent_last="Nordberg", player_color="White",
                      player_outcome="Draw"),
            uscf_game(opponent_id="32697429", opponent_first="Will",
                      opponent_last="Soublo", player_color="Black",
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
        df = games_df(chapter(opponent="Justin Nordberg", opponent_id="32668352",
                              color="Black", result="1/2-1/2"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="32668352", opponent_first="Justin",
                      opponent_last="Nordberg", player_color="White",
                      player_outcome="Draw"),
        ])

        first = _reconcile(df, records)
        second = _reconcile(df, records)

        assert [e.entry_id for e in first] == [e.entry_id for e in second]


class TestReconcileAgainstRealData:
    """The full Reconciliation ground truth for the captured fixture pair.

    Note: planning (PRD #24 / issue #30) predicted 2 color conflicts; the
    captured data actually contains 3 — the Miles April chapter (chapter says
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
            "Justin Nordberg",     # Feb 2026 — known at planning time
            "Christian Miles",     # Apr 2026 — found by this engine
            "Wade Robertson",      # May 2026 — known at planning time
        }

    def test_one_uscf_only_entry_the_online_game(
        self, study_snapshot_df, uscf_games_json, uscf_supplements_json
    ):
        entries = self._entries(study_snapshot_df, uscf_games_json,
                                uscf_supplements_json)
        uscf_only = [e for e in entries if e.kind == "uscf_only"]

        assert len(uscf_only) == 1
        assert uscf_only[0].opponent == "Will Soublo"

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
        assert mismatches[0].opponent == "John Fontaine"


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
            '[White "Test Player"]\n[Black "John Fontaine"]\n[Result "1-0"]\n'
            '[WhiteFideId "99999999"]\n[BlackFideId "16441708"]\n'
            "\n1. e4 e5 2. Nf3 1-0\n"
        )
        df = games_df(no_url_chapter)
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
                      player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()
        assert result.unmatched_chapter_urls == ()        # no identity → not listed
        assert len(result.unmatched_records) == 1          # the record IS listed

    def test_record_without_a_color_never_flags_a_conflict(self):
        """A USCF record missing player.color (API quirk) is not a color
        disagreement — '' is absence of data, not a color."""
        df = games_df(chapter(opponent_id="16441708", color="White", result="1-0"))
        raw = uscf_game(opponent_id="16441708", player_outcome="Win")
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
        df = games_df(chapter(opponent_id="16441708", color="White",
                              result="1-0", date="2026.05.01",
                              player_rating="1440"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
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
#              unmatched Games fall back to the Official basis.
#
# Opponent ratings stay typed under both lenses (the documented Phase D
# limitation); the lens never hides Games.
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
        df = games_df(chapter(opponent_id="16441708", color="White",
                              result="1-0", date="2025.10.15"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="16441708", player_color="White",
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
        """The Live basis: the matched Section's pre-rating, decimals preserved.
        Daniel entered ACC MAY 2026 at 1544.47 — that's his Live Rating for
        all four of its Games, whatever he typed."""
        lensed = _lensed(real_career, "live")

        may_games = _games_of_event(real_career, lensed, "ACC MAY 2026")
        assert len(may_games) == 4
        assert (may_games["PlayerRatingNum"] == 1544.47).all()
        assert (may_games["PlayerRating"] == "1544.47").all()

    def test_two_sections_of_one_event_get_their_own_pre_ratings(self, real_career):
        """The join is per Section, not per Rated Event: DMV's Under 1800
        Games entered at 1465.03, its Extra-games Game at 1468.80."""
        lensed = _lensed(real_career, "live")

        by_section: dict = {}
        for m in real_career.matches.matches:
            if "DMV Chess Second Annual" in m.record.event_name:
                row = lensed[lensed["ChapterURL"] == m.chapter_url].iloc[0]
                by_section.setdefault(m.record.section_name, set()).add(
                    row["PlayerRatingNum"]
                )
        assert by_section["Under 1800"] == {1465.03}
        assert by_section["Extra games - Classical"] == {1468.8}

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


class TestRatingLensInvariants:
    def test_rating_diff_follows_the_lens_but_opponents_stay_typed(self, real_career):
        """Rating-diff (and so upsets and strength buckets) is recomputed from
        the lensed player rating, while opponent ratings keep Daniel's typed
        values until Phase D's crosstable enrichment — the documented
        limitation in issue #32."""
        typed = real_career.df
        lensed = _lensed(real_career, "live")

        may_urls = {m.chapter_url for m in real_career.matches.matches
                    if m.record.event_name == "ACC MAY 2026"}
        for url in may_urls:
            before = typed[typed["ChapterURL"] == url].iloc[0]
            after = lensed[lensed["ChapterURL"] == url].iloc[0]
            # opponent side untouched...
            assert after["OpponentRatingNum"] == before["OpponentRatingNum"]
            assert after["OpponentRating"] == before["OpponentRating"]
            # ...the diff rebuilt against the Live basis
            assert after["RatingDiff"] == before["OpponentRatingNum"] - 1544.47

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

