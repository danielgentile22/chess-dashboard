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

COACH_PGN = (Path(__file__).parent / "data" / "coach-study.pgn").read_text()
ALICE_URL = "https://lichess.org/study/abcdWXYZ/alic0001"

# A second coach Study's PGN (distinct Chapter, its own Lichess study id) — used
# to prove per-Study caching keeps one Study's content when another is down.
SECOND_STUDY_PGN = (
    '[Event "Coach review — extra"]\n'
    '[ChapterURL "https://lichess.org/study/coachBBBB/xx000001"]\n\n'
    "1. d4 d5 2. c4 e6 *\n"
)


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
        # the Alice win is matched, with the coach's 7 comments
        assert len(result.result.comments_for(ALICE_URL)) == 7

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
        assert len(result.result.comments_for(ALICE_URL)) == 7

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
        assert len(result.result.comments_for(ALICE_URL)) == 7

    def test_sync_coach_never_raises(self, study_snapshot_df, tmp_path):
        with _stub_fetch(coach1=StudyNotFoundError("boom")):
            # must return a result, not raise
            result = sync.sync_coach(["coach1"], study_snapshot_df,
                                     cache_path=str(tmp_path / "coach.pgn"))
        assert result is not None

    def test_partial_fetch_never_shrinks_or_clobbers_the_full_cache(
        self, study_snapshot_df, tmp_path
    ):
        """One Study down must not lose its Chapters nor overwrite its cache with
        the Studies that happened to fetch — each Study is cached on its own so a
        later total outage still finds the full content (issue #92)."""
        cache = str(tmp_path / "coach.pgn")
        coach2_cache = tmp_path / "coach-coach2.pgn"

        # Sync 1: both reachable → each Study cached under its own file.
        with _stub_fetch(coach1=COACH_PGN, coach2=SECOND_STUDY_PGN):
            sync.sync_coach(["coach1", "coach2"], study_snapshot_df, cache_path=cache)
        assert coach2_cache.read_text() == SECOND_STUDY_PGN

        # Sync 2: coach2 down → its cache is reused and left intact (not shrunk).
        with _stub_fetch(coach1=COACH_PGN, coach2=StudyNotFoundError("down")):
            result = sync.sync_coach(["coach1", "coach2"], study_snapshot_df,
                                     cache_path=cache)
        assert result.from_cache is True          # coach2 served from its cache
        assert result.failure                     # the failure is recorded
        assert coach2_cache.read_text() == SECOND_STUDY_PGN  # untouched
        assert len(result.result.comments_for(ALICE_URL)) == 7  # coach1 still fresh

    def test_legacy_merged_cache_survives_a_full_outage_after_upgrade(
        self, study_snapshot_df, tmp_path
    ):
        """Installs from before per-Study caching have one merged coach.pgn.  A
        full outage right after upgrading (no per-Study caches yet) must still
        serve it, not lose coach content (issue #92)."""
        cache = tmp_path / "coach.pgn"
        cache.write_text(COACH_PGN)   # the pre-upgrade merged cache

        with _stub_fetch(coach1=StudyNotFoundError("down")):
            result = sync.sync_coach(["coach1"], study_snapshot_df,
                                     cache_path=str(cache))

        assert result.from_cache is True
        assert len(result.result.comments_for(ALICE_URL)) == 7
