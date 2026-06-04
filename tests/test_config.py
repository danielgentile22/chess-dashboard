"""
tests/test_config.py
====================
Tests for configuration parsing.
"""
from __future__ import annotations

import importlib

import config
from config import parse_member_id, parse_study_ids


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


class TestParseMemberId:
    """The USCF member ID setting, configurable alongside the Study IDs (issue #25)."""

    def test_member_id_passed_through(self):
        assert parse_member_id("12345678") == "12345678"

    def test_whitespace_is_stripped(self):
        assert parse_member_id("  12345678 ") == "12345678"

    def test_unset_means_no_uscf(self):
        """No member ID configured → the dashboard runs without USCF enrichment."""
        assert parse_member_id("") is None
        assert parse_member_id("   ") is None


class TestAnalysisSettings:
    """The AI-summary settings (issue #59 [F5]): both optional, safe defaults."""

    def test_api_key_unset_means_no_summaries(self, monkeypatch):
        """No key → ai_summary is a no-op; the dashboard runs without one."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        reloaded = importlib.reload(config)
        assert reloaded.config.ANTHROPIC_API_KEY is None

    def test_api_key_is_read_and_trimmed(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-ant-123  ")
        reloaded = importlib.reload(config)
        assert reloaded.config.ANTHROPIC_API_KEY == "sk-ant-123"

    def test_cache_path_defaults_to_the_disposable_file(self, monkeypatch):
        monkeypatch.delenv("ANALYSIS_CACHE_PATH", raising=False)
        reloaded = importlib.reload(config)
        assert reloaded.config.ANALYSIS_CACHE_PATH == "analysis_cache.json"

    def test_cache_path_is_configurable(self, monkeypatch):
        monkeypatch.setenv("ANALYSIS_CACHE_PATH", "/var/cache/analysis.json")
        reloaded = importlib.reload(config)
        assert reloaded.config.ANALYSIS_CACHE_PATH == "/var/cache/analysis.json"
