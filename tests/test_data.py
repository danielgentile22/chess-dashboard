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
