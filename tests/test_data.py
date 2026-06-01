"""
tests/test_data.py
==================
Tests for the application data store (data.py).

The Lichess client is stubbed at the module boundary (inside sync) — no
network. The real Sync orchestrator runs, so these are integration tests
of the store + orchestrator through the store's public interface.
"""
from __future__ import annotations

from unittest import mock

import pytest

import data
import sync
from lichess_client import LichessUnreachableError, StudyNotFoundError


@pytest.fixture(autouse=True)
def reset_data_store():
    """Each test starts with an empty store."""
    data.reset()
    yield
    data.reset()


def stub_studies(**study_pgns):
    """Stub the Lichess client: study_id → PGN text (or an Exception to raise)."""
    def fake_fetch(study_id, **kwargs):
        value = study_pgns[study_id]
        if isinstance(value, Exception):
            raise value
        return value

    return mock.patch.object(sync, "fetch_study_pgn", side_effect=fake_fetch)


class TestInitialize:
    def test_boots_from_a_lichess_study(self, sample_pgn_text):
        """initialize() Syncs the designated Studies and serves the games."""
        with stub_studies(study1=sample_pgn_text):
            df, player = data.initialize(["study1"], player_name="Test Player")

        assert len(df) == 7
        assert player == "Test Player"
        # The store now serves the same data
        assert len(data.get_df()) == 7
        assert data.get_player() == "Test Player"
        assert data.is_loaded()

    def test_boots_from_multiple_studies_merged(
        self, sample_pgn_text, sample_pgn_study2_text
    ):
        with stub_studies(study1=sample_pgn_text, study2=sample_pgn_study2_text):
            df, _ = data.initialize(["study1", "study2"], player_name="Test Player")

        assert len(df) == 9  # 7 + 3 - 1 duplicate

    def test_partial_failure_still_loads_and_is_reported(self, sample_pgn_text):
        """One Study down → other Studies' games load; failure is queryable."""
        boom = LichessUnreachableError("lichess is down")
        with stub_studies(study1=sample_pgn_text, study2=boom):
            df, _ = data.initialize(["study1", "study2"], player_name="Test Player")

        assert len(df) == 7
        failures = data.get_sync_failures()
        assert len(failures) == 1
        assert failures[0][0] == "study2"

    def test_empty_studies_raise_clear_error(self):
        with stub_studies(study1=""):
            with pytest.raises(RuntimeError) as exc_info:
                data.initialize(["study1"], player_name="Test Player")

        assert "study1" in str(exc_info.value)
        assert not data.is_loaded()

    def test_unknown_study_error_propagates(self):
        """Total failure reaches the caller so app startup can report it."""
        with stub_studies(badstudy=StudyNotFoundError("Study 'badstudy' not found")):
            with pytest.raises(sync.SyncError):
                data.initialize(["badstudy"], player_name="Test Player")

        assert not data.is_loaded()

    def test_initialize_records_sync_time(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        assert data.synced_at() is not None


# ---------------------------------------------------------------------------
# refresh() — the Sync button path (issue #6)
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_refresh_picks_up_new_games(self, sample_pgn_text, sample_pgn_study2_text):
        """A Game added on Lichess appears after refresh(), and is reported as new."""
        # Startup: only study1's 7 games exist
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")
        assert len(data.get_df()) == 7

        # On Lichess, the study now has 2 more games (simulated by study2's content
        # appended to study1's export under the same study ID)
        grown_study = sample_pgn_text + "\n\n" + sample_pgn_study2_text
        with stub_studies(study1=grown_study):
            outcome = data.refresh()

        assert outcome.status == "success"
        assert len(data.get_df()) == 9  # 7 + 3 - 1 duplicate
        # The two genuinely new Games are reported with opponent + result
        assert len(outcome.new_games) == 2
        opponents = {g["Opponent"] for g in outcome.new_games}
        assert opponents == {"Opponent E", "Opponent A"}
        for g in outcome.new_games:
            assert g["Outcome"] in ("Win", "Draw", "Loss", "Unknown")

    def test_no_change_refresh_reports_nothing_new(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")
            outcome = data.refresh()

        assert outcome.status == "success"
        assert outcome.new_games == []
        assert len(data.get_df()) == 7

    def test_failed_refresh_leaves_current_data_untouched(self, sample_pgn_text):
        """Atomic swap: a failed Sync never disturbs what's currently shown."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")
        df_before = data.get_df()
        synced_before = data.synced_at()

        with stub_studies(study1=LichessUnreachableError("lichess is down")):
            outcome = data.refresh()

        assert outcome.status == "error"
        assert "down" in outcome.error
        # Current data and freshness are exactly what they were
        assert data.get_df() is df_before
        assert data.synced_at() == synced_before

    def test_refresh_updates_sync_time_on_success(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")
            before = data.synced_at()
            outcome = data.refresh()

        assert outcome.status == "success"
        assert data.synced_at() >= before

    def test_concurrent_refresh_is_ignored_not_doubled(self, sample_pgn_text):
        """A Sync triggered while one is running reports 'already running'."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

            # Simulate an in-flight Sync holding the lock
            acquired = data._sync_lock.acquire(blocking=False)
            assert acquired
            try:
                outcome = data.refresh()
            finally:
                data._sync_lock.release()

        assert outcome.status == "already_running"
        assert len(data.get_df()) == 7  # nothing changed

    def test_refresh_before_initialize_errors_cleanly(self):
        outcome = data.refresh()
        assert outcome.status == "error"
