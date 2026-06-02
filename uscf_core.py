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
parse_member_profile    raw /members/{id} response → UscfProfile
membership_alert        Warning text when the membership has lapsed / expires soon.
build_official_series   raw supplement items → the Official Rating series.
build_live_series       raw section items → the Live Rating series (continuous chain).
UscfProfile             Who the member is according to USCF.
UscfRating              One rating system's entry (rating, provisional, floor).
OfficialRatingPoint     One supplement month's Official Rating.
LiveRatingPoint         One Section's pre→post Live Rating change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

__all__ = [
    "LiveRatingPoint",
    "OfficialRatingPoint",
    "UscfProfile",
    "UscfRating",
    "build_live_series",
    "build_official_series",
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


# ---------------------------------------------------------------------------
# The Official Rating series (issue #27)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OfficialRatingPoint:
    """The Official Rating as published in one monthly supplement."""

    month: date   # the supplement date
    rating: int


def build_official_series(supplement_items: list[dict]) -> list[OfficialRatingPoint]:
    """
    The Official Rating series: one integer per supplement month,
    chronological, starting at the first supplement.

    Months before the first supplement have no official value and are never
    invented; neither are gap months nor months whose supplement carries no
    Regular rating.
    """
    points = []
    for item in supplement_items:
        month = _parse_date(item.get("ratingSupplementDate"))
        if month is None:
            continue
        regular = next(
            (entry for entry in item.get("ratings", []) if entry.get("source") == "R"),
            None,
        )
        if regular is None or regular.get("rating") is None:
            continue
        points.append(OfficialRatingPoint(month=month, rating=regular["rating"]))

    return sorted(points, key=lambda p: p.month)


# ---------------------------------------------------------------------------
# The Live Rating series (issue #27)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveRatingPoint:
    """One Section's effect on the Live Rating: pre → post, decimals preserved."""

    event_id: str
    event_name: str
    section_name: str
    end_date: date
    pre: float | None   # None for the first Section ever played
    post: float


def build_live_series(section_items: list[dict]) -> list[LiveRatingPoint]:
    """
    The Live Rating series: one pre→post pair per Regular-rated Section,
    decimals preserved, ordered chronologically by event end date.

    Dual-rated (D) Sections move the Regular chain via their Regular rating
    record; Quick and Online (Q/OR/OQ/OB) Sections are excluded.  The result
    is a continuous chain — each Section's post-rating is the next Section's
    pre-rating — including Sections that end on the same day, which are
    ordered by following that chain.
    """
    points = []
    for item in section_items:
        # Only Sections that move the Regular rating (the backbone — PRD #24)
        if item.get("ratingSystem") not in ("R", "D"):
            continue
        record = next(
            (r for r in item.get("ratingRecords", []) if r.get("ratingSource") == "R"),
            None,
        )
        if record is None:
            continue
        post = record.get("postRatingDecimal", record.get("postRating"))
        if post is None:
            continue  # a Section with no rating outcome moves nothing
        event = item.get("event", {})
        end_date = _parse_date(event.get("endDate")) or _parse_date(item.get("endDate"))
        if end_date is None:
            continue
        points.append(LiveRatingPoint(
            event_id=str(event.get("id", "")),
            event_name=str(event.get("name", "")),
            section_name=str(item.get("sectionName", "")),
            end_date=end_date,
            pre=record.get("preRatingDecimal", record.get("preRating")),
            post=post,
        ))

    points.sort(key=lambda p: p.end_date)
    return _chain_same_day_sections(points)


def _chain_same_day_sections(points: list[LiveRatingPoint]) -> list[LiveRatingPoint]:
    """
    Order Sections that end on the same day by following the rating chain.

    USCF rates same-day Sections in sequence; their pre/post values say which
    came first.  The API's response order does not.
    """
    ordered: list[LiveRatingPoint] = []
    i = 0
    while i < len(points):
        j = i
        while j < len(points) and points[j].end_date == points[i].end_date:
            j += 1
        group = points[i:j]
        if len(group) > 1:
            group = _chain_group(group, ordered[-1].post if ordered else None)
        ordered.extend(group)
        i = j
    return ordered


def _chain_group(
    group: list[LiveRatingPoint], previous_post: float | None
) -> list[LiveRatingPoint]:
    """Greedily link a same-day group: each Section's pre is the previous post."""
    remaining = list(group)
    chained: list[LiveRatingPoint] = []
    current = previous_post
    while remaining:
        next_point = next((p for p in remaining if p.pre == current), None)
        if next_point is None:
            # The chain can't tell us the order — keep what's left as-is
            chained.extend(remaining)
            break
        remaining.remove(next_point)
        chained.append(next_point)
        current = next_point.post
    return chained


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
