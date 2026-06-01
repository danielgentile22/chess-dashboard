"""
callbacks.py
============
All Dash callbacks for the Chess Stats Dashboard.

Call ``register_callbacks(app)`` after the layout is set.

Each callback group reads the module-level DataFrame via ``data.get_df()``,
applies the current filter selections, and builds Plotly figures or HTML
components.  No global mutable state is written here.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, callback_context, dash_table, html, no_update

import data
from pgn_stats_core import (
    activity_data,
    apply_filters,
    compute_milestones,
    event_summary,
    game_length_data,
    head_to_head,
    kpi_stats,
    opening_summary,
    opponent_rating_bucket_summary,
    opponent_summary,
    outcome_vs_rating_data,
    performance_rating_stats,
    player_rating_over_time,
    streaks,
    termination_counts,
    win_draw_loss_counts,
    win_rate_over_time,
)
from styles import COLORS, WDL_COLOR_MAP, apply_dark_theme, empty_fig

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DISPLAY_COLS = [
    "Index", "Date", "Event", "Round", "White", "WhiteRating",
    "Black", "BlackRating", "Result", "Outcome", "Color",
    "PlayerRating", "OpponentRating", "Termination",
    "FullMoves", "ECO", "Opening",
]

_TABLE_CELL = dict(
    fontFamily="Inter, system-ui, sans-serif", fontSize="12px",
    padding="7px 10px", whiteSpace="normal", height="auto",
    minWidth="70px", maxWidth="200px",
    backgroundColor=COLORS["card"], color=COLORS["text"],
    border=f"1px solid {COLORS['border']}",
)
_TABLE_HEADER = dict(
    fontWeight="700", backgroundColor=COLORS["card2"],
    color=COLORS["accent"], border=f"1px solid {COLORS['border']}",
    fontSize="10px", letterSpacing="0.07em", textTransform="uppercase",
)


def _get_filtered(colors, outcomes, terminations, start_date, end_date, events, moves) -> pd.DataFrame:
    """Apply all filter inputs and return the filtered DataFrame."""
    df = data.get_df()
    min_mv = max_mv = None
    if moves and len(moves) == 2:
        min_mv, max_mv = moves
    return apply_filters(
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


# ---------------------------------------------------------------------------
# Register all callbacks
# ---------------------------------------------------------------------------

def register_callbacks(app) -> None:  # noqa: C901 (intentionally long)

    FILTER_INPUTS = [
        Input("color-filter",       "value"),
        Input("outcome-filter",     "value"),
        Input("termination-filter", "value"),
        Input("date-filter",        "start_date"),
        Input("date-filter",        "end_date"),
        Input("event-filter",       "value"),
        Input("moves-filter",       "value"),
    ]

    # ------------------------------------------------------------------ #
    # Preset buttons → update filter controls                             #
    # ------------------------------------------------------------------ #
    @app.callback(
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

    # ------------------------------------------------------------------ #
    # Filter badge                                                         #
    # ------------------------------------------------------------------ #
    @app.callback(Output("filter-badge", "children"), FILTER_INPUTS)
    def update_badge(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        total = len(data.get_df())
        filtered = len(df_f)
        if filtered == total:
            return f"Showing all {total} games"
        return f"Showing {filtered} of {total} games"

    # ------------------------------------------------------------------ #
    # KPI bar                                                              #
    # ------------------------------------------------------------------ #
    @app.callback(
        Output("kpi-total",    "children"),
        Output("kpi-win-pct",  "children"),
        Output("kpi-draw-pct", "children"),
        Output("kpi-loss-pct", "children"),
        Output("kpi-rating",   "children"),
        Output("kpi-peak",     "children"),
        Output("kpi-perf",     "children"),
        Output("kpi-streak",   "children"),
        Output("kpi-opps",     "children"),
        Output("kpi-fav-opn",  "children"),
        FILTER_INPUTS,
    )
    def update_kpi(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        k = kpi_stats(df_f)
        def _r(x): return str(int(x)) if x is not None else "—"
        def _p(x): return f"{x}%" if x is not None else "—"
        fav = k["favorite_opening"]
        fav_short = (fav[:22] + "…") if len(fav) > 24 else fav
        return (
            str(k["total_games"]),
            _p(k["win_pct"]),
            _p(k["draw_pct"]),
            _p(k["loss_pct"]),
            _r(k["current_rating"]),
            _r(k["peak_rating"]),
            _r(k["performance_rating"]),
            str(k["longest_win_streak"]),
            str(k["unique_opponents"]),
            fav_short,
        )

    # ------------------------------------------------------------------ #
    # Streak badges + stats                                                #
    # ------------------------------------------------------------------ #
    @app.callback(
        Output("streak-badges", "children"),
        Output("streak-stats",  "children"),
        FILTER_INPUTS,
    )
    def update_streak(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        s = streaks(df_f)

        badges = [
            html.Span(
                className=f"sbadge {o.lower()}",
                title=o,
            )
            for o in s["last_20"]
        ]
        if not badges:
            badges = [html.Span("No games", style={"color": COLORS["dim"], "fontSize": "12px"})]

        cur_class = s["current_streak_outcome"].lower() if s["current_streak_outcome"] != "N/A" else ""
        stats = [
            html.Div(className="streak-stat", children=[
                html.Div("Unbeaten streak", className="streak-stat-label"),
                html.Div(str(s["longest_streak_no_loss"]), className="streak-stat-value win"),
            ]),
            html.Div(className="streak-stat", children=[
                html.Div("Win streak",      className="streak-stat-label"),
                html.Div(str(s["longest_streak_wins_only"]), className="streak-stat-value win"),
            ]),
            html.Div(className="streak-stat", children=[
                html.Div(f"Current ({s['current_streak_outcome']})", className="streak-stat-label"),
                html.Div(str(s["current_streak_same_outcome"]), className=f"streak-stat-value {cur_class}"),
            ]),
        ]
        return badges, stats

    # ------------------------------------------------------------------ #
    # W/D/L donut                                                          #
    # ------------------------------------------------------------------ #
    @app.callback(Output("wdl-pie", "figure"), FILTER_INPUTS)
    def update_wdl(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        counts = win_draw_loss_counts(df_f)
        pie_df = counts[counts > 0].reset_index()
        pie_df.columns = ["Outcome", "Games"]
        if pie_df.empty:
            return empty_fig("No data")

        fig = go.Figure(go.Pie(
            labels=pie_df["Outcome"],
            values=pie_df["Games"],
            hole=0.54,
            marker=dict(
                colors=[WDL_COLOR_MAP.get(o, COLORS["dim"]) for o in pie_df["Outcome"]],
                line=dict(color=COLORS["card"], width=2),
            ),
            textinfo="percent+label",
            textfont=dict(size=12, color=COLORS["text"]),
            hovertemplate="%{label}: %{value} games (%{percent})<extra></extra>",
        ))
        total = int(pie_df["Games"].sum())
        fig.add_annotation(
            text=f"<b>{total}</b><br><span style='font-size:11px'>games</span>",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color=COLORS["text"]),
        )
        apply_dark_theme(fig, show_legend=False)
        return fig

    # ------------------------------------------------------------------ #
    # Termination bar                                                       #
    # ------------------------------------------------------------------ #
    @app.callback(Output("termination-bar", "figure"), FILTER_INPUTS)
    def update_termination(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        tc = termination_counts(df_f)
        if tc.empty:
            return empty_fig("No data")
        # Shorten long labels
        tc["Label"] = tc["Termination"].apply(
            lambda x: (x[:28] + "…") if len(str(x)) > 30 else x
        )
        fig = px.bar(
            tc, x="Games", y="Label", orientation="h",
            color_discrete_sequence=[COLORS["primary"]],
            hover_data={"Termination": True, "Games": True, "Label": False},
        )
        fig.update_traces(
            hovertemplate="%{customdata[0]}: %{x} games<extra></extra>",
        )
        apply_dark_theme(fig, xaxis_title="Games")
        fig.update_yaxes(categoryorder="total ascending")
        return fig

    # ------------------------------------------------------------------ #
    # Win rate over time                                                    #
    # ------------------------------------------------------------------ #
    @app.callback(Output("winrate-line", "figure"), FILTER_INPUTS)
    def update_winrate(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        wr = win_rate_over_time(df_f)
        if wr.empty:
            return empty_fig("No dated games")
        fig = go.Figure()
        # Shaded area
        fig.add_trace(go.Scatter(
            x=wr["Date_dt"], y=wr["WinRate"],
            mode="lines+markers",
            line=dict(color=COLORS["win"], width=2),
            marker=dict(size=5, color=COLORS["win"]),
            fill="tozeroy",
            fillcolor="rgba(63,185,80,.10)",
            hovertemplate="%{x|%Y-%m-%d}<br>Win rate: %{y:.1f}%<br>(%{customdata[0]}W / %{customdata[1]} games)<extra></extra>",
            customdata=wr[["CumWins", "CumGames"]].values,
            name="Win rate",
        ))
        # 50% reference line
        fig.add_hline(
            y=50, line_dash="dash",
            line_color=COLORS["muted"], line_width=1,
            annotation_text="50%", annotation_position="right",
            annotation_font=dict(color=COLORS["muted"], size=10),
        )
        apply_dark_theme(fig, yaxis_title="Win % (cumulative)")
        fig.update_yaxes(range=[0, 100])
        return fig

    # ------------------------------------------------------------------ #
    # Rating over time                                                      #
    # ------------------------------------------------------------------ #
    @app.callback(Output("rating-line", "figure"), FILTER_INPUTS)
    def update_rating(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        pr = player_rating_over_time(df_f)
        if pr.empty:
            return empty_fig("No rating data")
        fig = go.Figure()
        # Main line
        fig.add_trace(go.Scatter(
            x=pr["Date_dt"], y=pr["PlayerRating"],
            mode="lines+markers",
            line=dict(color=COLORS["accent"], width=2),
            marker=dict(size=5, color=COLORS["accent"]),
            hovertemplate="%{x|%Y-%m-%d}: %{y}<extra></extra>",
            name="Rating",
        ))
        # Trend line (linear via numpy)
        if len(pr) >= 3:
            x_num = np.arange(len(pr))
            coeffs = np.polyfit(x_num, pr["PlayerRating"].values, 1)
            trend = np.polyval(coeffs, x_num)
            fig.add_trace(go.Scatter(
                x=pr["Date_dt"], y=trend,
                mode="lines",
                line=dict(color=COLORS["muted"], width=1, dash="dot"),
                hoverinfo="skip",
                name="Trend",
            ))
        apply_dark_theme(fig, yaxis_title="Rating", legend_title="")
        return fig

    # ------------------------------------------------------------------ #
    # Opening family bar                                                   #
    # ------------------------------------------------------------------ #
    @app.callback(Output("opening-family-bar", "figure"), FILTER_INPUTS)
    def update_opening_family(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        fam, _ = opening_summary(df_f)
        if fam.empty:
            return empty_fig("No ECO data")
        long = fam.melt(
            id_vars=["FamilyName"],
            value_vars=["Win", "Draw", "Loss"],
            var_name="Outcome", value_name="Count",
        )
        fig = px.bar(
            long, x="Count", y="FamilyName", color="Outcome",
            orientation="h", barmode="stack",
            color_discrete_map=WDL_COLOR_MAP,
        )
        apply_dark_theme(fig, xaxis_title="Games", legend_title="Outcome")
        fig.update_yaxes(categoryorder="total ascending")
        return fig

    # ------------------------------------------------------------------ #
    # Opening table                                                         #
    # ------------------------------------------------------------------ #
    @app.callback(Output("opening-table", "data"), FILTER_INPUTS)
    def update_opening_table(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        _, opn = opening_summary(df_f)
        return opn.head(50).to_dict("records")

    # ------------------------------------------------------------------ #
    # Opponent bar                                                          #
    # ------------------------------------------------------------------ #
    @app.callback(Output("opponent-bar", "figure"), FILTER_INPUTS)
    def update_opponents(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        opp = opponent_summary(df_f)
        if opp.empty:
            return empty_fig("No repeat opponents in current filter")
        top = opp.head(25).copy()
        long = top.melt(
            id_vars=["Opponent"],
            value_vars=["Win", "Draw", "Loss"],
            var_name="Outcome", value_name="Count",
        )
        fig = px.bar(
            long, x="Opponent", y="Count", color="Outcome",
            barmode="stack", color_discrete_map=WDL_COLOR_MAP,
        )
        apply_dark_theme(fig, legend_title="Outcome")
        fig.update_xaxes(tickangle=35, automargin=True)
        return fig

    # ------------------------------------------------------------------ #
    # Head-to-head                                                          #
    # ------------------------------------------------------------------ #
    @app.callback(
        Output("h2h-stats", "children"),
        Input("h2h-opponent", "value"),
        FILTER_INPUTS,
    )
    def update_h2h(opponent, colors, outcomes, terminations, start, end, events, moves):
        if not opponent:
            return html.Div("Select an opponent above.", style={"color": COLORS["dim"], "fontSize": "12px"})
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        h = head_to_head(df_f, opponent)
        if h["total"] == 0:
            return html.Div(f"No games vs {opponent} in current filter.",
                            style={"color": COLORS["dim"], "fontSize": "12px"})

        avg_str = f" (avg rating {int(h['avg_opp_rating'])})" if h["avg_opp_rating"] else ""
        return html.Div([
            html.Div(f"{h['total']} games{avg_str}", style={"fontSize": "12px", "color": COLORS["muted"], "marginBottom": "10px"}),
            html.Div(className="h2h-stat-grid", children=[
                html.Div(className="h2h-stat", children=[
                    html.Div("Wins", className="h2h-stat-label"),
                    html.Div(str(h["win"]), className="h2h-stat-val win"),
                ]),
                html.Div(className="h2h-stat", children=[
                    html.Div("Draws", className="h2h-stat-label"),
                    html.Div(str(h["draw"]), className="h2h-stat-val draw"),
                ]),
                html.Div(className="h2h-stat", children=[
                    html.Div("Losses", className="h2h-stat-label"),
                    html.Div(str(h["loss"]), className="h2h-stat-val loss"),
                ]),
                html.Div(className="h2h-stat", children=[
                    html.Div("As White W/D/L", className="h2h-stat-label"),
                    html.Div(f"{h['as_white_w']}/{h['as_white_d']}/{h['as_white_l']}", className="h2h-stat-val"),
                ]),
                html.Div(className="h2h-stat", children=[
                    html.Div("As Black W/D/L", className="h2h-stat-label"),
                    html.Div(f"{h['as_black_w']}/{h['as_black_d']}/{h['as_black_l']}", className="h2h-stat-val"),
                ]),
                html.Div(className="h2h-stat", children=[
                    html.Div("Score", className="h2h-stat-label"),
                    html.Div(f"{h['win'] + .5*h['draw']:g}/{h['total']}", className="h2h-stat-val"),
                ]),
            ]),
            html.Div(style={"overflow": "auto", "maxHeight": "180px"}, children=[
                dash_table.DataTable(
                    columns=[
                        {"name": "Date",        "id": "Date"},
                        {"name": "Color",       "id": "Color"},
                        {"name": "Result",      "id": "Outcome"},
                        {"name": "My Rtg",      "id": "MyRating"},
                        {"name": "Opp Rtg",     "id": "OppRating"},
                        {"name": "Moves",       "id": "FullMoves"},
                        {"name": "Termination", "id": "Termination"},
                    ],
                    data=h["game_rows"],
                    page_size=20, sort_action="native",
                    style_table={"overflowX": "auto"},
                    style_cell={**_TABLE_CELL, "fontSize": "11px", "padding": "5px 8px"},
                    style_header=_TABLE_HEADER,
                    style_data_conditional=[
                        {"if": {"filter_query": '{Outcome} = "Win"'},
                         "backgroundColor": "rgba(63,185,80,.13)"},
                        {"if": {"filter_query": '{Outcome} = "Loss"'},
                         "backgroundColor": "rgba(248,81,73,.11)"},
                        {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]},
                    ],
                ),
            ]),
        ])

    # ------------------------------------------------------------------ #
    # Rating bucket bar                                                     #
    # ------------------------------------------------------------------ #
    @app.callback(Output("rating-bucket-bar", "figure"), FILTER_INPUTS)
    def update_bucket(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        buckets = opponent_rating_bucket_summary(df_f)
        if buckets.empty:
            return empty_fig("Need rated games")
        long = buckets.melt(
            id_vars=["Bucket"],
            value_vars=["Win", "Draw", "Loss"],
            var_name="Outcome", value_name="Count",
        )
        fig = px.bar(
            long, x="Bucket", y="Count", color="Outcome",
            barmode="stack", color_discrete_map=WDL_COLOR_MAP,
        )
        apply_dark_theme(fig, xaxis_title="Opponent rating difference", yaxis_title="Games", legend_title="Outcome")
        return fig

    # ------------------------------------------------------------------ #
    # Outcome vs rating scatter                                             #
    # ------------------------------------------------------------------ #
    @app.callback(Output("outcome-scatter", "figure"), FILTER_INPUTS)
    def update_scatter(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        sc = outcome_vs_rating_data(df_f)
        if sc.empty:
            return empty_fig("Need rated games")
        fig = px.scatter(
            sc, x="OpponentRatingNum", y="OutcomeNum",
            color="Outcome", color_discrete_map=WDL_COLOR_MAP,
            hover_data={"Opponent": True, "Date": True,
                        "OutcomeNum": False, "OpponentRatingNum": True},
            labels={"OpponentRatingNum": "Opponent Rating", "OutcomeNum": "Outcome"},
        )
        fig.update_traces(
            marker=dict(size=8, opacity=0.8, line=dict(width=1, color=COLORS["border"])),
        )
        # Replace OutcomeNum y-axis with readable labels
        fig.update_yaxes(
            tickvals=[0, 0.5, 1],
            ticktext=["Loss", "Draw", "Win"],
            range=[-0.15, 1.15],
        )
        apply_dark_theme(fig, xaxis_title="Opponent rating", legend_title="Outcome")
        return fig

    # ------------------------------------------------------------------ #
    # Game length histogram                                                 #
    # ------------------------------------------------------------------ #
    @app.callback(Output("length-hist", "figure"), FILTER_INPUTS)
    def update_length_hist(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        hist_df, _ = game_length_data(df_f)
        if hist_df.empty:
            return empty_fig("No data")
        fig = px.histogram(
            hist_df, x="FullMoves", color="Outcome",
            barmode="overlay", nbins=30,
            color_discrete_map=WDL_COLOR_MAP,
            opacity=0.75,
        )
        apply_dark_theme(fig, xaxis_title="Moves", yaxis_title="Games", legend_title="Outcome")
        return fig

    # ------------------------------------------------------------------ #
    # Game length stats card                                               #
    # ------------------------------------------------------------------ #
    @app.callback(Output("length-stats", "children"), FILTER_INPUTS)
    def update_length_stats(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        _, avgs = game_length_data(df_f)
        if not avgs:
            return html.Div("No data", style={"color": COLORS["dim"]})

        def _row(label, val, cls=""):
            if val is None:
                return None
            return html.Div(style={"display": "flex", "justifyContent": "space-between",
                                   "padding": "8px 0", "borderBottom": f"1px solid {COLORS['border']}"},
                            children=[
                html.Span(label, style={"color": COLORS["muted"], "fontSize": "13px"}),
                html.Span(f"{val} moves", className=cls,
                          style={"fontWeight": "700", "fontSize": "16px"}),
            ])

        rows = [
            _row("Avg moves (Wins)",   avgs.get("Win"),  "text-win"),
            _row("Avg moves (Draws)",  avgs.get("Draw"), "text-muted"),
            _row("Avg moves (Losses)", avgs.get("Loss"), "text-loss"),
        ]
        return html.Div([r for r in rows if r is not None])

    # ------------------------------------------------------------------ #
    # Activity — monthly bar                                               #
    # ------------------------------------------------------------------ #
    @app.callback(Output("monthly-bar", "figure"), FILTER_INPUTS)
    def update_monthly(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        m, _ = activity_data(df_f)
        if m.empty:
            return empty_fig("No dated games")
        fig = px.bar(
            m, x="YearMonth", y="Games",
            custom_data=["WinRate"],
            color_discrete_sequence=[COLORS["primary"]],
        )
        fig.update_traces(
            hovertemplate="%{x}<br>%{y} games<br>Win rate: %{customdata[0]:.1f}%<extra></extra>",
        )
        apply_dark_theme(fig, yaxis_title="Games")
        fig.update_xaxes(tickangle=45, automargin=True)
        return fig

    # ------------------------------------------------------------------ #
    # Activity — day of week                                               #
    # ------------------------------------------------------------------ #
    @app.callback(Output("dow-bar", "figure"), FILTER_INPUTS)
    def update_dow(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        _, dw = activity_data(df_f)
        if dw.empty:
            return empty_fig("No dated games")
        fig = px.bar(
            dw, x="DayOfWeek", y="WinRate",
            custom_data=["Games"],
            color="WinRate",
            color_continuous_scale=[[0, COLORS["loss"]], [0.5, COLORS["muted"]], [1, COLORS["win"]]],
            range_color=[30, 70],
        )
        fig.update_traces(
            hovertemplate="%{x}<br>Win rate: %{y:.1f}%<br>(%{customdata[0]} games)<extra></extra>",
        )
        apply_dark_theme(fig, yaxis_title="Win rate (%)")
        fig.update_coloraxes(showscale=False)
        fig.add_hline(y=50, line_dash="dash", line_color=COLORS["muted"], line_width=1)
        return fig

    # ------------------------------------------------------------------ #
    # Event stacked bar                                                     #
    # ------------------------------------------------------------------ #
    @app.callback(Output("event-bar", "figure"), FILTER_INPUTS)
    def update_event_bar(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        ev = event_summary(df_f)
        if ev.empty:
            return empty_fig("No event data")
        long = ev.tail(20).melt(
            id_vars=["Event"],
            value_vars=["Win", "Draw", "Loss"],
            var_name="Outcome", value_name="Count",
        )
        fig = px.bar(long, x="Event", y="Count", color="Outcome",
                     barmode="stack", color_discrete_map=WDL_COLOR_MAP)
        apply_dark_theme(fig, legend_title="Outcome")
        fig.update_xaxes(tickangle=35, automargin=True)
        return fig

    # ------------------------------------------------------------------ #
    # Event table + tournament detail                                       #
    # ------------------------------------------------------------------ #
    @app.callback(Output("event-table", "data"), FILTER_INPUTS)
    def update_event_table(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        return event_summary(df_f).to_dict("records")

    @app.callback(
        Output("tournament-detail", "children"),
        Input("event-table", "selected_rows"),
        Input("event-table", "data"),
        FILTER_INPUTS,
    )
    def update_tournament_detail(selected_rows, table_data, colors, outcomes,
                                 terminations, start, end, events, moves):
        if not selected_rows or not table_data:
            return None
        row = table_data[selected_rows[0]]
        event_name = row.get("Event", "")
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        ev_games = df_f[df_f["Event"] == event_name].sort_values(["Date", "Round"], na_position="last")
        if ev_games.empty:
            return None

        pr = performance_rating_stats(ev_games)
        pr_str = f"  |  Performance rating: {pr['performance_rating']}" if pr["performance_rating"] else ""

        cols = [
            {"name": "Round",       "id": "Round"},
            {"name": "Color",       "id": "Color"},
            {"name": "Opponent",    "id": "Opponent"},
            {"name": "Opp Rating",  "id": "OpponentRating"},
            {"name": "Result",      "id": "Result"},
            {"name": "Outcome",     "id": "Outcome"},
            {"name": "Termination", "id": "Termination"},
            {"name": "Moves",       "id": "FullMoves"},
        ]
        return html.Div([
            html.Div(
                f"{event_name}  —  {row.get('Score', '')} points{pr_str}",
                style={"fontWeight": "600", "marginBottom": "8px",
                       "fontSize": "13px", "color": COLORS["text"]},
            ),
            dash_table.DataTable(
                columns=cols,
                data=ev_games[["Round", "Color", "Opponent", "OpponentRating",
                               "Result", "Outcome", "Termination", "FullMoves"]].to_dict("records"),
                page_size=20, sort_action="native",
                style_table={"overflowX": "auto"},
                style_cell={**_TABLE_CELL, "fontSize": "11px"},
                style_header=_TABLE_HEADER,
                style_data_conditional=[
                    {"if": {"filter_query": '{Outcome} = "Win"'},
                     "backgroundColor": "rgba(63,185,80,.13)"},
                    {"if": {"filter_query": '{Outcome} = "Loss"'},
                     "backgroundColor": "rgba(248,81,73,.11)"},
                    {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]},
                ],
            ),
        ], style={
            "border": f"1px solid {COLORS['border']}",
            "borderRadius": "8px", "padding": "12px",
            "background": COLORS["card"], "marginTop": "12px",
        })

    # ------------------------------------------------------------------ #
    # Milestones                                                            #
    # ------------------------------------------------------------------ #
    @app.callback(Output("milestones-content", "children"), FILTER_INPUTS)
    def update_milestones(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        ms = compute_milestones(df_f)
        if not ms:
            return html.Div("No milestone data", style={"color": COLORS["dim"]})
        rows = [
            html.Div(className="milestone-row", children=[
                html.Div(className=f"milestone-dot {m['kind']}"),
                html.Div(m["date"],                 className="milestone-date"),
                html.Div(f"#{m['game_num']}",       className="milestone-num"),
                html.Div(m["description"],           className="milestone-desc"),
            ])
            for m in ms
        ]
        return html.Div(rows, className="milestone-list")

    # ------------------------------------------------------------------ #
    # All games table                                                       #
    # ------------------------------------------------------------------ #
    @app.callback(Output("games-table", "data"), FILTER_INPUTS)
    def update_games_table(colors, outcomes, terminations, start, end, events, moves):
        df_f = _get_filtered(colors, outcomes, terminations, start, end, events, moves)
        cols = [c for c in _DISPLAY_COLS if c in df_f.columns]
        return df_f[cols].to_dict("records")
