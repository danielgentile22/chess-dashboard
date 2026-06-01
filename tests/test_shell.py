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


# ---------------------------------------------------------------------------
# Sync button
# ---------------------------------------------------------------------------

class TestSyncButton:
    def test_successful_sync_bumps_store_and_reports(self, sample_pgn_text):
        from shell import run_sync
        with stub_studies(teststudy=sample_pgn_text):
            store, is_open, header, icon, body = run_sync(1, {"seq": 0, "new_games": 0})

        assert store["seq"] == 1
        assert is_open is True
        assert icon == "success"
        assert "up to date" in str(body)

    def test_sync_with_new_games_names_them(self, sample_pgn_text, sample_pgn_study2_text):
        from shell import run_sync
        grown = sample_pgn_text + "\n\n" + sample_pgn_study2_text
        with stub_studies(teststudy=grown):
            store, _, _, icon, body = run_sync(1, {"seq": 0, "new_games": 0})

        assert icon == "success"
        assert store["new_games"] == 2
        assert "Opponent E" in str(body)

    def test_failed_sync_keeps_data_and_warns(self):
        from lichess_client import LichessUnreachableError
        from shell import run_sync
        with stub_studies(teststudy=LichessUnreachableError("lichess is down")):
            store, is_open, header, icon, body = run_sync(1, {"seq": 0, "new_games": 0})

        assert store is no_update           # charts must NOT re-render
        assert is_open is True
        assert icon == "danger"
        assert len(data.get_df()) == 7      # current data untouched


# ---------------------------------------------------------------------------
# Freshness label + cached-data notice
# ---------------------------------------------------------------------------

class TestFreshness:
    def test_live_data_shows_synced_label_and_no_notice(self):
        from shell import update_freshness
        label, notice = update_freshness(0, {"seq": 0})
        assert "synced" in label
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
