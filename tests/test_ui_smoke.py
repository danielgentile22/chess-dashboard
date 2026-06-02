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

from unittest import mock

import dash
import pytest

# Every page the app must serve: (path, registry name)
PAGES = [
    ("/",          "Overview"),
    ("/trends",    "Trends"),
    ("/openings",  "Openings"),
    ("/opponents", "Opponents"),
    ("/events",    "Events"),
    ("/games",     "Games"),
    ("/lessons",   "Lessons"),
]

# Default filter-callback arguments: everything selected / no restriction,
# matching what the UI sends when no filter has been touched.
ALL_FILTERS = dict(
    colors=["White", "Black"],
    outcomes=["Win", "Draw", "Loss"],
    terminations=[],
    start=None,
    end=None,
    events=[],
    moves=None,
    sync=None,
)


def _filter_args(**overrides):
    """Positional args for a standard filter-driven callback."""
    a = {**ALL_FILTERS, **overrides}
    return (a["colors"], a["outcomes"], a["terminations"], a["start"],
            a["end"], a["events"], a["moves"], a["sync"])


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

    def test_all_seven_pages_registered(self, ui_app, ui_data):
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

    def test_header_has_sync_and_freshness(self, shell_ids):
        assert "sync-button" in shell_ids
        assert "sync-freshness" in shell_ids
        assert "header-games-count" in shell_ids

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


# ---------------------------------------------------------------------------
# Callback integrity: every callback wires to components that exist
# ---------------------------------------------------------------------------

class TestCallbackIntegrity:
    def test_every_callback_id_exists_in_some_layout(self, ui_app, ui_data):
        # All IDs available across the shell and every page
        layout = ui_app.layout
        known = _collect_ids(layout() if callable(layout) else layout)
        for path, _ in PAGES:
            known |= _collect_ids(_render(_page(path)))

        # Some components only exist after a user action creates them
        # dynamically: the Scouting Report (appears once an opponent is chosen),
        # the event detail table (appears once an event row is selected), and
        # the review overlay (appears at /lessons?review=1).
        from pages.events import update_event_table, update_tournament_detail
        from pages.opponents import update_scouting_report
        known |= _collect_ids(update_scouting_report("Opponent A", *_filter_args()))
        event_rows = update_event_table(*_filter_args())
        known |= _collect_ids(update_tournament_detail([0], event_rows, *_filter_args()))
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
        assert "1570.7" in rendered    # the per-Section chain, decimals preserved

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
             mock.patch.object(sync, "fetch_member_sections", return_value=[]):
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
        """Hovering a cell shows that day's Games (opponent, result)."""
        from pages.trends import update_activity_calendar
        hover = self._calendar_hover_text(update_activity_calendar(*_filter_args()))
        assert "Win vs Opponent A" in hover
        assert "Draw vs Opponent B" in hover
        assert "No games" in hover  # days without Games are visibly empty

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
# Events page callbacks (issue #9)
# ---------------------------------------------------------------------------

class TestEventsCallbacks:
    def test_event_chart_and_table(self, ui_app, ui_data):
        from pages.events import update_event_bar, update_event_table
        assert update_event_bar(*_filter_args()).data
        rows = update_event_table(*_filter_args())
        assert {r["Event"] for r in rows} == {"Test Open", "Summer Cup"}

    def test_tournament_detail_for_selected_row(self, ui_app, ui_data):
        from pages.events import update_event_table, update_tournament_detail
        rows = update_event_table(*_filter_args())
        detail = update_tournament_detail([0], rows, *_filter_args())
        assert detail is not None

    def test_no_selection_means_no_detail(self, ui_app, ui_data):
        from pages.events import update_event_table, update_tournament_detail
        rows = update_event_table(*_filter_args())
        assert update_tournament_detail([], rows, *_filter_args()) is None

    def test_event_games_sort_by_round_numerically(self, ui_app):
        """Round 10 belongs after round 2, not between rounds 1 and 2 —
        the lexical-sort bug fixed in issue #17."""
        import data
        import sync

        pgn = "\n".join(
            f'[Event "Blitz Championship"]\n[Site "S"]\n[Date "2024.05.01"]\n'
            f'[Round "{rnd}"]\n[White "Me"]\n[Black "Opp {rnd}"]\n[Result "1-0"]\n'
            f"\n1. e4 1-0\n"
            for rnd in ("2", "10", "1")
        )
        data.reset()
        with mock.patch.object(sync, "fetch_study_pgn", return_value=pgn):
            data.initialize(["teststudy"], player_name="Me")
        try:
            from pages.events import update_event_table, update_tournament_detail
            rows = update_event_table(*_filter_args())
            detail = update_tournament_detail([0], rows, *_filter_args())
            table = next(c for c in _walk_components(detail)
                         if getattr(c, "id", "") == "event-games-table")
            # Numeric values under a numeric column: the browser's native
            # re-sort stays numeric too, not just the default order
            assert [r["RoundNum"] for r in table.data] == [1, 2, 10]
        finally:
            data.reset()


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

    def test_embed_url_derivation(self, ui_app, ui_data):
        from pages.game_detail import embed_url
        assert (embed_url("https://lichess.org/study/abc123/def456")
                == "https://lichess.org/study/embed/abc123/def456")

    def test_detail_shows_board_metadata_and_lesson(self, ui_app, ui_data):
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="chap0001"))
        # The interactive board: an iframe on the Chapter's embed URL
        assert "lichess.org/study/embed/teststudy/chap0001" in rendered
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
        assert "lichess.org/study/embed/teststudy/chap0002" in rendered
        assert "No Lesson" in rendered  # deliberate empty state, not a crash

    def test_unknown_chapter_shows_not_found(self, ui_app, ui_data):
        from pages.game_detail import layout
        rendered = str(layout(chapter_id="doesnotexist"))
        assert "not in your archive" in rendered

    def test_no_chapter_id_shows_not_found(self, ui_app, ui_data):
        from pages.game_detail import layout
        rendered = str(layout())
        assert "not in your archive" in rendered


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

    def test_all_three_tables_have_navigation_callbacks(self, ui_app, ui_data):
        """Games table, Scouting Report timeline, and event detail all open Games."""
        from pages.events import navigate_to_game_from_event
        from pages.games import navigate_to_game
        from pages.opponents import navigate_to_game_from_scout

        for fn in (navigate_to_game, navigate_to_game_from_scout, navigate_to_game_from_event):
            href, _reset = fn({"row": 0, "column_id": "Date"}, self.ROWS)
            assert href == "/game/chap0003"


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
