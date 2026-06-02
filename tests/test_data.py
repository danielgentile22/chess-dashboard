"""
tests/test_data.py
==================
Tests for the application data store (data.py).

The Lichess client is stubbed at the module boundary (inside sync) — no
network. The real Sync orchestrator runs, so these are integration tests
of the store + orchestrator through the store's public interface.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

import data
import sync
from lichess_client import LichessUnreachableError, StudyNotFoundError
from uscf_client import UscfUnreachableError


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


@contextlib.contextmanager
def stub_uscf(profile, supplements=None, sections=None, games=None):
    """Stub the USCF client inside sync: raw JSON values, or Exceptions to raise."""
    def fake(value):
        def fetch(member_id, **kwargs):
            if isinstance(value, Exception):
                raise value
            return value
        return fetch

    with mock.patch.object(sync, "fetch_member_profile", side_effect=fake(profile)), \
         mock.patch.object(sync, "fetch_rating_supplements",
                           side_effect=fake(supplements or [])), \
         mock.patch.object(sync, "fetch_member_sections",
                           side_effect=fake(sections or [])), \
         mock.patch.object(sync, "fetch_member_games",
                           side_effect=fake(games or [])):
        yield


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
# USCF enrichment in the store (issue #25, ADR 0003)
# ---------------------------------------------------------------------------

class TestUscfInStore:
    def test_initialize_with_member_id_loads_the_uscf_profile(
        self, sample_pgn_text, uscf_profile_json
    ):
        """A Sync fetches the USCF record alongside the Studies."""
        with stub_studies(study1=sample_pgn_text), stub_uscf(uscf_profile_json):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )

        profile = data.get_uscf_profile()
        assert profile is not None
        assert profile.rating("R").rating == 1545
        assert data.uscf_synced_at() is not None
        assert data.uscf_failure() == ""

    def test_no_member_id_means_lichess_only(self, sample_pgn_text):
        """Without a configured member ID the dashboard runs exactly as before.
        (No USCF stub here: any USCF HTTP attempt would trip the network guard.)"""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        assert data.get_uscf_profile() is None
        assert data.uscf_failure() == ""
        assert data.uscf_synced_at() is None

    def test_uscf_down_at_startup_still_boots(self, sample_pgn_text):
        """ADR 0003: the dashboard never fails to start because USCF is down."""
        boom = UscfUnreachableError("Could not reach USCF: connection refused")
        with stub_studies(study1=sample_pgn_text), stub_uscf(boom):
            df, _ = data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )

        # Lichess data is completely unaffected
        assert len(df) == 7
        assert data.is_loaded()
        # The USCF surfaces know they're unavailable, and why
        assert data.get_uscf_profile() is None
        assert "Could not reach USCF" in data.uscf_failure()

    def test_refresh_picks_up_uscf_changes(self, sample_pgn_text, uscf_profile_json):
        """A rating change published by USCF appears after the next Sync."""
        with stub_studies(study1=sample_pgn_text), stub_uscf(uscf_profile_json):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )
        assert data.get_uscf_profile().rating("R").rating == 1545

        # USCF publishes the June supplement: Regular becomes 1571
        newer = dict(
            uscf_profile_json,
            ratings=[
                dict(r, rating=1571) if r.get("ratingSystem") == "R" else r
                for r in uscf_profile_json["ratings"]
            ],
        )
        with stub_studies(study1=sample_pgn_text), stub_uscf(newer):
            outcome = data.refresh()

        assert outcome.status == "success"
        assert data.get_uscf_profile().rating("R").rating == 1571

    def test_uscf_failure_during_refresh_keeps_the_sync_successful(
        self, sample_pgn_text, uscf_profile_json
    ):
        """ADR 0003 applies to the Sync button too: USCF down ≠ Sync failed."""
        with stub_studies(study1=sample_pgn_text), stub_uscf(uscf_profile_json):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )

        boom = UscfUnreachableError("USCF is down")
        with stub_studies(study1=sample_pgn_text), stub_uscf(boom):
            outcome = data.refresh()

        assert outcome.status == "success"      # the Sync itself succeeded
        assert len(data.get_df()) == 7          # Lichess data is fresh
        assert "down" in data.uscf_failure()    # the USCF problem is visible


# ---------------------------------------------------------------------------
# The matching engine in the data layer (issue #28)
# ---------------------------------------------------------------------------

# A Game and the USCF Game Record that matches it (opponent ID + result), in
# the store's own fixture style: the chapter is what Lichess exports, the
# record is what the games endpoint returns.
MATCHED_PGN = """\
[Event "Test Open"]
[Site "Springfield"]
[Date "2024.01.06"]
[Round "1"]
[White "Test Player"]
[Black "John Fontaine"]
[Result "1-0"]
[WhiteElo "1500"]
[BlackElo "1465"]
[WhiteFideId "99999999"]
[BlackFideId "16441708"]
[Termination "win by resignation"]
[StudyName "Test Study"]
[ChapterName "Test Player - John Fontaine"]
[ChapterURL "https://lichess.org/study/teststudy/matched01"]

1. e4 e5 2. Nf3 Nc6 1-0
"""

MATCHED_USCF_GAME = {
    "section": {"id": "x", "number": 1, "name": "LADDER"},
    "event": {"id": "202401060001", "name": "TEST OPEN JANUARY",
              "startDate": "2024-01-06", "endDate": "2024-01-06", "stateCode": "VA"},
    "ratingSystem": "R",
    "player": {"color": "White", "outcome": "Win"},
    "opponent": {"id": "16441708", "firstName": "JOHN", "lastName": "FONTAINE",
                 "stateRep": "VA", "color": "Black", "outcome": "Loss"},
}


class TestUscfMatchingInStore:
    """A Sync matches USCF Game Records to Games and enriches the store's df."""

    def test_matched_games_carry_uscf_facts_in_the_store(self, uscf_profile_json):
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[MATCHED_USCF_GAME]):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )

        game = data.get_df().iloc[0]
        assert bool(game["UscfMatched"]) is True
        assert game["UscfMatchedBy"] == "id"
        assert game["UscfEventName"] == "TEST OPEN JANUARY"
        assert game["UscfSection"] == "LADDER"
        assert game["UscfOpponentName"] == "JOHN FONTAINE"
        assert game["UscfOpponentId"] == "16441708"

    def test_match_result_is_exposed_for_later_slices(self, uscf_profile_json):
        """Reconciliation (issue #30) consumes the full MatchResult — both
        leftovers included."""
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[MATCHED_USCF_GAME]):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )

        matches = data.get_uscf_matches()
        assert len(matches.matches) == 1
        assert matches.unmatched_chapter_urls == ()
        assert matches.unmatched_records == ()

    def test_lichess_only_runs_still_have_the_enrichment_columns(self, sample_pgn_text):
        """Without USCF, pages can still reference UscfMatched — it is just
        False everywhere (no column-existence checks anywhere)."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        df = data.get_df()
        assert "UscfMatched" in df.columns
        assert not df["UscfMatched"].any()
        assert data.get_uscf_matches().matches == ()

    def test_matches_survive_uscf_being_down(self, uscf_profile_json, tmp_path):
        """ADR 0003: cached USCF Game Records keep the matching working when
        USCF is unreachable."""
        from uscf_client import UscfUnreachableError

        cache = str(tmp_path / "uscf_cache.json")
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[MATCHED_USCF_GAME]):
            data.initialize(["study1"], player_name="Test Player",
                            uscf_member_id="32487228", uscf_cache_path=cache)
        data.reset()

        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(UscfUnreachableError("USCF is down")):
            data.initialize(["study1"], player_name="Test Player",
                            uscf_member_id="32487228", uscf_cache_path=cache)

        game = data.get_df().iloc[0]
        assert bool(game["UscfMatched"]) is True
        assert data.uscf_from_cache() is True

    def test_refresh_rematches_against_fresh_data(self, uscf_profile_json):
        """A Sync that brings new USCF Game Records re-runs the matching."""
        with stub_studies(study1=MATCHED_PGN), stub_uscf(uscf_profile_json):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )
        assert not data.get_df()["UscfMatched"].any()  # no games → no matches

        # USCF rates the event: the games endpoint now returns the record
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[MATCHED_USCF_GAME]):
            outcome = data.refresh()

        assert outcome.status == "success"
        assert bool(data.get_df().iloc[0]["UscfMatched"]) is True


# ---------------------------------------------------------------------------
# Reconciliation in the data layer (issue #30)
# ---------------------------------------------------------------------------

# The same matched game, but USCF disagrees about the color → a conflict entry
CONFLICTED_USCF_GAME = {
    **MATCHED_USCF_GAME,
    "player": {"color": "Black", "outcome": "Win"},
    "opponent": {**MATCHED_USCF_GAME["opponent"], "color": "White", "outcome": "Loss"},
}


class TestReconciliationInStore:
    def test_open_entries_are_exposed(self, uscf_profile_json):
        """After a Sync, every disagreement is queryable for the page and the
        header badge."""
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[CONFLICTED_USCF_GAME]):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )

        entries = data.get_reconciliation()
        assert len(entries) == 1
        assert entries[0].kind == "conflict"
        assert entries[0].opponent == "John Fontaine"

    def test_dismissing_an_entry_removes_it_immediately(self, uscf_profile_json):
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[CONFLICTED_USCF_GAME]):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )
        entry = data.get_reconciliation()[0]

        data.dismiss_reconciliation_entry(entry.entry_id)

        assert data.get_reconciliation() == []

    def test_dismissals_survive_restarts_via_the_uscf_cache(
        self, uscf_profile_json, tmp_path
    ):
        """The best-effort persistence path: dismiss, restart the app, the
        entry stays dismissed (issue #30)."""
        cache = str(tmp_path / "uscf_cache.json")

        def boot():
            with stub_studies(study1=MATCHED_PGN), \
                 stub_uscf(uscf_profile_json, games=[CONFLICTED_USCF_GAME]):
                data.initialize(["study1"], player_name="Test Player",
                                uscf_member_id="32487228", uscf_cache_path=cache)

        boot()
        entry = data.get_reconciliation()[0]
        data.dismiss_reconciliation_entry(entry.entry_id)

        data.reset()
        boot()  # the app restarts

        assert data.get_reconciliation() == []

    def test_no_uscf_configured_means_no_reconciliation(self, sample_pgn_text):
        """Lichess-only runs have nothing to reconcile against."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        assert data.get_reconciliation() == []

    def test_uscf_never_reached_means_no_reconciliation(self, sample_pgn_text):
        """With no USCF data at all (down, no cache), claiming every Game is
        'Lichess-only' would be noise, not insight."""
        from uscf_client import UscfUnreachableError

        with stub_studies(study1=sample_pgn_text), \
             stub_uscf(UscfUnreachableError("USCF is down")):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )

        assert data.get_reconciliation() == []


# ---------------------------------------------------------------------------
# The Official and Live rating series in the data layer (issue #27)
# ---------------------------------------------------------------------------

class TestRatingSeriesInStore:
    def test_data_layer_exposes_both_rating_series(
        self, sample_pgn_text, uscf_profile_json,
        uscf_supplements_json, uscf_sections_json,
    ):
        """After a Sync, both series are available to every page."""
        with stub_studies(study1=sample_pgn_text), stub_uscf(
            uscf_profile_json,
            supplements=uscf_supplements_json["items"],
            sections=uscf_sections_json["items"],
        ):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="32487228"
            )

        official = data.get_official_series()
        assert len(official) == 10
        assert official[-1].rating == 1545          # current Official Rating

        live = data.get_live_series()
        assert len(live) == 23
        assert live[-1].post == 1570.72             # current Live Rating, decimals kept

    def test_series_are_empty_without_uscf(self, sample_pgn_text):
        """Lichess-only runs have no series — pages get empty lists, not errors."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        assert data.get_official_series() == []
        assert data.get_live_series() == []


# ---------------------------------------------------------------------------
# USCF cache fallback (issue #26)
# ---------------------------------------------------------------------------

class TestUscfCacheFallback:
    def _initialize(self, pgn, uscf, cache_path):
        with stub_studies(study1=pgn), stub_uscf(uscf):
            return data.initialize(
                ["study1"], player_name="Test Player",
                uscf_member_id="32487228", uscf_cache_path=cache_path,
            )

    def test_restart_with_uscf_down_serves_cached_data(
        self, sample_pgn_text, uscf_profile_json, tmp_path
    ):
        """USCF panels survive an app restart while USCF is unreachable."""
        cache = str(tmp_path / "uscf_cache.json")
        # A successful run caches the USCF data...
        self._initialize(sample_pgn_text, uscf_profile_json, cache)
        data.reset()

        # ...then the app restarts while USCF is down
        boom = UscfUnreachableError("Could not reach USCF")
        self._initialize(sample_pgn_text, boom, cache)

        profile = data.get_uscf_profile()
        assert profile is not None                       # cached data is served
        assert profile.rating("R").rating == 1545
        assert data.uscf_from_cache() is True            # clearly marked stale
        assert "Could not reach USCF" in data.uscf_failure()
        assert data.uscf_synced_at() is not None         # "unavailable since X"

    def test_failed_refresh_degrades_to_cached_data(
        self, sample_pgn_text, uscf_profile_json, tmp_path
    ):
        """A Sync with USCF unreachable: Lichess fresh, USCF cached + warned."""
        cache = str(tmp_path / "uscf_cache.json")
        self._initialize(sample_pgn_text, uscf_profile_json, cache)
        assert data.uscf_from_cache() is False

        boom = UscfUnreachableError("USCF is down")
        with stub_studies(study1=sample_pgn_text), stub_uscf(boom):
            outcome = data.refresh()

        assert outcome.status == "success"
        assert data.get_uscf_profile() is not None       # still showing USCF data
        assert data.uscf_from_cache() is True            # from the cache
        assert "down" in data.uscf_failure()

    def test_uscf_recovery_clears_the_stale_state(
        self, sample_pgn_text, uscf_profile_json, tmp_path
    ):
        """Once USCF is back, the next Sync replaces cached data with live data."""
        cache = str(tmp_path / "uscf_cache.json")
        self._initialize(sample_pgn_text, uscf_profile_json, cache)
        data.reset()
        self._initialize(
            sample_pgn_text, UscfUnreachableError("down"), cache
        )
        assert data.uscf_from_cache() is True

        with stub_studies(study1=sample_pgn_text), stub_uscf(uscf_profile_json):
            outcome = data.refresh()

        assert outcome.status == "success"
        assert data.uscf_from_cache() is False
        assert data.uscf_failure() == ""


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


# ---------------------------------------------------------------------------
# Cache fallback / offline resilience (issue #7)
# ---------------------------------------------------------------------------

class TestCacheFallback:
    def test_successful_startup_writes_the_cache(self, sample_pgn_text, tmp_path):
        cache = tmp_path / "games.pgn"
        with stub_studies(study1=sample_pgn_text):
            data.initialize(
                ["study1"], player_name="Test Player", cache_path=str(cache)
            )

        assert cache.exists()
        assert data.source() == "lichess"

    def test_lichess_down_with_cache_boots_from_cache(self, sample_pgn_text, tmp_path):
        """The dashboard never goes blank because Lichess is down."""
        cache = tmp_path / "games.pgn"
        # A previous successful Sync left a cache behind
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player", cache_path=str(cache))
        data.reset()

        # Now Lichess is down at startup
        with stub_studies(study1=LichessUnreachableError("lichess is down")):
            df, player = data.initialize(
                ["study1"], player_name="Test Player", cache_path=str(cache)
            )

        assert len(df) == 7
        assert data.is_loaded()
        # The UI can tell the user they're looking at cached data and how old it is
        assert data.source() == "cache"
        assert data.cached_at() is not None

    def test_lichess_down_without_cache_raises_clear_error(self, tmp_path):
        with stub_studies(study1=LichessUnreachableError("lichess is down")):
            with pytest.raises(sync.SyncError) as exc_info:
                data.initialize(
                    ["study1"], player_name="Test Player",
                    cache_path=str(tmp_path / "no-cache-here.pgn"),
                )

        assert "study1" in str(exc_info.value)
        assert not data.is_loaded()

    def test_successful_refresh_after_cache_boot_restores_live_data(
        self, sample_pgn_text, tmp_path
    ):
        """Once Lichess comes back, a button-Sync replaces cached data with live data."""
        cache = tmp_path / "games.pgn"
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player", cache_path=str(cache))
        data.reset()
        with stub_studies(study1=LichessUnreachableError("down")):
            data.initialize(["study1"], player_name="Test Player", cache_path=str(cache))
        assert data.source() == "cache"

        # Lichess is back
        with stub_studies(study1=sample_pgn_text):
            outcome = data.refresh()

        assert outcome.status == "success"
        assert data.source() == "lichess"

    def test_failed_refresh_after_cache_boot_keeps_cached_data(
        self, sample_pgn_text, tmp_path
    ):
        """A failed button-Sync leaves the (cached) data intact — never blank."""
        cache = tmp_path / "games.pgn"
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player", cache_path=str(cache))
        data.reset()
        with stub_studies(study1=LichessUnreachableError("down")):
            data.initialize(["study1"], player_name="Test Player", cache_path=str(cache))

        with stub_studies(study1=LichessUnreachableError("still down")):
            outcome = data.refresh()

        assert outcome.status == "error"
        assert len(data.get_df()) == 7
        assert data.source() == "cache"
