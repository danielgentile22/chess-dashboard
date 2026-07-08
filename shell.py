"""
shell.py
========
The persistent app shell of the multi-page Chess Dashboard.

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
from components import celebration_banner, form_indicator
from filters import FILTER_INPUTS, get_filtered, make_filter_button, make_filter_drawer
from pgn_stats_core import current_form, milestone_deltas
from uscf_core import LIVE_LENS, OFFICIAL_LENS

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
        # Pages registered with nav=False (e.g. the Game detail view) are
        # reached by clicking into them, not from the tabs.
        if page.get("nav", True)
    ])


def _lens_toggle() -> html.Div:
    """
    The Official/Live rating lens (issue #31) — a lens, not a filter: it
    selects which rating series (CONTEXT.md: Official Rating vs Live Rating)
    powers rating-derived numbers, and never hides Games.

    It lives in the header so it exists on every page; its value rides
    FILTER_INPUTS so every page follows it exactly like the global filters.
    Session persistence keeps the choice across full page reloads too.
    """
    return html.Div(className="rating-lens", children=[
        dcc.RadioItems(
            id="rating-lens",
            className="rating-lens-control",
            options=[
                {"label": "Official", "value": OFFICIAL_LENS},
                {"label": "Live", "value": LIVE_LENS},
            ],
            value=OFFICIAL_LENS,
            inline=True,
            inputClassName="rating-lens-input",
            labelClassName="rating-lens-option",
            persistence=True,
            persistence_type="session",
        ),
    ], title="Rating lens — which rating series every rating-derived stat uses")


def _header(player_name: str) -> html.Header:
    """
    The calm header (issue #45): brand, form/streak, reconciliation badge,
    the Official/Live lens, Filters, Sync.  Nothing else.

    The game count and date range now live in the filter drawer summary, and
    sync freshness moved to the Sync button's tooltip and the post-Sync toast —
    so the header carries only what's needed on every page and lays out
    correctly on a phone (no more overlapping stats at 390px).
    """
    return html.Header(className="app-header", children=[
        # Row 1: brand + form + actions
        html.Div(className="app-header-top", children=[
            html.Div(className="app-header-brand", children=[
                html.Span("♞", className="app-header-icon"),
                html.Span([
                    html.Span("Chess Dashboard", className="app-header-title"),
                    html.Span(player_name, className="app-header-player"),
                ], className="app-header-titles"),
                # Streak fire + form dots (issue #10) — filled by callback
                html.Div(id="header-form", className="header-form"),
            ]),
            html.Div(className="app-header-right", children=[
                # Open Reconciliation items (issue #30) — filled by callback
                html.Div(id="reconciliation-badge", className="reconciliation-badge-slot"),
                # The Official/Live rating lens (issue #31)
                _lens_toggle(),
                make_filter_button(),
                html.Button(
                    # title holds the sync-freshness tooltip — filled by callback
                    className="header-btn header-btn-sync", id="sync-button",
                    title="Sync", children=[
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
        _header(player),

        # Cache / offline notice (filled by callback when relevant)
        html.Div(id="cache-notice"),

        # Milestone celebrations (issue #15) — lives in the shell so a banner
        # earned by a Sync survives page navigation until it's dismissed
        html.Div(id="celebration-zone"),

        # Programmatic navigation target: clicking a Game row anywhere
        # outputs a /game/<id> path here (issue #11)
        dcc.Location(id="url", refresh="callback-nav"),

        # Sync machinery (invisible)
        dcc.Store(id="sync-store", data={"seq": 0, "new_games": 0}),
        # Bumped on every Reconciliation dismissal so the badge follows (issue #30)
        dcc.Store(id="reconciliation-store", data=0),
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


def _uscf_freshness_label() -> str:
    """
    The USCF half of the per-source freshness indicator (issue #26).

    '' when USCF isn't configured — Lichess-only users see no USCF noise.
    """
    if not data.uscf_enabled():
        return ""
    stale = data.uscf_unavailable_since()
    if stale:
        return stale
    if data.get_uscf_profile() is None:
        return "USCF unavailable"
    return f"USCF {_freshness_label(data.uscf_synced_at())}"


def _per_source_freshness(lichess_label: str) -> str:
    """Join the Lichess and USCF freshness labels into the header string."""
    uscf_label = _uscf_freshness_label()
    if not uscf_label:
        return lichess_label
    if not lichess_label:
        return uscf_label
    return f"Lichess {lichess_label} · {uscf_label}"


def _describe_new_games(new_games: list[dict]) -> str:
    """Toast body for a successful Sync, e.g. '2 new games: vs Edwards (Win), vs Lopez (Loss)'."""
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
    Output("celebration-zone", "children"),
    Input("sync-button", "n_clicks"),
    State("sync-store", "data"),
    prevent_initial_call=True,
)
def run_sync(n_clicks, store):
    """The Sync button: re-Sync all designated Studies, report the outcome."""
    # Snapshot the pre-Sync Games: refresh() rebinds the store, so this
    # reference keeps pointing at the old data — the milestone baseline.
    pre_sync_df = data.get_df()
    outcome = data.refresh()

    if outcome.status == "already_running":
        return (no_update, True, "Sync already running", "warning",
                "A Sync is already in progress — hang tight.", no_update)

    if outcome.status == "error":
        return (no_update, True, "Sync failed", "danger",
                f"{outcome.error} — still showing your current games.", no_update)

    # Success: bump the store so every chart re-renders on the new data
    seq = (store or {}).get("seq", 0) + 1
    body = _describe_new_games(outcome.new_games)
    if outcome.failures:
        failed = ", ".join(study_id for study_id, _ in outcome.failures)
        body += f" (couldn't fetch: {failed})"
    # Sync freshness rides along in the toast (issue #45): it left the header,
    # so the post-Sync toast is where you confirm both sources are current.
    body = [html.Div(body),
            html.Div(_per_source_freshness(_freshness_label(data.synced_at())),
                     className="sync-toast-freshness")]
    new_store = {"seq": seq, "new_games": len(outcome.new_games)}

    # Did the new Games set any personal bests? (issue #15)
    deltas = milestone_deltas(pre_sync_df, data.get_df())
    # Did USCF recognize anything new — a norm, an award? (issue #36)
    deltas += [{
        "kind": "uscf_achievement",
        "description": f"Official USCF achievement: {a.title}"
                       + (f" — {a.event_name}" if a.event_name else ""),
    } for a in data.get_new_achievements()]
    celebration = celebration_banner(deltas) if deltas else no_update

    return new_store, True, "Sync complete", "success", body, celebration


@callback(Output("header-form", "children"), FILTER_INPUTS)
def update_form(colors, outcomes, terminations, start, end, events, moves, _sync=None, lens=None):
    """Streak fire + form dots in the header — follows filters and Syncs."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    return form_indicator(current_form(df_f))


@callback(
    Output("reconciliation-badge", "children"),
    Input("sync-store", "data"),
    Input("reconciliation-store", "data"),
)
def update_reconciliation_badge(_sync, _dismissals):
    """
    The header's open-disagreements count (issue #30), on every page,
    linking to the Reconciliation page.  No badge when everything agrees —
    silence is the reward.
    """
    count = len(data.get_reconciliation())
    if count == 0:
        return None
    return dcc.Link(
        [html.Span("⚠", className="reconciliation-badge-icon"),
         html.Span(str(count), className="reconciliation-badge-count")],
        href="/reconciliation",
        className="reconciliation-badge",
        title=f"{count} open Reconciliation item{'s' if count != 1 else ''} — "
              "your Studies and USCF disagree",
    )


@callback(
    Output("sync-button", "title"),
    Output("cache-notice", "children"),
    Input("freshness-interval", "n_intervals"),
    Input("sync-store", "data"),
)
def update_freshness(_n, _sync):
    """
    The per-source 'synced X ago' label moves onto the Sync button as its
    tooltip (issue #45), and the cached-data notice shows when offline.

    Freshness no longer stares from the header on every page — it's one hover
    (or tap-and-hold) away on the Sync button, and it's restated in the
    post-Sync toast.
    """
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
        return _per_source_freshness("showing cached data"), notice
    return _per_source_freshness(_freshness_label(data.synced_at())), None
