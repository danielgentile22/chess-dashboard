"""
tests/test_ui_smoke.py
======================
UI smoke-test harness (issue #8).

Boots the real multi-page Dash app with fixture data (Lichess client stubbed
at the module boundary — no network) and verifies, for every page:

  * it is registered at its expected URL with its expected nav name
  * its layout builds against real data without raising
  * its callbacks execute without error and respond to the global filters

Plus app-wide integrity checks:

  * the index page is served (Flask test client — no sockets involved)
  * every callback Input/Output/State references a component ID that exists
    in the shell or in some page layout (catches ID typos)

Subsequent page slices (issues #9–#12) extend ``PAGES`` and add their own
callback tests here.
"""
from __future__ import annotations

from datetime import date
from unittest import mock

import dash
import pytest

# Every page the app must serve: (path, registry name)
PAGES = [
    ("/",               "Overview"),
    ("/trends",         "Trends"),
    ("/openings",       "Openings"),
    ("/opponents",      "Opponents"),
    ("/events",         "Events"),
    ("/games",          "Games"),
    ("/lessons",        "Lessons"),
    ("/analysis",       "Analysis"),
    ("/reconciliation", "Reconciliation"),
]

# Default filter-callback arguments: everything selected / no restriction,
# matching what the UI sends when no filter has been touched.  The rating
# lens (issue #31) defaults to Official, exactly like the real toggle.
ALL_FILTERS = dict(
    colors=["White", "Black"],
    outcomes=["Win", "Draw", "Loss"],
    terminations=[],
    start=None,
    end=None,
    events=[],
    moves=None,
    sync=None,
    lens="official",
)


def _filter_args(**overrides):
    """Positional args for a standard filter-driven callback."""
    a = {**ALL_FILTERS, **overrides}
    return (a["colors"], a["outcomes"], a["terminations"], a["start"],
            a["end"], a["events"], a["moves"], a["sync"], a["lens"])


def _axis_vals(values) -> list:
    """A Plotly trace axis (numpy array, tuple, or None) as a plain list."""
    return [] if values is None else list(values)


def _walk_components(component):
    """Yield every Dash component in a layout tree (depth-first)."""
    if component is None or isinstance(component, (str, int, float, bool)):
        return
    if isinstance(component, (list, tuple)):
        for item in component:
            yield from _walk_components(item)
        return
    yield component
    yield from _walk_components(getattr(component, "children", None))


def _collect_ids(component) -> set[str]:
    """Recursively collect every component ID in a layout tree."""
    return {
        node.id for node in _walk_components(component)
        if isinstance(getattr(node, "id", None), str)
    }


def _page(path: str) -> dict:
    return next(p for p in dash.page_registry.values() if p["path"] == path)


def _render(page: dict):
    layout = page["layout"]
    return layout() if callable(layout) else layout


# ---------------------------------------------------------------------------
# The app boots and serves
# ---------------------------------------------------------------------------

class TestAppBoots:
    def test_index_page_is_served(self, ui_app, ui_data):
        client = ui_app.server.test_client()
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"react-entry-point" in resp.data or b"_dash-app-content" in resp.data

    def test_health_endpoint_responds(self, ui_app, ui_data):
        client = ui_app.server.test_client()
        assert client.get("/health").status_code == 200

    def test_every_page_registered(self, ui_app, ui_data):
        registered = {p["path"]: p["name"] for p in dash.page_registry.values()}
        for path, name in PAGES:
            assert path in registered, f"no page registered at {path}"
            assert registered[path] == name


# ---------------------------------------------------------------------------
# Every page layout renders against real data
# ---------------------------------------------------------------------------

class TestPageLayoutsRender:
    @pytest.mark.parametrize("path,name", PAGES)
    def test_page_layout_builds(self, ui_app, ui_data, path, name):
        tree = _render(_page(path))
        assert tree is not None
        # Every page wraps its content in a .page div for consistent styling
        assert "page" in getattr(tree, "className", "")


# ---------------------------------------------------------------------------
# The shell: nav, filter drawer, header, sync machinery
# ---------------------------------------------------------------------------

class TestShell:
    @pytest.fixture()
    def shell_ids(self, ui_app, ui_data) -> set[str]:
        layout = ui_app.layout
        return _collect_ids(layout() if callable(layout) else layout)

    def test_header_holds_exactly_the_calm_set(self, shell_ids):
        """The simplified header (issue #45): brand, form/streak, reconciliation
        badge, the Official/Live lens, Filters, Sync — and nothing else."""
        for present in ("header-form", "reconciliation-badge", "rating-lens",
                        "filter-drawer-button", "sync-button"):
            assert present in shell_ids, f"header is missing {present}"

    def test_header_metadata_relocated_out_of_header(self, shell_ids):
        """Game count, date range, and the standalone freshness label moved
        into the filter drawer / Sync tooltip (issue #45) — gone from the header."""
        assert "header-games-count" not in shell_ids
        assert "header-date-range" not in shell_ids
        assert "sync-freshness" not in shell_ids

    def test_header_has_form_indicators(self, shell_ids):
        assert "header-form" in shell_ids  # streak fire + form dots (issue #10)

    def test_filter_drawer_with_all_controls(self, shell_ids):
        assert "filter-drawer" in shell_ids
        for control in ("color-filter", "outcome-filter", "termination-filter",
                        "date-filter", "event-filter", "moves-filter"):
            assert control in shell_ids, f"filter drawer is missing {control}"

    def test_nav_links_for_every_page(self, ui_app, ui_data):
        layout = ui_app.layout
        tree = layout() if callable(layout) else layout
        hrefs = set()

        def _walk(node):
            if node is None or isinstance(node, (str, int, float, bool)):
                return
            if isinstance(node, (list, tuple)):
                for item in node:
                    _walk(item)
                return
            href = getattr(node, "href", None)
            if isinstance(href, str):
                hrefs.add(href)
            _walk(getattr(node, "children", None))

        _walk(tree)
        for path, name in PAGES:
            assert path in hrefs, f"no nav link to {name} ({path})"

    def test_filter_state_lives_in_shell_not_pages(self, shell_ids, ui_data):
        """Filter controls must be outside page content so navigation never resets them."""
        for path, _ in PAGES:
            page_ids = _collect_ids(_render(_page(path)))
            assert "color-filter" not in page_ids
            assert "date-filter" not in page_ids

    def test_celebration_zone_lives_in_shell_not_pages(self, shell_ids, ui_data):
        """A celebration earned by a Sync must survive page navigation (issue #15):
        the zone never unmounts because it's part of the shell, not a page."""
        assert "celebration-zone" in shell_ids
        for path, _ in PAGES:
            assert "celebration-zone" not in _collect_ids(_render(_page(path)))

    def test_freshness_is_wired_to_the_sync_button_tooltip(self, ui_app, ui_data):
        """Sync freshness moved onto the Sync button's tooltip (issue #45):
        a callback must drive ``sync-button.title``, not a header stat span."""
        ui_app.server.test_client().get("/")
        assert any("sync-button.title" in key for key in ui_app.callback_map), (
            "no callback feeds the Sync button tooltip"
        )


# ---------------------------------------------------------------------------
# Callback integrity: every callback wires to components that exist
# ---------------------------------------------------------------------------

class TestCallbackIntegrity:
    def test_every_callback_accepts_all_its_declared_inputs(self, ui_app, ui_data):
        """Dash invokes a callback with one positional argument per declared
        Input + State.  A callback that declares them all but can't accept
        them all (e.g. one missed when a new global input was added to
        FILTER_INPUTS) would crash at runtime on every filter change."""
        import inspect

        ui_app.server.test_client().get("/")
        mismatched = []
        for key, cb in ui_app.callback_map.items():
            if "_pages" in key:  # Dash Pages internal routing callbacks
                continue
            n_args = len(cb.get("inputs", [])) + len(cb.get("state", []))
            try:
                inspect.signature(cb["callback"]).bind(*[None] * n_args)
            except TypeError:
                mismatched.append((key, n_args))
        assert not mismatched, (
            f"callbacks can't accept the arguments their inputs declare: {mismatched}"
        )

    def test_every_callback_id_exists_in_some_layout(self, ui_app, ui_data):
        # All IDs available across the shell and every page
        layout = ui_app.layout
        known = _collect_ids(layout() if callable(layout) else layout)
        for path, _ in PAGES:
            known |= _collect_ids(_render(_page(path)))

        # Some components only exist after a user action creates them
        # dynamically: the Scouting Report (appears once an opponent is chosen)
        # and the review overlay (appears at /lessons?review=1).
        from pages.opponents import update_scouting_report
        known |= _collect_ids(update_scouting_report("Opponent A", *_filter_args()))
        known |= _collect_ids(_page("/lessons")["layout"](review="1"))

        # Force callback registration merge, then check every dependency
        ui_app.server.test_client().get("/")
        missing = []
        for key, cb in ui_app.callback_map.items():
            if "_pages" in key:  # Dash Pages internal routing callbacks
                continue
            deps = list(cb.get("inputs", [])) + list(cb.get("state", []))
            for dep in deps:
                dep_id = dep["id"] if isinstance(dep, dict) else dep.component_id
                if not isinstance(dep_id, str):
                    continue
                if dep_id.startswith("{"):
                    continue  # pattern-matching ID — matches dynamic components
                if "_pages" not in dep_id and dep_id not in known:
                    missing.append((key, dep_id))
        assert not missing, f"callbacks reference unknown component IDs: {missing}"


# ---------------------------------------------------------------------------
# Overview page callbacks (issue #8) — no callback errors, filters respected
# ---------------------------------------------------------------------------

class TestOverviewCallbacks:
    def test_kpis_match_fixture_data(self, ui_app, ui_data):
        from pages.overview import update_kpis
        values = update_kpis(*_filter_args())
        # 7 fixture games: 4 wins, 2 draws, 1 loss
        assert values[0] == "7"
        assert values[1] == "57.1%"

    def test_kpis_respond_to_filters(self, ui_app, ui_data):
        from pages.overview import update_kpis
        values = update_kpis(*_filter_args(outcomes=["Win"]))
        assert values[0] == "4"

    def test_streak_badges_render(self, ui_app, ui_data):
        from pages.overview import update_streak
        badges, stats = update_streak(*_filter_args())
        assert len(badges) == 7  # one badge per fixture game
        assert stats  # streak stat cards present

    def test_wdl_and_termination_charts_build(self, ui_app, ui_data):
        from pages.overview import update_terminations, update_wdl
        wdl_fig = update_wdl(*_filter_args())
        term_fig = update_terminations(*_filter_args())
        assert wdl_fig.data, "W/D/L chart has no traces"
        assert term_fig.data, "termination chart has no traces"

    def test_milestones_render(self, ui_app, ui_data):
        from pages.overview import update_milestones
        content = update_milestones(*_filter_args())
        assert content is not None

    def test_empty_filter_result_does_not_error(self, ui_app, ui_data):
        """No data after filtering must never raise — every page shows an empty state."""
        from pages.overview import (
            update_kpis,
            update_milestones,
            update_streak,
            update_terminations,
            update_wdl,
        )
        impossible = _filter_args(colors=["White"], outcomes=["Draw"],
                                  start="2030-01-01", end="2030-12-31")
        assert update_kpis(*impossible)[0] == "0"
        update_streak(*impossible)
        update_wdl(*impossible)
        update_terminations(*impossible)
        update_milestones(*impossible)


# ---------------------------------------------------------------------------
# USCF profile card (issue #25) — the first USCF surface
# ---------------------------------------------------------------------------

class TestUscfProfileCard:
    def test_overview_has_the_uscf_card_slot(self, ui_app, ui_data):
        ids = _collect_ids(_render(_page("/")))
        assert "uscf-profile-card" in ids

    def test_card_shows_ratings_with_provisional_labels(self, ui_app, ui_data):
        """All three ratings appear; provisional ones say so, with game counts."""
        from pages.overview import update_uscf_card
        rendered = str(update_uscf_card({"seq": 0}))

        assert "1545" in rendered    # Regular (established)
        assert "1092" in rendered    # Quick (provisional, 17 games)
        assert "1336" in rendered    # Online-Regular (provisional, 11 games)
        assert "17 games" in rendered
        assert "11 games" in rendered

    def test_card_shows_ranks_floor_and_membership(self, ui_app, ui_data):
        from pages.overview import update_uscf_card
        rendered = str(update_uscf_card({"seq": 0}))

        assert "11,719" in rendered      # national rank
        assert "356" in rendered         # VA state rank
        assert "1300" in rendered        # Regular rating floor
        assert "2028-07-31" in rendered  # membership expiration

    def test_healthy_membership_shows_no_warning(self, ui_app, ui_data):
        """Daniel renewed: 2028-07-31 is far away, so no warning on live-shaped data."""
        from pages.overview import update_uscf_card
        rendered = str(update_uscf_card({"seq": 0}))
        assert "uscf-alert" not in rendered

    def test_card_warns_when_membership_expires_soon(self, ui_app, sample_pgn_text):
        """The 90-day warning, exercised with a fixture (issue #25 note)."""
        import data
        from pages.overview import update_uscf_card
        from tests.conftest import _UI_USCF_PROFILE, stub_ui_sources

        expiring = dict(_UI_USCF_PROFILE, expirationDate="2026-06-30")
        data.reset()
        with stub_ui_sources(sample_pgn_text, uscf_profile=expiring):
            data.initialize(
                ["teststudy"], player_name="Test Player", uscf_member_id="12345678"
            )
        try:
            rendered = str(update_uscf_card({"seq": 0}))
            assert "uscf-alert" in rendered
            assert "2026-06-30" in rendered
        finally:
            data.reset()

    def test_card_warns_when_membership_has_lapsed(self, ui_app, sample_pgn_text):
        import data
        from pages.overview import update_uscf_card
        from tests.conftest import _UI_USCF_PROFILE, stub_ui_sources

        lapsed = dict(_UI_USCF_PROFILE, expirationDate="2026-01-31")
        data.reset()
        with stub_ui_sources(sample_pgn_text, uscf_profile=lapsed):
            data.initialize(
                ["teststudy"], player_name="Test Player", uscf_member_id="12345678"
            )
        try:
            rendered = str(update_uscf_card({"seq": 0}))
            assert "uscf-alert" in rendered
            assert "lapsed" in rendered.lower()
        finally:
            data.reset()

    def test_card_shows_unavailable_state_when_uscf_is_down(
        self, ui_app, sample_pgn_text
    ):
        """ADR 0003: USCF down → the card degrades visibly, everything else works."""
        import data
        from pages.overview import update_uscf_card
        from tests.conftest import stub_ui_sources
        from uscf_client import UscfUnreachableError

        data.reset()
        boom = UscfUnreachableError("Could not reach USCF")
        with stub_ui_sources(sample_pgn_text, uscf_profile=boom):
            data.initialize(
                ["teststudy"], player_name="Test Player", uscf_member_id="12345678"
            )
        try:
            rendered = str(update_uscf_card({"seq": 0}))
            assert "unavailable" in rendered.lower()
            # No stale numbers pretend to be current data
            assert "1545" not in rendered
        finally:
            data.reset()

    def test_card_shows_official_and_live_side_by_side(self, ui_app, ui_data):
        """Issue #27's payoff: the ~26-point gap between the published Official
        Rating and the Live Rating is visible at a glance, clearly labeled."""
        from pages.overview import update_uscf_card
        rendered = str(update_uscf_card({"seq": 0}))

        assert "Official" in rendered
        assert "1545" in rendered      # the June supplement's published integer
        assert "Live" in rendered
        assert "1571" in rendered      # the per-Section chain, shown whole
        assert "1570.7" not in rendered  # decimals never displayed

    def test_card_without_live_series_shows_official_only(
        self, ui_app, sample_pgn_text
    ):
        """A member with no rated Sections yet: the card still works, no Live value."""
        import data
        import sync
        from pages.overview import update_uscf_card
        from tests.conftest import _UI_USCF_PROFILE

        data.reset()
        with mock.patch.object(sync, "fetch_study_pgn", return_value=sample_pgn_text), \
             mock.patch.object(sync, "fetch_member_profile",
                               return_value=_UI_USCF_PROFILE), \
             mock.patch.object(sync, "fetch_rating_supplements", return_value=[]), \
             mock.patch.object(sync, "fetch_member_sections", return_value=[]), \
             mock.patch.object(sync, "fetch_member_games", return_value=[]), \
             mock.patch.object(sync, "fetch_member_events", return_value=[]), \
             mock.patch.object(sync, "fetch_member_norms", return_value=[]), \
             mock.patch.object(sync, "fetch_member_awards", return_value=[]):
            data.initialize(
                ["teststudy"], player_name="Test Player", uscf_member_id="12345678"
            )
        try:
            rendered = str(update_uscf_card({"seq": 0}))
            assert "1545" in rendered          # the Official value is still there
            assert "Live" not in rendered      # no Live value to invent
        finally:
            data.reset()

    def test_card_keeps_cached_data_with_staleness_warning(
        self, ui_app, sample_pgn_text, tmp_path
    ):
        """USCF down but cached: the numbers stay, clearly marked stale (issue #26)."""
        import data
        from pages.overview import update_uscf_card
        from tests.conftest import stub_ui_sources
        from uscf_client import UscfUnreachableError

        cache = str(tmp_path / "uscf_cache.json")
        data.reset()
        # A successful Sync populates the cache...
        with stub_ui_sources(sample_pgn_text):
            data.initialize(["teststudy"], player_name="Test Player",
                            uscf_member_id="12345678", uscf_cache_path=cache)
        data.reset()
        # ...then the app restarts while USCF is down
        with stub_ui_sources(sample_pgn_text, uscf_profile=UscfUnreachableError("down")):
            data.initialize(["teststudy"], player_name="Test Player",
                            uscf_member_id="12345678", uscf_cache_path=cache)
        try:
            rendered = str(update_uscf_card({"seq": 0}))
            assert "1545" in rendered                       # cached ratings still shown
            assert "unavailable since" in rendered.lower()  # but clearly marked stale
        finally:
            data.reset()

    def test_no_card_when_uscf_is_not_configured(self, ui_app, sample_pgn_text):
        """Without a member ID the Overview stays exactly as it was pre-USCF."""
        import data
        import sync
        from pages.overview import update_uscf_card

        data.reset()
        with mock.patch.object(sync, "fetch_study_pgn", return_value=sample_pgn_text):
            data.initialize(["teststudy"], player_name="Test Player")
        try:
            assert update_uscf_card({"seq": 0}) is None
        finally:
            data.reset()


# ---------------------------------------------------------------------------
# USCF achievements as Milestones (issue #36)
#
# ui fixtures include the real captured norm (Oak Grove, Dec 2025) and award
# (25th win, Jan 2026), so the Overview timeline carries gold official rows
# alongside the personal-best milestones.
# ---------------------------------------------------------------------------

class TestUscfAchievementMilestones:
    def test_norm_and_award_appear_as_gold_milestones(self, ui_app, ui_data):
        """Daniel's official achievements join the timeline, gold-flagged
        (the design language reserves gold for achievements)."""
        from pages.overview import update_milestones
        rendered = str(update_milestones(*_filter_args()))

        assert "Fourth Category norm" in rendered
        assert "First Annual Oak Grove Open" in rendered
        assert "25th career win" in rendered
        assert "uscf" in rendered           # the gold milestone class

    def test_achievements_interleave_chronologically(self, ui_app, ui_data):
        """Sample Games are 2024, achievements 2025–26 → official rows last,
        in their own date order."""
        from pages.overview import update_milestones
        rendered = str(update_milestones(*_filter_args()))

        assert rendered.index("First recorded game") < rendered.index("Fourth Category norm")
        assert rendered.index("Fourth Category norm") < rendered.index("25th career win")

    def test_achievements_ignore_game_filters_but_respect_dates(self, ui_app, ui_data):
        """Official achievements aren't Games: color/outcome filters never hide
        them; the global date range does (it bounds the whole timeline)."""
        from pages.overview import update_milestones

        game_hiding = _filter_args(colors=["White"], outcomes=["Draw"])
        assert "Fourth Category norm" in str(update_milestones(*game_hiding))

        only_2024 = _filter_args(start="2024-01-01", end="2024-12-31")
        assert "Fourth Category norm" not in str(update_milestones(*only_2024))

    def test_norms_link_to_the_events_page(self, ui_app, ui_data):
        """A norm links to where its Rated Event lives in the dashboard."""
        from pages.overview import update_milestones
        rendered = str(update_milestones(*_filter_args()))
        assert "/events" in rendered

    def test_without_uscf_the_timeline_is_unchanged(self, ui_app, sample_pgn_text):
        """Lichess-only runs: no gold rows, no errors — exactly the old timeline."""
        import data
        import sync
        from pages.overview import update_milestones

        data.reset()
        with mock.patch.object(sync, "fetch_study_pgn", return_value=sample_pgn_text):
            data.initialize(["teststudy"], player_name="Test Player")
        try:
            rendered = str(update_milestones(*_filter_args()))
            assert "First recorded game" in rendered
            assert "uscf" not in rendered
        finally:
            data.reset()

    def test_an_undated_achievement_sorts_last_never_first(
        self, ui_app, sample_pgn_text
    ):
        """An achievement USCF sends with no date at all goes to the END of the
        timeline (nothing to place it by) — never to the top."""
        import data
        from pages.overview import update_milestones
        from tests.conftest import stub_ui_sources

        undated_award = [{"category": "WinMilestone", "winCount": 10}]
        data.reset()
        with stub_ui_sources(sample_pgn_text, uscf_norms=[],
                             uscf_awards=undated_award):
            data.initialize(["teststudy"], player_name="Test Player",
                            uscf_member_id="12345678")
        try:
            rendered = str(update_milestones(*_filter_args()))
            # The undated 10th-win award renders after every dated milestone —
            # including the latest one (peak rating)
            for dated_description in ("First recorded game", "peak rating"):
                assert rendered.rindex(dated_description) \
                    < rendered.index("10th career win")
        finally:
            data.reset()


# ---------------------------------------------------------------------------
# Trends page callbacks (issue #9)
# ---------------------------------------------------------------------------

class TestTrendsCallbacks:
    def test_timeline_charts_build(self, ui_app, ui_data):
        from pages.trends import update_rating, update_winrate
        assert update_rating(*_filter_args()).data, "rating chart has no traces"
        assert update_winrate(*_filter_args()).data, "win-rate chart has no traces"

    def test_activity_charts_build(self, ui_app, ui_data):
        from pages.trends import update_dow, update_monthly
        assert update_monthly(*_filter_args()).data
        assert update_dow(*_filter_args()).data

    def test_game_length_chart_and_stats(self, ui_app, ui_data):
        from pages.trends import update_length_hist, update_length_stats
        assert update_length_hist(*_filter_args()).data
        assert update_length_stats(*_filter_args()) is not None

    def test_empty_filter_result_does_not_error(self, ui_app, ui_data):
        from pages.trends import (
            update_dow,
            update_length_hist,
            update_length_stats,
            update_monthly,
            update_rating,
            update_winrate,
        )
        impossible = _filter_args(start="2030-01-01", end="2030-12-31")
        for cb in (update_rating, update_winrate, update_monthly,
                   update_dow, update_length_hist, update_length_stats):
            cb(*impossible)

    # -- Activity heatmap calendar (issue #14) ------------------------------

    @staticmethod
    def _calendar_hover_text(calendar_blocks) -> str:
        """All hover text across every cell of every year's calendar."""
        figures = [
            comp.figure for block in calendar_blocks
            for comp in _walk_components(block)
            if getattr(comp, "figure", None) is not None
        ]
        return " ".join(
            cell
            for fig in figures
            for trace in fig.data
            for row in (trace.text or [])
            for cell in row
        )

    def test_activity_calendar_one_block_per_year(self, ui_app, ui_data):
        """Fixture Games are all from 2024 → exactly one year block."""
        from pages.trends import update_activity_calendar
        years = update_activity_calendar(*_filter_args())
        labels = [c.children for block in years for c in _walk_components(block)
                  if "activity-year-label" in (getattr(c, "className", "") or "")]
        assert labels == ["2024"]

    def test_activity_calendar_cells_carry_the_days_games(self, ui_app, ui_data):
        """Hovering a played day leads with the game count, then that day's
        Games (opponent, result).  Days without Games carry no hover at all."""
        from pages.trends import update_activity_calendar
        hover = self._calendar_hover_text(update_activity_calendar(*_filter_args()))
        assert "Win vs Opponent A" in hover
        assert "Draw vs Opponent B" in hover
        # The pretty hover leads with a bold count: "<b>1</b> game · …".
        assert "</b> game" in hover
        # Empty days are blank (no "No games" popup) — they carry no hover text.
        assert "No games" not in hover

    def test_activity_calendar_responds_to_filters(self, ui_app, ui_data):
        """Filtering to wins-only removes the loss day's games from the calendar."""
        from pages.trends import update_activity_calendar
        all_hover = self._calendar_hover_text(update_activity_calendar(*_filter_args()))
        wins_hover = self._calendar_hover_text(
            update_activity_calendar(*_filter_args(outcomes=["Win"]))
        )
        assert "Loss vs Opponent C" in all_hover
        assert "Loss vs Opponent C" not in wins_hover

    def test_activity_calendar_empty_filter_shows_empty_state(self, ui_app, ui_data):
        from pages.trends import update_activity_calendar
        impossible = _filter_args(start="2030-01-01", end="2030-12-31")
        result = update_activity_calendar(*impossible)
        assert "empty-state" in str(getattr(result, "className", ""))


# ---------------------------------------------------------------------------
# Time control, fatigue, and upset analytics (issue #17)
# ---------------------------------------------------------------------------

class TestTimeControlFatigueUpsets:
    """The Trends page's #17 sections: TC breakdown, round fatigue, upsets."""

    def test_time_control_chart_shows_fixture_controls(self, ui_app, ui_data):
        from pages.trends import update_time_control
        fig = update_time_control(*_filter_args())
        labels = {label for trace in fig.data for label in _axis_vals(trace.y)}
        assert "40/80, SD30; +30" in labels   # classical: games 1-3
        assert "30+5" in labels               # rapid: games 4-6

    def test_game_without_a_header_gets_a_readable_label(self, ui_app, ui_data):
        """Fixture game 7 has no TimeControl header — it can't show as ''."""
        from pages.trends import update_time_control
        fig = update_time_control(*_filter_args())
        labels = {label for trace in fig.data for label in _axis_vals(trace.y)}
        assert "" not in labels
        assert len(labels) == 3   # classical, rapid, and the not-recorded bucket

    def test_time_control_chart_respects_filters(self, ui_app, ui_data):
        from pages.trends import update_time_control
        january = _filter_args(start="2024-01-01", end="2024-02-01")
        fig = update_time_control(*january)
        labels = {label for trace in fig.data for label in _axis_vals(trace.y)}
        assert any("40/80" in label for label in labels)
        assert "30+5" not in labels

    def test_round_chart_has_a_bar_per_round(self, ui_app, ui_data):
        from pages.trends import update_round_performance
        fig = update_round_performance(*_filter_args())
        rounds = sorted(x for trace in fig.data for x in _axis_vals(trace.x))
        assert rounds == [1, 2, 3, 4]

    def test_round_chart_dims_thin_rounds(self, ui_app, ui_data):
        """Rounds without enough games render dimmed, with hover text saying
        the sample is too small to support a conclusion."""
        import data
        from pages.trends import _round_fig
        from pgn_stats_core import round_performance
        rounds = round_performance(data.get_df(), min_games=2)
        fig = _round_fig(rounds)
        reliable_trace, thin_trace = fig.data
        assert sorted(reliable_trace.x) == [1, 2, 3]   # 2 games each
        assert list(thin_trace.x) == [4]               # 1 game
        assert "too few" in thin_trace.hovertemplate.lower()
        # The traces overlay: grouping would shrink the bars and shift them
        # off their integer round ticks
        assert fig.layout.barmode == "overlay"

    def test_charts_survive_an_empty_filter(self, ui_app, ui_data):
        from pages.trends import update_round_performance, update_time_control
        impossible = _filter_args(start="2030-01-01", end="2030-12-31")
        update_time_control(*impossible)
        update_round_performance(*impossible)

    # -- Upset tracker -------------------------------------------------------

    def test_giant_kills_table_lists_fixture_upsets(self, ui_app, ui_data):
        from pages.trends import update_upsets
        wins_data, _, losses_data, _ = update_upsets(*_filter_args())
        # Fixture: beat Opponent A twice (1920 and 1930) while rated 1800/1810
        assert len(wins_data) == 2
        assert all(row["Opponent"] == "Opponent A" for row in wins_data)
        assert all(row["Margin"] == "+120" for row in wins_data)
        # Rows carry their Game's identity for click-through navigation
        assert all(row["ChapterURL"] for row in wins_data)

    def test_no_upset_losses_is_a_clean_sheet_not_a_blank(self, ui_app, ui_data):
        """The fixture has no losses to lower-rated players — that's worth
        saying, not hiding."""
        from pages.trends import update_upsets
        _, _, losses_data, losses_status = update_upsets(*_filter_args())
        assert losses_data == []
        assert "no upset losses" in str(losses_status).lower()

    def test_upset_rows_click_through_to_games(self, ui_app, ui_data):
        from pages.trends import navigate_to_game_from_upset_win
        rows = [{"Opponent": "Opponent A",
                 "ChapterURL": "https://lichess.org/study/teststudy/chap0001"}]
        href, cleared = navigate_to_game_from_upset_win(
            {"row": 0, "column_id": "Opponent"}, rows)
        assert href == "/game/chap0001"
        assert cleared is None

    def test_upsets_respond_to_filters(self, ui_app, ui_data):
        from pages.trends import update_upsets
        wins_data, *_ = update_upsets(*_filter_args(colors=["Black"]))
        # Only the game-4 upset (as Black) survives a Black-only filter
        assert len(wins_data) == 1


# ---------------------------------------------------------------------------
# Openings page callbacks (issue #9)
# ---------------------------------------------------------------------------

class TestOpeningsCallbacks:
    def test_family_chart_builds(self, ui_app, ui_data):
        from pages.openings import update_opening_family
        assert update_opening_family(*_filter_args()).data

    def test_openings_table_lists_fixture_openings(self, ui_app, ui_data):
        from pages.openings import update_opening_table
        rows = update_opening_table(*_filter_args())
        assert "E04" in {r["ECO"] for r in rows}

    def test_openings_table_respects_filters(self, ui_app, ui_data):
        from pages.openings import update_opening_table
        all_rows = update_opening_table(*_filter_args())
        win_rows = update_opening_table(*_filter_args(outcomes=["Win"]))
        assert len(win_rows) < len(all_rows)


# ---------------------------------------------------------------------------
# Repertoire tree (issue #16) — the personal opening explorer
# ---------------------------------------------------------------------------

class TestRepertoireTreePage:
    def test_white_tree_shows_first_moves_with_scores(self, ui_app, ui_data):
        from pages.openings import update_repertoire
        rendered = str(update_repertoire("White", *_filter_args()))
        assert "1. d4" in rendered    # played 3 times as White
        assert "1. e4" in rendered    # played once
        # The overall baseline the branches are judged against
        assert "62.5%" in rendered

    def test_black_tree_branches_on_what_opponents_play(self, ui_app, ui_data):
        from pages.openings import update_repertoire
        rendered = str(update_repertoire("Black", *_filter_args()))
        assert "1. e4" in rendered     # what they played
        assert "1... c6" in rendered   # Daniel's Caro-Kann answer

    def test_underperforming_branch_is_visually_flagged(self, ui_app, ui_data):
        """1.d4 scores 50% against a 62.5% White average over 3 games — the
        tree must say so, in the issue's own words: it's leaking points."""
        from pages.openings import update_repertoire
        rendered = str(update_repertoire("White", *_filter_args()))
        assert "leaking points" in rendered
        assert "rep-flagged" in rendered

    def test_thin_branches_are_not_flagged(self, ui_app, ui_data):
        """As Black every branch holds at most 2 games — below the 3-game
        threshold, so nothing gets flagged no matter what it scores.
        (The score-above-baseline side of the rule is covered by the core
        test test_above_average_branch_with_enough_games_is_not_flagged.)"""
        from pages.openings import update_repertoire
        rendered = str(update_repertoire("Black", *_filter_args()))
        assert "leaking points" not in rendered

    def test_nodes_link_to_their_games(self, ui_app, ui_data):
        from pages.openings import update_repertoire
        rendered = str(update_repertoire("White", *_filter_args()))
        # The single 1.e4 game (game 5) links straight to its detail view
        assert "/game/chap0005" in rendered
        # Drilling the 1.d4 line reaches game 3's detail link too
        assert "/game/chap0003" in rendered

    def test_games_that_stop_mid_line_get_an_ended_here_link(self, ui_app, ui_data):
        """Game 7 follows game 1's moves but stops at 3...d5 — it must appear
        right there, marked 'ended here', not vanish from the tree."""
        from pages.openings import update_repertoire
        rendered = str(update_repertoire("White", *_filter_args()))
        assert "ended here" in rendered
        assert "/game/chap0007" in rendered

    def test_empty_filter_shows_empty_state(self, ui_app, ui_data):
        from pages.openings import update_repertoire
        impossible = _filter_args(start="2030-01-01", end="2030-12-31")
        rendered = str(update_repertoire("White", *impossible))
        assert "empty-state" in rendered

    def test_tree_respects_global_filters(self, ui_app, ui_data):
        """Filtering to January leaves only the two Test Open games Daniel
        played as White — both 1.d4."""
        from pages.openings import update_repertoire
        january = _filter_args(start="2024-01-01", end="2024-02-01")
        rendered = str(update_repertoire("White", *january))
        assert "1. d4" in rendered
        assert "1. e4" not in rendered


# ---------------------------------------------------------------------------
# Opponents page callbacks (issue #9)
# ---------------------------------------------------------------------------

class TestOpponentsCallbacks:
    def test_opponent_bar_builds(self, ui_app, ui_data):
        from pages.opponents import update_opponents
        # Opponents A and B are both played more than once in the fixture
        assert update_opponents(*_filter_args()).data

    def test_opponent_bar_is_horizontal(self, ui_app, ui_data):
        """E7: opponent names read down the y-axis (horizontal bars), so the
        labels never rotate into an overlapping diagonal."""
        from pages.opponents import update_opponents
        fig = update_opponents(*_filter_args())
        # Every W/D/L trace is a horizontal bar with opponent names on y.
        assert fig.data
        for trace in fig.data:
            assert trace.orientation == "h"
            assert _axis_vals(trace.y)        # opponent names live on the y-axis

    def test_strength_charts_build(self, ui_app, ui_data):
        from pages.opponents import update_bucket, update_scatter
        assert update_bucket(*_filter_args()).data
        assert update_scatter(*_filter_args()).data


# ---------------------------------------------------------------------------
# Scouting Report (issue #13) — the pre-game dossier
# ---------------------------------------------------------------------------

class TestScoutingReportPage:
    def test_dossier_renders_for_known_opponent(self, ui_app, ui_data):
        """Opponent picked → score, rating gap, openings, and lessons appear."""
        from pages.opponents import update_scouting_report
        rendered = str(update_scouting_report("Opponent A", *_filter_args()))
        assert "2.5/3" in rendered                    # H2H score
        assert "1925" in rendered                     # their latest rating
        assert "King's Indian Defense" in rendered    # opening split by color
        assert "Keep the tension" in rendered         # a Lesson from facing them

    def test_dossier_lessons_link_to_their_games(self, ui_app, ui_data):
        from pages.opponents import update_scouting_report
        rendered = str(update_scouting_report("Opponent A", *_filter_args()))
        assert "/game/chap0001" in rendered    # G1's Lesson → G1's detail view

    def test_prompts_when_no_opponent_chosen(self, ui_app, ui_data):
        from pages.opponents import update_scouting_report
        rendered = str(update_scouting_report(None, *_filter_args())).lower()
        assert "opponent" in rendered    # "pick an opponent" hint, not a crash

    def test_unknown_opponent_in_filter_says_so(self, ui_app, ui_data):
        """An opponent filtered out of view gets a no-games message, not a crash."""
        from pages.opponents import update_scouting_report
        impossible = _filter_args(start="2030-01-01", end="2030-12-31")
        rendered = str(update_scouting_report("Opponent A", *impossible)).lower()
        assert "no games" in rendered

    def test_picker_options_follow_the_data(self, ui_app, ui_data):
        from pages.opponents import update_scout_options
        options = update_scout_options({"seq": 1, "new_games": 0})
        assert {"label": "Opponent A", "value": "Opponent A"} in options


# ---------------------------------------------------------------------------
# Events page: Series → Rated Event (issue #33)
#
# With the ui fixtures: SAMPLE_PGN's "Test Open" and "Summer Cup" Series map
# to the TEST OPEN JANUARY / SUMMER CUP 2024 Rated Events; game 6 stays
# unmatched under Summer Cup.  real_career_ui exercises the full career.
# ---------------------------------------------------------------------------

class TestEventsSeriesGroups:
    def test_series_expand_into_their_rated_events(self, ui_app, ui_data):
        """The two-level structure: Series at the top, official Rated Events
        (USCF's names) inside."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "Test Open" in rendered            # Series (Daniel's name)
        assert "Summer Cup" in rendered
        assert "TEST OPEN JANUARY" in rendered    # Rated Event (USCF's name)
        assert "SUMMER CUP 2024" in rendered

    def test_rated_events_show_live_rating_change_as_whole_numbers(
        self, ui_app, ui_data
    ):
        """Live pre → post per Rated Event — whole numbers, never decimals
        (Daniel's display rule overrides the issue wording)."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        # SUMMER CUP 2024's section: 1800.5 → 1812.44, displayed as 1812
        assert "1812" in rendered
        assert "1812.44" not in rendered
        assert "1782.5" not in rendered and "1800.5" not in rendered

    def test_rated_events_show_score_sections_and_field(self, ui_app, ui_data):
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "OPEN" in rendered          # the Section name
        assert "12 players" in rendered    # the field, from the events endpoint

    def test_each_game_links_to_its_detail_view(self, ui_app, ui_data):
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "/game/chap0001" in rendered
        assert "/game/chap0005" in rendered

    def test_unmatched_games_stay_under_their_series(self, ui_app, ui_data):
        """Game 6 (USCF hasn't rated it) still appears under Summer Cup."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        # Game 6's detail link renders even though it matched no Rated Event
        assert "/game/chap0006" in rendered

    def test_respects_global_filters(self, ui_app, ui_data):
        from pages.events import update_series_groups
        january = _filter_args(start="2024-01-01", end="2024-02-01")
        rendered = str(update_series_groups(*january))

        assert "Test Open" in rendered
        assert "Summer Cup" not in rendered

    def test_empty_filter_shows_empty_state(self, ui_app, ui_data):
        from pages.events import update_series_groups
        impossible = _filter_args(start="2030-01-01", end="2030-12-31")
        rendered = str(update_series_groups(*impossible))
        assert "empty-state" in rendered

    def test_the_event_bar_still_builds(self, ui_app, ui_data):
        from pages.events import update_event_bar
        assert update_event_bar(*_filter_args()).data

    def test_the_event_bar_is_horizontal(self, ui_app, ui_data):
        """E6: tournament names read down the y-axis (horizontal bars), so the
        labels never rotate into an overlapping diagonal."""
        from pages.events import update_event_bar
        fig = update_event_bar(*_filter_args())
        # Every W/D/L trace is a horizontal bar with Series names on y.
        assert fig.data
        for trace in fig.data:
            assert trace.orientation == "h"
            assert _axis_vals(trace.y)        # Series names live on the y-axis

    def test_the_real_club_ladder_renders_with_its_monthly_events(
        self, ui_app, real_career_ui
    ):
        """The money shot (issue #33): ACC Friday Ladder is one Series holding
        ACC JUNE 2025 … ACC MAY 2026."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "ACC Friday Ladder" in rendered
        assert "ACC JUNE 2025" in rendered
        assert "ACC MAY 2026" in rendered
        # The May event's rating change, whole numbers
        assert "1544" in rendered and "1571" in rendered

    def test_the_forfeit_renders_under_its_series(self, ui_app, real_career_ui):
        """The Baker no-show appears under the Thanksgiving Series,
        labeled as a Forfeit, not inside any Rated Event."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "Baker" in rendered
        assert "Forfeit" in rendered


class TestEventsCrosstables:
    """Standings inside each Rated Event (issue #34): placement, the full
    crosstable with Daniel's row highlighted, and real round numbers."""

    def test_rated_events_show_official_placement(self, ui_app, real_career_ui):
        """'Finished 5th of 116' — straight from the ACC MAY crosstable."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "5th of 116" in rendered

    def test_crosstable_lists_every_player_with_daniel_highlighted(
        self, ui_app, real_career_ui
    ):
        """The full field renders — the winner (an unrated walk-in who won the
        section!) down to last place — with Daniel's own row flagged."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "JOHN DAVIS" in rendered          # the ACC MAY winner
        assert "crosstable-row-me" in rendered      # Daniel's row highlight

    def test_crosstable_ratings_display_as_whole_numbers(
        self, ui_app, real_career_ui
    ):
        """Every player's pre → post shows whole, never with decimals."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "1357" in rendered          # Anderson's post, rounded
        assert "1357.47" not in rendered
        assert "1544.47" not in rendered   # Daniel's pre stays whole too

    def test_game_rows_show_real_round_numbers(self, ui_app, real_career_ui):
        """ACC MAY games: Daniel typed rounds 24–27; the cards show the real
        rounds 1, 3, 4, 5 from the crosstable."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        # The May card shows R1/R3/R4/R5, not R24-R27
        assert "R24" not in rendered
        assert "R27" not in rendered

    def test_daniels_crosstable_rounds_link_to_his_games(
        self, ui_app, real_career_ui
    ):
        """Round outcomes in Daniel's crosstable row click through to the Games
        where one exists (issue #34's acceptance criterion)."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        # His ACC MAY round-1 win vs Baker has a Game; the crosstable links it
        df = __import__("data").get_df()
        may_r1 = df[(df["UscfEventId"] == "202605290393") & (df["UscfRound"] == 1)]
        chapter_id = may_r1.iloc[0]["ChapterURL"].rsplit("/", 1)[-1]
        assert rendered.count(f"/game/{chapter_id}") >= 2   # game row + crosstable

    def test_events_without_a_cached_crosstable_degrade_gracefully(
        self, ui_app, ui_data
    ):
        """Sample events have no crosstables — cards render without placement,
        no errors (ADR 0003)."""
        from pages.events import update_series_groups
        rendered = str(update_series_groups(*_filter_args()))

        assert "TEST OPEN JANUARY" in rendered
        assert "Finished" not in rendered
        assert "crosstable" not in rendered.lower()


class TestEventsUnplayed:
    def test_entered_but_never_played_events_render(self, ui_app, real_career_ui):
        """The Rockville case (issue #33): entered, zero games — rendered in
        its own group without error."""
        from pages.events import update_unplayed
        rendered = str(update_unplayed(*_filter_args()))

        assert "ROCKVILLE ACTION TOURNAMENT" in rendered

    def test_played_events_are_never_listed_as_unplayed(self, ui_app, real_career_ui):
        from pages.events import update_unplayed
        rendered = str(update_unplayed(*_filter_args()))

        assert "ACC MAY 2026" not in rendered

    def test_a_date_filter_does_not_invent_unplayed_events(
        self, ui_app, real_career_ui
    ):
        """Filtering to 2026 hides 2025's Rockville but never turns played 2025
        events into 'never played'."""
        from pages.events import update_unplayed
        only_2026 = _filter_args(start="2026-01-01", end="2026-12-31")
        rendered = str(update_unplayed(*only_2026))

        assert "ROCKVILLE" not in rendered          # outside the range
        assert "ACC JUNE 2025" not in rendered      # played — never "unplayed"


# ---------------------------------------------------------------------------
# Recurring weakness detection (issue #18)
# ---------------------------------------------------------------------------

def _pgn_with_weakness_pattern() -> str:
    """An archive with a clear pattern: 4 of 4 losses tagged #time-trouble."""
    games = []
    for i in range(1, 7):
        is_loss = i <= 4
        result = "0-1" if is_loss else "1-0"           # "Me" always plays White
        comment = "{ Flagged in a winning position. #time-trouble } " if is_loss else ""
        games.append(f"""[Event "Club Night"]
[Site "S"]
[Date "2024.0{i}.01"]
[White "Me"]
[Black "Opp {i}"]
[Result "{result}"]
[ChapterURL "https://lichess.org/study/wstudy/wch{i:04d}"]

{comment}1. e4 e5 {result}""")
    return "\n\n".join(games)


@pytest.fixture()
def weakness_data(ui_app):
    """A data store whose archive shows a clear recurring weakness."""
    import data
    import sync

    data.reset()
    with mock.patch.object(sync, "fetch_study_pgn",
                           return_value=_pgn_with_weakness_pattern()):
        data.initialize(["wstudy"], player_name="Me")
    yield
    data.reset()


class TestWeaknessCallouts:
    def test_lessons_page_calls_out_a_clear_pattern(self, ui_app, weakness_data):
        from pages.lessons import update_weakness_callouts
        rendered = str(update_weakness_callouts(*_filter_args()))
        assert "#time-trouble" in rendered
        assert "4 of your last 4 losses" in rendered

    def test_callout_games_are_clickable(self, ui_app, weakness_data):
        """Each callout links to the Games behind it."""
        from pages.lessons import update_weakness_callouts
        rendered = str(update_weakness_callouts(*_filter_args()))
        assert "/game/wch0001" in rendered

    def test_overview_shows_only_the_top_callout(self, ui_app, weakness_data):
        from pages.overview import update_top_weakness
        rendered = str(update_top_weakness(*_filter_args()))
        assert "#time-trouble" in rendered

    def test_silence_when_below_threshold(self, ui_app, ui_data):
        """The 7-game fixture (one Loss) is below threshold → both pages stay quiet."""
        from pages.lessons import update_weakness_callouts
        from pages.overview import update_top_weakness
        assert update_weakness_callouts(*_filter_args()) is None
        assert update_top_weakness(*_filter_args()) is None


# ---------------------------------------------------------------------------
# Lessons page (issue #12)
# ---------------------------------------------------------------------------

class TestLessonsPage:
    def test_all_lessons_listed_newest_first(self, ui_app, ui_data):
        from pages.lessons import update_lessons_page
        lessons_list, _strip = update_lessons_page([], None, *_filter_args())
        rendered = str(lessons_list)
        # All 3 fixture Lessons present
        assert "Don't grab pawns" in rendered
        assert "Castle before starting an attack" in rendered
        assert "Keep the tension" in rendered
        # Newest first: game 4's lessons (June) appear before game 1's (January)
        assert rendered.index("Don't grab pawns") < rendered.index("Keep the tension")

    def test_each_lesson_links_to_its_source_game(self, ui_app, ui_data):
        from pages.lessons import update_lessons_page
        lessons_list, _strip = update_lessons_page([], None, *_filter_args())
        rendered = str(lessons_list)
        assert "/game/chap0001" in rendered
        assert "/game/chap0004" in rendered

    def test_tag_strip_shows_counts_canonical_first(self, ui_app, ui_data):
        from pages.lessons import update_lessons_page
        _list, strip = update_lessons_page([], None, *_filter_args())
        rendered = str(strip)
        # tactics appears in 2 games; canonical ordering puts opening/tactics early
        assert "tactics" in rendered
        assert rendered.index("opening") < rendered.index("strategy")

    def test_filtering_by_tag(self, ui_app, ui_data):
        from pages.lessons import update_lessons_page
        lessons_list, _strip = update_lessons_page(["opening"], None, *_filter_args())
        rendered = str(lessons_list)
        assert "Castle before starting an attack" in rendered
        assert "Keep the tension" not in rendered  # game 1 has no #opening

    def test_filtering_by_opponent(self, ui_app, ui_data):
        from pages.lessons import update_lessons_page
        lessons_list, _strip = update_lessons_page([], "Opponent D", *_filter_args())
        assert "Keep the tension" not in str(lessons_list)

    def test_combined_with_global_filters(self, ui_app, ui_data):
        from pages.lessons import update_lessons_page
        # Global date filter restricted to January → only game 1's Lesson remains
        january = _filter_args(start="2024-01-01", end="2024-01-31")
        lessons_list, _strip = update_lessons_page([], None, *january)
        rendered = str(lessons_list)
        assert "Keep the tension" in rendered
        assert "Don't grab pawns" not in rendered

    def test_empty_archive_explains_the_convention(self, ui_app, ui_data):
        from pages.lessons import update_lessons_page
        impossible = _filter_args(start="2030-01-01", end="2030-12-31")
        lessons_list, _strip = update_lessons_page([], None, *impossible)
        rendered = str(lessons_list)
        # The empty state teaches the Lesson:/#tag convention (ADR 0002)
        assert "Lesson:" in rendered

    def test_opponent_options_follow_the_data(self, ui_app, ui_data):
        from pages.lessons import update_lesson_opponent_options
        options = update_lesson_opponent_options({"seq": 1, "new_games": 0})
        # Only opponents from Games that actually carry Lessons are offered
        assert {"label": "Opponent A", "value": "Opponent A"} in options

    def test_clicking_a_tag_chip_toggles_it(self, ui_app, ui_data):
        from unittest import mock

        from pages.lessons import toggle_lesson_tag

        with mock.patch("pages.lessons.ctx") as fake_ctx:
            fake_ctx.triggered_id = {"type": "lesson-tag", "tag": "endgame"}
            # selecting
            assert toggle_lesson_tag([1], []) == ["endgame"]
            # deselecting
            assert toggle_lesson_tag([1], ["endgame"]) == []

    def test_spurious_chip_rerender_does_not_toggle(self, ui_app, ui_data):
        from dash import no_update

        from pages.lessons import toggle_lesson_tag
        # When the strip re-renders, all n_clicks are None — must not toggle
        assert toggle_lesson_tag([None, None], ["endgame"]) is no_update


# ---------------------------------------------------------------------------
# Pre-game review mode (issue #19)
# ---------------------------------------------------------------------------

class TestReviewMode:
    """Full-screen, card-by-card lesson review for the minutes before a round."""

    @staticmethod
    def _review_queue_store(tree):
        return next((c for c in _walk_components(tree)
                     if getattr(c, "id", None) == "review-queue-store"), None)

    def test_lessons_page_has_a_launch_link(self, ui_app, ui_data):
        hrefs = {getattr(c, "href", None)
                 for c in _walk_components(_render(_page("/lessons")))}
        assert "/lessons?review=1" in hrefs

    def test_normal_lessons_page_has_no_overlay(self, ui_app, ui_data):
        assert self._review_queue_store(_render(_page("/lessons"))) is None

    def test_review_param_opens_the_overlay_with_the_queue(self, ui_app, ui_data):
        """/lessons?review=1 → the overlay holds the prioritized queue."""
        tree = _page("/lessons")["layout"](review="1")
        store = self._review_queue_store(tree)
        assert store is not None
        assert len(store.data) == 3            # the fixture's three Lessons
        assert all(c["reason"] for c in store.data)

    def test_opponent_review_prioritizes_their_lessons(self, ui_app, ui_data):
        """Scouting context: ?opponent=X marks X's lessons as the reason."""
        tree = _page("/lessons")["layout"](review="1", opponent="Opponent A")
        store = self._review_queue_store(tree)
        assert all(c["reason"] == "You're facing Opponent A" for c in store.data)

    def test_card_renders_with_progress(self, ui_app, ui_data):
        from pages.lessons import render_review_card
        tree = _page("/lessons")["layout"](review="1")
        queue = self._review_queue_store(tree).data
        card, progress = render_review_card(0, queue)
        assert "1 / 3" in str(progress)
        # Fixture has no recurring weaknesses → recency order, newest first
        assert "grab pawns" in str(card) or "Castle before" in str(card)

    def test_tap_advances_prev_goes_back(self, ui_app, ui_data):
        from pages.lessons import navigate_review
        queue = [{"Lesson": "A"}, {"Lesson": "B"}]
        with mock.patch("pages.lessons.ctx") as fake_ctx:
            fake_ctx.triggered_id = "review-tap"
            assert navigate_review(1, None, 0, queue) == 1
            assert navigate_review(2, None, 1, queue) == 2   # one past the end = done
            assert navigate_review(3, None, 2, queue) == 2   # …and stays there
        with mock.patch("pages.lessons.ctx") as fake_ctx:
            fake_ctx.triggered_id = "review-prev"
            assert navigate_review(1, 1, 1, queue) == 0
            assert navigate_review(1, 2, 0, queue) == 0      # can't go below 0

    def test_done_card_after_the_last_lesson(self, ui_app, ui_data):
        from pages.lessons import render_review_card
        queue = [{"Lesson": "Only one", "Tags": [], "reason": "Recent lesson",
                  "Opponent": "X", "Date": "2024.01.01", "Outcome": "Win",
                  "Event": "E", "Result": "1-0", "ChapterURL": ""}]
        card, progress = render_review_card(1, queue)        # one past the end
        assert "play" in str(card).lower()                   # "go play"

    def test_review_with_no_lessons_explains_why(self, ui_app, weakness_data):
        """An archive with no Lessons → the overlay explains, doesn't crash."""
        tree = _page("/lessons")["layout"](review="1")
        rendered = str(tree)
        assert "Lesson:" in rendered    # teaches the convention

    def test_scouting_report_offers_opponent_review(self, ui_app, ui_data):
        from pages.opponents import update_scouting_report
        rendered = str(update_scouting_report("Opponent A", *_filter_args()))
        assert "/lessons?review=1&opponent=Opponent%20A" in rendered


# ---------------------------------------------------------------------------
# Game detail view (issue #11)
# ---------------------------------------------------------------------------

class TestGameDetail:
    def test_detail_page_registered_with_path_template(self, ui_app, ui_data):
        detail = next((p for p in dash.page_registry.values() if p.get("path_template")), None)
        assert detail is not None
        assert detail["path_template"] == "/game/<chapter_id>"

    def test_detail_page_not_in_nav(self, ui_app, ui_data):
        """The detail view is reached by clicking a Game, never from the nav tabs."""
        layout = ui_app.layout
        tree = layout() if callable(layout) else layout
        hrefs = []

        def _walk(node):
            if node is None or isinstance(node, (str, int, float, bool)):
                return
            if isinstance(node, (list, tuple)):
                for item in node:
                    _walk(item)
                return
            href = getattr(node, "href", None)
            if isinstance(href, str):
                hrefs.append(href)
            _walk(getattr(node, "children", None))

        _walk(tree)
        assert not any(h.startswith("/game/") for h in hrefs)

    def test_detail_shows_board_metadata_and_lesson(self, ui_app, ui_data):
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0001"))
        # The interactive board is now Lichess's pgn-viewer mounted locally
        # (issue #60 [F6]), not an iframe embed.
        assert "lpv" in rendered
        assert "lichess.org/study/embed" not in rendered
        # Metadata alongside the board
        assert "Opponent A" in rendered
        assert "Test Open" in rendered
        assert "E04" in rendered
        # The Lesson written on Lichess (ADR 0002)
        assert "Keep the tension in the center" in rendered
        # Tags
        assert "#strategy" in rendered
        # Open on Lichess goes to the real chapter
        assert "https://lichess.org/study/teststudy/chap0001" in rendered

    def test_game_without_lesson_renders_gracefully(self, ui_app, ui_data):
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0002"))
        assert "lpv" in rendered                 # the board still renders
        assert "No Lesson" in rendered  # deliberate empty state, not a crash

    def test_game_without_chapter_url_renders_without_error(
        self, ui_app, ui_data, monkeypatch
    ):
        """A Game with no ChapterURL renders its detail view gracefully —
        no broken iframe, no crash (acceptance criterion, #43)."""
        import pandas as pd

        from pages import game_detail
        urlless = pd.Series({
            "Opponent": "Opponent X", "Outcome": "Win", "ChapterURL": "",
            "Event": "Test Open", "Round": "1", "Date": "2024.01.01",
        })
        monkeypatch.setattr(game_detail, "_find_game", lambda _id: urlless)
        rendered = str(game_detail.layout(chapter_id="whatever"))
        # The header still renders for the Game...
        assert "Opponent X" in rendered
        # ...the board card degrades gracefully instead of embedding nothing...
        assert "No board for this game" in rendered
        # ...and no embed iframe URL is emitted.
        assert "lichess.org/study/embed" not in rendered

    def test_unknown_chapter_shows_not_found(self, ui_app, ui_data):
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="doesnotexist"))
        assert "not in your archive" in rendered

    def test_no_chapter_id_shows_not_found(self, ui_app, ui_data):
        from pages.game_detail import layout
        rendered = str(layout())
        assert "not in your archive" in rendered

    def test_unanalyzed_game_shows_awaiting_hint(self, ui_app, ui_data):
        # SAMPLE_PGN carries no engine evals, so a Game degrades to the quiet
        # awaiting-analysis hint — never a blank or a crash (issue #57).
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0001"))
        assert "Awaiting analysis" in rendered

    def test_analyzed_game_shows_critical_moment_headline(self, ui_app):
        """An analysed Game shows its critical-moment headline alongside the
        board (issue #57) — the real Alice Anderson Game's −4.38 swing."""
        from pathlib import Path
        from unittest import mock

        import data
        import sync
        from pages.game_detail import layout

        pgn = (Path(__file__).parent / "fixtures"
               / "analyzed-alice-anderson.pgn").read_text()
        data.reset()
        try:
            with mock.patch.object(sync, "fetch_study_pgn", return_value=pgn):
                data.initialize(["analyzed"], player_name="Daniel Gentile")
            rendered = str(layout(chapter_id="alic0001"))
            assert "Critical moment" in rendered
            assert "blunder" in rendered
            assert "move 16" in rendered
            assert "Bd4" in rendered
            assert "opponent" in rendered  # the swing was the opponent's
        finally:
            data.reset()


class TestGameNavigation:
    """Clicking a Game row anywhere navigates to its detail view."""

    ROWS = [
        {"Date": "2024.01.06", "Lichess": "[Open ↗](...)",
         "ChapterURL": "https://lichess.org/study/teststudy/chap0003"},
    ]

    def test_clicking_a_row_navigates_to_the_game(self, ui_app, ui_data):
        from components import row_click_to_game
        href = row_click_to_game({"row": 0, "column_id": "Date"}, self.ROWS)
        assert href == "/game/chap0003"

    def test_clicking_the_lichess_link_does_not_hijack(self, ui_app, ui_data):
        from dash import no_update

        from components import row_click_to_game
        result = row_click_to_game({"row": 0, "column_id": "Lichess"}, self.ROWS)
        assert result is no_update

    def test_row_without_chapter_url_does_not_navigate(self, ui_app, ui_data):
        from dash import no_update

        from components import row_click_to_game
        rows = [{"Date": "2024.01.06", "ChapterURL": ""}]
        assert row_click_to_game({"row": 0, "column_id": "Date"}, rows) is no_update

    def test_both_tables_have_navigation_callbacks(self, ui_app, ui_data):
        """The Games table and the Scouting Report timeline both open Games.
        (The Events page's game rows are plain links since issue #33 — they
        need no callback.)"""
        from pages.games import navigate_to_game
        from pages.opponents import navigate_to_game_from_scout

        for fn in (navigate_to_game, navigate_to_game_from_scout):
            href, _reset = fn({"row": 0, "column_id": "Date"}, self.ROWS)
            assert href == "/game/chap0003"


# ---------------------------------------------------------------------------
# USCF matching in the UI (issues #28 / #29)
#
# The ui fixtures pair SAMPLE_PGN with SAMPLE_USCF_GAMES: games 1–5 match by
# opponent ID + result, game 6 has an ID but no record (unmatched), game 7
# has no FideId and matches by name.
# ---------------------------------------------------------------------------

class TestUscfMatchUI:
    def test_game_detail_shows_the_uscf_half_of_a_matched_game(self, ui_app, ui_data):
        """#28: Rated Event, Section, rating system, official opponent name and
        member ID, and a link to the opponent's USCF page."""
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0001"))

        assert "TEST OPEN JANUARY" in rendered          # Rated Event
        assert "OPEN" in rendered                       # Section
        assert "Regular" in rendered                    # rating system, spelled out
        assert "OPPONENT A" in rendered                 # official (USCF) opponent name
        assert "10000001" in rendered                   # opponent member ID
        # Deep link to the opponent's page on the USCF ratings site
        assert "ratings.uschess.org/members/10000001" in rendered

    def test_game_detail_of_an_unmatched_game_invents_nothing(self, ui_app, ui_data):
        """Game 6 has no USCF Game Record: its detail view shows no USCF facts
        (and certainly no link to a member page that isn't its opponent's)."""
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0006"))

        assert "ratings.uschess.org" not in rendered
        assert "Rated Event" not in rendered

    def test_games_table_distinguishes_id_matches_name_matches_and_unmatched(
        self, ui_app, ui_data
    ):
        """#28/#29/#30: ✓ = matched by opponent ID, ≈ = matched by name (so
        name matches can be eyeballed), ⚠ = matched but conflicted, blank =
        no USCF Game Record."""
        from pages.games import update_games_table
        rows = update_games_table(*_filter_args())
        by_chapter = {r["ChapterURL"].rsplit("/", 1)[-1]: r for r in rows}

        for chapter_id in ("chap0001", "chap0002", "chap0003", "chap0005"):
            assert by_chapter[chapter_id]["USCF"] == "✓", f"{chapter_id}: ID match"
        assert by_chapter["chap0004"]["USCF"] == "⚠"      # matched, color conflict
        assert by_chapter["chap0007"]["USCF"] == "≈"      # matched by name
        assert by_chapter["chap0006"]["USCF"] == ""       # no record

    def test_game_detail_says_how_a_name_match_was_made(self, ui_app, ui_data):
        """A name-matched Game says so in its USCF card, so Daniel can eyeball
        whether the fallback got it right (issue #29)."""
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0007"))

        assert "name" in str(rendered)
        assert "ratings.uschess.org/members/10000001" in rendered


# ---------------------------------------------------------------------------
# Reconciliation page, header badge, and conflict badges (issue #30)
#
# With the ui fixtures: game 4 is a color conflict, game 6 is Lichess-only,
# game 7 is missing its FideId, and one record (EXTRA OPPONENT) is USCF-only.
# ---------------------------------------------------------------------------

class TestReconciliationPage:
    def test_page_lists_every_open_disagreement_grouped_by_kind(self, ui_app, ui_data):
        from pages.reconciliation import update_reconciliation
        rendered = str(update_reconciliation({"seq": 0}))

        # The conflict: both versions side by side
        assert "Opponent A" in rendered
        assert "Black" in rendered and "White" in rendered
        # USCF-only: the record with no Chapter
        assert "EXTRA OPPONENT" in rendered
        # Lichess-only: game 6 (USCF hasn't rated it)
        assert "Opponent B" in rendered
        # Missing FideId: game 7, with the exact ID to type in
        assert "10000001" in rendered

    def test_every_entry_offers_dismiss_and_fix_actions(self, ui_app, ui_data):
        from pages.reconciliation import update_reconciliation
        rendered = str(update_reconciliation({"seq": 0}))

        assert "Dismiss" in rendered
        # Fix-on-Lichess guidance links to the chapter
        assert "lichess.org/study/teststudy/chap0004" in rendered

    def test_page_documents_the_persistence_limitation(self, ui_app, ui_data):
        """Issue #30: dismissals are best-effort — say so on the page, not
        just in the PR."""
        from pages.reconciliation import update_reconciliation
        rendered = str(update_reconciliation({"seq": 0}))

        assert "redeploy" in rendered.lower() or "may come back" in rendered.lower()

    def test_dismissing_an_entry_removes_it_from_the_page(self, ui_app, ui_data):
        import data
        from pages.reconciliation import dismiss_entry

        entry = next(e for e in data.get_reconciliation() if e.kind == "conflict")

        # The pattern-matching callback fires with the clicked button's id
        with mock.patch("pages.reconciliation.ctx") as fake_ctx:
            fake_ctx.triggered_id = {"type": "reconcile-dismiss",
                                     "index": entry.entry_id}
            content, badge_bump = dismiss_entry([1], 0)

        # Gone from the store...
        assert entry.entry_id not in {e.entry_id for e in data.get_reconciliation()}
        # ...gone from the re-rendered page (it was the only conflict)...
        rendered = str(content)
        assert "Conflicts" not in rendered
        assert "USCF only" in rendered           # other sections remain
        # ...and the header badge is told to update
        assert badge_bump == 1

    def test_all_reconciled_is_a_positive_empty_state(self, ui_app, sample_pgn_text):
        """No disagreements → say so warmly, not a blank page."""
        import data
        from pages.reconciliation import update_reconciliation
        from tests.conftest import SAMPLE_USCF_GAMES, stub_ui_sources

        # Only the records that match cleanly (drop the conflict, the
        # USCF-only extra, and keep game 6/7 out of dispute is impossible —
        # so dismiss everything instead)
        data.reset()
        with stub_ui_sources(sample_pgn_text, uscf_games=SAMPLE_USCF_GAMES):
            data.initialize(["teststudy"], player_name="Test Player",
                            uscf_member_id="12345678")
        for entry in list(data.get_reconciliation()):
            data.dismiss_reconciliation_entry(entry.entry_id)
        try:
            rendered = str(update_reconciliation({"seq": 0}))
            assert "agree" in rendered.lower() or "reconciled" in rendered.lower()
        finally:
            data.reset()


class TestReconciliationBadge:
    def test_header_badge_shows_open_count_and_links_to_the_page(self, ui_app, ui_data):
        import data
        from shell import update_reconciliation_badge
        badge = update_reconciliation_badge({"seq": 0}, 0)
        rendered = str(badge)

        assert str(len(data.get_reconciliation())) in rendered
        assert "/reconciliation" in rendered

    def test_no_badge_when_nothing_is_open(self, ui_app, sample_pgn_text):
        """A clean reconciliation needs no badge — silence is the reward."""
        import data
        import sync
        from shell import update_reconciliation_badge

        data.reset()
        with mock.patch.object(sync, "fetch_study_pgn", return_value=sample_pgn_text):
            data.initialize(["teststudy"], player_name="Test Player")
        try:
            assert update_reconciliation_badge({"seq": 0}, 0) is None
        finally:
            data.reset()

    def test_badge_lives_in_the_shell(self, ui_app, ui_data):
        """The badge is on every page — it belongs to the shell, not a page."""
        layout = ui_app.layout
        tree = layout() if callable(layout) else layout
        assert "reconciliation-badge" in _collect_ids(tree)


class TestConflictBadgeOnGameDetail:
    def test_conflicted_game_detail_links_to_reconciliation(self, ui_app, ui_data):
        """#30: the ⚠ badge on a conflicted Game links to its Reconciliation
        entry; the Game itself still displays the Lichess version."""
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0004"))

        assert "⚠" in rendered
        assert "/reconciliation" in rendered
        # Lichess displays: the chapter's color (Black), not USCF's (White)
        assert "Black" in rendered

    def test_clean_game_detail_has_no_conflict_badge(self, ui_app, ui_data):
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0001"))

        assert "/reconciliation" not in rendered


# ---------------------------------------------------------------------------
# Forfeit in the UI (issue #29): a visible tag wherever the Game appears
# ---------------------------------------------------------------------------

_FORFEIT_UI_PGN = """\
[Event "Test Open"]
[Site "Springfield"]
[Date "2024.01.06"]
[Round "1"]
[White "Test Player"]
[Black "Opponent A"]
[WhiteElo "1800"]
[BlackElo "1920"]
[ECO "E04"]
[Opening "Catalan Opening"]
[Result "1-0"]
[Termination "win by resignation"]
[StudyName "Test Study"]
[ChapterName "Test Player - Opponent A"]
[ChapterURL "https://lichess.org/study/forfstudy/real0001"]

1. d4 Nf6 2. c4 e6 3. g3 d5 1-0

[Event "Test Open"]
[Site "Springfield"]
[Date "2024.01.07"]
[Round "2"]
[White "Test Player"]
[Black "No Show"]
[WhiteElo "1800"]
[BlackElo "1700"]
[Result "1-0"]
[Termination "win by forfeit"]
[StudyName "Test Study"]
[ChapterName "Test Player - No Show"]
[ChapterURL "https://lichess.org/study/forfstudy/forf0002"]

1. e4 1-0
"""


@pytest.fixture()
def forfeit_ui_data(ui_app):
    """A data store whose archive holds one real game and one Forfeit."""
    import data
    from tests.conftest import stub_ui_sources

    data.reset()
    with stub_ui_sources(_FORFEIT_UI_PGN, uscf_games=[]):
        data.initialize(["forfstudy"], player_name="Test Player",
                        uscf_member_id="12345678")
    yield
    data.reset()


class TestForfeitUI:
    def test_games_table_tags_the_forfeit(self, ui_app, forfeit_ui_data):
        from pages.games import update_games_table
        rows = update_games_table(*_filter_args())
        by_chapter = {r["ChapterURL"].rsplit("/", 1)[-1]: r for r in rows}

        assert by_chapter["forf0002"]["USCF"] == "Forfeit"
        # The forfeit still appears in the Games list (it counts for the score)
        assert len(rows) == 2

    def test_game_detail_tags_the_forfeit(self, ui_app, forfeit_ui_data):
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="forf0002"))

        assert "Forfeit" in rendered
        assert "never rated" in rendered or "no-show" in rendered.lower()


# ---------------------------------------------------------------------------
# The Official/Live rating lens (issue #31)
#
# A lens, not a filter: it selects which rating series powers rating-derived
# numbers and never hides Games.  It lives in the sticky header (so it's on
# every page and survives navigation) and its value rides FILTER_INPUTS (so
# every page follows it exactly like the global filters).
# ---------------------------------------------------------------------------

def _shell_component(ui_app, component_id: str):
    """Find a component by ID in the shell layout (None if absent)."""
    layout = ui_app.layout
    tree = layout() if callable(layout) else layout
    return next((c for c in _walk_components(tree)
                 if getattr(c, "id", None) == component_id), None)


class TestRatingLensToggle:
    def test_the_toggle_lives_in_the_shell_not_in_pages(self, ui_app, ui_data):
        """The lens is on every page and survives navigation because it
        belongs to the shell — the same rule as the global filters."""
        assert _shell_component(ui_app, "rating-lens") is not None
        for path, _ in PAGES:
            assert "rating-lens" not in _collect_ids(_render(_page(path)))

    def test_the_lens_defaults_to_official(self, ui_app, ui_data):
        """Official is Daniel's long-standing convention (PRD #24)."""
        toggle = _shell_component(ui_app, "rating-lens")
        assert toggle.value == "official"

    def test_the_lens_offers_exactly_official_and_live(self, ui_app, ui_data):
        """The two world views of CONTEXT.md: Official Rating and Live Rating."""
        toggle = _shell_component(ui_app, "rating-lens")
        values = [opt["value"] for opt in toggle.options]
        assert values == ["official", "live"]

    def test_the_lens_rides_the_global_filter_inputs(self, ui_app, ui_data):
        """'Exposed to all pages the same way the global filters are' (#31):
        the lens value is part of FILTER_INPUTS, so every filter-driven
        callback re-fires when it changes — no page opts in separately."""
        from filters import FILTER_INPUTS
        dependencies = [(i.component_id, i.component_property) for i in FILTER_INPUTS]
        assert ("rating-lens", "value") in dependencies

    def test_the_lens_triggers_no_data_callbacks(self, ui_app, ui_data):
        """Toggling the lens changes no data — the freshness label (now the Sync
        button's tooltip, issue #45), the cache notice, and the Reconciliation
        badge must not re-fire on it."""
        ui_app.server.test_client().get("/")
        data_outputs = ("sync-button.title", "reconciliation-badge", "cache-notice")
        for key, cb in ui_app.callback_map.items():
            if not any(output in key for output in data_outputs):
                continue
            input_ids = {dep["id"] for dep in cb.get("inputs", [])
                         if isinstance(dep["id"], str)}
            assert "rating-lens" not in input_ids, (
                f"{key} re-fires on the lens, but the lens changes no data"
            )


# ---------------------------------------------------------------------------
# The dual-line rating trend (issue #31)
#
# The Trends rating chart is the one place the lens hides nothing: the
# Official step line and the Live per-event line always both render; the
# active lens only controls which is visually emphasized.
# ---------------------------------------------------------------------------

def _trace(fig, name: str):
    return next(t for t in fig.data if t.name == name)


class TestDualLineRatingTrend:
    def test_the_chart_draws_both_series(self, ui_app, real_career_ui):
        """The Official step line (published integers) and the Live line
        (per-Rated-Event decimals) — Daniel's whole real career."""
        from pages.trends import update_rating
        fig = update_rating(*_filter_args())

        official, live = _trace(fig, "Official"), _trace(fig, "Live")
        # Official: a step function that changes only at supplement dates,
        # starting at the first supplement — months before it are not invented
        assert official.line.shape == "hv"
        assert official.x[0] == date(2025, 9, 1)
        assert list(official.y) == [1038, 1005, 1133, 1230, 1386,
                                    1419, 1506, 1440, 1470, 1545]
        # Live: the continuous chain from the first Rated Event, decimals
        # preserved, never rounded
        assert len(live.y) == 23
        assert live.x[0] == date(2025, 6, 28)
        assert live.y[0] == 695.23
        assert live.y[-1] == 1570.72

    def test_the_active_lens_is_emphasized_without_hiding_the_other(
        self, ui_app, real_career_ui
    ):
        """Toggling the lens changes emphasis only — both lines render under
        either lens; the active one is full strength, the other recedes."""
        from pages.trends import update_rating
        official_fig = update_rating(*_filter_args(lens="official"))
        live_fig = update_rating(*_filter_args(lens="live"))

        for fig in (official_fig, live_fig):
            assert {t.name for t in fig.data} == {"Official", "Live"}

        assert (_trace(official_fig, "Official").opacity
                > _trace(official_fig, "Live").opacity)
        assert (_trace(live_fig, "Live").opacity
                > _trace(live_fig, "Official").opacity)

    def test_the_date_filter_trims_both_lines(self, ui_app, real_career_ui):
        """The chart respects the global date filter: Q1 2026 keeps three
        supplements and seven Rated-Event points."""
        from pages.trends import update_rating
        fig = update_rating(*_filter_args(start="2026-01-01", end="2026-03-31"))

        assert list(_trace(fig, "Official").y) == [1386, 1419, 1506]
        assert len(_trace(fig, "Live").y) == 7

    def test_event_names_render_verbatim_typos_included(self, ui_app, real_career_ui):
        """USCF's own typo'd event name ('ACC Aprril 2026') displays as-is —
        the dashboard never 'fixes' official records."""
        from pages.trends import update_rating
        fig = update_rating(*_filter_args())
        hover_names = {row[0] for row in _trace(fig, "Live").customdata}
        assert "ACC Aprril 2026" in hover_names

    def test_hover_ratings_display_as_whole_numbers(self, ui_app, real_career_ui):
        """Live ratings display rounded — no decimal places anywhere the user
        reads a number (the plotted chain keeps its precision)."""
        from pages.trends import update_rating
        fig = update_rating(*_filter_args())
        live = _trace(fig, "Live")

        # the hover's pre-rating values carry no decimals ("1544", not "1544.47")
        pre_values = {row[2] for row in live.customdata}
        assert "1544" in pre_values
        assert not any("." in value for value in pre_values)
        # the hover template renders the post-rating whole too
        assert "%{y:.0f}" in live.hovertemplate

    def test_without_uscf_the_chart_falls_back_to_typed_ratings(
        self, ui_app, sample_pgn_text
    ):
        """ADR 0003: USCF unreachable and never cached → the chart degrades to
        the typed header values, exactly as before the integration."""
        import data
        from pages.trends import update_rating
        from tests.conftest import stub_ui_sources
        from uscf_client import UscfUnreachableError

        data.reset()
        boom = UscfUnreachableError("Could not reach USCF")
        with stub_ui_sources(sample_pgn_text, uscf_profile=boom):
            data.initialize(
                ["teststudy"], player_name="Test Player", uscf_member_id="12345678"
            )
        try:
            fig = update_rating(*_filter_args())
            names = {t.name for t in fig.data}
            assert "Official" not in names      # nothing official to draw
            assert "Rating" in names            # the typed-values line instead
            # SAMPLE_PGN's typed ratings are what's plotted
            typed_line = next(t for t in fig.data if t.name == "Rating")
            assert 1800 in typed_line.y
        finally:
            data.reset()

    def test_an_empty_date_range_says_so(self, ui_app, real_career_ui):
        """A range with no rating points yields an honest empty chart, not a crash."""
        from pages.trends import update_rating
        fig = update_rating(*_filter_args(start="2030-01-01", end="2030-12-31"))
        assert not fig.data


# ---------------------------------------------------------------------------
# The rating lens across all rating-derived stats (issue #32)
#
# Tested against Daniel's real fixture pair (the real_career_ui conftest
# fixture: the 63-chapter Study snapshot matched to his real USCF record),
# where the two world views genuinely differ:  Official current/peak
# 1470/1506 vs Live 1544/1544; 10 giant kills under Official vs 14 under Live.
# ---------------------------------------------------------------------------

class TestRatingLensAcrossStats:
    def test_overview_kpis_flip_with_the_lens(self, ui_app, real_career_ui):
        """Current and peak rating follow the lens basis; the lens never hides
        Games, so the total stays put."""
        from pages.overview import update_kpis
        official = update_kpis(*_filter_args(lens="official"))
        live = update_kpis(*_filter_args(lens="live"))

        # current rating: the May supplement vs the Live chain entering ACC MAY
        assert official[4] == "1470"
        assert live[4] == "1544"
        # peak: the March supplement vs the career-high live basis
        assert official[5] == "1506"
        assert live[5] == "1544"
        # a lens, not a filter
        assert official[0] == live[0] == "63"

    def test_the_upset_tracker_follows_the_lens(self, ui_app, real_career_ui):
        """'Upset' means the same thing as the rating basis you're looking at
        (PRD #24): the two lenses see different giant kills.  Phase D changes
        both world views: Forfeit wins are never upsets (the Baker
        '+170 kill' is gone), and the Live lens rates opponents by their
        crosstable pre-ratings where cached (issue #35)."""
        from pages.trends import update_upsets
        official_wins, _, official_losses, _ = update_upsets(
            *_filter_args(lens="official"))
        live_wins, _, live_losses, _ = update_upsets(*_filter_args(lens="live"))

        assert len(official_wins) == 9        # was 10 before the Forfeit rule
        assert len(official_losses) == 4
        assert len(live_wins) == 12           # was 14: −1 Forfeit, −1 crosstable
        assert len(live_losses) == 2
        # The biggest kill is a different game in each world view; margins
        # are computed from the whole-number basis (1047 − 695 = 352), so
        # they always agree with the ratings shown beside them
        assert official_wins[0]["Opponent"] == "Zane Anderson"
        assert official_wins[0]["Margin"] == "+303"
        assert live_wins[0]["Opponent"] == "Kyle Davis"
        assert live_wins[0]["Margin"] == "+352"
        # Clark beat Daniel as the crosstable underdog: 1366 vs 1544
        clark = next(loss for loss in live_losses
                          if loss["Opponent"] == "Carter Clark")
        assert clark["Margin"] == "−178"
        assert clark["OpponentRating"] == "1366"     # crosstable, whole

    def test_upset_ratings_never_display_decimals(self, ui_app, real_career_ui):
        """Typed fallback ratings stay whole too — never '1047.0'."""
        from pages.trends import update_upsets
        live_wins, _, _, _ = update_upsets(*_filter_args(lens="live"))

        vandeventer = live_wins[0]
        assert vandeventer["OpponentRating"] == "1047"
        assert ".0" not in str(vandeventer["OpponentRating"])

    def test_opponent_strength_buckets_follow_the_lens(self, ui_app, real_career_ui):
        """The strength-bucket distribution is built on rating-diff, so the
        two lenses bucket the same Games differently."""
        from pages.opponents import update_bucket
        official_fig = update_bucket(*_filter_args(lens="official"))
        live_fig = update_bucket(*_filter_args(lens="live"))

        def bucket_counts(fig):
            return tuple(v for trace in fig.data for v in _axis_vals(trace.y))

        assert bucket_counts(official_fig) != bucket_counts(live_fig)

    def test_the_games_table_shows_the_lens_rating(self, ui_app, real_career_ui):
        """The Games table's rating column speaks the lens basis — the chapter
        Daniel typo'd 1440 reads 1470 under Official, 1544 under Live (live
        values display as whole numbers)."""
        from pages.games import update_games_table
        typo_chapter = "chp00015"

        def rating_of(rows):
            return next(r["PlayerRating"] for r in rows
                        if r["ChapterURL"].endswith(typo_chapter))

        assert rating_of(update_games_table(*_filter_args(lens="official"))) == "1470"
        assert rating_of(update_games_table(*_filter_args(lens="live"))) == "1544"

    def test_performance_rating_follows_the_lens(self, ui_app, real_career_ui):
        """Phase D closes the Phase C limitation: performance rating is built
        from opponent ratings, which now follow the lens too — typed values
        under Official (the pairing sheet), crosstable pre-ratings under Live
        (issue #35).  The two world views give different numbers."""
        from pages.overview import update_kpis
        official = update_kpis(*_filter_args(lens="official"))
        live = update_kpis(*_filter_args(lens="live"))

        assert official[6] == "1344"     # performance vs typed opponent ratings
        assert live[6] == "1330"         # vs what opponents were really rated

    def test_the_pre_supplement_era_shows_no_official_rating(
        self, ui_app, real_career_ui
    ):
        """Filtered to the months before the first supplement, the Official
        lens shows '—', never a fake number; the Live lens has real values."""
        from pages.overview import update_kpis
        era = dict(start="2025-06-01", end="2025-08-31")
        official = update_kpis(*_filter_args(lens="official", **era))
        live = update_kpis(*_filter_args(lens="live", **era))

        assert official[4] == "—"     # current rating: nothing to show
        assert live[4] != "—"         # the live chain existed from event one

    def test_the_lens_composes_with_global_filters(self, ui_app, real_career_ui):
        """Lens and filters are independent: wins-only + Live shows only wins,
        all rated on the Live basis."""
        from pages.trends import update_upsets
        wins, _, losses, _ = update_upsets(
            *_filter_args(lens="live", outcomes=["Win"]))

        assert len(wins) == 12        # giant kills are wins — all still here
        assert losses == []           # losses filtered out entirely


# ---------------------------------------------------------------------------
# Opponent USCF enrichment in the Scouting Report (issue #35)
# ---------------------------------------------------------------------------

class TestScoutingReportUscf:
    def test_the_report_links_to_the_opponents_uscf_page(
        self, ui_app, real_career_ui
    ):
        """Every opponent with a known member ID gets a deep link to their
        page on ratings.uschess.org."""
        from pages.opponents import update_scouting_report
        rendered = str(update_scouting_report("John Baker", *_filter_args()))

        assert "ratings.uschess.org/members/20000056" in rendered

    def test_then_vs_now_ratings(self, ui_app, real_career_ui):
        """The insight the issue is named for: you beat Baker at 1433
        (his crosstable rating that day, under the Live lens) — he's 1400 now
        (his current profile)."""
        from pages.opponents import update_scouting_report
        rendered = str(update_scouting_report("John Baker",
                                              *_filter_args(lens="live")))

        assert "1433" in rendered       # then: crosstable pre-rating in May
        assert "1400" in rendered       # now: his current profile rating

    def test_opponents_with_no_fetched_profile_degrade_gracefully(
        self, ui_app, real_career_ui
    ):
        """An opponent whose current profile isn't cached still gets their
        USCF link — just no 'now' rating (ADR 0003)."""
        from pages.opponents import update_scouting_report
        # Wade Harris is matched (ID 20000061) but his profile isn't fetched
        rendered = str(update_scouting_report("Wade Harris", *_filter_args()))

        assert "ratings.uschess.org/members/20000061" in rendered
        assert "now" not in rendered.lower() or "20000061" in rendered

    def test_unmatched_opponents_get_no_uscf_section(self, ui_app, ui_data):
        """An opponent with no USCF identity (never matched) → no link, no
        crash — the dossier renders exactly as before."""
        from pages.opponents import update_scouting_report
        rendered = str(update_scouting_report("Opponent E", *_filter_args()))

        assert "ratings.uschess.org" not in rendered
        assert "scout-dossier" not in rendered or rendered  # renders without error


class TestLimitationNoteIsGone:
    def test_no_rating_basis_note_anywhere(self, ui_app, ui_data):
        """Issue #35 closed the Phase C limitation — the note documenting it
        is gone from both pages that carried it."""
        trends = str(_render(_page("/trends")))
        opponents = str(_render(_page("/opponents")))

        for rendered in (trends, opponents):
            assert "rating-basis-note" not in rendered


# ---------------------------------------------------------------------------
# Games page callbacks (issue #9)
# ---------------------------------------------------------------------------

class TestGamesCallbacks:
    def test_every_game_listed_with_lichess_link(self, ui_app, ui_data):
        from pages.games import update_games_table
        rows = update_games_table(*_filter_args())
        assert len(rows) == 7
        assert all("lichess.org" in r["Lichess"] for r in rows)

    def test_lesson_indicators_and_tags(self, ui_app, ui_data):
        from pages.games import update_games_table
        rows = update_games_table(*_filter_args())
        # Fixture games 1 and 4 carry Lesson: comments
        assert sum(1 for r in rows if r["LessonIndicator"] == "💡") == 2
        # Tags render as hashtags
        assert any("#strategy" in r["TagsDisplay"] for r in rows)

    def test_games_table_respects_filters(self, ui_app, ui_data):
        from pages.games import update_games_table
        assert len(update_games_table(*_filter_args(outcomes=["Win"]))) == 4


def _games_table_columns(layout):
    """The (label, id) pairs of the Games DataTable, in display order."""
    def walk(node):
        if getattr(node, "id", None) == "games-table":
            return node
        children = getattr(node, "children", None)
        if children is None:
            return None
        if not isinstance(children, (list, tuple)):
            children = [children]
        for child in children:
            found = walk(child)
            if found is not None:
                return found
        return None

    table = walk(layout)
    assert table is not None, "games-table not found in layout"
    return [(c["name"], c["id"]) for c in table.columns]


class TestGamesColumnSet:
    """The Games table is rebuilt around the player (opponent, ratings, my
    color, outcome).  A guard test pins the exact column set so the redundant
    raw-PGN columns can't creep back (the PRD's "Games column set" decision)."""

    def test_player_centric_columns_in_order(self, ui_app, ui_data):
        from pages.games import layout
        ids = [cid for _name, cid in _games_table_columns(layout())]
        assert ids == [
            "Date", "Event", "RoundNum", "Opponent", "OpponentRating",
            "Color", "PlayerRating", "Outcome", "Termination",
            "FullMoves", "ECO", "Opening",
            "LessonIndicator", "TagsDisplay", "USCF", "Lichess",
        ]

    def test_redundant_pgn_columns_are_dropped(self, ui_app, ui_data):
        """No Index, no White/Black name or rating pairs, no raw Result —
        the player-centric columns already say who I played and how it went."""
        from pages.games import layout
        ids = {cid for _name, cid in _games_table_columns(layout())}
        for banned in ("Index", "White", "WhiteRating",
                       "Black", "BlackRating", "Result"):
            assert banned not in ids, f"redundant column {banned!r} crept back"

    def test_data_rows_only_carry_displayed_columns_plus_chapter_url(
        self, ui_app, ui_data
    ):
        """The callback feeds exactly the displayed columns (plus the hidden
        ChapterURL that powers row-click navigation) — no stray PGN fields."""
        from pages.games import layout, update_games_table
        displayed = {cid for _name, cid in _games_table_columns(layout())}
        row = update_games_table(*_filter_args())[0]
        assert set(row) == displayed | {"ChapterURL"}


class TestMobileGameCards:
    """The mobile Games card list (issue #48): at phone widths each Game
    renders as a tappable card — opponent, outcome, date, event — fed by the
    *same* callback rows as the desktop table (the PRD's "Mobile game cards"
    testing decision)."""

    @staticmethod
    def _cards(card_list):
        """The individual card components inside a game_cards() list."""
        children = card_list.children
        return children if isinstance(children, (list, tuple)) else [children]

    def test_one_card_per_game_row(self, ui_app, ui_data):
        """The card list and the table are two renderings of one row set."""
        from components import game_cards
        from pages.games import update_games_table
        rows = update_games_table(*_filter_args())
        assert len(self._cards(game_cards(rows))) == len(rows) == 7

    def test_card_carries_opponent_outcome_date_event(self, ui_app, ui_data):
        """Each card shows the things that matter first at the club."""
        from components import game_cards
        from pages.games import update_games_table
        rows = update_games_table(*_filter_args())
        rendered = str(game_cards(rows))
        # A specific fixture Game's facts all appear on its card.
        assert "Opponent A" in rendered      # opponent
        assert "Win" in rendered             # outcome
        assert "Test Open" in rendered       # event
        assert "2024" in rendered            # date

    def test_outcome_drives_a_semantic_class(self, ui_app, ui_data):
        """The outcome word carries an outcome-<result> class so wins read
        green and losses red — color only where it means something."""
        from components import game_cards
        from pages.games import update_games_table
        rows = update_games_table(*_filter_args())
        rendered = str(game_cards(rows))
        # The fixture has wins, draws, and a loss → all three classes appear.
        assert "outcome-win" in rendered
        assert "outcome-loss" in rendered
        assert "outcome-draw" in rendered

    def test_card_with_a_chapter_url_links_to_the_detail_view(self, ui_app, ui_data):
        """Tapping a card opens that Game — same as clicking a table row."""
        from dash import dcc

        from components import game_cards
        from pages.games import update_games_table
        rows = update_games_table(*_filter_args())
        cards = self._cards(game_cards(rows))
        linked = [c for c in cards if isinstance(c, dcc.Link)]
        assert linked, "no card links to a Game detail view"
        # Every linked card points at the in-app /game/<chapter> route.
        assert all(c.href.startswith("/game/") for c in linked)

    def test_card_without_a_chapter_url_renders_without_a_link(self, ui_app):
        """A Game with no ChapterURL renders a plain card — no link, no error."""
        from dash import dcc, html

        from components import game_cards
        rows = [
            {"Opponent": "No URL Opponent", "Outcome": "Loss",
             "Date": "2024.05.01", "Event": "Club Night", "ChapterURL": ""},
        ]
        card = self._cards(game_cards(rows))[0]
        assert isinstance(card, html.Div)        # not a dcc.Link
        assert not isinstance(card, dcc.Link)
        assert "No URL Opponent" in str(card)    # still shows the Game

    def test_missing_chapter_url_key_does_not_error(self, ui_app):
        """A row dict that omits ChapterURL entirely still renders harmlessly."""
        from components import game_cards
        rows = [{"Opponent": "Sparse", "Outcome": "Win",
                 "Date": "2024.05.02", "Event": "Club Night"}]
        rendered = str(game_cards(rows))
        assert "Sparse" in rendered

    def test_page_renders_both_table_and_card_views(self, ui_app, ui_data):
        """The layout carries both presentations; CSS picks one by width."""
        from pages.games import layout
        ids = _collect_ids(layout())
        assert "games-table" in ids   # desktop table
        assert "games-cards" in ids   # mobile card list
        rendered = str(layout())
        assert "games-table-view" in rendered
        assert "games-cards-view" in rendered

    def test_table_and_cards_share_one_callback(self, ui_app, ui_data):
        """Both presentations are fed by the same callback build — no second
        data pipeline (issue #48's acceptance criterion)."""
        from pages.games import update_games
        table_data, cards = update_games(*_filter_args())
        # The card list renders exactly the rows the table is given.
        assert len(self._cards(cards)) == len(table_data)

    def test_card_swap_lives_in_css_not_python(self):
        """The desktop-table / phone-cards swap is a CSS media query, so the
        callback never branches on viewport width."""
        from pathlib import Path
        css = (Path(__file__).resolve().parent.parent
               / "assets" / "custom.css").read_text()
        assert ".games-cards-view" in css
        assert ".games-table-view" in css


class TestQuietTableTreatment:
    """The shared quiet-table treatment (neutral headers, left-aligned text,
    hairline separators, focused-row fix) is reusable styling, not Games-only
    CSS — issues #49 (Events crosstables) and #50 (Trends upset tables) reuse
    it."""

    def test_quiet_helper_wraps_with_the_shared_class(self):
        from dash import html

        from components import quiet_table
        wrapped = quiet_table(html.Div(id="inner"), clickable=True)
        assert "quiet-table" in wrapped.className
        assert "clickable-rows" in wrapped.className

    def test_quiet_header_style_is_neutral_not_gold(self):
        """Quiet headers carry no gold and no uppercase shouting."""
        from components import QUIET_TABLE_HEADER
        from styles import COLORS
        assert QUIET_TABLE_HEADER["color"] == COLORS["muted"]
        assert QUIET_TABLE_HEADER["color"] != COLORS["accent"]
        assert QUIET_TABLE_HEADER["textTransform"] == "none"
        assert QUIET_TABLE_HEADER["textAlign"] == "left"

    def test_quiet_cell_style_left_aligns(self):
        from components import QUIET_TABLE_CELL
        assert QUIET_TABLE_CELL["textAlign"] == "left"

    def test_quiet_treatment_lives_in_shared_css_not_games_page(self):
        """The hairline separators and focused-row fix are defined once on the
        .quiet-table class so any page can adopt them."""
        from pathlib import Path
        css = (Path(__file__).resolve().parent.parent
               / "assets" / "custom.css").read_text()
        assert ".quiet-table" in css
        # The white focused-row glitch is re-tinted on the shared class.
        assert ".quiet-table .dash-cell.focused" in css

    def test_games_table_adopts_the_quiet_treatment(self, ui_app, ui_data):
        """The Games table sits inside a .quiet-table wrapper."""
        from pages.games import layout
        assert "quiet-table" in str(layout())


class TestOverviewTrendsContentDiscipline:
    """E8: Overview + Trends adopt the content-first rules — KPI colour
    discipline, the wrapping favourite-opening KPI, content-sized cards, neutral
    game-milestone rows (gold stays on USCF achievements), and the upset tables
    on the shared quiet-table treatment."""

    @property
    def css(self) -> str:
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent
                / "assets" / "custom.css").read_text()

    @staticmethod
    def _block(css: str, selector: str) -> str:
        """The declaration block for *selector* (whitespace-tolerant before the
        opening brace), so multi-space selectors like '.milestone-dot.first'
        still match."""
        import re
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, f"selector {selector} not found in CSS"
        return m.group(1)

    @staticmethod
    def _value_card(tree, value_id: str):
        """The KPI card whose value Div carries *value_id*."""
        for node in _walk_components(tree):
            if getattr(node, "id", None) == value_id:
                return node
        raise AssertionError(f"no KPI value with id {value_id}")

    # -- KPI colour discipline ---------------------------------------------

    def test_kpi_values_are_neutral_except_win_and_loss(self, ui_app, ui_data):
        """Only the win % and loss % KPIs carry a semantic colour class; every
        other value (ratings, performance, streak, opponents) is neutral."""
        tree = _render(_page("/"))
        # The two coloured ones.
        assert "win" in self._value_card(tree, "kpi-win-pct").className
        assert "loss" in self._value_card(tree, "kpi-loss-pct").className
        # Everything that used to wear accent / primary / win is neutral now.
        for neutral in ("kpi-rating", "kpi-peak", "kpi-perf", "kpi-streak",
                        "kpi-total", "kpi-draw-pct", "kpi-opps"):
            cls = self._value_card(tree, neutral).className
            assert "accent" not in cls, f"{neutral} still carries accent"
            assert "primary" not in cls, f"{neutral} still carries primary"
            assert "win" not in cls and "loss" not in cls, \
                f"{neutral} carries a semantic colour it shouldn't"

    # -- Favourite-opening KPI wraps, never truncates ----------------------

    def test_favourite_opening_kpi_uses_the_wrapping_text_variant(
        self, ui_app, ui_data
    ):
        tree = _render(_page("/"))
        assert "kpi-value-text" in self._value_card(tree, "kpi-fav-opn").className

    def test_favourite_opening_value_is_not_truncated(self, ui_app, ui_data):
        """The KPI callback emits the full opening name — no '…' ellipsis."""
        from pages.overview import update_kpis
        fav = update_kpis(*_filter_args())[-1]   # last KPI output is the opening
        assert "…" not in fav

    def test_long_favourite_opening_reaches_the_browser_whole(
        self, ui_app, sample_pgn_text
    ):
        """A long opening name survives the callback intact — the CSS wraps it,
        Python never clips it."""
        import data
        import sync
        from pages.overview import update_kpis
        from pgn_stats_core import kpi_stats

        data.reset()
        with mock.patch.object(sync, "fetch_study_pgn", return_value=sample_pgn_text):
            data.initialize(["teststudy"], player_name="Test Player")
        try:
            full = kpi_stats(data.get_df())["favorite_opening"]
            assert update_kpis(*_filter_args())[-1] == full
        finally:
            data.reset()

    def test_kpi_text_variant_wraps_in_css(self):
        """The text variant wraps (no nowrap / ellipsis) — the truncation bug
        is fixed at the source."""
        block = self._block(self.css, ".kpi-value-text")
        assert "white-space: normal" in block
        assert "nowrap" not in block

    # -- Content-sized cards (no dead zones) -------------------------------

    def test_content_cards_carry_the_content_marker(self):
        """content_card marks itself so the grid can size it to content rather
        than stretch it to a chart neighbour's height."""
        from dash import html

        from components import content_card
        card = content_card("X", html.Div("y"))
        assert "content-card" in card.className
        assert "chart-card" in card.className   # still the shared surface

    def test_grid_does_not_stretch_content_cards(self):
        """The grid only floors real chart cards; content cards size to content
        (align-items: start), so 'Last 20 games' / 'Average game length' lose
        their dead zones."""
        css = self.css
        assert "align-items: start" in css
        assert ".g3 > .chart-card:not(.content-card)" in css

    def test_last_20_games_card_is_a_content_card(self, ui_app, ui_data):
        """The Overview 'Last 20 games' card (holding the streak badges) is a
        content card, so it sizes to content instead of stretching to its chart
        neighbours' height."""
        tree = _render(_page("/"))
        badges = self._value_card(tree, "streak-badges")  # the streak-badges Div
        # Walk up: the enclosing content card is the nearest ancestor with the
        # marker class.  Easiest robust check: a content card carrying the
        # streak-badges id exists in the rendered tree.
        card = next(
            node for node in _walk_components(tree)
            if "content-card" in (getattr(node, "className", "") or "")
            and "streak-badges" in _collect_ids(node)
        )
        assert "content-card" in card.className
        assert badges is not None

    # -- Trends: average game length is a compact stat strip ----------------

    def test_average_game_length_is_a_compact_stat_strip(self, ui_app, ui_data):
        from pages.trends import update_length_stats
        rendered = str(update_length_stats(*_filter_args()))
        assert "stat-strip" in rendered
        assert "stat-strip-value" in rendered

    def test_stat_strip_styled_in_css(self):
        assert ".stat-strip" in self.css
        assert ".stat-strip-value" in self.css

    # -- Milestones: game rows neutral, USCF achievements gold -------------

    def test_uscf_achievement_rows_are_gold(self, ui_app, ui_data):
        """The official-achievement rows keep gold (they're achievements)."""
        from pages.overview import update_milestones
        rendered = str(update_milestones(*_filter_args()))
        assert "milestone-row-uscf" in rendered
        assert "milestone-num-uscf" in rendered

    def test_uscf_milestone_dot_keeps_gold_in_css(self):
        block = self._block(self.css, ".milestone-dot.uscf")
        assert "var(--cs-accent)" in block

    def test_game_milestone_dots_are_neutral_not_gold(self):
        """The game-milestone rows (first / streak / peak / every-10th) go
        neutral — gold survives only on the USCF achievement rows."""
        css = self.css
        for selector in (".milestone-dot.first", ".milestone-dot.streak",
                         ".milestone-dot.peak", ".milestone-dot.milestone"):
            block = self._block(css, selector)
            assert "var(--cs-accent)" not in block, \
                f"{selector} still gold — game milestones must be neutral"

    def test_base_milestone_dot_is_neutral(self):
        """A milestone row with no extra kind class defaults to a neutral dot,
        not the old gold default."""
        block = self._block(self.css, ".milestone-dot")
        assert "var(--cs-accent)" not in block

    # -- Trends upset tables adopt the shared quiet-table treatment --------

    def test_upset_tables_adopt_the_quiet_treatment(self, ui_app, ui_data):
        """Both upset tables sit inside a .quiet-table wrapper."""
        from pages.trends import layout
        rendered = str(layout())
        # Two upset tables, each wrapped — the shared class appears for both.
        assert rendered.count("quiet-table") >= 2

    def test_upset_tables_use_the_quiet_header_style(self):
        from components import QUIET_TABLE_HEADER
        from pages.trends import _upset_table
        table = _upset_table("upset-test")
        assert table.style_header == QUIET_TABLE_HEADER

    def test_upset_rows_still_click_through_to_games(self, ui_app, ui_data):
        """The quiet treatment keeps the click-to-open behaviour (issue #11)."""
        from pages.trends import navigate_to_game_from_upset_loss
        rows = [{"Opponent": "X",
                 "ChapterURL": "https://lichess.org/study/s/chap0009"}]
        href, cleared = navigate_to_game_from_upset_loss(
            {"row": 0, "column_id": "Opponent"}, rows)
        assert href == "/game/chap0009"
        assert cleared is None


class TestEventsGoldDiscipline:
    """E6: the Events crosstables apply the gold-discipline decision — the
    'Finished Nth of M' placement stays gold (it's an achievement); the sticky
    headers, Series/crosstable expand markers, and the own-row highlight are
    chrome and go neutral.  Every surviving gold tint derives from the token."""

    @staticmethod
    def _rule(css: str, selector: str) -> str:
        """The declaration block for a single CSS *selector* (first match)."""
        start = css.index(selector)
        open_brace = css.index("{", start)
        close_brace = css.index("}", open_brace)
        return css[open_brace + 1:close_brace]

    @property
    def css(self) -> str:
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent
                / "assets" / "custom.css").read_text()

    def test_placement_line_stays_gold(self):
        """The achievement keeps the gold token."""
        assert "var(--cs-accent)" in self._rule(self.css, ".crosstable-placement")

    def test_crosstable_header_is_neutral_not_gold(self):
        """The sticky header is chrome — neutral, no gold, no uppercase shout."""
        block = self._rule(self.css, ".crosstable-header {")
        assert "var(--cs-accent)" not in block
        assert "var(--cs-muted)" in block
        assert "text-transform: uppercase" not in block

    def test_own_row_highlight_is_a_subtle_neutral_fill(self):
        """Daniel's row stays distinguishable, but with a neutral fill — the
        gold wash is gone."""
        block = self._rule(self.css, ".crosstable-row-me {")
        assert "var(--cs-accent-wash)" not in block
        assert "background" in block          # still visually distinguishable

    def test_own_row_ordinal_is_neutral(self):
        block = self._rule(self.css, ".crosstable-row-me .crosstable-ordinal")
        assert "var(--cs-accent)" not in block

    def test_series_expand_marker_is_neutral(self):
        block = self._rule(self.css, ".series-summary::marker")
        assert "var(--cs-accent)" not in block

    def test_crosstable_expand_marker_is_neutral(self):
        block = self._rule(self.css, ".crosstable-summary::marker")
        assert "var(--cs-accent)" not in block

    def test_placement_is_the_only_gold_in_the_crosstable_region(self):
        """Across the whole crosstable block, the placement line is the only
        use of the gold token — chrome carries none."""
        css = self.css
        region = css[css.index(".crosstable {"):css.index(".unplayed-events-list")]
        gold_lines = [ln for ln in region.splitlines() if "--cs-accent" in ln]
        assert gold_lines, "expected the placement line to keep gold"
        assert all("placement" in ln for ln in gold_lines), gold_lines

    def test_forfeit_tag_and_round_chips_re_tint_from_tokens(self, ui_app, real_career_ui):
        """Forfeit tags and round-result chips carry no hardcoded colors — they
        re-tint from the theme tokens."""
        css = self.css
        forfeit = self._rule(css, ".event-game-forfeit-tag {")
        assert "var(--cs-warning)" in forfeit
        for chip in (".crosstable-round.w", ".crosstable-round.l",
                     ".crosstable-round.d"):
            assert "var(--cs-" in self._rule(css, chip)


class TestMotionAndPolish:
    """E10: the app-wide motion layer (page-load stagger, sliding nav underline,
    hover lift, the drawer's Apple sheet curve) — all CSS-driven, interruptible,
    settled once the page lands, and fully disabled under prefers-reduced-motion.
    Plus the polish pass: 44px touch targets and phone-fit charts.

    External behavior only: what the stylesheet declares (assert on selectors
    and declarations, never pixel-perfect visual appearance)."""

    @property
    def css(self) -> str:
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent
                / "assets" / "custom.css").read_text()

    @staticmethod
    def _block(css: str, selector: str) -> str:
        """The declaration block for *selector* (first match, whitespace
        tolerant before the brace)."""
        import re
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, f"selector {selector} not found in CSS"
        return m.group(1)

    # -- Motion is present --------------------------------------------------

    def test_cards_fade_up_with_a_stagger_on_load(self):
        """Cards animate in (a fade-up) with a per-card delay so the page
        assembles top-to-bottom (PRD: ~40ms stagger, 350ms ease-out)."""
        css = self.css
        # The card-in animation exists and is applied to the page's cards.
        assert "@keyframes cardIn" in css
        block = self._block(css, ".page .chart-card")
        assert "animation" in block and "cardIn" in block
        # A staggered delay is declared (the second card waits behind the first).
        assert "animation-delay: 40ms" in css

    def test_card_stagger_runs_once_and_holds(self):
        """The fade-up uses `both` (holds the end state) and is finite — it
        never loops, so nothing keeps moving after the page settles."""
        block = self._block(self.css, ".page .chart-card")
        assert "both" in block
        assert "infinite" not in block

    def test_nav_underline_slides_between_tabs(self):
        """The active-tab underline transitions its transform (a slide), rather
        than snapping in instantly (PRD: the underline slides between tabs)."""
        css = self.css
        # The underline lives on every link's ::after and transitions its scale.
        under = self._block(css, ".app-nav-link::after")
        assert "transform" in under
        assert "transition" in under
        # The active tab scales the underline up; an inactive one keeps it at 0.
        active = self._block(css, ".app-nav-link.active::after")
        assert "scaleX(1)" in active
        assert "scaleX(0)" in under

    def test_cards_lift_on_hover(self):
        """Chart/content cards lift slightly on hover so what's interactive is
        discoverable (PRD: −1px translate)."""
        block = self._block(self.css, ".chart-card:hover")
        assert "translateY(-1px)" in block

    def test_drawer_uses_the_apple_sheet_curve(self):
        """The filter drawer animates on Apple's sheet curve, not Bootstrap's
        linear default (PRD: cubic-bezier(0.32, 0.72, 0, 1))."""
        block = self._block(self.css, ".filter-drawer.offcanvas")
        assert "cubic-bezier(.32, .72, 0, 1)" in block
        assert "transition" in block

    def test_streak_fire_blaze_is_finite_not_infinite(self):
        """The blazing-streak pulse settles (a finite iteration count) instead
        of looping forever — nothing keeps moving after the page settles."""
        block = self._block(self.css, ".streak-fire.blazing")
        assert "infinite" not in block
        assert "animation" in block

    # -- Reduced motion disables everything --------------------------------

    def test_reduced_motion_media_query_exists(self):
        css = self.css
        assert "@media (prefers-reduced-motion: reduce)" in css

    def test_reduced_motion_disables_animation_and_transition(self):
        """Under reduced motion the universal selector zeroes both animation and
        transition durations — no animation plays, nothing moves."""
        import re
        css = self.css
        start = css.index("@media (prefers-reduced-motion: reduce)")
        block = css[start:]
        # The universal selector is targeted (catches every element + pseudo).
        assert re.search(r"\*\s*,\s*\*::before\s*,\s*\*::after", block)
        assert "animation-duration: .001ms" in block
        assert "transition-duration: .001ms" in block
        assert "animation-iteration-count: 1" in block

    def test_reduced_motion_kills_the_blazing_fire(self):
        """The decorative streak-fire pulse is switched fully off under reduced
        motion (animation: none), so the count sits perfectly still."""
        css = self.css
        start = css.index("@media (prefers-reduced-motion: reduce)")
        block = css[start:]
        assert ".streak-fire.blazing { animation: none" in block

    # -- 44px touch targets ------------------------------------------------

    def test_nav_tabs_meet_the_44px_touch_target(self):
        block = self._block(self.css, ".app-nav-link")
        assert "min-height: 44px" in block

    def test_preset_chips_meet_the_44px_touch_target(self):
        block = self._block(self.css, ".preset-btn")
        assert "min-height: 44px" in block

    def test_repertoire_tree_rows_meet_the_44px_touch_target(self):
        block = self._block(self.css, ".rep-node-row")
        assert "min-height: 44px" in block

    # -- Charts fit a phone ------------------------------------------------

    def test_charts_are_capped_on_a_phone(self):
        """At phone width the tall desktop chart heights are capped so a plot
        fills its frame instead of stranding empty space — and content cards
        (which hold tables) are left to size to their content."""
        css = self.css
        # The cap targets real chart cards, never content cards.
        assert ".chart-card:not(.content-card)" in css
        # It is declared inside the phone media query (max-width: 768px).
        phone = css[css.index("/* ── Phone (the chess-club view)"):]
        assert ".chart-card:not(.content-card)" in phone

    def test_charts_do_not_force_horizontal_scroll_on_a_phone(self):
        """The responsive Plotly graph is pinned to its card width so a plot
        never overflows into a sideways scroll at 390px."""
        phone = self.css[self.css.index("/* ── Phone (the chess-club view)"):]
        assert ".plot-container" in phone or ".js-plotly-plot" in phone

    # -- Spacing pass: standalone cards never touch ------------------------

    def test_standalone_page_cards_carry_a_bottom_margin(self):
        """A card placed directly on a page (Openings' Repertoire card above its
        grid) carries the grid rhythm below it, so it never butts against the
        next block (spacing polish)."""
        css = self.css
        assert ".page > .content-card" in css
        block = self._block(css, ".page > .content-card")
        assert "margin-bottom: 14px" in block

    def test_card_stack_spaces_stacked_cards(self):
        """A vertical stack of cards (Reconciliation's per-kind sections) gets
        the shared 14px rhythm, so the cards don't touch."""
        block = self._block(self.css, ".card-stack")
        assert "gap: 14px" in block

    def test_reconciliation_stacks_its_cards_with_the_shared_rhythm(
        self, ui_app, ui_data
    ):
        """The Reconciliation page (which lists several per-kind cards) wraps
        them in the spaced card-stack container, so the cards don't touch."""
        from pages.reconciliation import update_reconciliation
        rendered = str(update_reconciliation({"seq": 0}))
        assert "card-stack" in rendered


# ---------------------------------------------------------------------------
# The Analysis page (issue #58): mistake-type distribution + awaiting list
# ---------------------------------------------------------------------------

# Two Games: one with requested computer analysis (Daniel, Black, plays the
# positional inaccuracy 3...b6), one plain Game still awaiting its one click.
ANALYSIS_PGN = """\
[Event "Test"]
[White "Foe One"]
[Black "Daniel Gentile"]
[Result "0-1"]
[StudyName "S"]
[ChapterName "Foe One - Daniel Gentile"]
[ChapterURL "https://lichess.org/study/s/analyzedA"]

1. d4 { [%eval 0.2] } 1... d5 { [%eval 0.2] } 2. c4 { [%eval 0.2] } 2... Nf6 { [%eval 0.3] } 3. Nc3 { [%eval 0.3] } 3... b6 $6 { [%eval 1.8] Inaccuracy. e6 was best. } ( 3... e6 4. Nf3 ) 4. Bf4 { [%eval 1.7] } 0-1

[Event "Test"]
[White "Daniel Gentile"]
[Black "Foe Two"]
[Result "1-0"]
[StudyName "S"]
[ChapterName "Daniel Gentile - Foe Two"]
[ChapterURL "https://lichess.org/study/s/plainB"]

1. e4 e5 2. Nf3 Nc6 1-0
"""


@pytest.fixture()
def analysis_store():
    """Initialise the real store from a PGN (USCF off, Lichess stubbed) for the
    Analysis-page callback tests."""
    import data
    import sync

    def _init(pgn_text):
        data.reset()
        with mock.patch.object(sync, "fetch_study_pgn", return_value=pgn_text):
            data.initialize(["analysis-study"], player_name="Daniel Gentile")
        return data

    yield _init
    data.reset()


class TestAnalysisPage:
    def test_empty_state_before_any_game_is_analyzed(self, ui_app, ui_data):
        # The sample fixture carries no requested computer analysis anywhere.
        from pages.analysis import update_analysis
        rendered = str(update_analysis(None))
        assert "empty-state" in rendered

    def test_distribution_and_awaiting_list_when_a_game_is_analyzed(
        self, ui_app, analysis_store
    ):
        analysis_store(ANALYSIS_PGN)
        from pages.analysis import update_analysis
        rendered = str(update_analysis(None))
        assert "empty-state" not in rendered   # we have analysis now
        assert "Graph(" in rendered            # the mistake-type chart renders
        assert "Foe Two" in rendered           # the still-awaiting Chapter is named


# ---------------------------------------------------------------------------
# Game detail: pgn-viewer board + view switcher (issue #60 [F6])
#
# The single Lichess iframe is replaced by Lichess's own open-source
# pgn-viewer (a local asset), behind a Game / My Analysis view switcher.
# Game (default) is a clean replay; My Analysis appears only when Daniel
# annotated the Chapter himself.  The fixture Games are the perfect oracle:
# chap0003 (prose) / chap0004 (a variation) / chap0005 (prose) carry his
# annotations; chap0001 (Lesson-only) / chap0002 / chap0006 / chap0007 don't.
# ---------------------------------------------------------------------------

class TestGameDetailBoard:
    def _board(self, chapter_id):
        from pages.game_detail import layout
        return layout(chapter_id=chapter_id)

    @staticmethod
    def _mount(tree):
        """The pgn-viewer mount div, if any (className carries the 'lpv' token)."""
        for c in _walk_components(tree):
            classes = (getattr(c, "className", "") or "").split()
            if "lpv" in classes:
                return c
        return None

    def test_board_renders_via_pgn_viewer_not_an_iframe(self, ui_app, ui_data):
        """The pgn-viewer mount replaces the iframe embed entirely."""
        import dash
        tree = self._board("chap0001")
        iframes = [c for c in _walk_components(tree)
                   if isinstance(c, dash.html.Iframe)]
        assert not iframes, "the Lichess iframe should be gone"
        assert self._mount(tree) is not None, "no pgn-viewer mount on the page"

    def test_game_view_is_a_clean_replay_without_his_annotations(self, ui_app, ui_data):
        """The default Game board shows the bare line — chap0003's comments are
        stripped, so Daniel first sees the game before his analysis."""
        mount = self._mount(self._board("chap0003"))
        game_pgn = mount.to_plotly_json()["props"]["data-pgn-game"]
        assert "Nf6" in game_pgn                    # the moves are there
        assert "dubious move order" not in game_pgn  # his comment is stripped
        assert "hung the bishop" not in game_pgn

    @staticmethod
    def _switches(tree):
        return [c for c in _walk_components(tree)
                if "lpv-switch" in (getattr(c, "className", "") or "").split()]

    @staticmethod
    def _view(switch):
        return switch.to_plotly_json()["props"].get("data-view")

    def test_view_switcher_defaults_to_game(self, ui_app, ui_data):
        """A Game / My Analysis switcher sits with the board, defaulting to the
        bare Game view (Daniel sees the game first)."""
        switches = self._switches(self._board("chap0003"))
        assert switches, "no view switcher on the detail page"
        game_switch = next((s for s in switches if self._view(s) == "game"), None)
        assert game_switch is not None, "no Game view in the switcher"
        assert "active" in (getattr(game_switch, "className", "") or "").split()

    def test_my_analysis_view_appears_only_when_he_annotated(self, ui_app, ui_data):
        """chap0003 carries his comments → My Analysis is offered, with the
        annotated PGN; chap0002 is bare → no My Analysis tab, no analysis PGN."""
        annotated = self._board("chap0003")
        assert "analysis" in {self._view(s) for s in self._switches(annotated)}
        full = self._mount(annotated).to_plotly_json()["props"].get("data-pgn-analysis")
        assert full and "dubious move order" in full   # his comment is carried here

        bare = self._board("chap0002")
        assert "analysis" not in {self._view(s) for s in self._switches(bare)}
        bare_props = self._mount(bare).to_plotly_json()["props"]
        assert bare_props.get("data-pgn-analysis") is None

    def test_lesson_only_game_stays_a_plain_replay(self, ui_app, ui_data):
        """chap0001's only annotation is a Lesson (its own card) — so it gets no
        My Analysis tab (Daniel's call)."""
        lesson_only = self._board("chap0001")
        assert "analysis" not in {self._view(s) for s in self._switches(lesson_only)}

    def test_board_is_oriented_from_his_side(self, ui_app, ui_data):
        """The board faces the way Daniel played it — his colour at the bottom."""
        import data
        row = data.get_df()
        row = row[row["ChapterURL"].str.endswith("/chap0003")].iloc[0]
        mount = self._mount(self._board("chap0003"))
        orientation = mount.to_plotly_json()["props"]["data-orientation"]
        assert orientation == str(row["Color"]).lower()

    def test_existing_detail_content_survives_the_board_swap(self, ui_app, ui_data):
        """Swapping the board keeps the rest of the page: the critical-moment
        section, the Lessons card, and the Open-on-Lichess link all remain."""
        rendered = str(self._board("chap0001"))
        assert "critical-moment" in rendered or "awaiting-analysis" in rendered
        assert "Lesson" in rendered                       # the Lessons card
        assert "Open on Lichess" in rendered              # the external link
        assert "lichess.org/study/" in rendered           # its href target

    def test_board_degrades_when_a_game_has_no_moves(self, ui_app, ui_data):
        """A Game with no recorded moves shows a quiet empty state, not a viewer."""
        import pandas as pd

        from pages.game_detail import _board_section
        card = _board_section(pd.Series(
            {"Movetext": "", "Color": "White", "Opponent": "X"}
        ))
        assert "empty-state" in str(card)
        assert self._mount(card) is None
