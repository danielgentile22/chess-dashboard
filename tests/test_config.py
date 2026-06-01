"""
tests/test_config.py
====================
Tests for configuration parsing.
"""
from __future__ import annotations

from config import parse_study_ids


class TestParseStudyIds:
    def test_single_id(self):
        assert parse_study_ids("abcdWXYZ") == ["abcdWXYZ"]

    def test_comma_separated_ids(self):
        assert parse_study_ids("abcdWXYZ,abcd1234") == ["abcdWXYZ", "abcd1234"]

    def test_whitespace_around_ids_is_stripped(self):
        assert parse_study_ids(" abcdWXYZ , abcd1234 ") == ["abcdWXYZ", "abcd1234"]

    def test_empty_string_gives_no_ids(self):
        assert parse_study_ids("") == []

    def test_trailing_comma_ignored(self):
        assert parse_study_ids("abcdWXYZ,") == ["abcdWXYZ"]
