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
from pathlib import Path
from unittest import mock

import pytest

import ai_summary
import data
import sync
from config import config
from lichess_client import LichessUnreachableError, StudyNotFoundError
from uscf_client import UscfUnreachableError

ALICE_PGN = (
    Path(__file__).parent / "data" / "analyzed-alice-anderson.pgn"
).read_text()
ALICE_URL = "https://lichess.org/study/abcdWXYZ/alic0001"


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
def stub_uscf(profile, supplements=None, sections=None, games=None,
              events=None, norms=None, awards=None, standings=None,
              opponent_profiles=None):
    """Stub the USCF client inside sync: raw JSON values, or Exceptions to raise.

    *standings* maps (event_id, section_number) → raw item list; *opponent_profiles*
    maps member_id → raw profile.  Unstubbed crosstables/opponents raise (sync
    skips them gracefully, per-item degradation)."""
    from uscf_client import UscfUnreachableError

    def fake(value):
        def fetch(member_id, **kwargs):
            if isinstance(value, Exception):
                raise value
            return value
        return fetch

    def fake_profile(member_id, **kwargs):
        # The member's own profile, or a stubbed opponent's (issue #35)
        if isinstance(profile, Exception):
            raise profile
        if str(profile.get("id", "")) == str(member_id):
            return profile
        value = (opponent_profiles or {}).get(member_id)
        if value is None:
            raise UscfUnreachableError(f"no profile stubbed for {member_id!r}")
        if isinstance(value, Exception):
            raise value
        return value

    def fake_standings(event_id, section_number, **kwargs):
        value = (standings or {}).get((event_id, section_number))
        if value is None:
            raise UscfUnreachableError(
                f"no standings stubbed for {event_id}/{section_number}")
        if isinstance(value, Exception):
            raise value
        return value

    with mock.patch.object(sync, "fetch_member_profile", side_effect=fake_profile), \
         mock.patch.object(sync, "fetch_rating_supplements",
                           side_effect=fake(supplements or [])), \
         mock.patch.object(sync, "fetch_member_sections",
                           side_effect=fake(sections or [])), \
         mock.patch.object(sync, "fetch_member_games",
                           side_effect=fake(games or [])), \
         mock.patch.object(sync, "fetch_member_events",
                           side_effect=fake(events or [])), \
         mock.patch.object(sync, "fetch_member_norms",
                           side_effect=fake(norms or [])), \
         mock.patch.object(sync, "fetch_member_awards",
                           side_effect=fake(awards or [])), \
         mock.patch.object(sync, "fetch_event_standings",
                           side_effect=fake_standings):
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


class TestRefreshAtomicity:
    """The single-swap contract (ADR 0006, issue #87 [1]): the next state is
    built off-store and committed once, so a failure never half-updates it."""

    def test_enrichment_failure_leaves_current_data_untouched(self, sample_pgn_text):
        """A refresh that reaches Lichess but blows up during enrichment must
        keep the previous complete dataset — never a half-swapped store."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")
        good_df = data.get_df()
        assert not good_df.empty

        with stub_studies(study1=sample_pgn_text), \
             mock.patch("data.enrich_games_with_analysis",
                        side_effect=RuntimeError("enrichment blew up")):
            outcome = data.refresh()

        assert outcome.status == "error"
        # The store never rebound its df — same object, fully enriched as before.
        assert data.get_df() is good_df
        assert "UscfColorConflict" in data.get_df().columns

    def test_dismissal_during_snapshot_build_survives_the_commit(self, sample_pgn_text):
        """A dismissal made while a refresh's snapshot is still building (a
        threaded-server race) must not be lost when _commit swaps it in —
        dismissals are append-only user judgement (issue #87 [1])."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        def dismiss_midway(*args, **kwargs):
            data._current().dismissed.add("late:dismissal")
            return sync.CoachSyncResult()

        with stub_studies(study1=sample_pgn_text), \
             mock.patch("data.sync_coach", side_effect=dismiss_midway):
            outcome = data.refresh()

        assert outcome.status == "success"
        assert "late:dismissal" in data._current().dismissed


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
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
[Black "Bob Baker"]
[Result "1-0"]
[WhiteElo "1500"]
[BlackElo "1465"]
[WhiteFideId "99999999"]
[BlackFideId "20000056"]
[Termination "win by resignation"]
[StudyName "Test Study"]
[ChapterName "Test Player - Bob Baker"]
[ChapterURL "https://lichess.org/study/teststudy/matched01"]

1. e4 e5 2. Nf3 Nc6 1-0
"""

MATCHED_USCF_GAME = {
    "section": {"id": "x", "number": 1, "name": "LADDER"},
    "event": {"id": "202401060001", "name": "TEST OPEN JANUARY",
              "startDate": "2024-01-06", "endDate": "2024-01-06", "stateCode": "VA"},
    "ratingSystem": "R",
    "player": {"color": "White", "outcome": "Win"},
    "opponent": {"id": "20000056", "firstName": "BOB", "lastName": "BAKER",
                 "stateRep": "VA", "color": "Black", "outcome": "Loss"},
}


class TestUscfMatchingInStore:
    """A Sync matches USCF Game Records to Games and enriches the store's df."""

    def test_matched_games_carry_uscf_facts_in_the_store(self, uscf_profile_json):
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[MATCHED_USCF_GAME]):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
            )

        game = data.get_df().iloc[0]
        assert bool(game["UscfMatched"]) is True
        assert game["UscfMatchedBy"] == "id"
        assert game["UscfEventName"] == "TEST OPEN JANUARY"
        assert game["UscfSection"] == "LADDER"
        assert game["UscfOpponentName"] == "BOB BAKER"
        assert game["UscfOpponentId"] == "20000056"

    def test_match_result_is_exposed_for_later_slices(self, uscf_profile_json):
        """Reconciliation (issue #30) consumes the full MatchResult — both
        leftovers included."""
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[MATCHED_USCF_GAME]):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
                            uscf_member_id="12345678", uscf_cache_path=cache)
        data.reset()

        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(UscfUnreachableError("USCF is down")):
            data.initialize(["study1"], player_name="Test Player",
                            uscf_member_id="12345678", uscf_cache_path=cache)

        game = data.get_df().iloc[0]
        assert bool(game["UscfMatched"]) is True
        assert data.uscf_from_cache() is True

    def test_refresh_rematches_against_fresh_data(self, uscf_profile_json):
        """A Sync that brings new USCF Game Records re-runs the matching."""
        with stub_studies(study1=MATCHED_PGN), stub_uscf(uscf_profile_json):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
            )

        entries = data.get_reconciliation()
        assert len(entries) == 1
        assert entries[0].kind == "conflict"
        assert entries[0].opponent == "Bob Baker"

    def test_dismissing_an_entry_removes_it_immediately(self, uscf_profile_json):
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[CONFLICTED_USCF_GAME]):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
                                uscf_member_id="12345678", uscf_cache_path=cache)

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
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
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
# USCF achievements in the data layer (issue #36)
# ---------------------------------------------------------------------------

class TestAchievementsInStore:
    def test_data_layer_exposes_achievements(
        self, sample_pgn_text, uscf_profile_json, uscf_norms_json, uscf_awards_json
    ):
        """After a Sync, the norm and the award are available to every page."""
        with stub_studies(study1=sample_pgn_text), stub_uscf(
            uscf_profile_json,
            norms=uscf_norms_json["items"],
            awards=uscf_awards_json["items"],
        ):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
            )

        achievements = data.get_uscf_achievements()
        assert [a.title for a in achievements] == [
            "Fourth Category norm", "25th career win",
        ]

    def test_achievements_are_empty_without_uscf(self, sample_pgn_text):
        """Lichess-only runs have no achievements — empty list, never an error."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        assert data.get_uscf_achievements() == []


class TestEventsInStore:
    """The member's Rated Events in the data layer (issue #33)."""

    def test_data_layer_exposes_member_events(
        self, sample_pgn_text, uscf_profile_json, uscf_events_json
    ):
        with stub_studies(study1=sample_pgn_text), stub_uscf(
            uscf_profile_json, events=uscf_events_json["items"],
        ):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
            )

        events = data.get_uscf_events()
        assert len(events) == 23
        assert events[-1].name == "ACC MAY 2026"

    def test_events_are_empty_without_uscf(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        assert data.get_uscf_events() == []


class TestStandingsInStore:
    """Crosstables and real round numbers in the data layer (issue #34)."""

    @pytest.fixture()
    def real_career_store(self, uscf_profile_json, uscf_games_json,
                          uscf_sections_json, uscf_standings_json):
        """The store loaded with the real fixture pair + the 5 crosstables."""
        pgn_text = (
            __import__("pathlib").Path("tests/data/uscf/lichess-study-snapshot.pgn")
            .read_text()
        )
        raw_standings = {key: raw["items"]
                         for key, raw in uscf_standings_json.items()}
        with stub_studies(study1=pgn_text), stub_uscf(
            uscf_profile_json,
            sections=uscf_sections_json["items"],
            games=uscf_games_json["items"],
            standings=raw_standings,
        ):
            data.initialize(["study1"], player_name="Daniel Gentile",
                            uscf_member_id="12345678")

    def test_data_layer_exposes_standings(self, real_career_store):
        standings = data.get_uscf_standings()

        acc_may = standings[("202605290393", "LADDER")]
        assert len(acc_may) == 116
        daniel = next(s for s in acc_may if s.member_id == "12345678")
        assert daniel.ordinal == 5          # finished 5th of 116

    def test_games_carry_their_real_round_numbers(self, real_career_store):
        """The store's df has UscfRound: ACC MAY's typed rounds 24–27 are
        really rounds 1, 3, 4, 5."""
        df = data.get_df()
        may = df[df["UscfEventId"] == "202605290393"]

        assert list(may["UscfRound"]) == [1, 3, 4, 5]

    def test_standings_are_empty_without_uscf(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        assert data.get_uscf_standings() == {}
        # The round column still exists — downstream code never checks
        assert "UscfRound" in data.get_df().columns


class TestOpponentProfilesInStore:
    """Opponent current ratings in the data layer (issue #35)."""

    def test_data_layer_exposes_opponent_profiles(
        self, sample_pgn_text, uscf_profile_json, uscf_games_json
    ):
        import json
        from pathlib import Path
        baker = json.loads(
            Path("tests/data/uscf/opponent-bob-baker.json").read_text())

        with stub_studies(study1=sample_pgn_text), stub_uscf(
            uscf_profile_json, games=uscf_games_json["items"],
            opponent_profiles={"20000056": baker},
        ):
            data.initialize(["study1"], player_name="Test Player",
                            uscf_member_id="12345678")

        profiles = data.get_opponent_profiles()
        assert profiles["20000056"].name == "BOB BAKER"
        assert profiles["20000056"].rating("R").rating == 1400

    def test_opponent_profiles_are_empty_without_uscf(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")

        assert data.get_opponent_profiles() == {}


class TestNewAchievementDetection:
    """
    The celebration check (issue #36): an achievement is reported "new" the
    first time any Sync sees it — and never again, even across restarts.
    """

    def _initialize(self, pgn, profile, norms=(), awards=(), cache_path=None):
        with stub_studies(study1=pgn), \
             stub_uscf(profile, norms=list(norms), awards=list(awards)):
            data.initialize(["study1"], player_name="Test Player",
                            uscf_member_id="12345678",
                            uscf_cache_path=cache_path)

    def _refresh(self, pgn, profile, norms=(), awards=()):
        with stub_studies(study1=pgn), \
             stub_uscf(profile, norms=list(norms), awards=list(awards)):
            return data.refresh()

    def test_existing_achievements_are_not_reported_new(
        self, sample_pgn_text, uscf_profile_json, uscf_norms_json, uscf_awards_json
    ):
        """The first Sync that knows about achievements records Daniel's existing
        norm and award silently — celebrating months-old achievements is noise."""
        self._initialize(sample_pgn_text, uscf_profile_json,
                         norms=uscf_norms_json["items"],
                         awards=uscf_awards_json["items"])

        assert data.get_uscf_achievements() != []      # they ARE in the timeline
        assert data.get_new_achievements() == []       # but nothing to celebrate

    def test_an_achievement_appearing_in_a_later_sync_is_new(
        self, sample_pgn_text, uscf_profile_json, uscf_norms_json, uscf_awards_json
    ):
        """The norm existed at startup; the award appears in a later Sync —
        exactly the 'future norms and awards appear automatically' criterion."""
        self._initialize(sample_pgn_text, uscf_profile_json,
                         norms=uscf_norms_json["items"])

        self._refresh(sample_pgn_text, uscf_profile_json,
                      norms=uscf_norms_json["items"],
                      awards=uscf_awards_json["items"])

        assert [a.title for a in data.get_new_achievements()] == ["25th career win"]

    def test_an_achievement_is_new_exactly_once(
        self, sample_pgn_text, uscf_profile_json, uscf_norms_json, uscf_awards_json
    ):
        """Once celebrated, the same achievement never triggers again."""
        self._initialize(sample_pgn_text, uscf_profile_json,
                         norms=uscf_norms_json["items"])
        self._refresh(sample_pgn_text, uscf_profile_json,
                      norms=uscf_norms_json["items"],
                      awards=uscf_awards_json["items"])

        self._refresh(sample_pgn_text, uscf_profile_json,
                      norms=uscf_norms_json["items"],
                      awards=uscf_awards_json["items"])

        assert data.get_new_achievements() == []

    def test_seen_achievements_survive_restarts(
        self, sample_pgn_text, uscf_profile_json, uscf_norms_json, uscf_awards_json,
        tmp_path
    ):
        """With a cache path, what one run celebrated stays celebrated after a
        restart; only the genuinely-new award gets reported."""
        cache = str(tmp_path / "uscf_cache.json")
        self._initialize(sample_pgn_text, uscf_profile_json,
                         norms=uscf_norms_json["items"], cache_path=cache)
        data.reset()

        # The app restarts; USCF now also has the award
        self._initialize(sample_pgn_text, uscf_profile_json,
                         norms=uscf_norms_json["items"],
                         awards=uscf_awards_json["items"], cache_path=cache)

        assert [a.title for a in data.get_new_achievements()] == ["25th career win"]

    def test_uscf_being_down_does_not_forget_or_recelebrate(
        self, sample_pgn_text, uscf_profile_json, uscf_norms_json, tmp_path
    ):
        """A USCF outage mid-run neither wipes the seen-state nor causes the
        cached achievements to be re-celebrated when USCF comes back."""
        cache = str(tmp_path / "uscf_cache.json")
        self._initialize(sample_pgn_text, uscf_profile_json,
                         norms=uscf_norms_json["items"], cache_path=cache)

        with stub_studies(study1=sample_pgn_text), \
             stub_uscf(UscfUnreachableError("USCF is down")):
            data.refresh()
        assert data.get_new_achievements() == []

        # USCF recovers, same norm as before → still not new
        self._refresh(sample_pgn_text, uscf_profile_json,
                      norms=uscf_norms_json["items"])
        assert data.get_new_achievements() == []


# ---------------------------------------------------------------------------
# USCF cache fallback (issue #26)
# ---------------------------------------------------------------------------

class TestUscfCacheFallback:
    def _initialize(self, pgn, uscf, cache_path):
        with stub_studies(study1=pgn), stub_uscf(uscf):
            return data.initialize(
                ["study1"], player_name="Test Player",
                uscf_member_id="12345678", uscf_cache_path=cache_path,
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

            # Simulate an in-flight Sync holding the active store's lock
            lock = data._current().sync_lock
            acquired = lock.acquire(blocking=False)
            assert acquired
            try:
                outcome = data.refresh()
            finally:
                lock.release()

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

    def test_demo_mode_boots_from_cache_without_network_or_writes(
        self, sample_pgn_text, tmp_path
    ):
        cache = tmp_path / "games.pgn"
        cache.write_text(sample_pgn_text, encoding="utf-8")
        before = cache.read_text(encoding="utf-8")

        with mock.patch.object(sync, "fetch_study_pgn",
                               side_effect=AssertionError("no Lichess in demo")), \
             mock.patch.object(sync, "fetch_member_profile",
                               side_effect=AssertionError("no USCF in demo")):
            df, player = data.initialize(
                [], player_name="Test Player", cache_path=str(cache),
                uscf_member_id="12345678", anthropic_api_key="sk-ant-test",
                demo_mode=True,
            )
            outcome = data.refresh()

        assert len(df) == 7
        assert player == "Test Player"
        assert data.source() == "cache"
        assert data.demo_mode()
        assert not data.uscf_enabled()
        assert outcome.status == "demo"
        assert cache.read_text(encoding="utf-8") == before

    def test_committed_demo_seed_boots_and_is_anonymized(self):
        """`make demo` on a fresh clone must work: the seed config.DEMO_CACHE_PATH
        points at is committed, loads without network, and carries no real data.

        The older demo test uses a temp fixture, so it stayed green even while the
        real seed was missing from git — this one exercises the actual committed
        file the way a cold clone does.
        """
        seed = Path(config.DEMO_CACHE_PATH)
        assert seed.is_file(), f"demo seed missing from the repo: {seed}"

        text = seed.read_text(encoding="utf-8")
        # Anonymized: the real player's own USCF member ID and study ID must be
        # gone (see scripts/anonymize_pgn.py). These were the identifiers the
        # anonymization pass replaced.
        assert "32487228" not in text  # real member ID → 12345678
        assert "6jYtXHGp" not in text  # real study ID → abcdWXYZ
        # Quasi-identifiers perturbed too (#89): real Event/Site names that would
        # let a public USCF crosstable reverse the opponent pseudonyms are gone.
        assert "ACC Friday Ladder" not in text
        assert "Army Navy Club" not in text

        with mock.patch.object(sync, "fetch_study_pgn",
                               side_effect=AssertionError("no Lichess in demo")):
            df, _ = data.initialize(
                [], cache_path=config.DEMO_CACHE_PATH, demo_mode=True,
            )

        assert len(df) > 20  # a real, non-trivial history renders on every page
        assert data.source() == "cache"
        assert data.demo_mode()

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


class TestReconciliationDuringSync:
    def test_snapshot_is_fully_enriched_never_half_swapped(self, uscf_profile_json):
        """Atomic swap (ADR 0006, issue #87 [1]): the snapshot is built
        off-store and committed exactly once, so the store never holds a raw,
        un-enriched df while USCF is available.  The enrichment columns are
        present the instant uscf.available is true — the mid-Sync half-swapped
        window get_reconciliation used to guard no longer exists."""
        with stub_studies(study1=MATCHED_PGN), \
             stub_uscf(uscf_profile_json, games=[CONFLICTED_USCF_GAME]):
            data.initialize(
                ["study1"], player_name="Test Player", uscf_member_id="12345678"
            )
        store = data._current()
        assert store.uscf.available
        assert "UscfColorConflict" in store.df.columns
        assert len(data.get_reconciliation()) == 1


class TestAnalysisSummaries:
    """The AI-summary ingestion (issue #59 [F5]): a Sync runs ai_summary per
    analysed Game and the store exposes the result.  The boundary is stubbed
    like the USCF client; the summary is enrichment, never a dependency."""

    def test_analyzed_game_gets_a_summary_exposed_by_the_store(self, tmp_path):
        with stub_studies(study1=ALICE_PGN), \
             mock.patch.object(ai_summary, "_call_anthropic",
                               return_value="You won after your opponent blundered."):
            data.initialize(
                ["study1"], player_name="Daniel Gentile",
                anthropic_api_key="sk-test",
                analysis_cache_path=str(tmp_path / "analysis_cache.json"),
            )
        assert data.get_game_summary(ALICE_URL) == (
            "You won after your opponent blundered."
        )

    def test_no_key_still_syncs_with_empty_summaries_and_no_call(self):
        """No API key → the dashboard runs unchanged; the boundary is untouched
        (and the no_network guard would trip if it weren't)."""
        with stub_studies(study1=ALICE_PGN), \
             mock.patch.object(ai_summary, "_call_anthropic") as seam:
            df, _ = data.initialize(["study1"], player_name="Daniel Gentile")

        assert len(df) == 1  # the Sync succeeded
        assert data.get_game_summary(ALICE_URL) == ""
        seam.assert_not_called()

    def test_boundary_failure_does_not_fail_the_sync(self):
        """Any client failure degrades silently — the Sync still serves the
        Game, just without a summary (issue #59, ADR 0004)."""
        with stub_studies(study1=ALICE_PGN), \
             mock.patch.object(ai_summary, "_call_anthropic",
                               side_effect=RuntimeError("Anthropic is down")):
            df, _ = data.initialize(
                ["study1"], player_name="Daniel Gentile",
                anthropic_api_key="sk-test",
            )

        assert len(df) == 1
        assert data.get_game_summary(ALICE_URL) == ""

    def test_unknown_game_summary_is_empty(self, sample_pgn_text):
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")
        assert data.get_game_summary("https://lichess.org/study/x/never") == ""
        assert data.get_game_summary("") == ""


# ---------------------------------------------------------------------------
# Per-user store registry (issue #72 [G2], ADR 0005)
# ---------------------------------------------------------------------------

# One Game for a different player, so a user's data is unmistakably their own.
BOB_PGN = """\
[Event "Bob's Open"]
[Site "Capital City"]
[Date "2024.02.02"]
[Round "1"]
[White "Bob Smith"]
[Black "Rival R"]
[Result "1-0"]
[StudyName "Bob Study"]
[ChapterName "Bob Smith - Rival R"]
[ChapterURL "https://lichess.org/study/bobstudy/chapBOB1"]

1. d4 d5 2. c4 e6 3. Nc3 Nf6 1-0
"""


def _record(username, **overrides):
    from user_config import UserRecord, hash_password

    base = dict(username=username, password_hash=hash_password("pw"),
                study_ids=("s-" + username,), coach_study_ids=(),
                uscf_member_id=None, lichess_token=None)
    base.update(overrides)
    return UserRecord(**base)


class TestPerUserRegistry:
    def test_each_user_sees_only_their_own_games(self, sample_pgn_text, tmp_path):
        """Two users, two Studies, full isolation: one user's accessor never
        returns another user's Games (ADR 0005)."""
        users = {
            "alice": _record("alice", study_ids=("s-alice",)),
            "bob": _record("bob", study_ids=("s-bob",)),
        }
        with stub_studies(**{"s-alice": sample_pgn_text, "s-bob": BOB_PGN}):
            data.register_users(users, data_dir=str(tmp_path))
            data.sync_user("alice")
            data.sync_user("bob")

        data.activate("alice")
        assert len(data.get_df()) == 7
        assert data.get_player() == "Test Player"
        assert "Bob Smith" not in set(data.get_df()["White"])

        data.activate("bob")
        assert len(data.get_df()) == 1
        assert data.get_player() == "Bob Smith"
        assert set(data.get_df()["Opponent"]) == {"Rival R"}

    def test_sync_runs_against_each_users_own_config(self, sample_pgn_text, tmp_path):
        """A user is Synced against *their* Study IDs / token / USCF member ID."""
        captured: dict[str, dict] = {}

        def fake_fetch(study_id, **kwargs):
            captured[study_id] = kwargs
            return sample_pgn_text if study_id == "s-alice" else BOB_PGN

        users = {"alice": _record("alice", lichess_token="tok-alice"),
                 "bob": _record("bob")}
        with mock.patch.object(sync, "fetch_study_pgn", side_effect=fake_fetch):
            data.register_users(users, data_dir=str(tmp_path))
            data.sync_user("alice")
            data.sync_user("bob")

        # Alice's private token was used to fetch her Study, not bob's
        assert captured["s-alice"]["token"] == "tok-alice"
        assert captured["s-bob"]["token"] is None

    def test_case_only_username_difference_is_rejected(self, tmp_path):
        """'Alice' and 'alice' map to the same dir on a case-insensitive FS —
        registering both would silently share caches, so it's refused (#89)."""
        from user_config import UserConfigError

        users = {"Alice": _record("Alice"), "alice": _record("alice")}
        with pytest.raises(UserConfigError, match="(?i)same cache directory"):
            data.register_users(users, data_dir=str(tmp_path))

    def test_activate_unknown_user_is_empty_not_an_error(self, tmp_path):
        data.register_users({"alice": _record("alice")}, data_dir=str(tmp_path))
        data.activate("nobody")
        assert data.get_df().empty
        assert data.is_loaded() is False

    def test_activate_none_resolves_the_default_store(self, sample_pgn_text):
        """No active user → the single-user default store (CLI / ungated mode)."""
        with stub_studies(study1=sample_pgn_text):
            data.initialize(["study1"], player_name="Test Player")
        data.activate("someone")
        assert data.get_df().empty       # someone has no store
        data.activate(None)
        assert len(data.get_df()) == 7   # back to the default store

    def test_unreachable_main_study_degrades_to_empty_not_a_crash(self, tmp_path):
        """A user whose Study is unreachable (and has no cache) ends up with an
        empty store rather than crashing the multi-user app (ADR 0003 spirit)."""
        users = {"alice": _record("alice")}
        with stub_studies(**{"s-alice": StudyNotFoundError("gone")}):
            data.register_users(users, data_dir=str(tmp_path))
            data.sync_user("alice")  # must not raise
        data.activate("alice")
        assert data.get_df().empty

    def test_ensure_synced_loads_once_then_is_a_noop(self, sample_pgn_text, tmp_path):
        users = {"alice": _record("alice")}
        with stub_studies(**{"s-alice": sample_pgn_text}) as fetch:
            data.register_users(users, data_dir=str(tmp_path))
            data.ensure_synced("alice")
            data.ensure_synced("alice")  # second call must not re-Sync
            calls_after = fetch.call_count
        assert calls_after == 1
        data.activate("alice")
        assert len(data.get_df()) == 7

    def test_reset_clears_every_user_store(self, sample_pgn_text, tmp_path):
        with stub_studies(**{"s-alice": sample_pgn_text}):
            data.register_users({"alice": _record("alice")}, data_dir=str(tmp_path))
            data.sync_user("alice")
        data.reset()
        data.activate("alice")
        assert data.get_df().empty


# ---------------------------------------------------------------------------
# Coach content ingestion in the store (issue #74 [G4])
# ---------------------------------------------------------------------------

SNAPSHOT_PGN = (
    Path(__file__).parent / "data" / "uscf" / "lichess-study-snapshot.pgn"
).read_text()
COACH_PGN = (Path(__file__).parent / "data" / "coach-study.pgn").read_text()


class TestCoachContent:
    def _daniel(self):
        return {"daniel": _record("daniel", study_ids=("s-main",),
                                  coach_study_ids=("s-coach",))}

    def test_matched_coach_content_is_exposed_by_the_store(self, tmp_path):
        with stub_studies(**{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN}):
            data.register_users(self._daniel(), data_dir=str(tmp_path))
            data.sync_user("daniel")
        data.activate("daniel")

        chapter = data.get_coach_chapter(ALICE_URL)
        assert chapter is not None
        assert len(chapter.comments) == 7

    def test_a_game_with_no_coach_review_has_none(self, tmp_path):
        with stub_studies(**{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN}):
            data.register_users(self._daniel(), data_dir=str(tmp_path))
            data.sync_user("daniel")
        data.activate("daniel")
        # A real Game the coach never reviewed → graceful absence, not an error
        some_unreviewed = (
            "https://lichess.org/study/abcdWXYZ/fion0001"  # Fiona Foster
        )
        assert data.get_coach_chapter(some_unreviewed) is None

    def test_coach_study_unreachable_never_fails_the_sync(self, tmp_path):
        with stub_studies(**{"s-main": SNAPSHOT_PGN,
                             "s-coach": StudyNotFoundError("private")}):
            data.register_users(self._daniel(), data_dir=str(tmp_path))
            data.sync_user("daniel")
        data.activate("daniel")
        # The Lichess Sync still succeeded — all 63 Games are there
        assert len(data.get_df()) == 63
        # …just without coach content
        assert data.get_coach_chapter(ALICE_URL) is None

    def test_coach_content_survives_a_brief_outage_from_cache(self, tmp_path):
        users = self._daniel()
        data.register_users(users, data_dir=str(tmp_path))
        with stub_studies(**{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN}):
            data.sync_user("daniel")
        # Next Sync, coach Study is down → served from cache
        with stub_studies(**{"s-main": SNAPSHOT_PGN,
                             "s-coach": StudyNotFoundError("private")}):
            data.sync_user("daniel")
        data.activate("daniel")
        # Coach content is still served — from the previous Sync's cache — even
        # though the coach Study was unreachable this Sync (ADR 0003).
        assert data.get_coach_chapter(ALICE_URL) is not None

    def test_coach_content_is_isolated_per_user(self, sample_pgn_text, tmp_path):
        """A user with no coach Studies sees no coach content, even alongside a
        user who has it."""
        users = {
            "daniel": _record("daniel", study_ids=("s-main",),
                              coach_study_ids=("s-coach",)),
            "alice": _record("alice", study_ids=("s-alice",)),
        }
        with stub_studies(**{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN,
                             "s-alice": sample_pgn_text}):
            data.register_users(users, data_dir=str(tmp_path))
            data.sync_user("daniel")
            data.sync_user("alice")

        data.activate("alice")
        assert data.get_coach_chapter(ALICE_URL) is None
        data.activate("daniel")
        assert data.get_coach_chapter(ALICE_URL) is not None


DIANA_URL = "https://lichess.org/study/abcdWXYZ/dian0001"
ETHAN_URL = "https://lichess.org/study/abcdWXYZ/ethn0001"


class TestCoachNotesFeed:
    """The Coach's Notes feed accessor (issue #75 [G5])."""

    def _setup(self, tmp_path):
        users = {"daniel": _record("daniel", study_ids=("s-main",),
                                   coach_study_ids=("s-coach",))}
        with stub_studies(**{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN}):
            data.register_users(users, data_dir=str(tmp_path))
            data.sync_user("daniel")
        data.activate("daniel")

    def test_feed_collects_every_matched_games_prose(self, tmp_path):
        self._setup(tmp_path)
        notes = data.get_coach_notes()
        # 7 (Alice) + 2 (Diana) + 1 (Ethan) prose comments across matched Games
        assert len(notes) == 10

    def test_each_note_links_to_its_matched_game(self, tmp_path):
        self._setup(tmp_path)
        notes = data.get_coach_notes()
        matched = {ALICE_URL, DIANA_URL, ETHAN_URL}
        assert all(n["chapter_url"] in matched for n in notes)
        # the chapter_id used for the /game/<id> link is carried
        assert all(n["chapter_id"] for n in notes)

    def test_feed_is_newest_first(self, tmp_path):
        self._setup(tmp_path)
        dates = [n["date_dt"] for n in data.get_coach_notes()
                 if n["date_dt"] is not None]
        assert dates == sorted(dates, reverse=True)

    def test_unmatched_teaching_positions_never_appear(self, tmp_path):
        self._setup(tmp_path)
        texts = " ".join(n["text"] for n in data.get_coach_notes())
        # the extras' comments (blitz/endgame/master) must not leak in
        assert "teaching joke" not in texts
        assert "Build the bridge" not in texts
        assert "attacking ideas" not in texts

    def test_note_carries_opponent_and_text(self, tmp_path):
        self._setup(tmp_path)
        notes = data.get_coach_notes()
        alice = [n for n in notes if n["chapter_url"] == ALICE_URL]
        assert any("Caro-Kann again" in n["text"] for n in alice)
        assert all(n["opponent"] for n in alice)

    def test_no_coach_content_is_an_empty_feed(self, sample_pgn_text, tmp_path):
        with stub_studies(**{"s-alice": sample_pgn_text}):
            data.register_users({"alice": _record("alice")}, data_dir=str(tmp_path))
            data.sync_user("alice")
        data.activate("alice")
        assert data.get_coach_notes() == []
