"""
app.py
======
Plotly Dash dashboard for visualising personal OTB chess statistics from a PGN file.

Local usage
-----------
    python app.py --pgn "my-games.pgn" [--player "Last, First"] [--port 8050]

Gunicorn / Render deployment
-----------------------------
Set the environment variables PGN_PATH and (optionally) PLAYER_NAME, then run:

    gunicorn app:server --bind 0.0.0.0:$PORT

The module-level ``server`` object (Flask WSGI) is used by gunicorn.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd
import plotly.express as px
from dash import Dash, dcc, html, dash_table, Input, Output, State

from pgn_stats_core import (
    load_games_df,
    apply_filters,
    opponent_summary,
    win_draw_loss_counts,
    win_rate_over_time,
    termination_counts,
    streaks,
    event_summary,
    player_rating_over_time,
)

# Columns shown in the "All games" table, in display order.
DEFAULT_TABLE_COLS = [
    "Index",
    "Date",
    "Event",
    "Site",
    "Round",
    "Board",
    "White",
    "WhiteRating",
    "WhiteID",
    "Black",
    "BlackRating",
    "BlackID",
    "Result",
    "Winner",
    "Outcome",
    "Color",
    "PlayerRating",
    "OpponentRating",
    "Termination",
    "FullMoves",
    "Plies",
    "ECO",
    "Opening",
    "TimeControl",
]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def make_app(df: pd.DataFrame, player_name: str) -> Dash:
    """
    Build and return the Dash application for *df* and *player_name*.

    Parameters
    ----------
    df : pd.DataFrame
        Game data as returned by :func:`pgn_stats_core.load_games_df`.
    player_name : str
        The player's name, shown in the dashboard header.
    """
    dash_app = Dash(__name__)
    dash_app.title = "Chess Stats Dashboard"

    # ------------------------------------------------------------------
    # Shared styles
    # ------------------------------------------------------------------
    PAGE_STYLE = {"maxWidth": "1400px", "margin": "0 auto", "padding": "12px"}

    BASE_CARD = {
        "border": "1px solid #ddd",
        "borderRadius": "8px",
        "padding": "10px",
        "backgroundColor": "#fff",
    }

    # Cards support resize by dragging the bottom-right corner.
    RESIZABLE_CARD = {
        **BASE_CARD,
        "resize": "both",
        "overflow": "hidden",
        "minHeight": "340px",
        "height": "420px",
        "display": "flex",
        "flexDirection": "column",
    }

    GRAPH_CONTAINER = {"flex": "1", "minHeight": "280px", "overflow": "auto"}
    GRAPH_STYLE = {"height": "100%", "width": "100%"}
    DETAILS_STYLE = {**BASE_CARD, "marginBottom": "12px"}
    SUMMARY_STYLE = {"cursor": "pointer", "fontWeight": "bold", "padding": "4px 0"}

    # ------------------------------------------------------------------
    # Filter option lists derived from the full (unfiltered) DataFrame
    # ------------------------------------------------------------------
    termination_options = sorted(
        [t for t in df["Termination"].dropna().unique().tolist() if str(t).strip()]
    )
    outcome_options = ["Win", "Draw", "Loss"]
    color_options = ["White", "Black"]

    df_dates = df[df["Date_dt"].notna()]
    min_date = df_dates["Date_dt"].min().date().isoformat() if not df_dates.empty else None
    max_date = df_dates["Date_dt"].max().date().isoformat() if not df_dates.empty else None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    dash_app.layout = html.Div(
        style=PAGE_STYLE,
        children=[
            html.H2(f"Chess Stats Dashboard — {player_name}"),
            dcc.Store(id="df-store", data=df.to_dict("records")),

            # -- Filters (collapsible) -----------------------------------
            html.Details(
                open=True,
                style=DETAILS_STYLE,
                children=[
                    html.Summary("Filters", style=SUMMARY_STYLE),
                    html.Div(
                        style={
                            "display": "grid",
                            "gridTemplateColumns": "1fr 1fr 1fr 1fr",
                            "gap": "12px",
                        },
                        children=[
                            html.Div([
                                html.Div("Color"),
                                dcc.Checklist(
                                    id="color-filter",
                                    options=[{"label": c, "value": c} for c in color_options],
                                    value=color_options,
                                    inline=True,
                                ),
                            ]),
                            html.Div([
                                html.Div("Outcome (your result)"),
                                dcc.Checklist(
                                    id="outcome-filter",
                                    options=[{"label": o, "value": o} for o in outcome_options],
                                    value=outcome_options,
                                    inline=True,
                                ),
                            ]),
                            html.Div([
                                html.Div("Termination"),
                                dcc.Dropdown(
                                    id="termination-filter",
                                    options=[{"label": t, "value": t} for t in termination_options],
                                    value=[],
                                    multi=True,
                                    placeholder="All terminations",
                                ),
                            ]),
                            html.Div([
                                html.Div("Date range"),
                                dcc.DatePickerRange(
                                    id="date-filter",
                                    min_date_allowed=min_date,
                                    max_date_allowed=max_date,
                                    start_date=min_date,
                                    end_date=max_date,
                                ),
                            ]),
                        ],
                    ),
                ],
            ),

            # -- Summary + core charts (collapsible) ---------------------
            html.Details(
                open=True,
                style=DETAILS_STYLE,
                children=[
                    html.Summary("Summary and core charts", style=SUMMARY_STYLE),
                    html.Div(
                        style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr", "gap": "12px"},
                        children=[
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Streaks (filtered set)", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(id="streaks-text", style={"whiteSpace": "pre-line", "overflow": "auto"}),
                                ],
                            ),
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Win / Draw / Loss", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[dcc.Graph(id="wdl-pie", style=GRAPH_STYLE, config={"displayModeBar": False, "responsive": True})],
                                    ),
                                ],
                            ),
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Termination counts", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[dcc.Graph(id="termination-bar", style=GRAPH_STYLE, config={"displayModeBar": False, "responsive": True})],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # -- Opponents + win rate (collapsible) ----------------------
            html.Details(
                open=True,
                style=DETAILS_STYLE,
                children=[
                    html.Summary("Opponents and win rate", style=SUMMARY_STYLE),
                    html.Div(
                        style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"},
                        children=[
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Opponent summary (played >1 game)", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[dcc.Graph(id="opponent-bar", style=GRAPH_STYLE, config={"displayModeBar": False, "responsive": True})],
                                    ),
                                ],
                            ),
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Win % over time (cumulative)", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[dcc.Graph(id="winrate-line", style=GRAPH_STYLE, config={"displayModeBar": False, "responsive": True})],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # -- Rating over time (collapsible) --------------------------
            html.Details(
                open=True,
                style=DETAILS_STYLE,
                children=[
                    html.Summary("Rating over time", style=SUMMARY_STYLE),
                    html.Div(
                        style={"display": "grid", "gridTemplateColumns": "1fr", "gap": "12px"},
                        children=[
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Your rating over time", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[dcc.Graph(id="rating-line", style=GRAPH_STYLE, config={"displayModeBar": False, "responsive": True})],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # -- Event performance (collapsible) -------------------------
            html.Details(
                open=True,
                style=DETAILS_STYLE,
                children=[
                    html.Summary("Performance per event", style=SUMMARY_STYLE),
                    html.Div(
                        style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"},
                        children=[
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Performance per event (W/D/L)", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[dcc.Graph(id="event-bar", style=GRAPH_STYLE, config={"displayModeBar": False, "responsive": True})],
                                    ),
                                ],
                            ),
                            html.Div(
                                style={
                                    **BASE_CARD,
                                    "resize": "both",
                                    "overflow": "hidden",
                                    "minHeight": "340px",
                                    "height": "420px",
                                    "display": "flex",
                                    "flexDirection": "column",
                                },
                                children=[
                                    html.Div("Event summary (sorted by first game date)", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style={"flex": "1", "overflow": "auto"},
                                        children=[
                                            dash_table.DataTable(
                                                id="event-table",
                                                columns=[
                                                    {"name": "First Date", "id": "FirstDate"},
                                                    {"name": "Event", "id": "Event"},
                                                    {"name": "W", "id": "Win"},
                                                    {"name": "D", "id": "Draw"},
                                                    {"name": "L", "id": "Loss"},
                                                    {"name": "Score", "id": "Score"},
                                                    {"name": "Best Opp", "id": "HighestOpp"},
                                                    {"name": "Best Opp Rtg", "id": "HighestOppRating"},
                                                    {"name": "vs Best", "id": "HighestOppOutcome"},
                                                    {"name": "Lowest Opp", "id": "LowestOpp"},
                                                    {"name": "Lowest Opp Rtg", "id": "LowestOppRating"},
                                                    {"name": "vs Lowest", "id": "LowestOppOutcome"},
                                                ],
                                                data=[],
                                                page_size=10,
                                                sort_action="native",
                                                style_table={"overflowX": "auto"},
                                                style_cell={
                                                    "fontFamily": "Arial",
                                                    "fontSize": "12px",
                                                    "padding": "6px",
                                                    "whiteSpace": "normal",
                                                    "height": "auto",
                                                    "minWidth": "60px",
                                                    "maxWidth": "200px",
                                                },
                                                style_header={"fontWeight": "bold", "backgroundColor": "#f7f7f7"},
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # -- All games table (collapsible) ---------------------------
            html.Details(
                open=True,
                style=DETAILS_STYLE,
                children=[
                    html.Summary("All games table", style=SUMMARY_STYLE),
                    html.Div(
                        style={
                            **BASE_CARD,
                            "resize": "both",
                            "overflow": "hidden",
                            "minHeight": "460px",
                            "height": "640px",
                            "display": "flex",
                            "flexDirection": "column",
                        },
                        children=[
                            html.Div("All games (filtered)", style={"fontWeight": "bold", "marginBottom": "6px"}),
                            html.Div(
                                style={"flex": "1", "overflow": "auto"},
                                children=[
                                    dash_table.DataTable(
                                        id="games-table",
                                        columns=[
                                            {"name": c, "id": c}
                                            for c in DEFAULT_TABLE_COLS
                                            if c in df.columns
                                        ],
                                        data=[],
                                        page_size=25,
                                        sort_action="native",
                                        filter_action="native",
                                        style_table={"overflowX": "auto"},
                                        style_cell={
                                            "fontFamily": "Arial",
                                            "fontSize": "12px",
                                            "padding": "6px",
                                            "whiteSpace": "normal",
                                            "height": "auto",
                                            "minWidth": "90px",
                                            "maxWidth": "240px",
                                        },
                                        style_header={"fontWeight": "bold", "backgroundColor": "#f7f7f7"},
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    @dash_app.callback(
        Output("games-table", "data"),
        Output("wdl-pie", "figure"),
        Output("opponent-bar", "figure"),
        Output("winrate-line", "figure"),
        Output("rating-line", "figure"),
        Output("termination-bar", "figure"),
        Output("event-bar", "figure"),
        Output("event-table", "data"),
        Output("streaks-text", "children"),
        Input("color-filter", "value"),
        Input("outcome-filter", "value"),
        Input("termination-filter", "value"),
        Input("date-filter", "start_date"),
        Input("date-filter", "end_date"),
        State("df-store", "data"),
    )
    def update_all(colors, outcomes, terminations, start_date, end_date, df_records):
        df_raw = pd.DataFrame(df_records)
        df_raw["Date_dt"] = pd.to_datetime(df_raw.get("Date_dt", None), errors="coerce")

        df_f = apply_filters(
            df_raw,
            colors=colors or [],
            outcomes=outcomes or [],
            terminations=terminations or [],
            date_start=start_date,
            date_end=end_date,
        )

        # Games table
        table_cols = [c for c in DEFAULT_TABLE_COLS if c in df_f.columns]
        table_data = df_f[table_cols].to_dict("records")

        # W/D/L pie
        counts = win_draw_loss_counts(df_f)
        pie_df = counts[counts > 0].reset_index()
        pie_df.columns = ["Outcome", "Games"]
        if pie_df.empty:
            fig_pie = px.pie(values=[1], names=["No data"])
        else:
            fig_pie = px.pie(pie_df, values="Games", names="Outcome")
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        fig_pie.update_layout(
            autosize=True, height=None,
            margin=dict(l=10, r=10, t=10, b=10),
            legend_title_text="Outcome",
        )

        # Opponent stacked bar (top 25, opponents played >1)
        opp = opponent_summary(df_f)
        if opp.empty:
            fig_opp = px.bar(
                pd.DataFrame({"Opponent": ["No data"], "Count": [0]}),
                x="Opponent", y="Count",
            )
        else:
            opp_long = opp.head(25).melt(
                id_vars=["Opponent"],
                value_vars=["Win", "Draw", "Loss"],
                var_name="Outcome",
                value_name="Count",
            )
            fig_opp = px.bar(opp_long, x="Opponent", y="Count", color="Outcome", barmode="stack")
        fig_opp.update_layout(
            autosize=True, height=None,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title=None, legend_title_text="Outcome",
        )
        fig_opp.update_xaxes(tickangle=30, automargin=True)

        # Cumulative win rate line
        wr = win_rate_over_time(df_f)
        if wr.empty:
            fig_wr = px.line(pd.DataFrame({"Date_dt": [], "WinRate": []}), x="Date_dt", y="WinRate")
        else:
            fig_wr = px.line(wr, x="Date_dt", y="WinRate", markers=True)
            fig_wr.update_yaxes(range=[0, 100])
        fig_wr.update_layout(
            autosize=True, height=None,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title=None, yaxis_title="Win % (cumulative)",
        )

        # Rating over time
        pr = player_rating_over_time(df_f)
        if pr.empty:
            fig_rating = px.line(
                pd.DataFrame({"Date_dt": [], "PlayerRating": []}),
                x="Date_dt", y="PlayerRating",
            )
        else:
            fig_rating = px.line(pr, x="Date_dt", y="PlayerRating", markers=True)
        fig_rating.update_layout(
            autosize=True, height=None,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title=None, yaxis_title="Rating",
        )

        # Termination bar
        tc = termination_counts(df_f)
        if tc.empty:
            fig_term = px.bar(
                pd.DataFrame({"Termination": ["No data"], "Games": [0]}),
                x="Termination", y="Games",
            )
        else:
            fig_term = px.bar(tc, x="Termination", y="Games")
        fig_term.update_layout(
            autosize=True, height=None,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title=None,
        )
        fig_term.update_xaxes(tickangle=30, automargin=True)

        # Event stacked bar (most recent 25)
        ev = event_summary(df_f)
        event_table_data = ev.to_dict("records")
        if ev.empty:
            fig_event = px.bar(
                pd.DataFrame({"Event": ["No data"], "Count": [0]}),
                x="Event", y="Count",
            )
        else:
            ev_long = ev.tail(25).melt(
                id_vars=["Event"],
                value_vars=["Win", "Draw", "Loss"],
                var_name="Outcome",
                value_name="Count",
            )
            fig_event = px.bar(ev_long, x="Event", y="Count", color="Outcome", barmode="stack")
        fig_event.update_layout(
            autosize=True, height=None,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title=None, legend_title_text="Outcome",
        )
        fig_event.update_xaxes(tickangle=30, automargin=True)

        # Streak summary text
        s = streaks(df_f)
        streak_text = (
            f"Longest streak without loss (W/D): {s['longest_streak_no_loss']}\n"
            f"Longest winning streak (W only):   {s['longest_streak_wins_only']}\n"
            f"Current streak ({s['current_streak_outcome']}): {s['current_streak_same_outcome']}\n"
            f"Games in filtered set: {len(df_f)}"
        )

        return (
            table_data,
            fig_pie,
            fig_opp,
            fig_wr,
            fig_rating,
            fig_term,
            fig_event,
            event_table_data,
            streak_text,
        )

    return dash_app


# ---------------------------------------------------------------------------
# Module-level server initialisation (for gunicorn / Render deployment)
# ---------------------------------------------------------------------------
# Set PGN_PATH (and optionally PLAYER_NAME) as environment variables.
# Gunicorn target: `gunicorn app:server`

server = None  # Flask WSGI server; populated below if PGN_PATH is set

_env_pgn = os.environ.get("PGN_PATH", "").strip()
if _env_pgn:
    if not os.path.exists(_env_pgn):
        raise FileNotFoundError(
            f"PGN file not found: {_env_pgn!r}\n"
            "Set the PGN_PATH environment variable to a valid path."
        )
    _df, _detected = load_games_df(_env_pgn, player_name=os.environ.get("PLAYER_NAME") or None)
    if _df.empty:
        raise RuntimeError(f"No games found in PGN: {_env_pgn!r}")
    _dash_app = make_app(_df, _detected)
    server = _dash_app.server  # Flask WSGI server for gunicorn


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Chess Stats Dashboard — visualise your OTB game history from a PGN file."
    )
    ap.add_argument("--pgn", required=True, help="Path to your PGN file.")
    ap.add_argument(
        "--player",
        default=None,
        help="Your name as it appears in PGN headers (auto-detected if omitted).",
    )
    ap.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1).")
    ap.add_argument("--port", default=8050, type=int, help="Port to listen on (default: 8050).")
    ap.add_argument("--debug", action="store_true", help="Enable Dash debug mode.")
    args = ap.parse_args()

    if not os.path.exists(args.pgn):
        raise FileNotFoundError(f"PGN file not found: {args.pgn!r}")

    df, detected = load_games_df(args.pgn, player_name=args.player)
    if df.empty:
        raise RuntimeError("No games found in PGN.")

    print(f"Loaded {len(df)} games for player: {detected!r}")
    print(f"Starting dashboard at http://{args.host}:{args.port}/")

    app_instance = make_app(df, detected)
    app_instance.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
