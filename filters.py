"""
filters.py
==========
The global filter drawer and everything that makes it work.

The filter controls live in the app shell (not inside any page), so their
state survives page navigation for free.  Every page's callbacks declare
``FILTER_INPUTS`` as inputs and call ``get_filtered()`` to read the Games
that match the current selection.

Public API
----------
FILTER_INPUTS        Dash Input list shared by every filter-driven callback.
get_filtered         Apply the current filter selections to the data store.
make_filter_drawer   The dbc.Offcanvas with every filter control.
make_filter_button   The header button that opens the drawer.
"""
from __future__ import annotations

from datetime import date

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, callback, callback_context, dcc, html, no_update

import data
from pgn_stats_core import apply_filters
from uscf_core import OFFICIAL_LENS, apply_rating_lens

# ---------------------------------------------------------------------------
# Shared filter dependencies
# ---------------------------------------------------------------------------

# Every chart callback in every page listens to exactly these inputs.
# sync-store is bumped after every successful Sync so charts re-render on
# fresh data without any page knowing how Syncing works.
# rating-lens is the Official/Live lens (issue #31) — not a filter (it never
# hides Games), but it rides the same dependency list so every page follows
# it the way it follows the global filters.
FILTER_INPUTS = [
    Input("color-filter",       "value"),
    Input("outcome-filter",     "value"),
    Input("termination-filter", "value"),
    Input("date-filter",        "start_date"),
    Input("date-filter",        "end_date"),
    Input("event-filter",       "value"),
    Input("moves-filter",       "value"),
    Input("sync-store",         "data"),
    Input("rating-lens",        "value"),
]


def get_filtered(colors, outcomes, terminations, start_date, end_date,
                 events, moves, lens=None) -> pd.DataFrame:
    """
    Apply all filter inputs and the rating lens to the data store and return
    the Games every chart should show.

    The lens (issue #32) is applied here, in exactly one place, so every page
    and every stat function follows it without knowing it exists: the returned
    Games carry the lens basis in their player-rating columns.
    """
    df = data.get_df()
    min_mv = max_mv = None
    if moves and len(moves) == 2:
        min_mv, max_mv = moves
    filtered = apply_filters(
        df,
        colors=colors or [],
        outcomes=outcomes or [],
        terminations=terminations or [],
        date_start=start_date,
        date_end=end_date,
        events=events or [],
        min_moves=min_mv,
        max_moves=max_mv,
    )
    return apply_rating_lens(
        filtered,
        lens or OFFICIAL_LENS,
        data.get_official_series(),
        data.get_live_series(),
        data.get_uscf_matches(),
        standings=data.get_uscf_standings(),  # opponent ratings too (issue #35)
    )


# ---------------------------------------------------------------------------
# Layout: the drawer and the button that opens it
# ---------------------------------------------------------------------------

def _label(text: str) -> html.Label:
    return html.Label(text, className="filter-label")


def _data_bounds(df: pd.DataFrame) -> dict:
    """Option lists and ranges derived from the current Games."""
    terminations = sorted(
        [t for t in df["Termination"].dropna().unique() if str(t).strip()]
    ) if not df.empty else []
    events = sorted(
        [e for e in df["Event"].dropna().unique() if str(e).strip()]
    ) if not df.empty else []

    dated = df[df["Date_dt"].notna()] if not df.empty else df
    min_date = dated["Date_dt"].min().date().isoformat() if len(dated) else None
    max_date = dated["Date_dt"].max().date().isoformat() if len(dated) else None

    moves = df["FullMoves"].dropna() if not df.empty else pd.Series(dtype=float)
    min_mv = int(moves.min()) if not moves.empty else 1
    max_mv = int(moves.max()) if not moves.empty else 100

    return dict(terminations=terminations, events=events,
                min_date=min_date, max_date=max_date,
                min_mv=min_mv, max_mv=max_mv)


def make_filter_button() -> html.Div:
    """The header button that opens the filter drawer, with an active-count badge."""
    return html.Button(
        className="header-btn", id="filter-drawer-button", children=[
            html.I(className="bi bi-sliders2"),
            html.Span("Filters", className="header-btn-text"),
            html.Span("", id="filter-active-count", className="filter-count-badge"),
        ],
    )


def make_filter_drawer(df: pd.DataFrame) -> dbc.Offcanvas:
    """The right-hand drawer holding every global filter control."""
    b = _data_bounds(df)

    return dbc.Offcanvas(
        id="filter-drawer",
        title="Filters",
        placement="end",
        is_open=False,
        className="filter-drawer",
        children=[html.Div(className="filter-sections", children=[
            html.Div(id="filter-summary", className="filter-summary"),

            # Quick presets
            html.Div(className="filter-section filter-section-presets", children=[
                _label("Presets"),
                html.Div(className="preset-row", children=[
                    html.Button("All games",  id="preset-all",    className="preset-btn"),
                    html.Button("Last 20",    id="preset-last20", className="preset-btn"),
                    html.Button("This year",  id="preset-year",   className="preset-btn"),
                    html.Button("White only", id="preset-white",  className="preset-btn"),
                    html.Button("Black only", id="preset-black",  className="preset-btn"),
                    html.Button("Wins only",  id="preset-wins",   className="preset-btn"),
                ]),
            ]),

            # Color + outcome
            html.Div(className="filter-section filter-section-split", children=[
                html.Div([
                    _label("Color"),
                    dcc.Checklist(
                        id="color-filter",
                        options=[{"label": " White", "value": "White"},
                                 {"label": " Black", "value": "Black"}],
                        value=["White", "Black"],
                        inputStyle={"marginRight": "6px"},
                    ),
                ]),
                html.Div([
                    _label("Outcome"),
                    dcc.Checklist(
                        id="outcome-filter",
                        options=[{"label": " Win",  "value": "Win"},
                                 {"label": " Draw", "value": "Draw"},
                                 {"label": " Loss", "value": "Loss"}],
                        value=["Win", "Draw", "Loss"],
                        inputStyle={"marginRight": "6px"},
                    ),
                ]),
            ]),

            html.Div(className="filter-section filter-section-termination", children=[
                _label("Termination"),
                dcc.Dropdown(
                    id="termination-filter",
                    options=[{"label": t, "value": t} for t in b["terminations"]],
                    value=[], multi=True, placeholder="All terminations",
                ),
            ]),

            html.Div(className="filter-section filter-section-events", children=[
                _label("Events"),
                dcc.Dropdown(
                    id="event-filter",
                    options=[{"label": e, "value": e} for e in b["events"]],
                    value=[], multi=True, placeholder="All events",
                ),
            ]),

            html.Div(className="filter-section filter-section-date", children=[
                _label("Date range"),
                dcc.DatePickerRange(
                    id="date-filter",
                    min_date_allowed=b["min_date"], max_date_allowed=b["max_date"],
                    start_date=b["min_date"], end_date=b["max_date"],
                    display_format="YYYY-MM-DD",
                ),
            ]),

            html.Div(className="filter-section filter-section-moves", children=[
                _label("Game length (moves)"),
                dcc.RangeSlider(
                    id="moves-filter",
                    min=b["min_mv"], max=b["max_mv"],
                    value=[b["min_mv"], b["max_mv"]],
                    step=1, marks=None,
                    tooltip={"placement": "bottom", "always_visible": True},
                ),
            ]),
        ])],
    )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output("filter-drawer", "is_open"),
    Input("filter-drawer-button", "n_clicks"),
    State("filter-drawer", "is_open"),
    prevent_initial_call=True,
)
def toggle_filter_drawer(n_clicks, is_open):
    return not is_open


@callback(
    Output("color-filter",   "value"),
    Output("outcome-filter", "value"),
    Output("date-filter",    "start_date"),
    Output("date-filter",    "end_date"),
    [Input("preset-all",    "n_clicks"),
     Input("preset-last20", "n_clicks"),
     Input("preset-year",   "n_clicks"),
     Input("preset-white",  "n_clicks"),
     Input("preset-black",  "n_clicks"),
     Input("preset-wins",   "n_clicks")],
    prevent_initial_call=True,
)
def apply_preset(n_all, n20, n_year, n_white, n_black, n_wins):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update, no_update
    btn = ctx.triggered[0]["prop_id"].split(".")[0]

    df = data.get_df()
    dated = df[df["Date_dt"].notna()]
    global_min = dated["Date_dt"].min().date().isoformat() if not dated.empty else None
    global_max = dated["Date_dt"].max().date().isoformat() if not dated.empty else None

    colors = ["White", "Black"]
    outcomes = ["Win", "Draw", "Loss"]
    start, end = global_min, global_max

    if btn == "preset-last20":
        if not dated.empty:
            last20 = df.sort_values("Date_dt").tail(20)
            start = last20["Date_dt"].min().date().isoformat()
    elif btn == "preset-year":
        start = f"{date.today().year}-01-01"
    elif btn == "preset-white":
        colors = ["White"]
    elif btn == "preset-black":
        colors = ["Black"]
    elif btn == "preset-wins":
        outcomes = ["Win"]

    return colors, outcomes, start, end


def _date_range_label(df: pd.DataFrame) -> str:
    """'Jun 2025 – May 2026' for the dated Games, or '' when there are none."""
    if df.empty:
        return ""
    dated = df[df["Date_dt"].notna()]
    if dated.empty:
        return ""
    return f"{dated['Date_dt'].min():%b %Y} – {dated['Date_dt'].max():%b %Y}"


@callback(
    Output("filter-summary", "children"),
    Output("filter-active-count", "children"),
    FILTER_INPUTS,
)
def update_filter_summary(colors, outcomes, terminations, start, end,
                          events, moves, _sync=None, lens=None):
    """
    The drawer summary line + the active-filter count badge.

    The game count and date range relocated here from the header (issue #45):
    e.g. "Showing all 63 games · Jun 2025 – May 2026".  Both follow the active
    filters — the count and the span describe exactly the Games in view.
    """
    df = data.get_df()
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    total, filtered = len(df), len(df_f)

    summary = (f"Showing all {total} games" if filtered == total
               else f"Showing {filtered} of {total} games")
    date_range = _date_range_label(df_f)
    if date_range:
        summary = f"{summary} · {date_range}"

    b = _data_bounds(df)
    active = 0
    if colors and set(colors) != {"White", "Black"}:
        active += 1
    if outcomes and set(outcomes) != {"Win", "Draw", "Loss"}:
        active += 1
    if terminations:
        active += 1
    if events:
        active += 1
    if (start and b["min_date"] and str(start)[:10] > b["min_date"]) or \
       (end and b["max_date"] and str(end)[:10] < b["max_date"]):
        active += 1
    if moves and len(moves) == 2 and (moves[0] > b["min_mv"] or moves[1] < b["max_mv"]):
        active += 1

    return summary, (str(active) if active else "")


@callback(
    Output("termination-filter", "options"),
    Output("event-filter", "options"),
    Output("date-filter", "min_date_allowed"),
    Output("date-filter", "max_date_allowed"),
    Output("date-filter", "start_date", allow_duplicate=True),
    Output("date-filter", "end_date", allow_duplicate=True),
    Output("moves-filter", "min"),
    Output("moves-filter", "max"),
    Output("moves-filter", "value"),
    Input("sync-store", "data"),
    prevent_initial_call=True,  # the layout holds correct startup values
)
def update_filter_options(sync_store):
    """Filter options follow the current data, not startup data.

    The game count and date range relocated to the drawer summary (issue #45),
    which is filter-driven; this callback now only refreshes option lists and
    ranges when a Sync changes the underlying Games.
    """
    df = data.get_df()
    if df.empty:
        return (no_update,) * 9

    b = _data_bounds(df)

    # Only push the *selected* ranges back to "everything" when new Games
    # arrived (so they're immediately visible); otherwise leave the user's
    # selection alone.
    has_new_games = (sync_store or {}).get("new_games", 0) > 0
    start_value = b["min_date"] if has_new_games else no_update
    end_value = b["max_date"] if has_new_games else no_update
    moves_value = [b["min_mv"], b["max_mv"]] if has_new_games else no_update

    return (
        [{"label": t, "value": t} for t in b["terminations"]],
        [{"label": e, "value": e} for e in b["events"]],
        b["min_date"], b["max_date"],
        start_value, end_value,
        b["min_mv"], b["max_mv"], moves_value,
    )
