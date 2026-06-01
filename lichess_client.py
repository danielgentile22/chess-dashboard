"""
lichess_client.py
=================
The only module that talks HTTP to Lichess.

Hides every transport concern from the rest of the app: the study export
endpoint, timeouts, the User-Agent requirement (Lichess returns 404 to
non-browser user agents on HTML routes), and the optional API token for
private Studies.

Public API
----------
fetch_study_pgn       Fetch every Chapter of one Study as PGN text.
LichessError          Base class for anything that goes wrong talking to Lichess.
StudyNotFoundError    The study ID does not exist (or is private without a token).
LichessUnreachableError  Network failure / timeout — Lichess is unreachable.
"""
from __future__ import annotations

import requests

__all__ = [
    "LichessError",
    "LichessUnreachableError",
    "StudyNotFoundError",
    "fetch_study_pgn",
]

_API_BASE = "https://lichess.org/api"

# Identify the app honestly. The /api routes accept any UA, but Lichess 404s
# generic library UAs on HTML routes, so never rely on the default.
_USER_AGENT = "uscf-dashboard/2.0 (https://github.com/danielgentile22/uscf-dashboard)"

_DEFAULT_TIMEOUT = 30.0


class LichessError(Exception):
    """Something went wrong talking to Lichess."""


class StudyNotFoundError(LichessError):
    """The Study does not exist on Lichess (or is private and needs a token)."""


class LichessUnreachableError(LichessError):
    """Lichess could not be reached (network failure or timeout)."""


def fetch_study_pgn(
    study_id: str,
    *,
    token: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """
    Fetch every Chapter of Study *study_id* as a single PGN text.

    Parameters
    ----------
    study_id : The Lichess study ID (e.g. "abcdWXYZ").
    token    : Optional Lichess API token, required only for private Studies.
    timeout  : Per-request timeout in seconds.

    Raises
    ------
    StudyNotFoundError       : The study ID does not exist / is not accessible.
    LichessUnreachableError  : Lichess could not be reached.
    LichessError             : Any other non-success response.
    """
    url = f"{_API_BASE}/study/{study_id}.pgn"
    try:
        response = requests.get(
            url,
            params={"clocks": "false"},  # comments and variations are included by default
            headers=_headers(token),
            timeout=timeout,
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise LichessUnreachableError(
            f"Could not reach Lichess to fetch Study {study_id!r}: {exc}"
        ) from exc

    if response.status_code == 404:
        raise StudyNotFoundError(
            f"Study {study_id!r} was not found on Lichess. "
            "Check the study ID (and the API token if the Study is private)."
        )
    if response.status_code != 200:
        raise LichessError(
            f"Lichess returned HTTP {response.status_code} for Study {study_id!r}."
        )
    return response.text


def _headers(token: str | None) -> dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
