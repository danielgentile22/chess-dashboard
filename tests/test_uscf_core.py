"""
tests/test_uscf_core.py
=======================
Tests for the pure USCF interpretation layer (uscf_core.py): raw MUIR API
responses in → typed records and rating series out.

No HTTP, no Dash — everything runs on real captured response shapes
(tests/fixtures/uscf/) plus inline variants for edge cases.
"""
from __future__ import annotations

from datetime import date

import uscf_core

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
