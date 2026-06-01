"""
tests/test_sync.py
==================
Tests for the Sync orchestrator (sync.py).

The Lichess client is stubbed at the module boundary — no network.
"""
from __future__ import annotations

from unittest import mock

import pytest

import sync
from lichess_client import LichessUnreachableError


def stub_studies(**study_pgns):
    """
    Patch the Lichess client inside sync with a stub that maps
    study_id → PGN text (or raises if the value is an Exception).
    """
    def fake_fetch(study_id, **kwargs):
        value = study_pgns[study_id]
        if isinstance(value, Exception):
            raise value
        return value

    return mock.patch.object(sync, "fetch_study_pgn", side_effect=fake_fetch)


class TestSyncSingleStudy:
    def test_one_study_produces_its_games(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            result = sync.sync_studies(["study1"], player_name="Test Player")

        assert len(result.df) == 7
        assert result.player == "Test Player"
        assert result.failures == []


class TestMultiStudyMerge:
    def test_games_from_all_studies_appear(self, sample_pgn_text, sample_pgn_study2_text):
        """Study 1 has 7 games; Study 2 adds 2 new ones (plus 1 duplicate)."""
        with stub_studies(study1=sample_pgn_text, study2=sample_pgn_study2_text):
            result = sync.sync_studies(["study1", "study2"], player_name="Test Player")

        # 7 + 3 - 1 duplicate = 9 unique Games
        assert len(result.df) == 9
        # Games from both Studies are present
        assert "Opponent E" in result.df["Opponent"].values  # only in study 2
        assert "Opponent C" in result.df["Opponent"].values  # only in study 1

    def test_games_sorted_by_date_across_studies(
        self, sample_pgn_text, sample_pgn_study2_text
    ):
        """Study 2's March game must land between Study 1's January and June games."""
        with stub_studies(study1=sample_pgn_text, study2=sample_pgn_study2_text):
            result = sync.sync_studies(["study1", "study2"], player_name="Test Player")

        dates = result.df["Date_dt"].tolist()
        assert dates == sorted(dates)
        # And the merged order is reflected in the Index column (1..N)
        assert result.df["Index"].tolist() == list(range(1, len(result.df) + 1))

    def test_duplicate_chapter_urls_appear_only_once(
        self, sample_pgn_text, sample_pgn_study2_text
    ):
        """The same ChapterURL across Studies (or twice in config) is one Game."""
        with stub_studies(study1=sample_pgn_text, study2=sample_pgn_study2_text):
            result = sync.sync_studies(["study1", "study2"], player_name="Test Player")

        assert result.df["ChapterURL"].is_unique

    def test_same_study_listed_twice_is_harmless(self, sample_pgn_text):
        """A config typo (same ID twice) must not double the Games."""
        with stub_studies(study1=sample_pgn_text):
            result = sync.sync_studies(["study1", "study1"], player_name="Test Player")

        assert len(result.df) == 7


class TestPartialFailure:
    def test_one_study_failing_keeps_games_from_the_others(
        self, sample_pgn_text
    ):
        """A Lichess outage on one Study never blanks the rest of the archive."""
        boom = LichessUnreachableError("Could not reach Lichess to fetch Study 'study2'")
        with stub_studies(study1=sample_pgn_text, study2=boom):
            result = sync.sync_studies(["study1", "study2"], player_name="Test Player")

        assert len(result.df) == 7
        assert result.partial
        # The failure names the Study and the reason
        assert result.failures[0][0] == "study2"
        assert "Could not reach Lichess" in result.failures[0][1]

    def test_all_studies_failing_raises_sync_error(self):
        boom1 = LichessUnreachableError("down")
        boom2 = LichessUnreachableError("down")
        with stub_studies(study1=boom1, study2=boom2):
            with pytest.raises(sync.SyncError) as exc_info:
                sync.sync_studies(["study1", "study2"], player_name="Test Player")

        # The error names every Study that failed
        assert "study1" in str(exc_info.value)
        assert "study2" in str(exc_info.value)
