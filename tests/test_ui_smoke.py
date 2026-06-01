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
        # dynamically: the H2H game list (appears once an opponent is chosen)
        # and the event detail table (appears once an event row is selected).
        from pages.events import update_event_table, update_tournament_detail
        from pages.opponents import update_h2h
        known |= _collect_ids(update_h2h("Opponent A", *_filter_args()))
        event_rows = update_event_table(*_filter_args())
        known |= _collect_ids(update_tournament_detail([0], event_rows, *_filter_args()))

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
# Opponents page callbacks (issue #9)
# ---------------------------------------------------------------------------

class TestOpponentsCallbacks:
    def test_opponent_bar_builds(self, ui_app, ui_data):
        from pages.opponents import update_opponents
        # Opponents A and B are both played more than once in the fixture
        assert update_opponents(*_filter_args()).data

    def test_h2h_renders_record_for_known_opponent(self, ui_app, ui_data):
        from pages.opponents import update_h2h
        result = update_h2h("Opponent A", *_filter_args())
        assert "Select an opponent" not in str(result)

    def test_h2h_prompts_when_no_opponent_chosen(self, ui_app, ui_data):
        from pages.opponents import update_h2h
        assert "Select an opponent" in str(update_h2h(None, *_filter_args()))

    def test_h2h_options_follow_the_data(self, ui_app, ui_data):
        from pages.opponents import update_h2h_options
        options = update_h2h_options({"seq": 1, "new_games": 0})
        assert {"label": "Opponent A", "value": "Opponent A"} in options

    def test_strength_charts_build(self, ui_app, ui_data):
        from pages.opponents import update_bucket, update_scatter
        assert update_bucket(*_filter_args()).data
        assert update_scatter(*_filter_args()).data


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
        """Games table, head-to-head list, and event detail all open Games."""
        from pages.events import navigate_to_game_from_event
        from pages.games import navigate_to_game
        from pages.opponents import navigate_to_game_from_h2h

        for fn in (navigate_to_game, navigate_to_game_from_h2h, navigate_to_game_from_event):
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
