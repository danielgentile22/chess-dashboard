"""
tests/test_lichess_client.py
============================
Unit tests for the Lichess API client.

All HTTP is mocked — the suite never touches the network.
"""
from __future__ import annotations

from unittest import mock

import pytest
import requests

from lichess_client import (
    LichessUnreachableError,
    StudyNotFoundError,
    fetch_study_pgn,
)

# A minimal but realistic study export body (two chapters).
STUDY_PGN = """\
[Event "ACC Friday Ladder"]
[White "Daniel Gentile"]
[Black "Opponent A"]
[Result "1-0"]
[StudyName "USCF OTB Games"]
[ChapterName "Daniel Gentile - Opponent A"]
[ChapterURL "https://lichess.org/study/abcdWXYZ/aaaaaaaa"]

1. e4 e5 1-0

[Event "ACC Friday Ladder"]
[White "Opponent B"]
[Black "Daniel Gentile"]
[Result "0-1"]
[StudyName "USCF OTB Games"]
[ChapterName "Opponent B - Daniel Gentile"]
[ChapterURL "https://lichess.org/study/abcdWXYZ/bbbbbbbb"]

1. d4 d5 0-1
"""


def _response(status_code: int = 200, text: str = STUDY_PGN):
    """Build a fake requests.Response."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = text
    return resp


class TestFetchStudyPgn:
    def test_returns_pgn_text_of_all_chapters(self):
        with mock.patch("lichess_client.requests.get", return_value=_response()) as get:
            pgn = fetch_study_pgn("abcdWXYZ")

        assert "ChapterURL" in pgn
        assert pgn.count("[Event ") == 2
        # The study export endpoint was called for the right study
        url = get.call_args.args[0]
        assert "abcdWXYZ" in url
        assert "/api/study/" in url

    def test_unknown_study_id_raises_not_found(self):
        with mock.patch("lichess_client.requests.get", return_value=_response(404, "")):
            with pytest.raises(StudyNotFoundError) as exc_info:
                fetch_study_pgn("n0Suchld")

        # The error message names the bad study ID so the user can fix their config
        assert "n0Suchld" in str(exc_info.value)

    def test_timeout_raises_unreachable(self):
        with mock.patch(
            "lichess_client.requests.get", side_effect=requests.Timeout("timed out")
        ):
            with pytest.raises(LichessUnreachableError):
                fetch_study_pgn("abcdWXYZ")

    def test_connection_error_raises_unreachable(self):
        with mock.patch(
            "lichess_client.requests.get",
            side_effect=requests.ConnectionError("no route to host"),
        ):
            with pytest.raises(LichessUnreachableError):
                fetch_study_pgn("abcdWXYZ")

    def test_token_sent_as_bearer_auth_header(self):
        with mock.patch("lichess_client.requests.get", return_value=_response()) as get:
            fetch_study_pgn("abcdWXYZ", token="lip_secret123")

        headers = get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer lip_secret123"

    def test_no_auth_header_without_token(self):
        with mock.patch("lichess_client.requests.get", return_value=_response()) as get:
            fetch_study_pgn("abcdWXYZ")

        headers = get.call_args.kwargs["headers"]
        assert "Authorization" not in headers

    def test_real_user_agent_always_sent(self):
        """Lichess 404s default library UAs on some routes — always identify ourselves."""
        with mock.patch("lichess_client.requests.get", return_value=_response()) as get:
            fetch_study_pgn("abcdWXYZ")

        headers = get.call_args.kwargs["headers"]
        assert "uscf-dashboard" in headers["User-Agent"]

    def test_request_has_a_timeout(self):
        """A hung Lichess must never hang the dashboard startup forever."""
        with mock.patch("lichess_client.requests.get", return_value=_response()) as get:
            fetch_study_pgn("abcdWXYZ")

        assert get.call_args.kwargs["timeout"] > 0
