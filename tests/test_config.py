"""
tests/test_config.py
====================
Tests for configuration parsing.
"""
from __future__ import annotations

from config import parse_member_id, parse_study_ids


class TestParseStudyIds:
    def test_single_id(self):
        assert parse_study_ids("6jYtXHGp") == ["6jYtXHGp"]

    def test_comma_separated_ids(self):
        assert parse_study_ids("6jYtXHGp,abcd1234") == ["6jYtXHGp", "abcd1234"]

    def test_whitespace_around_ids_is_stripped(self):
        assert parse_study_ids(" 6jYtXHGp , abcd1234 ") == ["6jYtXHGp", "abcd1234"]

    def test_empty_string_gives_no_ids(self):
        assert parse_study_ids("") == []

    def test_trailing_comma_ignored(self):
        assert parse_study_ids("6jYtXHGp,") == ["6jYtXHGp"]


class TestParseMemberId:
    """The USCF member ID setting, configurable alongside the Study IDs (issue #25)."""

    def test_member_id_passed_through(self):
        assert parse_member_id("32487228") == "32487228"

    def test_whitespace_is_stripped(self):
        assert parse_member_id("  32487228 ") == "32487228"

    def test_unset_means_no_uscf(self):
        """No member ID configured → the dashboard runs without USCF enrichment."""
        assert parse_member_id("") is None
        assert parse_member_id("   ") is None
