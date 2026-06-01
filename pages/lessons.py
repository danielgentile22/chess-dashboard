"""
pages/lessons.py
================
The Lessons page (issue #12) — every takeaway written on Lichess, in one place.

Each Lesson shows its text, the Game it came from, and that Game's Tags,
newest first.  A tag summary strip (canonical taxonomy first, then freeform)
doubles as the Tag filter; an opponent picker and the global filter drawer
narrow things further.  Lessons link to their Game's detail view.

Lessons are written on Lichess only (ADR 0002): a chapter comment starting
with ``Lesson:``.  The empty state teaches that convention.
"""
from __future__ import annotations

import dash
from dash import ALL, Input, Output, State, callback, ctx, dcc, html, no_update

import data
from components import content_card, game_detail_path, page_header
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import CANONICAL_TAGS, lessons_table, tag_counts

dash.register_page(
    __name__, path="/lessons", name="Lessons", title="Lessons — Chess Stats", order=6,
)


def _opponent_options() -> list[dict]:
    df = data.get_df()
    if df.empty:
        return []
    has_lessons = df[df["Lessons"].map(bool)] if "Lessons" in df.columns else df
    return [{"label": o, "value": o}
            for o in sorted(has_lessons["Opponent"].dropna().unique()) if o]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Lessons", "What your games have taught you"),

        # Selected-tag filter state (toggled by clicking chips in the strip)
        dcc.Store(id="lesson-selected-tags", data=[]),

        # Tag summary strip — counts + the Tag filter in one
        html.Div(id="lesson-tag-strip", className="lesson-tag-strip"),

        # Page-local opponent filter
        html.Div(className="lesson-controls", children=[
            dcc.Dropdown(
                id="lesson-opponent-filter",
                options=_opponent_options(),
                placeholder="All opponents",
                clearable=True,
            ),
        ]),

        # The Lessons themselves
        html.Div(id="lessons-list"),
    ])


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _lesson_card(row) -> html.Div:
    outcome = str(row["Outcome"] or "")
    meta_bits = [
        f"vs {row['Opponent']}" if row["Opponent"] else "",
        outcome,
        str(row["Event"] or ""),
        str(row["Date"] or ""),
    ]
    detail_path = game_detail_path(row["ChapterURL"])

    return html.Div(className="lesson-card", children=[
        html.Div(className="lesson-quote", children=[
            html.Span("💡", className="lesson-bulb"),
            html.Span(row["Lesson"], className="lesson-text"),
        ]),
        html.Div(className="lesson-card-footer", children=[
            html.Span(
                "  ·  ".join(b for b in meta_bits if b),
                className=f"lesson-meta outcome-{outcome.lower()}",
            ),
            html.Span(className="lesson-card-tags", children=[
                html.Span(f"#{t}", className="tag-chip tag-chip-small")
                for t in row["Tags"]
            ]),
            dcc.Link("View game →", href=detail_path, className="lesson-game-link")
            if detail_path else None,
        ]),
    ])


def _convention_explainer() -> html.Div:
    """The empty state: how to write Lessons on Lichess (ADR 0002)."""
    return html.Div(className="empty-state", children=[
        html.Div("♘", className="empty-state-glyph"),
        html.Div("No Lessons here yet", className="empty-state-title"),
        html.Div([
            "On Lichess, write a comment on any Chapter starting with ",
            html.Code("Lesson:"),
            " — it becomes that Game's Lesson here after the next Sync.",
        ], className="empty-state-line"),
        html.Div([
            "Add hashtags like ",
            html.Code("#endgame"), " or ", html.Code("#time-trouble"),
            " anywhere in your comments to categorize what the game taught you:",
        ], className="empty-state-line"),
        html.Div(className="tag-strip empty-state-tags", children=[
            html.Span(f"#{t}", className="tag-chip") for t in CANONICAL_TAGS
        ]),
    ])


def _tag_strip(df_filtered, selected_tags: list[str]) -> list:
    """Clickable tag chips with counts; canonical taxonomy first."""
    counts = tag_counts(df_filtered)
    if not counts:
        return [html.Span("No Tags in your archive yet",
                          className="lesson-strip-empty")]
    chips = []
    for item in counts:
        active = " active" if item["tag"] in selected_tags else ""
        freeform = "" if item["canonical"] else " freeform"
        chips.append(html.Button(
            [f"#{item['tag']}", html.Span(str(item["count"]), className="tag-count")],
            id={"type": "lesson-tag", "tag": item["tag"]},
            className=f"tag-chip tag-chip-button{active}{freeform}",
            n_clicks=0,
        ))
    return chips


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output("lesson-opponent-filter", "options"),
    Input("sync-store", "data"),
    prevent_initial_call=True,  # the layout holds correct values at page load
)
def update_lesson_opponent_options(_sync):
    """Keep the opponent picker in step with the data after a Sync."""
    return _opponent_options()


@callback(
    Output("lesson-selected-tags", "data"),
    Input({"type": "lesson-tag", "tag": ALL}, "n_clicks"),
    State("lesson-selected-tags", "data"),
    prevent_initial_call=True,
)
def toggle_lesson_tag(n_clicks_list, selected):
    """Clicking a chip in the tag strip toggles that Tag filter."""
    # The strip re-renders after every change, resetting n_clicks to None/0 —
    # only a real click (a truthy value) may toggle.
    if not any(n for n in n_clicks_list if n):
        return no_update
    tag = ctx.triggered_id["tag"]
    selected = list(selected or [])
    if tag in selected:
        selected.remove(tag)
    else:
        selected.append(tag)
    return selected


@callback(
    Output("lessons-list", "children"),
    Output("lesson-tag-strip", "children"),
    Input("lesson-selected-tags", "data"),
    Input("lesson-opponent-filter", "value"),
    FILTER_INPUTS,
)
def update_lessons_page(selected_tags, opponent, colors, outcomes, terminations,
                        start, end, events, moves, _sync=None):
    """The Lessons list + tag strip, honoring page filters and global filters."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves)
    selected_tags = selected_tags or []

    strip = _tag_strip(df_f, selected_tags)

    lessons = lessons_table(df_f, tags=selected_tags or None, opponent=opponent or None)
    if lessons.empty:
        return _convention_explainer(), strip

    cards = [_lesson_card(row) for _, row in lessons.iterrows()]
    count_label = html.Div(
        f"{len(lessons)} lesson{'s' if len(lessons) != 1 else ''}",
        className="lesson-count",
    )
    return content_card("Every lesson, newest first", count_label, *cards), strip
