"""
uscf_client.py
==============
The only module that talks HTTP to the USCF ratings API (MUIR).

The API (ratings-api.uschess.org) is undocumented and unofficial — its routes
were discovered by reading the ratings website's JS bundle (ADR 0003: USCF is
enrichment, never a dependency).  This module hides every transport concern:
the base URL, timeouts, the User-Agent (Cloudflare fronts the API — identify
honestly, never with a default library UA), and pagination.

Endpoints return raw JSON (dicts/lists); turning them into typed records is
uscf_core's job — the same split as lichess_client (raw PGN) / pgn_stats_core.

Public API
----------
fetch_member_profile       GET /members/{id} → dict (ratings, ranks, floor, membership)
fetch_rating_supplements   GET /members/{id}/rating-supplements → list (Official series)
fetch_member_sections      GET /members/{id}/sections → list (Live series)
fetch_member_games         GET /members/{id}/games → list (USCF Game Records)
fetch_member_norms         GET /members/{id}/norms → list (official norms)
fetch_member_awards        GET /members/{id}/awards → list (official awards)
UscfError                  Base class for anything that goes wrong talking to USCF.
UscfMemberNotFoundError    The member ID does not exist.
UscfUnreachableError       Network failure / timeout — USCF is unreachable.
"""
from __future__ import annotations

import requests

__all__ = [
    "UscfError",
    "UscfMemberNotFoundError",
    "UscfUnreachableError",
    "fetch_member_awards",
    "fetch_member_games",
    "fetch_member_norms",
    "fetch_member_profile",
    "fetch_member_sections",
    "fetch_rating_supplements",
]

_API_BASE = "https://ratings-api.uschess.org/api/v1"

# Cloudflare fronts the API: identify as a real app (mirrors the Lichess client).
_USER_AGENT = "uscf-dashboard/2.0 (https://github.com/danielgentile22/uscf-dashboard)"

_DEFAULT_TIMEOUT = 30.0

# List endpoints paginate; ask for big pages so a routine Sync stays at one
# request per endpoint (politeness toward an API we were not invited to use).
_PAGE_SIZE = 100


class UscfError(Exception):
    """Something went wrong talking to the USCF ratings API."""


class UscfMemberNotFoundError(UscfError):
    """The USCF member ID does not exist."""


class UscfUnreachableError(UscfError):
    """USCF could not be reached (network failure or timeout)."""


def fetch_member_profile(member_id: str, *, timeout: float = _DEFAULT_TIMEOUT) -> dict:
    """
    Fetch the member profile for *member_id*: ratings for every rating system,
    national/state rank, rating floor, membership status and expiration.

    Raises
    ------
    UscfMemberNotFoundError : the member ID does not exist.
    UscfUnreachableError    : USCF could not be reached.
    UscfError               : any other non-success response.
    """
    return _get_json(
        f"/members/{member_id}", what=f"member {member_id!r}", timeout=timeout
    )


def fetch_rating_supplements(
    member_id: str, *, timeout: float = _DEFAULT_TIMEOUT
) -> list[dict]:
    """
    Fetch every monthly rating supplement for *member_id* — the Official
    Rating series.  Pagination is handled internally.

    Raises the same typed errors as :func:`fetch_member_profile`.
    """
    return _get_all_pages(
        f"/members/{member_id}/rating-supplements",
        what=f"rating supplements for member {member_id!r}",
        timeout=timeout,
    )


def fetch_member_sections(
    member_id: str, *, timeout: float = _DEFAULT_TIMEOUT
) -> list[dict]:
    """
    Fetch every Section *member_id* has played, with per-Section pre/post
    ratings (decimals) — the Live Rating series.  Pagination is handled
    internally.

    Raises the same typed errors as :func:`fetch_member_profile`.
    """
    return _get_all_pages(
        f"/members/{member_id}/sections",
        what=f"sections for member {member_id!r}",
        timeout=timeout,
    )


def fetch_member_games(
    member_id: str, *, timeout: float = _DEFAULT_TIMEOUT
) -> list[dict]:
    """
    Fetch every USCF Game Record for *member_id* — opponent (with member ID),
    color, outcome, Rated Event, Section, and rating system (issue #28).
    Pagination is handled internally.

    Raises the same typed errors as :func:`fetch_member_profile`.
    """
    return _get_all_pages(
        f"/members/{member_id}/games",
        what=f"games for member {member_id!r}",
        timeout=timeout,
    )


def fetch_member_norms(
    member_id: str, *, timeout: float = _DEFAULT_TIMEOUT
) -> list[dict]:
    """
    Fetch every norm *member_id* has earned — official achievements toward
    titles (issue #36).  Pagination is handled internally (the live endpoint
    returns bare items without pagination fields; both shapes are tolerated).

    Raises the same typed errors as :func:`fetch_member_profile`.
    """
    return _get_all_pages(
        f"/members/{member_id}/norms",
        what=f"norms for member {member_id!r}",
        timeout=timeout,
    )


def fetch_member_awards(
    member_id: str, *, timeout: float = _DEFAULT_TIMEOUT
) -> list[dict]:
    """
    Fetch every award *member_id* has earned — milestones USCF itself
    recognizes, like the 25th career win (issue #36).  Pagination is handled
    internally.

    Raises the same typed errors as :func:`fetch_member_profile`.
    """
    return _get_all_pages(
        f"/members/{member_id}/awards",
        what=f"awards for member {member_id!r}",
        timeout=timeout,
    )


def _get_all_pages(path: str, *, what: str, timeout: float) -> list[dict]:
    """Follow hasNextPage until a list endpoint is exhausted; return all items."""
    items: list[dict] = []
    offset = 0
    while True:
        page = _get_json(
            path, what=what, timeout=timeout,
            params={"pageSize": _PAGE_SIZE, "offset": offset},
        )
        page_items = page.get("items", [])
        items.extend(page_items)
        if not page.get("hasNextPage") or not page_items:
            return items
        offset += len(page_items)


def _get_json(
    path: str, *, what: str, timeout: float, params: dict | None = None
) -> dict:
    """GET one API path and return its JSON body, or raise a typed error."""
    url = f"{_API_BASE}{path}"
    try:
        response = requests.get(
            url, params=params, headers={"User-Agent": _USER_AGENT}, timeout=timeout
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise UscfUnreachableError(
            f"Could not reach USCF to fetch {what}: {exc}"
        ) from exc

    if response.status_code == 404:
        raise UscfMemberNotFoundError(
            f"USCF has no record of {what}. Check the configured USCF member ID."
        )
    if response.status_code != 200:
        raise UscfError(f"USCF returned HTTP {response.status_code} for {what}.")
    return response.json()
