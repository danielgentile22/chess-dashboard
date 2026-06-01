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


def _collect_ids(component) -> set[str]:
    """Recursively collect every component ID in a layout tree."""
    ids: set[str] = set()

    def _walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
            return
        node_id = getattr(node, "id", None)
        if isinstance(node_id, str):
            ids.add(node_id)
        _walk(getattr(node, "children", None))

    _walk(component)
    return ids


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

        # Force callback registration merge, then check every dependency
        ui_app.server.test_client().get("/")
        missing = []
        for key, cb in ui_app.callback_map.items():
            if "_pages" in key:  # Dash Pages internal routing callbacks
                continue
            deps = list(cb.get("inputs", [])) + list(cb.get("state", []))
            for dep in deps:
                dep_id = dep["id"] if isinstance(dep, dict) else dep.component_id
                if isinstance(dep_id, str) and "_pages" not in dep_id and dep_id not in known:
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
