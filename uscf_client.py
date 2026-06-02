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
fetch_member_profile      GET /members/{id} → dict (ratings, ranks, floor, membership)
UscfError                 Base class for anything that goes wrong talking to USCF.
UscfMemberNotFoundError   The member ID does not exist.
UscfUnreachableError      Network failure / timeout — USCF is unreachable.
"""
from __future__ import annotations

import requests

__all__ = [
    "UscfError",
    "UscfMemberNotFoundError",
    "UscfUnreachableError",
    "fetch_member_profile",
]

_API_BASE = "https://ratings-api.uschess.org/api/v1"

# Cloudflare fronts the API: identify as a real app (mirrors the Lichess client).
_USER_AGENT = "uscf-dashboard/2.0 (https://github.com/danielgentile22/uscf-dashboard)"

_DEFAULT_TIMEOUT = 30.0


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


def _get_json(path: str, *, what: str, timeout: float) -> dict:
    """GET one API path and return its JSON body, or raise a typed error."""
    url = f"{_API_BASE}{path}"
    try:
        response = requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=timeout
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
