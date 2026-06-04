"""
pages/lessons.py
================
The Lessons page (issue #12) — every takeaway written on Lichess, in one place.

Each Lesson shows its text, the Game it came from, and that Game's Tags,
newest first.  A tag summary strip (canonical taxonomy first, then freeform)
doubles as the Tag filter; an opponent picker and the global filter drawer
narrow things further.  Lessons link to their Game's detail view.

Two insights live on top of the list:

  * recurring weakness callouts (issue #18) — Tags that keep showing up in
    recent losses
  * pre-game review mode (issue #19) — ``/lessons?review=1[&opponent=X]``
    opens a full-screen, card-by-card walk through the most relevant
    Lessons, prioritized by weakness → opponent → recency

Lessons are written on Lichess only (ADR 0002): a chapter comment starting
with ``Lesson:``.  The empty state teaches that convention.
"""
from __future__ import annotations

import dash
from dash import ALL, Input, Output, State, callback, ctx, dcc, html, no_update

import data
from components import (
    content_card,
    lesson_card,
    page_header,
    tag_chips,
    weakness_callout,
)
from filters import FILTER_INPUTS, get_filtered
from pgn_stats_core import (
    CANONICAL_TAGS,
    lessons_table,
    recurring_weaknesses,
    review_queue,
    tag_counts,
)

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

def layout(review: str | None = None, opponent: str | None = None, **kwargs) -> html.Div:
    """The Lessons page; ``?review=1[&opponent=X]`` adds the review overlay."""
    children = [
        page_header("Lessons", "What your games have taught you"),

        # Pre-game review mode launcher (issue #19)
        dcc.Link(
            [html.Span("♟", className="review-launch-icon"), "Review before playing"],
            href="/lessons?review=1",
            className="review-launch-btn",
        ),

        # Recurring weaknesses (issue #18) — what's actually costing you games
        html.Div(id="weakness-callouts"),

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
    ]

    # Review mode rides on top of the page (issue #19).  The page's entry
    # animation must be off in this case: its transform creates a CSS
    # containing block that would trap the fixed-position overlay.
    if review:
        children.append(_review_overlay(opponent or None))
        return html.Div(className="page page-no-anim", children=children)

    return html.Div(className="page", children=children)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

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

@callback(Output("weakness-callouts", "children"), FILTER_INPUTS)
def update_weakness_callouts(colors, outcomes, terminations, start, end,
                             events, moves, _sync=None, lens=None):
    """Recurring weaknesses (issue #18). Silent below threshold."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    callouts = recurring_weaknesses(df_f)
    if not callouts:
        return None
    return html.Div(className="weakness-callouts", children=[
        weakness_callout(c) for c in callouts
    ])


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
                        start, end, events, moves, _sync=None, lens=None):
    """The Lessons list + tag strip, honoring page filters and global filters."""
    df_f = get_filtered(colors, outcomes, terminations, start, end, events, moves, lens)
    selected_tags = selected_tags or []

    strip = _tag_strip(df_f, selected_tags)

    lessons = lessons_table(df_f, tags=selected_tags or None, opponent=opponent or None)
    if lessons.empty:
        return _convention_explainer(), strip

    cards = [lesson_card(row) for _, row in lessons.iterrows()]
    count_label = html.Div(
        f"{len(lessons)} lesson{'s' if len(lessons) != 1 else ''}",
        className="lesson-count",
    )
    return content_card("Every lesson, newest first", count_label, *cards), strip


# ---------------------------------------------------------------------------
# Pre-game review mode (issue #19)
# ---------------------------------------------------------------------------

def _review_overlay(opponent: str | None) -> html.Div:
    """
    The full-screen review overlay: built at page-render time from the whole
    archive (the five-minutes-before-a-round ritual reviews everything
    relevant, not the current filter selection).
    """
    queue = review_queue(data.get_df(), opponent=opponent)
    # Date_dt (a Timestamp) stays out of the browser store; Date is enough
    cards = [{k: v for k, v in card.items() if k != "Date_dt"} for card in queue]

    if not cards:
        body: list = [
            html.Div(className="review-card review-empty-card", children=[
                html.Div("♘", className="empty-state-glyph"),
                html.Div("Nothing to review yet", className="review-done-title"),
                html.Div([
                    "Write a comment starting with ", html.Code("Lesson:"),
                    " on any Chapter on Lichess and it will show up here.",
                ], className="review-done-line"),
            ]),
        ]
    else:
        body = [
            # The card itself is one giant tap target: tap anywhere to advance
            html.Button(id="review-tap", className="review-card-tap", n_clicks=0,
                        children=html.Div(id="review-card-zone")),
            # Controls live at the bottom — thumb reach on a phone
            html.Div(className="review-controls", children=[
                html.Button("← Back", id="review-prev",
                            className="review-prev-btn", n_clicks=0),
                html.Div(id="review-progress", className="review-progress"),
                html.Div("tap card to continue", className="review-tap-hint"),
            ]),
            dcc.Store(id="review-queue-store", data=cards),
            dcc.Store(id="review-index", data=0),
        ]

    return html.Div(className="review-overlay", children=[
        html.Div(className="review-topbar", children=[
            html.Div(className="review-titles", children=[
                html.Span("Pre-game review", className="review-title"),
                html.Span(f"vs {opponent}" if opponent else "",
                          className="review-subtitle"),
            ]),
            dcc.Link("✕ Done", href="/lessons", className="review-done-link"),
        ]),
        *body,
    ])


@callback(
    Output("review-card-zone", "children"),
    Output("review-progress", "children"),
    Input("review-index", "data"),
    State("review-queue-store", "data"),
)
def render_review_card(index, queue):
    """The current card: one Lesson, why it's here, and where it came from."""
    if not queue:
        return None, ""
    index, total = index or 0, len(queue)

    if index >= total:
        # Walked through everything — the send-off
        done = html.Div(className="review-card review-done-card", children=[
            html.Div("♟", className="review-done-glyph"),
            html.Div("That's everything.", className="review-done-title"),
            html.Div("Now go play.", className="review-done-line"),
        ])
        return done, f"{total} / {total}"

    card = queue[index]
    weakness = card.get("priority") == 0
    meta = "  ·  ".join(b for b in [
        f"vs {card['Opponent']}" if card["Opponent"] else "",
        str(card["Outcome"] or ""),
        str(card["Date"] or ""),
    ] if b)

    rendered = html.Div(className="review-card", children=[
        html.Div(card["reason"],
                 className="review-reason" + (" weakness" if weakness else "")),
        html.Div(card["Lesson"], className="review-lesson-text"),
        html.Div(className="review-card-footer", children=[
            html.Div(meta, className="review-card-meta"),
            html.Div(className="review-card-tags", children=tag_chips(
                card["Tags"], card.get("TagSources"), base_class="tag-chip tag-chip-small",
            )),
        ]),
    ])
    return rendered, f"{index + 1} / {total}"


@callback(
    Output("review-index", "data"),
    Input("review-tap", "n_clicks"),
    Input("review-prev", "n_clicks"),
    State("review-index", "data"),
    State("review-queue-store", "data"),
    prevent_initial_call=True,
)
def navigate_review(_tap, _prev, index, queue):
    """Tap the card to advance; Back goes one card back. Index can sit one
    past the end (the done state) but never beyond."""
    if not queue:
        return no_update
    index = index or 0
    if ctx.triggered_id == "review-prev":
        return max(0, index - 1)
    return min(len(queue), index + 1)
