"""
shell.py
========
The persistent app shell of the multi-page Chess Stats Dashboard.

Everything here exists on every page and never unmounts during navigation:

  * the sticky header — brand, player, game count, freshness, Sync button
  * the page navigation tabs (one per registered page)
  * the global filter drawer (see filters.py)
  * the Sync machinery: sync-store, toast, cache notice, freshness interval

Page content renders inside ``dash.page_container`` below the header.
"""
from __future__ import annotations

from datetime import datetime, timezone

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html, no_update

import data
from components import form_indicator
from filters import FILTER_INPUTS, get_filtered, make_filter_button, make_filter_drawer
from pgn_stats_core import current_form

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _nav() -> html.Nav:
    """One tab per registered page, in registration order."""
    return html.Nav(className="app-nav", children=[
        dbc.NavLink(
            page["name"],
            href=page["path"],
            active="exact",
            className="app-nav-link",
        )
        for page in dash.page_registry.values()
    ])


def _header(df, player_name: str) -> html.Header:
    total = len(df)
    date_range = ""
    if not df.empty:
        dated = df[df["Date_dt"].notna()]
        if not dated.empty:
            lo = dated["Date_dt"].min().strftime("%b %Y")
            hi = dated["Date_dt"].max().strftime("%b %Y")
            date_range = f"{lo} – {hi}"

    return html.Header(className="app-header", children=[
        # Row 1: brand + form + stats + actions
        html.Div(className="app-header-top", children=[
            html.Div(className="app-header-brand", children=[
                html.Span("♞", className="app-header-icon"),
                html.Span([
                    html.Span("Chess Stats", className="app-header-title"),
                    html.Span(player_name, className="app-header-player"),
                ], className="app-header-titles"),
                # Streak fire + form dots (issue #10) — filled by callback
                html.Div(id="header-form", className="header-form"),
            ]),
            html.Div(className="app-header-right", children=[
                html.Span(
                    [html.Strong(f"{total}"), " games"],
                    id="header-games-count", className="app-header-stat",
                ),
                html.Span(date_range, id="header-date-range",
                          className="app-header-stat app-header-stat-wide"),
                html.Span("", id="sync-freshness",
                          className="app-header-stat app-header-stat-wide"),
                make_filter_button(),
                html.Button(
                    className="header-btn header-btn-sync", id="sync-button", children=[
                        html.I(className="bi bi-arrow-repeat"),
                        html.Span("Sync", className="header-btn-text"),
                    ],
                ),
            ]),
        ]),
        # Row 2: page navigation
        _nav(),
    ])


def make_shell() -> html.Div:
    """
    Build the root layout from the current data store.

    Used as a layout *function* (``app.layout = make_shell``) so a browser
    page load always reflects the latest Synced data.
    """
    df = data.get_df()
    player = data.get_player()

    return html.Div(className="app-root", children=[
        _header(df, player),

        # Cache / offline notice (filled by callback when relevant)
        html.Div(id="cache-notice"),

        # Sync machinery (invisible)
        dcc.Store(id="sync-store", data={"seq": 0, "new_games": 0}),
        dcc.Interval(id="freshness-interval", interval=30_000, n_intervals=0),
        dbc.Toast(
            id="sync-toast",
            header="Sync", icon="success",
            is_open=False, dismissable=True, duration=8000,
            className="sync-toast",
        ),

        # Global filter drawer (state survives navigation — it never unmounts)
        make_filter_drawer(df),

        # Page content
        html.Main(className="app-main", children=[dash.page_container]),
    ])


# ---------------------------------------------------------------------------
# Sync callbacks
# ---------------------------------------------------------------------------

def _freshness_label(synced_at: datetime | None) -> str:
    """'synced X ago' label for the header ('' if never synced)."""
    if synced_at is None:
        return ""
    age = (datetime.now(timezone.utc) - synced_at).total_seconds()
    if age < 60:
        return "synced just now"
    if age < 3600:
        return f"synced {int(age // 60)} min ago"
    if age < 86400:
        return f"synced {int(age // 3600)} h ago"
    return f"synced {int(age // 86400)} d ago"


def _describe_new_games(new_games: list[dict]) -> str:
    """Toast body for a successful Sync, e.g. '2 new games: vs Shao (Win), vs Lopez (Loss)'."""
    if not new_games:
        return "No new games — everything is already up to date."
    parts = [f"vs {g['Opponent']} ({g['Outcome']})" for g in new_games]
    n = len(new_games)
    return f"{n} new game{'s' if n > 1 else ''}: " + ", ".join(parts)


@callback(
    Output("sync-store", "data"),
    Output("sync-toast", "is_open"),
    Output("sync-toast", "header"),
    Output("sync-toast", "icon"),
    Output("sync-toast", "children"),
    Input("sync-button", "n_clicks"),
    State("sync-store", "data"),
    prevent_initial_call=True,
)
def run_sync(n_clicks, store):
    """The Sync button: re-Sync all designated Studies, report the outcome."""
    outcome = data.refresh()

    if outcome.status == "already_running":
        return (no_update, True, "Sync already running", "warning",
                "A Sync is already in progress — hang tight.")

    if outcome.status == "error":
        return (no_update, True, "Sync failed", "danger",
                f"{outcome.error} — still showing your current games.")

    # Success: bump the store so every chart re-renders on the new data
    seq = (store or {}).get("seq", 0) + 1
    body = _describe_new_games(outcome.new_games)
    if outcome.failures:
        failed = ", ".join(study_id for study_id, _ in outcome.failures)
        body += f" (couldn't fetch: {failed})"
    new_store = {"seq": seq, "new_games": len(outcome.new_games)}
    return new_store, True, "Sync complete", "success", body


@callback(Output("header-form", "children"), FILTER_INPUTS)
def update_form(colors, outcomes, terminations, start, end, events, moves, _sync=None):
    """Streak fire + form dots in the header — follows filters and Syncs."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    return form_indicator(current_form(df_f))


@callback(
    Output("sync-freshness", "children"),
    Output("cache-notice", "children"),
    Input("freshness-interval", "n_intervals"),
    Input("sync-store", "data"),
)
def update_freshness(_n, _sync):
    """The 'synced X ago' label, or the cached-data notice when offline."""
    if data.source() == "cache":
        cached = data.cached_at()
        when = f"{cached:%Y-%m-%d %H:%M} UTC" if cached else "an earlier run"
        notice = dbc.Alert(
            [
                html.Strong("Showing cached data "),
                f"from {when} — Lichess was unreachable at startup. "
                "Click Sync to retry.",
            ],
            color="warning", className="cache-notice-alert mb-0",
        )
        return "showing cached data", notice
    return _freshness_label(data.synced_at()), None
