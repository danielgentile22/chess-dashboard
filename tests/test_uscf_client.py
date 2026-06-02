"""
tests/test_uscf_client.py
=========================
Unit tests for the USCF MUIR API client.

All HTTP is mocked with real captured response shapes — the suite never
touches the network (the conftest guard enforces this).
"""
from __future__ import annotations

from unittest import mock

import pytest
import requests

import uscf_client


def _response(status_code: int = 200, json_body=None):
    """Build a fake requests.Response with a JSON body."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


class TestFetchMemberProfile:
    def test_returns_the_member_profile(self, uscf_profile_json):
        with mock.patch(
            "uscf_client.requests.get", return_value=_response(json_body=uscf_profile_json)
        ) as get:
            profile = uscf_client.fetch_member_profile("32487228")

        # The raw profile comes back intact for uscf_core to interpret
        assert profile["id"] == "32487228"
        assert profile["lastName"] == "Gentile"
        # The member profile endpoint was called for the right member
        url = get.call_args.args[0]
        assert url.endswith("/members/32487228")
        assert "ratings-api.uschess.org" in url


class TestTypedErrors:
    """Transport failures surface as typed errors the Sync can catch (ADR 0003)."""

    def test_unknown_member_id_raises_not_found(self):
        with mock.patch("uscf_client.requests.get", return_value=_response(404)):
            with pytest.raises(uscf_client.UscfMemberNotFoundError) as exc_info:
                uscf_client.fetch_member_profile("00000000")

        # The error names the bad member ID so the user can fix their config
        assert "00000000" in str(exc_info.value)

    def test_timeout_raises_unreachable(self):
        with mock.patch(
            "uscf_client.requests.get", side_effect=requests.Timeout("timed out")
        ):
            with pytest.raises(uscf_client.UscfUnreachableError):
                uscf_client.fetch_member_profile("32487228")

    def test_connection_error_raises_unreachable(self):
        with mock.patch(
            "uscf_client.requests.get",
            side_effect=requests.ConnectionError("no route to host"),
        ):
            with pytest.raises(uscf_client.UscfUnreachableError):
                uscf_client.fetch_member_profile("32487228")

    def test_server_error_raises_uscf_error(self):
        """A 5xx (or Cloudflare block page) is a UscfError, not a crash."""
        with mock.patch("uscf_client.requests.get", return_value=_response(503)):
            with pytest.raises(uscf_client.UscfError):
                uscf_client.fetch_member_profile("32487228")

    def test_every_error_is_a_uscf_error(self):
        """Sync needs exactly one exception type to catch for the whole client."""
        assert issubclass(uscf_client.UscfMemberNotFoundError, uscf_client.UscfError)
        assert issubclass(uscf_client.UscfUnreachableError, uscf_client.UscfError)


class TestRequestHygiene:
    """Politeness toward an API we were not invited to use (ADR 0003)."""

    def test_real_user_agent_always_sent(self, uscf_profile_json):
        """Cloudflare fronts the API — never rely on a default library UA."""
        with mock.patch(
            "uscf_client.requests.get", return_value=_response(json_body=uscf_profile_json)
        ) as get:
            uscf_client.fetch_member_profile("32487228")

        headers = get.call_args.kwargs["headers"]
        assert "uscf-dashboard" in headers["User-Agent"]
        assert "python-requests" not in headers["User-Agent"]

    def test_request_has_a_timeout(self, uscf_profile_json):
        """A hung USCF API must never hang a Sync forever."""
        with mock.patch(
            "uscf_client.requests.get", return_value=_response(json_body=uscf_profile_json)
        ) as get:
            uscf_client.fetch_member_profile("32487228")

        assert get.call_args.kwargs["timeout"] > 0
