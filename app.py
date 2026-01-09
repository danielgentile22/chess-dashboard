# app.py
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
)

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
    "Termination",
    "FullMoves",
    "Plies",
    "ECO",
    "Opening",
    "TimeControl",
]


def make_app(df: pd.DataFrame, player_name: str) -> Dash:
    app = Dash(__name__)
    app.title = "USCF PGN Dashboard"

    # ----------------------------
    # Styles: resizable + consistent
    # ----------------------------
    PAGE_STYLE = {"maxWidth": "1400px", "margin": "0 auto", "padding": "12px"}

    BASE_CARD = {"border": "1px solid #ddd", "borderRadius": "8px", "padding": "10px", "backgroundColor": "#fff"}

    # Resizable cards. Drag the bottom-right corner.
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

    # Filter option lists
    termination_options = sorted([t for t in df["Termination"].dropna().unique().tolist() if str(t).strip()])
    outcome_options = ["Win", "Draw", "Loss"]
    color_options = ["White", "Black"]

    # Date range bounds
    df_dates = df[df["Date_dt"].notna()]
    min_date = df_dates["Date_dt"].min().date().isoformat() if not df_dates.empty else None
    max_date = df_dates["Date_dt"].max().date().isoformat() if not df_dates.empty else None

    app.layout = html.Div(
        style=PAGE_STYLE,
        children=[
            html.H2(f"USCF OTB Dashboard: {player_name}"),
            dcc.Store(id="df-store", data=df.to_dict("records")),

            # ----------------------------
            # Filters (collapsible)
            # ----------------------------
            html.Details(
                open=True,
                style=DETAILS_STYLE,
                children=[
                    html.Summary("Filters", style=SUMMARY_STYLE),
                    html.Div(
                        style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr 1fr", "gap": "12px"},
                        children=[
                            html.Div(
                                children=[
                                    html.Div("Color"),
                                    dcc.Checklist(
                                        id="color-filter",
                                        options=[{"label": c, "value": c} for c in color_options],
                                        value=color_options,
                                        inline=True,
                                    ),
                                ]
                            ),
                            html.Div(
                                children=[
                                    html.Div("Result (Outcome for you)"),
                                    dcc.Checklist(
                                        id="outcome-filter",
                                        options=[{"label": o, "value": o} for o in outcome_options],
                                        value=outcome_options,
                                        inline=True,
                                    ),
                                ]
                            ),
                            html.Div(
                                children=[
                                    html.Div("Termination"),
                                    dcc.Dropdown(
                                        id="termination-filter",
                                        options=[{"label": t, "value": t} for t in termination_options],
                                        value=[],
                                        multi=True,
                                        placeholder="All terminations",
                                    ),
                                ]
                            ),
                            html.Div(
                                children=[
                                    html.Div("Date range"),
                                    dcc.DatePickerRange(
                                        id="date-filter",
                                        min_date_allowed=min_date,
                                        max_date_allowed=max_date,
                                        start_date=min_date,
                                        end_date=max_date,
                                    ),
                                ]
                            ),
                        ],
                    ),
                ],
            ),

            # ----------------------------
            # Summary + charts row 1 (collapsible)
            # ----------------------------
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
                                    html.Div("Win/Draw/Loss", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[
                                            dcc.Graph(
                                                id="wdl-pie",
                                                style=GRAPH_STYLE,
                                                config={"displayModeBar": False, "responsive": True},
                                            )
                                        ],
                                    ),
                                ],
                            ),
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Termination counts", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[
                                            dcc.Graph(
                                                id="termination-bar",
                                                style=GRAPH_STYLE,
                                                config={"displayModeBar": False, "responsive": True},
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # ----------------------------
            # Opponents + winrate row (collapsible)
            # ----------------------------
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
                                    html.Div("Opponent summary (played >1)", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[
                                            dcc.Graph(
                                                id="opponent-bar",
                                                style=GRAPH_STYLE,
                                                config={"displayModeBar": False, "responsive": True},
                                            )
                                        ],
                                    ),
                                ],
                            ),
                            html.Div(
                                style=RESIZABLE_CARD,
                                children=[
                                    html.Div("Win % over time (cumulative)", style={"fontWeight": "bold", "marginBottom": "6px"}),
                                    html.Div(
                                        style=GRAPH_CONTAINER,
                                        children=[
                                            dcc.Graph(
                                                id="winrate-line",
                                                style=GRAPH_STYLE,
                                                config={"displayModeBar": False, "responsive": True},
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # ----------------------------
            # Event performance (collapsible)
            # ----------------------------
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
                                        children=[
                                            dcc.Graph(
                                                id="event-bar",
                                                style=GRAPH_STYLE,
                                                config={"displayModeBar": False, "responsive": True},
                                            )
                                        ],
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
                                                    {"name": "FirstDate", "id": "FirstDate"},
                                                    {"name": "Event", "id": "Event"},
                                                    {"name": "Win", "id": "Win"},
                                                    {"name": "Draw", "id": "Draw"},
                                                    {"name": "Loss", "id": "Loss"},
                                                    {"name": "Score", "id": "Score"},
                                                    {"name": "HighestOpp", "id": "HighestOpp"},
                                                    {"name": "HighestOppRating", "id": "HighestOppRating"},
                                                    {"name": "HighestOppOutcome", "id": "HighestOppOutcome"},
                                                    {"name": "LowestOpp", "id": "LowestOpp"},
                                                    {"name": "LowestOppRating", "id": "LowestOppRating"},
                                                    {"name": "LowestOppOutcome", "id": "LowestOppOutcome"},
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
            ),

            # ----------------------------
            # Games table (collapsible, resizable)
            # ----------------------------
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
                                        columns=[{"name": c, "id": c} for c in DEFAULT_TABLE_COLS if c in df.columns],
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

    @app.callback(
        Output("games-table", "data"),
        Output("wdl-pie", "figure"),
        Output("opponent-bar", "figure"),
        Output("winrate-line", "figure"),
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

        # ----------------------------
        # Table
        # ----------------------------
        table_cols = [c for c in DEFAULT_TABLE_COLS if c in df_f.columns]
        table_data = df_f[table_cols].to_dict("records")

        # ----------------------------
        # W/D/L pie
        # ----------------------------
        counts = win_draw_loss_counts(df_f)
        pie_df = counts[counts > 0].reset_index()
        pie_df.columns = ["Outcome", "Games"]
        if pie_df.empty:
            fig_pie = px.pie(values=[1], names=["No data"])
        else:
            fig_pie = px.pie(pie_df, values="Games", names="Outcome")

        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        fig_pie.update_layout(autosize=True, height=None, margin=dict(l=10, r=10, t=10, b=10), legend_title_text="Outcome")

        # ----------------------------
        # Opponent chart (stacked W/D/L, opponents played >1)
        # ----------------------------
        opp = opponent_summary(df_f)
        if opp.empty:
            fig_opp = px.bar(pd.DataFrame({"Opponent": ["No data"], "Count": [0]}), x="Opponent", y="Count")
        else:
            opp_top = opp.head(25).copy()
            opp_long = opp_top.melt(
                id_vars=["Opponent"],
                value_vars=["Win", "Draw", "Loss"],
                var_name="Outcome",
                value_name="Count",
            )
            fig_opp = px.bar(opp_long, x="Opponent", y="Count", color="Outcome", barmode="stack")

        fig_opp.update_layout(
            autosize=True, height=None, margin=dict(l=10, r=10, t=10, b=10), xaxis_title=None, legend_title_text="Outcome"
        )
        fig_opp.update_xaxes(tickangle=30, automargin=True)

        # ----------------------------
        # Win rate over time (cumulative)
        # ----------------------------
        wr = win_rate_over_time(df_f)
        if wr.empty:
            fig_wr = px.line(pd.DataFrame({"Date_dt": [], "WinRate": []}), x="Date_dt", y="WinRate")
        else:
            fig_wr = px.line(wr, x="Date_dt", y="WinRate", markers=True)
            fig_wr.update_yaxes(range=[0, 100])

        fig_wr.update_layout(
            autosize=True,
            height=None,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title=None,
            yaxis_title="Win % (cumulative)",
        )

        # ----------------------------
        # Termination bar
        # ----------------------------
        tc = termination_counts(df_f)
        if tc.empty:
            fig_term = px.bar(pd.DataFrame({"Termination": ["No data"], "Games": [0]}), x="Termination", y="Games")
        else:
            fig_term = px.bar(tc, x="Termination", y="Games")

        fig_term.update_layout(autosize=True, height=None, margin=dict(l=10, r=10, t=10, b=10), xaxis_title=None)
        fig_term.update_xaxes(tickangle=30, automargin=True)

        # ----------------------------
        # Event summary + stacked bar
        # ----------------------------
        ev = event_summary(df_f)
        event_table_data = ev.to_dict("records")

        if ev.empty:
            fig_event = px.bar(pd.DataFrame({"Event": ["No data"], "Count": [0]}), x="Event", y="Count")
        else:
            ev_plot = ev.tail(25).copy()
            ev_long = ev_plot.melt(
                id_vars=["Event"],
                value_vars=["Win", "Draw", "Loss"],
                var_name="Outcome",
                value_name="Count",
            )
            fig_event = px.bar(ev_long, x="Event", y="Count", color="Outcome", barmode="stack")

        fig_event.update_layout(
            autosize=True, height=None, margin=dict(l=10, r=10, t=10, b=10), xaxis_title=None, legend_title_text="Outcome"
        )
        fig_event.update_xaxes(tickangle=30, automargin=True)

        # ----------------------------
        # Streaks
        # ----------------------------
        s = streaks(df_f)
        streak_text = (
            f"Longest streak without loss (W/D only): {s['longest_streak_no_loss']}\n"
            f"Longest winning streak (wins only): {s['longest_streak_wins_only']}\n"
            f"Current streak ({s['current_streak_outcome']}): {s['current_streak_same_outcome']}\n"
            f"Games in current filtered set: {len(df_f)}"
        )

        return (
            table_data,
            fig_pie,
            fig_opp,
            fig_wr,
            fig_term,
            fig_event,
            event_table_data,
            streak_text,
        )

    return app


def main():
    ap = argparse.ArgumentParser(description="USCF PGN Dashboard (Dash)")
    ap.add_argument("--pgn", required=True, help="Path to PGN file")
    ap.add_argument("--player", default=None, help="Your name as it appears in PGN headers")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=8050, type=int)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.pgn):
        raise FileNotFoundError(f"PGN not found: {args.pgn}")

    df, detected = load_games_df(args.pgn, player_name=args.player)
    if df.empty:
        raise RuntimeError("No games found in PGN.")

    app = make_app(df, detected)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()