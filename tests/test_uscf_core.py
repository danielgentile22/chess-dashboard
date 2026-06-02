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
