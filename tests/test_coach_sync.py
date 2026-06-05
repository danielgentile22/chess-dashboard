"""
tests/test_coach_sync.py
========================
Coach ingestion on Sync (issue #74 [G4]).

After the Lichess Sync, a coach-ingestion pass fetches each designated coach
Study (with the user's token for private ones), runs ``coach_match_core``, and
caches the result with the disposable-cache lifecycle: a coach Study being
unreachable degrades to cached/empty and never fails the Sync (ADR 0003).

Mirrors the existing mocked-client Sync tests — the Lichess client is stubbed at
sync's module boundary; no network.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import sync
from lichess_client import StudyNotFoundError

COACH_PGN = (Path(__file__).parent / "fixtures" / "coach-study.pgn").read_text()
GEORGINA_URL = "https://lichess.org/study/6jYtXHGp/GKSICQAY"


def _stub_fetch(**study_pgns):
    def fake(study_id, **kwargs):
        value = study_pgns[study_id]
        if isinstance(value, Exception):
            raise value
        return value
    return mock.patch.object(sync, "fetch_study_pgn", side_effect=fake)


class TestSyncCoach:
    def test_no_coach_studies_is_an_empty_no_failure_result(self, study_snapshot_df):
        result = sync.sync_coach([], study_snapshot_df)
        assert result.result.matches == ()
        assert result.failure == ""
        assert result.available is False

    def test_reachable_coach_study_is_fetched_matched_and_available(
        self, study_snapshot_df, tmp_path
    ):
        with _stub_fetch(coach1=COACH_PGN):
            result = sync.sync_coach(
                ["coach1"], study_snapshot_df,
                cache_path=str(tmp_path / "coach.pgn"),
            )
        assert result.available is True
        assert result.from_cache is False
        # the Georgina win is matched, with the coach's 7 comments
        assert len(result.result.comments_for(GEORGINA_URL)) == 7

    def test_private_coach_study_is_fetched_with_the_users_token(
        self, study_snapshot_df, tmp_path
    ):
        seen = {}

        def fake(study_id, **kwargs):
            seen["token"] = kwargs.get("token")
            return COACH_PGN

        with mock.patch.object(sync, "fetch_study_pgn", side_effect=fake):
            sync.sync_coach(["coach1"], study_snapshot_df, token="lip_secret",
                            cache_path=str(tmp_path / "coach.pgn"))
        assert seen["token"] == "lip_secret"

    def test_unreachable_coach_study_with_no_cache_degrades_to_empty(
        self, study_snapshot_df, tmp_path
    ):
        with _stub_fetch(coach1=StudyNotFoundError("private")):
            result = sync.sync_coach(
                ["coach1"], study_snapshot_df,
                cache_path=str(tmp_path / "coach.pgn"),
            )
        assert result.available is False
        assert result.failure  # a reason is recorded
        # the matcher never ran on absent content — no Games invented (ADR 0001)
        assert result.result.matches == ()

    def test_coach_content_survives_a_brief_outage_from_cache(
        self, study_snapshot_df, tmp_path
    ):
        cache = str(tmp_path / "coach.pgn")
        with _stub_fetch(coach1=COACH_PGN):
            sync.sync_coach(["coach1"], study_snapshot_df, cache_path=cache)
        # Next Sync: the coach Study is unreachable — fall back to the cache
        with _stub_fetch(coach1=StudyNotFoundError("private")):
            result = sync.sync_coach(["coach1"], study_snapshot_df, cache_path=cache)
        assert result.available is True
        assert result.from_cache is True
        assert len(result.result.comments_for(GEORGINA_URL)) == 7

    def test_one_unreachable_study_never_loses_the_reachable_ones(
        self, study_snapshot_df, tmp_path
    ):
        """A partial fetch still matches the Studies that succeeded."""
        with _stub_fetch(coach1=COACH_PGN, coach2=StudyNotFoundError("private")):
            result = sync.sync_coach(
                ["coach1", "coach2"], study_snapshot_df,
                cache_path=str(tmp_path / "coach.pgn"),
            )
        assert result.available is True
        assert len(result.result.comments_for(GEORGINA_URL)) == 7

    def test_sync_coach_never_raises(self, study_snapshot_df, tmp_path):
        with _stub_fetch(coach1=StudyNotFoundError("boom")):
            # must return a result, not raise
            result = sync.sync_coach(["coach1"], study_snapshot_df,
                                     cache_path=str(tmp_path / "coach.pgn"))
        assert result is not None
