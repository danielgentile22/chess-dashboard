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

    def test_chapter_without_fide_id_is_not_matched_by_this_pass(self):
        """A chapter where Daniel never typed the opponent's member ID cannot
        match by ID — it waits for the name-fallback pass (issue #29)."""
        df = games_df(chapter(opponent="James K. Williams", opponent_id="",
                              color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="12424913", opponent_first="JAMES K",
                      opponent_last="WILLIAMS", player_outcome="Win"),
        ])

        result = uscf_core.match_games(df, records)

        assert result.matches == ()
        assert len(result.unmatched_chapter_urls) == 1
        assert len(result.unmatched_records) == 1

    def test_two_missing_ids_never_match_each_other(self):
        """'' == '' is not an ID match — absence of data is not a key."""
        df = games_df(chapter(opponent_id="", color="White", result="1-0"))
        records = uscf_core.build_game_records([
            uscf_game(opponent_id="", player_outcome="Win"),
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
# The matching engine against the real fixture pair: Daniel's full Study
# (63 chapters) ↔ his full USCF record (63 USCF Game Records), captured the
# same day (2026-06-02).  This is the engine's ground truth.
# ---------------------------------------------------------------------------

class TestIdPassAgainstRealData:
    def test_the_id_pass_matches_55_of_63_games(
        self, study_snapshot_df, uscf_games_json
    ):
        """Every chapter with a typed FideId whose USCF record exists matches:
        54 with full agreement + 1 with a color conflict (Nordberg)."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        id_matches = [m for m in result.matches if m.matched_by == "id"]
        assert len(id_matches) == 55

    def test_unmatched_chapters_are_the_seven_id_less_ones_plus_the_forfeit(
        self, study_snapshot_df, uscf_games_json
    ):
        """After the ID pass: the 7 chapters Daniel never typed FideIds into
        (Apr–May 2026) plus the Forfeit (Feketekuty no-show) remain unmatched."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        unmatched = study_snapshot_df[
            study_snapshot_df["ChapterURL"].isin(result.unmatched_chapter_urls)
        ]
        assert len(unmatched) == 8
        # The Forfeit: Dennis Feketekuty never showed; USCF never rated it
        assert "Dennis Feketekuty" in set(unmatched["Opponent"])
        # The other 7 are exactly the chapters with no typed opponent FideId
        with_ids = unmatched[unmatched["Opponent"] != "Dennis Feketekuty"]
        for _, game in with_ids.iterrows():
            opponent_id = game["BlackID"] if game["Color"] == "White" else game["WhiteID"]
            assert opponent_id == "", f"{game['Opponent']} has an ID but didn't match"

    def test_unmatched_records_include_the_online_game(
        self, study_snapshot_df, uscf_games_json
    ):
        """The online-rated (OR) game Daniel deliberately keeps out of his OTB
        Study is exposed as an unmatched record, never silently dropped."""
        records = uscf_core.build_game_records(uscf_games_json["items"])
        result = uscf_core.match_games(study_snapshot_df, records)

        online = [r for r in result.unmatched_records if r.rating_system == "OR"]
        assert len(online) == 1
        assert online[0].opponent_name == "Will Soublo"

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
