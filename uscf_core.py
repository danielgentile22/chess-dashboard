"""
uscf_core.py
============
Pure functions that interpret raw USCF API responses (no HTTP, no Dash).

uscf_client fetches raw JSON from the MUIR API; this module turns it into
typed records the rest of the app understands — the same split as
lichess_client (raw PGN) / pgn_stats_core (parsed Games).

The MUIR API is undocumented (ADR 0003), so parsing is deliberately tolerant:
unrated systems appear as entries with no `rating` key, decimals can be
missing, and `fideId` of "0" means "none".

Public API
----------
parse_member_profile   raw /members/{id} response → UscfProfile
membership_alert       Warning text when the membership has lapsed / expires soon.
UscfProfile            Who the member is according to USCF.
UscfRating             One rating system's entry (rating, provisional, floor).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

__all__ = [
    "UscfProfile",
    "UscfRating",
    "membership_alert",
    "parse_member_profile",
]

# Warn this many days before the membership expires — enough time to renew
# before the next monthly tournament sneaks up.
_EXPIRATION_WARNING_DAYS = 90


# ---------------------------------------------------------------------------
# Typed records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UscfRating:
    """One rating system's entry in a member profile (system codes: R, Q, B, OR, OQ, OB)."""

    system: str
    rating: int | None        # None → unrated in this system
    is_provisional: bool
    games_played: int | None  # provisional game count (absent once established)
    floor: int | None         # the rating this member can never fall below


@dataclass(frozen=True)
class UscfProfile:
    """A member profile: ratings, ranks, floor, and membership status."""

    member_id: str
    name: str
    state: str
    national_rank: int | None
    state_rank: int | None
    ratings: tuple[UscfRating, ...]
    membership_status: str
    membership_expires: date | None

    def rating(self, system: str) -> UscfRating | None:
        """The entry for one rating system ('R', 'Q', 'OR', …), or None if absent."""
        return next((r for r in self.ratings if r.system == system), None)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_member_profile(raw: dict) -> UscfProfile:
    """
    Interpret a raw /members/{id} response.

    Tolerant of the API's quirks: unrated systems have no `rating` key,
    established ratings have no `gamesPlayed`, and unrated systems no `floor`.
    """
    ratings = tuple(
        UscfRating(
            system=str(entry.get("ratingSystem", "")),
            rating=entry.get("rating"),
            is_provisional=bool(entry.get("isProvisional", False)),
            games_played=entry.get("gamesPlayed"),
            floor=entry.get("floor"),
        )
        for entry in raw.get("ratings", [])
    )

    first = str(raw.get("firstName", "")).strip()
    last = str(raw.get("lastName", "")).strip()

    return UscfProfile(
        member_id=str(raw.get("id", "")),
        name=" ".join(part for part in (first, last) if part),
        state=str(raw.get("stateRep", "")),
        national_rank=raw.get("rank"),
        state_rank=raw.get("stateRank"),
        ratings=ratings,
        membership_status=str(raw.get("status", "")),
        membership_expires=_parse_date(raw.get("expirationDate")),
    )


def membership_alert(profile: UscfProfile, *, today: date) -> str | None:
    """
    The membership warning for the profile card, or None when all is well.

    Warns when the membership has lapsed or expires within 90 days — so a
    lapsed membership is discovered at home, not at the tournament hall.
    """
    expires = profile.membership_expires
    if expires is None:
        return None

    days_left = (expires - today).days
    if days_left < 0:
        return f"Membership lapsed on {expires.isoformat()} — renew before your next rated event."
    if days_left <= _EXPIRATION_WARNING_DAYS:
        return f"Membership expires in {days_left} days ({expires.isoformat()})."
    return None


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO 'YYYY-MM-DD' string, or None when absent/malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None
