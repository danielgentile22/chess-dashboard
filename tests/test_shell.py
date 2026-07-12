"""
tests/test_shell.py
===================
Behavior tests for the app shell (shell.py) and global filters (filters.py):
the Sync button, the freshness indicator, the filter drawer, presets, and
filter options that follow the data.

The Lichess client is stubbed at the module boundary (inside sync) — no
network.  Callbacks are invoked directly: Dash returns the original function
from the @callback decorator, so they are plain functions here.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest
from dash import no_update

import data
import sync


@pytest.fixture(autouse=True)
def fresh_store(sample_pgn_text):
    """Each test starts with the store holding the 7 fixture games."""
    data.reset()
    with mock.patch.object(sync, "fetch_study_pgn", return_value=sample_pgn_text):
        data.initialize(["teststudy"], player_name="Test Player")
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


# ---------------------------------------------------------------------------
# Sync button
# ---------------------------------------------------------------------------

class TestSyncButton:
    def test_successful_sync_bumps_store_and_reports(self, sample_pgn_text):
        from shell import run_sync
        with stub_studies(teststudy=sample_pgn_text):
            store, is_open, header, icon, body, _ = run_sync(1, {"seq": 0, "new_games": 0})

        assert store["seq"] == 1
        assert is_open is True
        assert icon == "success"
        assert "up to date" in str(body)

    def test_successful_sync_restates_freshness_in_the_toast(self, sample_pgn_text):
        """Freshness left the header (issue #45) — the post-Sync toast is where
        a fresh Sync confirms its sources are current."""
        from shell import run_sync
        with stub_studies(teststudy=sample_pgn_text):
            *_, body, _ = run_sync(1, {"seq": 0, "new_games": 0})
        assert "synced" in str(body)

    def test_sync_with_new_games_names_them(self, sample_pgn_text, sample_pgn_study2_text):
        from shell import run_sync
        grown = sample_pgn_text + "\n\n" + sample_pgn_study2_text
        with stub_studies(teststudy=grown):
            store, _, _, icon, body, _ = run_sync(1, {"seq": 0, "new_games": 0})

        assert icon == "success"
        assert store["new_games"] == 2
        assert "Opponent E" in str(body)

    def test_failed_sync_keeps_data_and_warns(self):
        from lichess_client import LichessUnreachableError
        from shell import run_sync
        with stub_studies(teststudy=LichessUnreachableError("lichess is down")):
            store, is_open, header, icon, body, _ = run_sync(1, {"seq": 0, "new_games": 0})

        assert store is no_update           # charts must NOT re-render
        assert is_open is True
        assert icon == "danger"
        assert len(data.get_df()) == 7      # current data untouched


# ---------------------------------------------------------------------------
# Milestone celebrations (issue #15)
# ---------------------------------------------------------------------------

class TestCelebrations:
    """A Sync that sets a personal best earns a gold banner."""

    def _sync_grown_archive(self, sample_pgn_text, sample_pgn_study2_text):
        """Run a Sync that adds Study 2's games (new peak rating + new streak)."""
        from shell import run_sync
        grown = sample_pgn_text + "\n\n" + sample_pgn_study2_text
        with stub_studies(teststudy=grown):
            return run_sync(1, {"seq": 0, "new_games": 0})

    def test_record_breaking_sync_shows_celebration(self, sample_pgn_text,
                                                    sample_pgn_study2_text):
        outputs = self._sync_grown_archive(sample_pgn_text, sample_pgn_study2_text)
        celebration = outputs[-1]
        rendered = str(celebration)
        assert "1815" in rendered                  # the new peak rating…
        assert "1810" in rendered                  # …and the record it broke
        assert "win streak" in rendered.lower()    # the new longest streak too

    def test_celebration_is_dismissible(self, sample_pgn_text, sample_pgn_study2_text):
        outputs = self._sync_grown_archive(sample_pgn_text, sample_pgn_study2_text)
        celebration = outputs[-1]
        assert celebration.dismissable is True

    def test_sync_without_new_bests_changes_nothing(self, sample_pgn_text):
        """Re-Syncing the same games breaks no records → the zone is untouched."""
        from shell import run_sync
        with stub_studies(teststudy=sample_pgn_text):
            outputs = run_sync(1, {"seq": 0, "new_games": 0})
        assert outputs[-1] is no_update

    def test_failed_sync_changes_nothing(self):
        from lichess_client import LichessUnreachableError
        from shell import run_sync
        with stub_studies(teststudy=LichessUnreachableError("down")):
            outputs = run_sync(1, {"seq": 0, "new_games": 0})
        assert outputs[-1] is no_update


# ---------------------------------------------------------------------------
# Official achievement celebrations (issue #36)
# ---------------------------------------------------------------------------

class TestAchievementCelebrations:
    """A Sync that first sees a new norm or award earns the gold banner —
    official achievements get the same treatment as personal bests."""

    def test_a_new_award_is_celebrated(self, sample_pgn_text, uscf_profile_json,
                                       uscf_norms_json, uscf_awards_json):
        from shell import run_sync

        # Boot knowing only the norm...
        data.reset()
        with stub_studies(teststudy=sample_pgn_text), \
             stub_uscf(uscf_profile_json, norms=uscf_norms_json["items"]):
            data.initialize(["teststudy"], player_name="Test Player",
                            uscf_member_id="12345678")

        # ...then a Sync brings the 25th-win award for the first time
        with stub_studies(teststudy=sample_pgn_text), \
             stub_uscf(uscf_profile_json, norms=uscf_norms_json["items"],
                       awards=uscf_awards_json["items"]):
            outputs = run_sync(1, {"seq": 0, "new_games": 0})

        rendered = str(outputs[-1])
        assert "25th career win" in rendered
        assert "Newcomb" in rendered                     # where it was earned
        # The norm was already known at boot — only the fresh award celebrates
        assert "Fourth Category norm" not in rendered

    def test_achievement_only_celebrations_say_official_not_personal_best(
        self, sample_pgn_text, uscf_profile_json, uscf_awards_json
    ):
        """An official USCF achievement is not a 'personal best' — the banner
        says what it actually is."""
        from shell import run_sync

        data.reset()
        with stub_studies(teststudy=sample_pgn_text), stub_uscf(uscf_profile_json):
            data.initialize(["teststudy"], player_name="Test Player",
                            uscf_member_id="12345678")

        with stub_studies(teststudy=sample_pgn_text), \
             stub_uscf(uscf_profile_json, awards=uscf_awards_json["items"]):
            outputs = run_sync(1, {"seq": 0, "new_games": 0})

        rendered = str(outputs[-1])
        assert "official" in rendered.lower()
        assert "personal best" not in rendered.lower()

    def test_resyncing_known_achievements_celebrates_nothing(
        self, sample_pgn_text, uscf_profile_json, uscf_norms_json, uscf_awards_json
    ):
        """Achievements known since boot never re-celebrate on routine Syncs."""
        from shell import run_sync

        data.reset()
        with stub_studies(teststudy=sample_pgn_text), \
             stub_uscf(uscf_profile_json, norms=uscf_norms_json["items"],
                       awards=uscf_awards_json["items"]):
            data.initialize(["teststudy"], player_name="Test Player",
                            uscf_member_id="12345678")
            outputs = run_sync(1, {"seq": 0, "new_games": 0})

        assert outputs[-1] is no_update


# ---------------------------------------------------------------------------
# Freshness label + cached-data notice
# ---------------------------------------------------------------------------

class TestFreshness:
    def test_live_data_shows_synced_label_and_no_notice(self):
        from shell import update_freshness
        label, notice = update_freshness(0, {"seq": 0})
        assert "synced" in label  # the Sync button's tooltip text (issue #45)
        assert notice is None

    def test_cache_boot_shows_notice(self, sample_pgn_text, tmp_path):
        from lichess_client import LichessUnreachableError
        from shell import update_freshness

        cache = tmp_path / "games.pgn"
        with stub_studies(teststudy=sample_pgn_text):
            data.initialize(["teststudy"], player_name="Test Player", cache_path=str(cache))
        data.reset()
        with stub_studies(teststudy=LichessUnreachableError("down")):
            data.initialize(["teststudy"], player_name="Test Player", cache_path=str(cache))

        label, notice = update_freshness(0, {"seq": 0})
        assert "cached" in label
        assert notice is not None


# ---------------------------------------------------------------------------
# Per-source freshness (issue #26)
# ---------------------------------------------------------------------------

class TestPerSourceFreshness:
    """The freshness indicator distinguishes Lichess and USCF (issue #26)."""

    def _init_with_uscf(self, pgn, uscf, cache_path=None):
        data.reset()
        with stub_studies(teststudy=pgn), stub_uscf(uscf):
            data.initialize(
                ["teststudy"], player_name="Test Player",
                uscf_member_id="12345678", uscf_cache_path=cache_path,
            )

    def test_both_sources_fresh_both_named(self, sample_pgn_text, uscf_profile_json):
        from shell import update_freshness
        self._init_with_uscf(sample_pgn_text, uscf_profile_json)

        label, notice = update_freshness(0, {"seq": 0})

        assert "Lichess" in label and "USCF" in label
        assert label.count("synced") == 2    # each source has its own freshness
        assert notice is None

    def test_uscf_unavailable_is_said_plainly(self, sample_pgn_text, uscf_profile_json):
        """USCF down, nothing cached → 'USCF unavailable', Lichess unaffected."""
        from shell import update_freshness
        from uscf_client import UscfUnreachableError
        self._init_with_uscf(sample_pgn_text, UscfUnreachableError("down"))

        label, _ = update_freshness(0, {"seq": 0})

        assert "USCF unavailable" in label
        assert "Lichess synced" in label

    def test_cached_uscf_says_unavailable_since_when(
        self, sample_pgn_text, uscf_profile_json, tmp_path
    ):
        """USCF down with cached data → 'USCF unavailable since <time>'."""
        from shell import update_freshness
        from uscf_client import UscfUnreachableError

        cache = str(tmp_path / "uscf_cache.json")
        self._init_with_uscf(sample_pgn_text, uscf_profile_json, cache)
        data.reset()
        self._init_with_uscf(sample_pgn_text, UscfUnreachableError("down"), cache)

        label, _ = update_freshness(0, {"seq": 0})

        assert "USCF unavailable since" in label

    def test_without_uscf_configured_label_is_unchanged(self):
        """Lichess-only users see exactly the pre-USCF label (no 'USCF' noise).
        (The autouse fresh_store fixture initializes without USCF.)"""
        from shell import update_freshness

        label, _ = update_freshness(0, {"seq": 0})

        assert "USCF" not in label
        assert "synced" in label


# ---------------------------------------------------------------------------
# Streak fire + form dots (issue #10)
# ---------------------------------------------------------------------------

ALL_FILTERS = (["White", "Black"], ["Win", "Draw", "Loss"], [], None, None, [], None, None)


class TestFormIndicator:
    """The form_indicator component: fire / cold / dots rules."""

    def _render(self, **form):
        from components import form_indicator
        defaults = {"win_streak": 0, "loss_streak": 0, "last_5": []}
        return str(form_indicator({**defaults, **form}))

    def test_no_streak_means_no_fire_no_cold(self):
        rendered = self._render(win_streak=1, last_5=["Win"])
        assert "streak-fire" not in rendered
        assert "streak-cold" not in rendered

    def test_fire_at_win_streak_2(self):
        rendered = self._render(win_streak=2, last_5=["Win", "Win"])
        assert "streak-fire" in rendered
        assert "blazing" not in rendered

    def test_fire_blazes_at_win_streak_5(self):
        rendered = self._render(win_streak=5, last_5=["Win"] * 5)
        assert "streak-fire" in rendered
        assert "blazing" in rendered

    def test_fire_grows_with_the_streak(self):
        from components import form_indicator
        small = form_indicator({"win_streak": 2, "loss_streak": 0, "last_5": []})
        large = form_indicator({"win_streak": 8, "loss_streak": 0, "last_5": []})

        def _fire_size(children):
            fire = next(c for c in children if "streak-fire" in (c.className or ""))
            return float(fire.style["fontSize"].rstrip("px"))

        assert _fire_size(large) > _fire_size(small)

    def test_cold_below_3_losses_shows_nothing(self):
        rendered = self._render(loss_streak=2, last_5=["Loss", "Loss"])
        assert "streak-cold" not in rendered

    def test_cold_at_loss_streak_3(self):
        rendered = self._render(loss_streak=3, last_5=["Loss"] * 3)
        assert "streak-cold" in rendered
        assert "streak-fire" not in rendered

    def test_dots_ordered_oldest_to_newest(self):
        from components import form_indicator
        children = form_indicator(
            {"win_streak": 1, "loss_streak": 0, "last_5": ["Loss", "Draw", "Win"]}
        )
        dots_wrap = next(c for c in children if "form-dots" in (c.className or ""))
        classes = [dot.className for dot in dots_wrap.children]
        assert classes == ["form-dot loss", "form-dot draw", "form-dot win"]
        # Colourblind channel (issue #88): each dot carries its W/D/L letter.
        assert [dot.children for dot in dots_wrap.children] == ["L", "D", "W"]

    def test_empty_form_renders_nothing(self):
        from components import form_indicator
        assert form_indicator({"win_streak": 0, "loss_streak": 0, "last_5": []}) == []


class TestHeaderFormCallback:
    """update_form: the header indicators react to filters and Syncs."""

    def test_fixture_data_shows_dots_but_no_streak(self):
        """Fixture data ends on a Draw → no fire, no cold, 5 colored dots."""
        from shell import update_form
        rendered = str(update_form(*ALL_FILTERS))
        assert "streak-fire" not in rendered
        assert "streak-cold" not in rendered
        assert rendered.count("form-dot ") == 5

    def test_filtering_to_wins_lights_the_fire(self):
        """Filters apply to the form too: wins-only → a 4-game win streak."""
        from shell import update_form
        wins_only = (["White", "Black"], ["Win"], [], None, None, [], None, None)
        rendered = str(update_form(*wins_only))
        assert "streak-fire" in rendered

    def test_empty_data_shows_nothing(self):
        from shell import update_form
        impossible = (["White"], ["Win"], [], "2030-01-01", "2030-12-31", [], None, None)
        rendered = str(update_form(*impossible))
        assert "form-dot " not in rendered
        assert "streak-fire" not in rendered


# ---------------------------------------------------------------------------
# Filter drawer
# ---------------------------------------------------------------------------

class TestFilterDrawer:
    def test_button_toggles_drawer(self):
        from filters import toggle_filter_drawer
        assert toggle_filter_drawer(1, False) is True
        assert toggle_filter_drawer(2, True) is False

    def test_summary_counts_filtered_games(self):
        from filters import update_filter_summary
        summary, count, *_ = update_filter_summary(
            ["White", "Black"], ["Win", "Draw", "Loss"], [], None, None, [], None, None
        )
        assert "7" in str(summary)
        assert count == ""  # nothing active → no badge

    def test_summary_carries_the_relocated_date_range(self):
        """The date range moved from the header into the drawer summary (issue
        #45): it rides alongside the game count, separated by '·'."""
        from filters import update_filter_summary
        summary, _count, *_ = update_filter_summary(
            ["White", "Black"], ["Win", "Draw", "Loss"], [], None, None, [], None, None
        )
        rendered = str(summary)
        assert "·" in rendered                  # count · date-range
        assert "2024" in rendered               # the fixture games are from 2024

    def test_summary_date_range_follows_active_filters(self):
        """Filtering to a narrower set narrows the reported span too — the
        summary describes exactly the Games in view."""
        from filters import update_filter_summary
        # An impossible window leaves zero Games → no span to report
        empty, _count, *_ = update_filter_summary(
            ["White"], ["Win"], [], "2030-01-01", "2030-12-31", [], None, None
        )
        assert "·" not in str(empty)            # nothing matched, no date range

    def test_summary_reports_active_filter_count(self):
        from filters import update_filter_summary
        summary, count, *_ = update_filter_summary(
            ["White"], ["Win"], [], None, None, [], None, None
        )
        assert count == "2"  # color + outcome restricted

    def test_presets_set_filter_values(self):
        from filters import apply_preset

        with mock.patch("filters.callback_context") as ctx:
            ctx.triggered = [{"prop_id": "preset-wins.n_clicks"}]
            colors, outcomes, start, end = apply_preset(None, None, None, None, None, 1)
        assert outcomes == ["Win"]
        assert colors == ["White", "Black"]

        with mock.patch("filters.callback_context") as ctx:
            ctx.triggered = [{"prop_id": "preset-black.n_clicks"}]
            colors, outcomes, start, end = apply_preset(None, None, None, None, 1, None)
        assert colors == ["Black"]


# ---------------------------------------------------------------------------
# Filter options follow the data (after a Sync brings new games)
# ---------------------------------------------------------------------------

class TestFilterOptions:
    def test_options_reflect_current_data(self):
        from filters import update_filter_options
        out = update_filter_options({"seq": 1, "new_games": 0})
        termination_options = out[0]
        event_options = out[1]
        assert any("resignation" in str(o).lower() for o in termination_options)
        assert any("Test Open" in str(o) for o in event_options)

    def test_new_games_widen_date_selection(self, sample_pgn_text, sample_pgn_study2_text):
        """After a Sync that found new Games, the selected ranges reset so they're visible."""
        from filters import update_filter_options
        grown = sample_pgn_text + "\n\n" + sample_pgn_study2_text
        with stub_studies(teststudy=grown):
            data.refresh()

        out = update_filter_options({"seq": 2, "new_games": 2})
        start_value, end_value = out[4], out[5]
        assert start_value == "2024-01-06"
        assert end_value == "2024-09-01"

    def test_no_new_games_leaves_selection_alone(self):
        from filters import update_filter_options
        out = update_filter_options({"seq": 2, "new_games": 0})
        start_value, end_value = out[4], out[5]
        assert start_value is no_update
        assert end_value is no_update
