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


class TestCache:
    """A successful Sync writes a disposable PGN cache (never a source of truth — ADR 0001)."""

    def test_successful_sync_writes_cache_file(self, sample_pgn_text, tmp_path):
        cache = tmp_path / "games.pgn"
        with stub_studies(study1=sample_pgn_text):
            sync.sync_studies(["study1"], player_name="Test Player", cache_path=str(cache))

        assert cache.exists()
        # The cache holds real PGN that parses back to the same games
        cached_df, _, _ = sync.load_from_cache(str(cache), player_name="Test Player")
        assert len(cached_df) == 7

    def test_next_sync_overwrites_cache(
        self, sample_pgn_text, sample_pgn_study2_text, tmp_path
    ):
        cache = tmp_path / "games.pgn"
        with stub_studies(study1=sample_pgn_text):
            sync.sync_studies(["study1"], player_name="Test Player", cache_path=str(cache))
        with stub_studies(study1=sample_pgn_text, study2=sample_pgn_study2_text):
            sync.sync_studies(
                ["study1", "study2"], player_name="Test Player", cache_path=str(cache)
            )

        cached_df, _, _ = sync.load_from_cache(str(cache), player_name="Test Player")
        assert len(cached_df) == 9  # the bigger, newer dataset

    def test_load_from_cache_reports_cache_age(self, sample_pgn_text, tmp_path):
        cache = tmp_path / "games.pgn"
        with stub_studies(study1=sample_pgn_text):
            sync.sync_studies(["study1"], player_name="Test Player", cache_path=str(cache))

        _, _, cached_at = sync.load_from_cache(str(cache), player_name="Test Player")
        assert cached_at is not None  # a timestamp the UI can show

    def test_load_from_missing_cache_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sync.load_from_cache(str(tmp_path / "nope.pgn"), player_name="Test Player")

    def test_unwritable_cache_path_does_not_break_the_sync(self, sample_pgn_text):
        """The app stays stateless on hosts without a writable disk (ADR 0002)."""
        with stub_studies(study1=sample_pgn_text):
            result = sync.sync_studies(
                ["study1"], player_name="Test Player",
                cache_path="/nonexistent-dir/sub/games.pgn",
            )

        # Sync still succeeded; the cache write failure was only logged
        assert len(result.df) == 7


class TestDetectNewGames:
    """New-Game detection compares ChapterURLs against the previous Sync's data."""

    def test_games_with_unseen_chapter_urls_are_new(
        self, sample_pgn_text, sample_pgn_study2_text
    ):
        # Previous sync had only study1; now study2 is designated too
        with stub_studies(study1=sample_pgn_text):
            before = sync.sync_studies(["study1"], player_name="Test Player")
        with stub_studies(study1=sample_pgn_text, study2=sample_pgn_study2_text):
            after = sync.sync_studies(["study1", "study2"], player_name="Test Player")

        new = sync.detect_new_games(after.df, set(before.df["ChapterURL"]))

        # Study 2 has 2 new games (its third is a duplicate of study 1's chap0007)
        assert len(new) == 2
        assert set(new["Opponent"]) == {"Opponent E", "Opponent A"}

    def test_no_change_sync_detects_nothing_new(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            before = sync.sync_studies(["study1"], player_name="Test Player")
            after = sync.sync_studies(["study1"], player_name="Test Player")

        new = sync.detect_new_games(after.df, set(before.df["ChapterURL"]))
        assert len(new) == 0

    def test_first_sync_everything_is_new(self, sample_pgn_text):
        """Against an empty previous state, every Game is new."""
        with stub_studies(study1=sample_pgn_text):
            result = sync.sync_studies(["study1"], player_name="Test Player")

        new = sync.detect_new_games(result.df, set())
        assert len(new) == 7
