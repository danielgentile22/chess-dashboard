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
            profile = uscf_client.fetch_member_profile("12345678")

        # The raw profile comes back intact for uscf_core to interpret
        assert profile["id"] == "12345678"
        assert profile["lastName"] == "Gentile"
        # The member profile endpoint was called for the right member
        url = get.call_args.args[0]
        assert url.endswith("/members/12345678")
        assert "ratings-api.uschess.org" in url


class TestFetchRatingSupplements:
    """The monthly Official Rating series endpoint (issue #27)."""

    def test_returns_the_supplement_items(self, uscf_supplements_json):
        with mock.patch(
            "uscf_client.requests.get",
            return_value=_response(json_body=uscf_supplements_json),
        ) as get:
            items = uscf_client.fetch_rating_supplements("12345678")

        # The items come back unwrapped — pagination is the client's problem
        assert len(items) == 10
        assert items[0]["ratingSupplementDate"] == "2026-06-01"
        url = get.call_args.args[0]
        assert url.endswith("/members/12345678/rating-supplements")


class TestFetchMemberSections:
    """The per-Section pre/post rating endpoint — the Live series (issue #27)."""

    def test_returns_the_section_items(self, uscf_sections_json):
        with mock.patch(
            "uscf_client.requests.get",
            return_value=_response(json_body=uscf_sections_json),
        ) as get:
            items = uscf_client.fetch_member_sections("12345678")

        assert len(items) == 24
        assert items[0]["sectionName"] == "LADDER"
        url = get.call_args.args[0]
        assert url.endswith("/members/12345678/sections")


class TestFetchMemberGames:
    """The games endpoint — every rated game with opponent and outcome (issue #28)."""

    def test_returns_the_game_record_items(self, uscf_games_json):
        with mock.patch(
            "uscf_client.requests.get",
            return_value=_response(json_body=uscf_games_json),
        ) as get:
            items = uscf_client.fetch_member_games("12345678")

        # All 63 USCF Game Records come back unwrapped, raw, for uscf_core to match
        assert len(items) == 63
        assert items[0]["opponent"]["id"] == "20000056"
        assert items[0]["player"]["outcome"] == "Win"
        url = get.call_args.args[0]
        assert url.endswith("/members/12345678/games")


class TestFetchMemberNorms:
    """The norms endpoint — official achievements toward titles (issue #36)."""

    def test_returns_the_norm_items(self, uscf_norms_json):
        with mock.patch(
            "uscf_client.requests.get",
            return_value=_response(json_body=uscf_norms_json),
        ) as get:
            items = uscf_client.fetch_member_norms("12345678")

        # Daniel's FourthCategory norm from the Oak Grove Open comes back raw
        assert len(items) == 1
        assert items[0]["level"] == "FourthCategory"
        assert items[0]["event"]["name"] == "First Annual Oak Grove Open"
        url = get.call_args.args[0]
        assert url.endswith("/members/12345678/norms")


class TestFetchMemberAwards:
    """The awards endpoint — milestones USCF itself recognizes (issue #36)."""

    def test_returns_the_award_items(self, uscf_awards_json):
        with mock.patch(
            "uscf_client.requests.get",
            return_value=_response(json_body=uscf_awards_json),
        ) as get:
            items = uscf_client.fetch_member_awards("12345678")

        # The 25th-career-win milestone comes back raw
        assert len(items) == 1
        assert items[0]["category"] == "WinMilestone"
        assert items[0]["winCount"] == 25
        url = get.call_args.args[0]
        assert url.endswith("/members/12345678/awards")


class TestPagination:
    """List endpoints paginate; the client follows hasNextPage internally.

    Daniel's data fits in one page today, but a long career won't (handoff
    API note: handle hasNextPage anyway)."""

    def test_all_pages_are_fetched_and_concatenated(self):
        page1 = {
            "items": [{"id": "older"}],
            "offset": 0, "pageSize": 1,
            "hasPreviousPage": False, "hasNextPage": True,
        }
        page2 = {
            "items": [{"id": "oldest"}],
            "offset": 1, "pageSize": 1,
            "hasPreviousPage": True, "hasNextPage": False,
        }
        responses = [_response(json_body=page1), _response(json_body=page2)]
        with mock.patch("uscf_client.requests.get", side_effect=responses) as get:
            items = uscf_client.fetch_member_sections("12345678")

        assert items == [{"id": "older"}, {"id": "oldest"}]
        # The second request asked for the next page (offset moved past page 1)
        assert get.call_count == 2
        second_params = get.call_args_list[1].kwargs["params"]
        assert second_params["offset"] == 1

    def test_single_page_needs_one_request(self, uscf_supplements_json):
        with mock.patch(
            "uscf_client.requests.get",
            return_value=_response(json_body=uscf_supplements_json),
        ) as get:
            uscf_client.fetch_rating_supplements("12345678")

        assert get.call_count == 1


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
                uscf_client.fetch_member_profile("12345678")

    def test_connection_error_raises_unreachable(self):
        with mock.patch(
            "uscf_client.requests.get",
            side_effect=requests.ConnectionError("no route to host"),
        ):
            with pytest.raises(uscf_client.UscfUnreachableError):
                uscf_client.fetch_member_profile("12345678")

    def test_server_error_raises_uscf_error(self):
        """A 5xx (or Cloudflare block page) is a UscfError, not a crash."""
        with mock.patch("uscf_client.requests.get", return_value=_response(503)):
            with pytest.raises(uscf_client.UscfError):
                uscf_client.fetch_member_profile("12345678")

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
            uscf_client.fetch_member_profile("12345678")

        headers = get.call_args.kwargs["headers"]
        assert "uscf-dashboard" in headers["User-Agent"]
        assert "python-requests" not in headers["User-Agent"]

    def test_request_has_a_timeout(self, uscf_profile_json):
        """A hung USCF API must never hang a Sync forever."""
        with mock.patch(
            "uscf_client.requests.get", return_value=_response(json_body=uscf_profile_json)
        ) as get:
            uscf_client.fetch_member_profile("12345678")

        assert get.call_args.kwargs["timeout"] > 0
