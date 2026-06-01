"""
tests/test_data.py
==================
Tests for the application data store (data.py).

The Lichess client is stubbed at the module boundary — no network.
"""
from __future__ import annotations

from unittest import mock

import pytest

import data
from lichess_client import StudyNotFoundError


@pytest.fixture(autouse=True)
def reset_data_store():
    """Each test starts with an empty store."""
    data.reset()
    yield
    data.reset()


class TestInitialize:
    def test_boots_from_a_lichess_study(self, sample_pgn_text):
        """initialize() fetches the Study, parses it, and serves the games."""
        with mock.patch.object(
            data, "fetch_study_pgn", return_value=sample_pgn_text
        ) as fetch:
            df, player = data.initialize("6jYtXHGp", player_name="Test Player")

        fetch.assert_called_once()
        assert fetch.call_args.args[0] == "6jYtXHGp"
        assert len(df) == 7
        assert player == "Test Player"
        # The store now serves the same data
        assert len(data.get_df()) == 7
        assert data.get_player() == "Test Player"
        assert data.is_loaded()

    def test_empty_study_raises_clear_error(self):
        with mock.patch.object(data, "fetch_study_pgn", return_value=""):
            with pytest.raises(RuntimeError) as exc_info:
                data.initialize("emptyStu", player_name="Test Player")

        assert "emptyStu" in str(exc_info.value)
        assert not data.is_loaded()

    def test_unknown_study_error_propagates(self):
        """Client errors reach the caller untouched so app startup can report them."""
        with mock.patch.object(
            data, "fetch_study_pgn", side_effect=StudyNotFoundError("Study 'x' not found")
        ):
            with pytest.raises(StudyNotFoundError):
                data.initialize("x", player_name="Test Player")

        assert not data.is_loaded()

    def test_api_token_passed_to_client(self, sample_pgn_text):
        with mock.patch.object(
            data, "fetch_study_pgn", return_value=sample_pgn_text
        ) as fetch:
            data.initialize("6jYtXHGp", player_name="Test Player", token="lip_tok")

        assert fetch.call_args.kwargs["token"] == "lip_tok"
