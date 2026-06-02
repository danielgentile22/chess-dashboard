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


@contextlib.contextmanager
def stub_uscf(profile, supplements=None, sections=None, games=None):
    """
    Patch the USCF client inside sync: each value is the raw JSON to return,
    or an Exception to raise.  List endpoints default to empty lists.
    """
    def fake(value):
        def fetch(member_id, **kwargs):
            if isinstance(value, Exception):
                raise value
            return value
        return fetch

    with mock.patch.object(sync, "fetch_member_profile",
                           side_effect=fake(profile), create=True), \
         mock.patch.object(sync, "fetch_rating_supplements",
                           side_effect=fake(supplements or []), create=True), \
         mock.patch.object(sync, "fetch_member_sections",
                           side_effect=fake(sections or []), create=True), \
         mock.patch.object(sync, "fetch_member_games",
                           side_effect=fake(games or []), create=True):
        yield


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
        assert result.game_records[0].opponent_name == "JOHN BAKER"
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
