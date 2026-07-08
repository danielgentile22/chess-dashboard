"""
tests/test_sync.py
==================
Tests for the Sync orchestrator (sync.py).

The Lichess and USCF clients are stubbed at the module boundary — no network.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

import sync
from lichess_client import LichessUnreachableError
from uscf_client import UscfUnreachableError


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


class UnstubbedStandingsError(UscfUnreachableError):
    """Raised when a test fetches a crosstable it didn't stub — sync treats it
    like any per-crosstable failure (skip that Section's standings)."""


@contextlib.contextmanager
def stub_uscf(profile, supplements=None, sections=None, games=None,
              events=None, norms=None, awards=None, standings=None,
              opponent_profiles=None):
    """
    Patch the USCF client inside sync: each value is the raw JSON to return,
    or an Exception to raise.  List endpoints default to empty lists.

    *standings* maps (event_id, section_number) → raw item list (or an
    Exception); *opponent_profiles* maps member_id → raw profile (or an
    Exception).  Unstubbed crosstables/opponents raise (sync skips them
    gracefully).  Yields a namespace of mocks so tests can assert on fetch
    counts (``.standings``, ``.profiles``).
    """
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
            raise UnstubbedStandingsError(
                f"no standings stubbed for {event_id}/{section_number}")
        if isinstance(value, Exception):
            raise value
        return value

    standings_mock = mock.Mock(side_effect=fake_standings)
    profile_mock = mock.Mock(side_effect=fake_profile)

    with mock.patch.object(sync, "fetch_member_profile",
                           profile_mock, create=True), \
         mock.patch.object(sync, "fetch_rating_supplements",
                           side_effect=fake(supplements or []), create=True), \
         mock.patch.object(sync, "fetch_member_sections",
                           side_effect=fake(sections or []), create=True), \
         mock.patch.object(sync, "fetch_member_games",
                           side_effect=fake(games or []), create=True), \
         mock.patch.object(sync, "fetch_member_events",
                           side_effect=fake(events or []), create=True), \
         mock.patch.object(sync, "fetch_member_norms",
                           side_effect=fake(norms or []), create=True), \
         mock.patch.object(sync, "fetch_member_awards",
                           side_effect=fake(awards or []), create=True), \
         mock.patch.object(sync, "fetch_event_standings",
                           standings_mock, create=True):
        yield mock.Mock(standings=standings_mock, profiles=profile_mock)


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


class TestUscfCache:
    """
    The local USCF response cache (issue #26): a disposable JSON file, never a
    source of truth (ADR 0003), tolerant of every filesystem misfortune.
    """

    def test_current_entries_survive_a_restart(self, tmp_path):
        """What one app run caches, the next app run can read."""
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.replace_current({"profile": {"id": "12345678", "rank": 11719}})

        reopened = sync.UscfCache(path)  # a fresh instance = an app restart
        assert reopened.get_current("profile") == {"id": "12345678", "rank": 11719}

    def test_fetched_at_reports_when_current_data_was_written(self, tmp_path):
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        assert cache.fetched_at() is None  # nothing cached yet

        cache.replace_current({"profile": {"id": "x"}})

        reopened = sync.UscfCache(path)
        assert reopened.fetched_at() is not None  # a timestamp the UI can show

    def test_missing_cache_file_is_just_empty(self, tmp_path):
        cache = sync.UscfCache(str(tmp_path / "never-written.json"))
        assert cache.get_current("profile") is None
        assert cache.fetched_at() is None

    def test_corrupt_cache_file_is_tolerated(self, tmp_path):
        """A half-written or corrupted cache must never break a Sync."""
        path = tmp_path / "uscf_cache.json"
        path.write_text("{not valid json", encoding="utf-8")

        cache = sync.UscfCache(str(path))
        assert cache.get_current("profile") is None
        # ...and it can be written over
        cache.replace_current({"profile": {"id": "x"}})
        assert sync.UscfCache(str(path)).get_current("profile") == {"id": "x"}

    def test_unwritable_path_fails_silently(self):
        """Hosts without a writable disk just go without the cache (ADR 0002)."""
        cache = sync.UscfCache("/nonexistent-dir/sub/uscf_cache.json")
        cache.replace_current({"profile": {"id": "x"}})  # must not raise
        assert cache.get_current("profile") == {"id": "x"}  # still served in-memory

    def test_no_path_means_no_caching(self):
        """cache_path=None (no cache configured) is valid and inert."""
        cache = sync.UscfCache(None)
        cache.replace_current({"profile": {"id": "x"}})
        assert cache.get_current("profile") == {"id": "x"}


class TestUscfCachePolicy:
    """
    Immutable vs current-state data (issue #26): immutable USCF data (rated
    crosstables, past supplements) is fetched once and never again; current
    data (the profile) is refreshed on every Sync.
    """

    def test_immutable_data_is_fetched_only_once(self, tmp_path):
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        fetcher = mock.Mock(return_value={"standings": ["..."]})

        first = cache.fetch_immutable("standings/202605290393/1", fetcher)
        second = cache.fetch_immutable("standings/202605290393/1", fetcher)

        assert first == second == {"standings": ["..."]}
        fetcher.assert_called_once()  # the second read came from the cache

    def test_immutable_data_is_never_refetched_across_restarts(self, tmp_path):
        path = str(tmp_path / "uscf_cache.json")
        sync.UscfCache(path).fetch_immutable("standings/1/1", lambda: {"final": True})

        # Next app run: the fetcher must not even be consulted
        never_called = mock.Mock(side_effect=AssertionError("re-fetched immutable data"))
        value = sync.UscfCache(path).fetch_immutable("standings/1/1", never_called)

        assert value == {"final": True}
        never_called.assert_not_called()

    def test_current_data_is_replaced_each_sync(self, tmp_path):
        """The profile is current-state: each Sync's data fully replaces the last."""
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.replace_current({"profile": {"rating": 1545}})
        cache.replace_current({"profile": {"rating": 1571}})

        assert sync.UscfCache(path).get_current("profile") == {"rating": 1571}

    def test_replacing_current_data_keeps_immutable_data(self, tmp_path):
        """A routine Sync must never wipe the permanently-cached entries."""
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.fetch_immutable("standings/1/1", lambda: {"final": True})
        cache.replace_current({"profile": {"rating": 1571}})

        reopened = sync.UscfCache(path)
        cached = reopened.fetch_immutable(
            "standings/1/1", mock.Mock(side_effect=AssertionError("re-fetched"))
        )
        assert cached == {"final": True}


class TestUscfCacheDismissals:
    """
    Reconciliation dismissals (issue #30): user judgements ("USCF is wrong",
    "intentionally skipped"), not API responses — they must survive every
    Sync's replace_current and app restarts.  Best-effort persistence: a
    redeploy on a stateless host may resurrect dismissed items (documented).
    """

    def test_dismissals_round_trip(self, tmp_path):
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.add_dismissal("conflict:https://lichess.org/study/x/abc")

        assert cache.dismissals() == ["conflict:https://lichess.org/study/x/abc"]

    def test_dismissals_survive_replace_current(self, tmp_path):
        """replace_current wipes API data every Sync — never user judgements."""
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.add_dismissal("uscf-only:202601300323:20000166:Black")

        cache.replace_current({"profile": {"id": "x"}})

        assert cache.dismissals() == ["uscf-only:202601300323:20000166:Black"]

    def test_dismissals_survive_restarts(self, tmp_path):
        path = str(tmp_path / "uscf_cache.json")
        sync.UscfCache(path).add_dismissal("rating-mismatch:url")

        reopened = sync.UscfCache(path)
        assert reopened.dismissals() == ["rating-mismatch:url"]

    def test_dismissing_twice_stores_once(self, tmp_path):
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.add_dismissal("conflict:url")
        cache.add_dismissal("conflict:url")

        assert cache.dismissals() == ["conflict:url"]

    def test_no_cache_path_keeps_dismissals_in_memory(self):
        """Stateless hosts: dismissals work for the app's lifetime, then are
        forgotten — the documented best-effort limitation."""
        cache = sync.UscfCache(None)
        cache.add_dismissal("conflict:url")  # must not raise

        assert cache.dismissals() == ["conflict:url"]


class TestUscfCacheAged:
    """
    Aged entries (issue #35): data that changes slowly — opponent current
    ratings — refreshed at most every *max_age*, never on every Sync.
    The fourth cache kind: not current (per-Sync), not immutable (forever),
    not user state.
    """

    def test_first_fetch_stores_and_returns(self, tmp_path):
        cache = sync.UscfCache(str(tmp_path / "uscf_cache.json"))
        fetcher = mock.Mock(return_value={"id": "20000056", "rating": 1400})

        value = cache.fetch_aged("opponent:20000056", fetcher,
                                 max_age=__import__("datetime").timedelta(days=7))

        assert value == {"id": "20000056", "rating": 1400}
        fetcher.assert_called_once()

    def test_fresh_entries_are_served_without_fetching(self, tmp_path):
        """Within the freshness window, the cache answers — politeness toward
        an API we were not invited to use."""
        from datetime import timedelta
        path = str(tmp_path / "uscf_cache.json")
        sync.UscfCache(path).fetch_aged(
            "opponent:20000056", lambda: {"rating": 1400}, max_age=timedelta(days=7))

        never_called = mock.Mock(side_effect=AssertionError("refetched fresh data"))
        value = sync.UscfCache(path).fetch_aged(
            "opponent:20000056", never_called, max_age=timedelta(days=7))

        assert value == {"rating": 1400}
        never_called.assert_not_called()

    def test_stale_entries_are_refetched(self, tmp_path):
        """Past the freshness window the fetcher runs again — max_age=0 makes
        everything stale, exercising the refresh path."""
        from datetime import timedelta
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.fetch_aged("opponent:20000056", lambda: {"rating": 1400},
                         max_age=timedelta(days=7))

        refreshed = cache.fetch_aged("opponent:20000056", lambda: {"rating": 1412},
                                     max_age=timedelta(0))

        assert refreshed == {"rating": 1412}

    def test_get_aged_serves_any_age_without_fetching(self, tmp_path):
        """The USCF-down path: whatever is stored is better than nothing,
        however old."""
        from datetime import timedelta
        path = str(tmp_path / "uscf_cache.json")
        sync.UscfCache(path).fetch_aged(
            "opponent:20000056", lambda: {"rating": 1400}, max_age=timedelta(days=7))

        assert sync.UscfCache(path).get_aged("opponent:20000056") == {"rating": 1400}
        assert sync.UscfCache(path).get_aged("opponent:99999999") is None

    def test_aged_entries_survive_replace_current(self, tmp_path):
        """A routine Sync's replace_current never wipes opponent profiles."""
        from datetime import timedelta
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.fetch_aged("opponent:20000056", lambda: {"rating": 1400},
                         max_age=timedelta(days=7))

        cache.replace_current({"profile": {"id": "x"}})

        assert cache.get_aged("opponent:20000056") == {"rating": 1400}


class TestUscfCacheSeenAchievements:
    """
    Seen-achievement memory (issue #36): which norms/awards every previous
    Sync has already seen, so a fresh one is celebrated exactly once — and
    never again after restarts.  Like dismissals, this is bookkeeping state
    that must survive replace_current.
    """

    def test_never_recorded_is_none_not_empty(self, tmp_path):
        """None = 'never recorded' (first run); [] = 'recorded, member has none'.
        The distinction decides whether existing achievements get celebrated."""
        cache = sync.UscfCache(str(tmp_path / "uscf_cache.json"))
        assert cache.seen_achievements() is None

    def test_recorded_achievements_round_trip(self, tmp_path):
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.record_achievements(["norm:FourthCategory:202512140213"])

        reopened = sync.UscfCache(path)
        assert reopened.seen_achievements() == ["norm:FourthCategory:202512140213"]

    def test_recording_an_empty_list_is_not_none(self, tmp_path):
        """A member with no achievements still gets their seen-state recorded,
        so their first-ever norm celebrates."""
        path = str(tmp_path / "uscf_cache.json")
        sync.UscfCache(path).record_achievements([])

        assert sync.UscfCache(path).seen_achievements() == []

    def test_seen_achievements_survive_replace_current(self, tmp_path):
        """replace_current wipes API data every Sync — never bookkeeping state."""
        path = str(tmp_path / "uscf_cache.json")
        cache = sync.UscfCache(path)
        cache.record_achievements(["award:01KRQV7CN2Z2KXA7V8EV9MAJVM"])

        cache.replace_current({"profile": {"id": "x"}})

        assert cache.seen_achievements() == ["award:01KRQV7CN2Z2KXA7V8EV9MAJVM"]


class TestSyncUscf:
    """The USCF half of a Sync (issue #25, ADR 0003)."""

    def test_successful_sync_returns_the_profile(self, uscf_profile_json):
        with stub_uscf(uscf_profile_json):
            result = sync.sync_uscf("12345678")

        assert result.available
        assert result.profile.name == "Daniel Gentile"
        assert result.profile.rating("R").rating == 1545
        # The UI can report when USCF data was last fetched
        assert result.synced_at is not None
        assert result.failure == ""

    def test_uscf_failure_never_raises(self):
        """ADR 0003: a Sync that reaches Lichess but not USCF is a successful Sync.
        The USCF half degrades to 'unavailable' — it never throws."""
        with stub_uscf(UscfUnreachableError("Could not reach USCF: connection refused")):
            result = sync.sync_uscf("12345678")

        assert not result.available
        assert result.profile is None
        assert result.synced_at is None
        # The reason is recorded so the UI can say why USCF panels are empty
        assert "Could not reach USCF" in result.failure


class TestSyncUscfWithCache:
    """USCF data survives the USCF API being down (issue #26)."""

    def test_successful_sync_writes_the_cache(self, uscf_profile_json, tmp_path):
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json):
            sync.sync_uscf("12345678", cache_path=cache_path)

        # The raw response is cached for the next run to fall back on
        cached = sync.UscfCache(cache_path).get_current("profile")
        assert cached == uscf_profile_json

    def test_failure_falls_back_to_cached_data(self, uscf_profile_json, tmp_path):
        """USCF down → the previous successful Sync's data, clearly marked stale."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json):
            good = sync.sync_uscf("12345678", cache_path=cache_path)

        boom = UscfUnreachableError("Could not reach USCF")
        with stub_uscf(boom):
            degraded = sync.sync_uscf("12345678", cache_path=cache_path)

        # The cached profile is served...
        assert degraded.available
        assert degraded.profile.rating("R").rating == 1545
        # ...visibly marked as cached/stale, with the failure and its age
        assert degraded.from_cache is True
        assert "Could not reach USCF" in degraded.failure
        # synced_at = when USCF was last actually reached (the cached data's age)
        assert degraded.synced_at is not None
        assert degraded.synced_at <= good.synced_at

    def test_failure_without_cache_is_unavailable(self, tmp_path):
        """First-ever run with USCF down: nothing to fall back on."""
        boom = UscfUnreachableError("Could not reach USCF")
        with stub_uscf(boom):
            result = sync.sync_uscf(
                "12345678", cache_path=str(tmp_path / "never-written.json")
            )

        assert not result.available
        assert result.from_cache is False
        assert "Could not reach USCF" in result.failure

    def test_fresh_data_is_not_marked_cached(self, uscf_profile_json, tmp_path):
        with stub_uscf(uscf_profile_json):
            result = sync.sync_uscf(
                "12345678", cache_path=str(tmp_path / "uscf_cache.json")
            )

        assert result.available
        assert result.from_cache is False
        assert result.failure == ""


class TestSyncUscfSeries:
    """sync_uscf also builds the Official and Live rating series (issue #27)."""

    def test_successful_sync_builds_both_series(
        self, uscf_profile_json, uscf_supplements_json, uscf_sections_json
    ):
        with stub_uscf(
            uscf_profile_json,
            supplements=uscf_supplements_json["items"],
            sections=uscf_sections_json["items"],
        ):
            result = sync.sync_uscf("12345678")

        # The Official series: one point per supplement month
        assert len(result.official_series) == 10
        assert result.official_series[-1].rating == 1545
        # The Live series: the Regular chain with decimals
        assert len(result.live_series) == 23
        assert result.live_series[-1].post == 1570.72

    def test_series_survive_uscf_being_down(
        self, uscf_profile_json, uscf_supplements_json, uscf_sections_json, tmp_path
    ):
        """The series degrade to cached data exactly like the profile (ADR 0003)."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(
            uscf_profile_json,
            supplements=uscf_supplements_json["items"],
            sections=uscf_sections_json["items"],
        ):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(UscfUnreachableError("USCF is down")):
            degraded = sync.sync_uscf("12345678", cache_path=cache_path)

        assert degraded.from_cache is True
        assert len(degraded.official_series) == 10
        assert len(degraded.live_series) == 23
        assert degraded.live_series[-1].post == 1570.72

    def test_any_endpoint_failing_degrades_the_whole_uscf_half(
        self, uscf_profile_json, uscf_supplements_json, uscf_sections_json, tmp_path
    ):
        """Partial USCF data would be inconsistent — one endpoint failing means
        the whole USCF half falls back to the cache."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(
            uscf_profile_json,
            supplements=uscf_supplements_json["items"],
            sections=uscf_sections_json["items"],
        ):
            sync.sync_uscf("12345678", cache_path=cache_path)

        # Profile fetch works, but the sections endpoint fails mid-Sync
        with stub_uscf(
            uscf_profile_json,
            supplements=uscf_supplements_json["items"],
            sections=UscfUnreachableError("sections endpoint broke"),
        ):
            result = sync.sync_uscf("12345678", cache_path=cache_path)

        # Everything (profile included) comes from the consistent cached snapshot
        assert result.from_cache is True
        assert result.available
        assert len(result.live_series) == 23
        assert "sections endpoint broke" in result.failure


class TestSyncUscfGames:
    """sync_uscf also fetches USCF Game Records — the matching engine's input
    (issue #28)."""

    def test_successful_sync_returns_typed_game_records(
        self, uscf_profile_json, uscf_games_json
    ):
        with stub_uscf(uscf_profile_json, games=uscf_games_json["items"]):
            result = sync.sync_uscf("12345678")

        assert len(result.game_records) == 63
        assert result.game_records[0].opponent_name == "BOB BAKER"
        assert result.game_records[0].event_name == "ACC MAY 2026"

    def test_game_records_survive_uscf_being_down(
        self, uscf_profile_json, uscf_games_json, tmp_path
    ):
        """Game records degrade to the cached snapshot like everything else
        (ADR 0003) — matching keeps working while USCF is unreachable."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, games=uscf_games_json["items"]):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(UscfUnreachableError("USCF is down")):
            degraded = sync.sync_uscf("12345678", cache_path=cache_path)

        assert degraded.from_cache is True
        assert len(degraded.game_records) == 63

    def test_games_endpoint_failing_degrades_the_whole_uscf_half(
        self, uscf_profile_json, uscf_games_json, tmp_path
    ):
        """All-or-nothing (PR #37's decision): the games endpoint failing means
        the whole USCF half comes from the consistent cached snapshot."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, games=uscf_games_json["items"]):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(
            uscf_profile_json,
            games=UscfUnreachableError("games endpoint broke"),
        ):
            result = sync.sync_uscf("12345678", cache_path=cache_path)

        assert result.from_cache is True
        assert result.available
        assert len(result.game_records) == 63
        assert "games endpoint broke" in result.failure


class TestSyncUscfEvents:
    """sync_uscf also fetches the member's Rated Events — the Events page's
    grouping data (issue #33)."""

    def test_successful_sync_returns_typed_events(
        self, uscf_profile_json, uscf_events_json
    ):
        with stub_uscf(uscf_profile_json, events=uscf_events_json["items"]):
            result = sync.sync_uscf("12345678")

        assert len(result.member_events) == 23
        assert result.member_events[0].name == "ACC JUNE 2025"     # chronological
        assert result.member_events[-1].name == "ACC MAY 2026"

    def test_events_survive_uscf_being_down(
        self, uscf_profile_json, uscf_events_json, tmp_path
    ):
        """Rated Events degrade to the cached snapshot like everything else."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, events=uscf_events_json["items"]):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(UscfUnreachableError("USCF is down")):
            degraded = sync.sync_uscf("12345678", cache_path=cache_path)

        assert degraded.from_cache is True
        assert len(degraded.member_events) == 23

    def test_events_endpoint_failing_degrades_the_whole_uscf_half(
        self, uscf_profile_json, uscf_events_json, tmp_path
    ):
        """All-or-nothing: the events endpoint is part of the member snapshot."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, events=uscf_events_json["items"]):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(uscf_profile_json,
                       events=UscfUnreachableError("events endpoint broke")):
            result = sync.sync_uscf("12345678", cache_path=cache_path)

        assert result.from_cache is True
        assert len(result.member_events) == 23
        assert "events endpoint broke" in result.failure


class TestSyncUscfStandings:
    """sync_uscf also fetches crosstables — one per OTB Section played,
    cached permanently (issue #34)."""

    @pytest.fixture()
    def raw_standings(self, uscf_standings_json):
        """The captured crosstables as the stub wants them: raw item lists."""
        return {key: raw["items"] for key, raw in uscf_standings_json.items()}

    def test_standings_are_fetched_per_played_otb_section(
        self, uscf_profile_json, uscf_sections_json, raw_standings
    ):
        with stub_uscf(uscf_profile_json, sections=uscf_sections_json["items"],
                       standings=raw_standings):
            result = sync.sync_uscf("12345678")

        # The 5 captured crosstables come back typed, keyed by section NAME
        # (what the enriched Games carry)
        assert ("202605290393", "LADDER") in result.standings
        assert ("202603290543", "Under 1800") in result.standings
        acc_may = result.standings[("202605290393", "LADDER")]
        assert len(acc_may) == 116
        assert acc_may[0].ordinal == 1

    def test_online_sections_never_fetch_a_crosstable(
        self, uscf_profile_json, uscf_sections_json, raw_standings
    ):
        """The OR section (DMVCHESS WEDNESDAY CLASS) is online-rated: its games
        are never Chapters, so its crosstable is never fetched (politeness)."""
        with stub_uscf(uscf_profile_json, sections=uscf_sections_json["items"],
                       standings=raw_standings) as mocks:
            sync.sync_uscf("12345678")

        fetched = {call.args for call in mocks.standings.call_args_list}
        assert ("202601300323", 6) not in fetched      # the online section

    def test_crosstables_are_cached_permanently(
        self, uscf_profile_json, uscf_sections_json, raw_standings, tmp_path
    ):
        """Issue #34's acceptance criterion: repeat Syncs make ZERO crosstable
        calls for already-cached events — they are immutable once rated."""
        cache_path = str(tmp_path / "uscf_cache.json")
        # Only the Sections whose crosstables were captured as fixtures
        sections = [s for s in uscf_sections_json["items"]
                    if (s["event"]["id"], s["sectionNumber"]) in raw_standings]

        with stub_uscf(uscf_profile_json, sections=sections,
                       standings=raw_standings) as first_mocks:
            sync.sync_uscf("12345678", cache_path=cache_path)
        assert first_mocks.standings.call_count == len(raw_standings)   # first-ever Sync fetches

        with stub_uscf(uscf_profile_json, sections=sections,
                       standings=raw_standings) as second_mocks:
            second = sync.sync_uscf("12345678", cache_path=cache_path)

        assert second_mocks.standings.call_count == 0          # everything served from cache
        assert ("202605290393", "LADDER") in second.standings

    def test_a_failed_crosstable_is_retried_on_the_next_sync(
        self, uscf_profile_json, uscf_sections_json, raw_standings, tmp_path
    ):
        """Only a SUCCESSFUL fetch is cached forever — a transient failure never
        permanently loses that Section's standings."""
        cache_path = str(tmp_path / "uscf_cache.json")
        sections = [s for s in uscf_sections_json["items"]
                    if (s["event"]["id"], s["sectionNumber"]) in raw_standings]

        broken = dict(raw_standings)
        broken[("202605290393", 1)] = UscfUnreachableError("transient failure")
        with stub_uscf(uscf_profile_json, sections=sections, standings=broken):
            first = sync.sync_uscf("12345678", cache_path=cache_path)
        assert ("202605290393", "LADDER") not in first.standings

        # USCF recovers → the next Sync fetches the one that failed, only that
        with stub_uscf(uscf_profile_json, sections=sections,
                       standings=raw_standings) as retry_mocks:
            second = sync.sync_uscf("12345678", cache_path=cache_path)

        assert {call.args for call in retry_mocks.standings.call_args_list} == {("202605290393", 1)}
        assert ("202605290393", "LADDER") in second.standings

    def test_one_crosstable_failing_skips_only_that_section(
        self, uscf_profile_json, uscf_sections_json, raw_standings
    ):
        """Crosstable failures degrade individually (unlike the member
        snapshot): one Section's standings missing never costs the others."""
        broken = dict(raw_standings)
        broken[("202605290393", 1)] = UscfUnreachableError("crosstable broke")

        with stub_uscf(uscf_profile_json, sections=uscf_sections_json["items"],
                       standings=broken):
            result = sync.sync_uscf("12345678")

        assert result.available                           # the Sync itself is fine
        assert ("202605290393", "LADDER") not in result.standings
        assert ("202603290543", "Under 1800") in result.standings

    def test_cached_crosstables_survive_uscf_being_down(
        self, uscf_profile_json, uscf_sections_json, raw_standings, tmp_path
    ):
        """USCF down: the member snapshot degrades to cache — and so do the
        crosstables (they're immutable; the cache is always correct)."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, sections=uscf_sections_json["items"],
                       standings=raw_standings):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(UscfUnreachableError("USCF is down")):
            degraded = sync.sync_uscf("12345678", cache_path=cache_path)

        assert degraded.from_cache is True
        assert ("202605290393", "LADDER") in degraded.standings
        assert len(degraded.standings[("202605290393", "LADDER")]) == 116


class TestSyncOpponentProfiles:
    """sync_uscf also fetches opponent current ratings — politely (issue #35):
    one call per unique opponent, refreshed at most weekly."""

    @pytest.fixture()
    def opponent_fixtures(self, uscf_games_json):
        """Two real opponent profiles + the games that reference them."""
        import json
        from pathlib import Path
        fixtures = Path("tests/data/uscf")
        return {
            "20000056": json.loads((fixtures / "opponent-bob-baker.json").read_text()),
            "20000144": json.loads((fixtures / "opponent-carver-clark.json").read_text()),
        }

    def test_one_call_per_unique_opponent(
        self, uscf_profile_json, uscf_games_json, opponent_fixtures
    ):
        """Daniel played Baker 5 times — his profile is fetched once."""
        with stub_uscf(uscf_profile_json, games=uscf_games_json["items"],
                       opponent_profiles=opponent_fixtures) as mocks:
            result = sync.sync_uscf("12345678")

        opponent_calls = [call.args[0] for call in mocks.profiles.call_args_list
                          if call.args[0] != "12345678"]
        assert len(opponent_calls) == len(set(opponent_calls))  # no duplicates

        # The stubbed opponents come back typed; Baker is rated 1400 now
        assert result.opponent_profiles["20000056"].rating("R").rating == 1400
        assert result.opponent_profiles["20000144"].name == "Carver Clark"

    def test_profiles_are_not_refetched_within_a_week(
        self, uscf_profile_json, uscf_games_json, opponent_fixtures, tmp_path
    ):
        """The politeness criterion: cached, not re-fetched on every Sync."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, games=uscf_games_json["items"],
                       opponent_profiles=opponent_fixtures):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(uscf_profile_json, games=uscf_games_json["items"],
                       opponent_profiles=opponent_fixtures) as mocks:
            second = sync.sync_uscf("12345678", cache_path=cache_path)

        # The opponents fetched last week are NOT asked for again...
        second_calls = [call.args[0] for call in mocks.profiles.call_args_list]
        assert "20000056" not in second_calls
        assert "20000144" not in second_calls
        # ...their cached profiles are simply served.  (Opponents whose fetch
        # FAILED last time are retried — a failure is never cached.)
        assert second.opponent_profiles["20000056"].rating("R").rating == 1400

    def test_an_unreachable_opponent_is_skipped_not_fatal(
        self, uscf_profile_json, uscf_games_json, opponent_fixtures
    ):
        """One opponent's profile failing never costs the others (ADR 0003) —
        the unstubbed 51 opponents here are exactly that case."""
        with stub_uscf(uscf_profile_json, games=uscf_games_json["items"],
                       opponent_profiles=opponent_fixtures):
            result = sync.sync_uscf("12345678")

        assert result.available
        # The two stubbed opponents made it; the rest are simply absent
        assert set(result.opponent_profiles) == {"20000056", "20000144"}

    def test_opponent_profiles_survive_uscf_being_down(
        self, uscf_profile_json, uscf_games_json, opponent_fixtures, tmp_path
    ):
        """Stale beats nothing: cached opponent ratings outlive a USCF outage."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, games=uscf_games_json["items"],
                       opponent_profiles=opponent_fixtures):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(UscfUnreachableError("USCF is down")):
            degraded = sync.sync_uscf("12345678", cache_path=cache_path)

        assert degraded.from_cache is True
        assert degraded.opponent_profiles["20000056"].rating("R").rating == 1400

    def test_no_games_means_no_opponent_calls(self, uscf_profile_json):
        """A member with no USCF Game Records triggers zero opponent fetches."""
        with stub_uscf(uscf_profile_json) as mocks:
            result = sync.sync_uscf("12345678")

        assert [call.args[0] for call in mocks.profiles.call_args_list] == ["12345678"]
        assert result.opponent_profiles == {}


class TestSyncUscfAchievements:
    """sync_uscf also fetches norms and awards — official achievements that
    become Milestones (issue #36)."""

    def test_successful_sync_returns_typed_achievements(
        self, uscf_profile_json, uscf_norms_json, uscf_awards_json
    ):
        with stub_uscf(uscf_profile_json, norms=uscf_norms_json["items"],
                       awards=uscf_awards_json["items"]):
            result = sync.sync_uscf("12345678")

        assert [a.title for a in result.achievements] == [
            "Fourth Category norm", "25th career win",
        ]

    def test_achievements_survive_uscf_being_down(
        self, uscf_profile_json, uscf_norms_json, uscf_awards_json, tmp_path
    ):
        """Previously seen norms/awards remain when USCF is unavailable —
        issue #36's acceptance criterion, via the cached snapshot (ADR 0003)."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, norms=uscf_norms_json["items"],
                       awards=uscf_awards_json["items"]):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(UscfUnreachableError("USCF is down")):
            degraded = sync.sync_uscf("12345678", cache_path=cache_path)

        assert degraded.from_cache is True
        assert [a.title for a in degraded.achievements] == [
            "Fourth Category norm", "25th career win",
        ]

    def test_norms_endpoint_failing_degrades_the_whole_uscf_half(
        self, uscf_profile_json, uscf_norms_json, tmp_path
    ):
        """All-or-nothing: norms/awards are part of the member snapshot."""
        cache_path = str(tmp_path / "uscf_cache.json")
        with stub_uscf(uscf_profile_json, norms=uscf_norms_json["items"]):
            sync.sync_uscf("12345678", cache_path=cache_path)

        with stub_uscf(uscf_profile_json,
                       norms=UscfUnreachableError("norms endpoint broke")):
            result = sync.sync_uscf("12345678", cache_path=cache_path)

        assert result.from_cache is True
        assert [a.title for a in result.achievements] == ["Fourth Category norm"]
        assert "norms endpoint broke" in result.failure


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
