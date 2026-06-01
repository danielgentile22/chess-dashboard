"""
pages/game_detail.py
====================
The Game detail view (issue #11) — one Game, in full.

An embedded interactive Lichess board (the Chapter's embed URL, with Daniel's
annotations and variations playable in place) alongside the Game's Lessons,
Tags, and metadata, plus an "Open on Lichess" button.

Reached by clicking a Game anywhere in the app (Games table, head-to-head
list, event detail) — never from the nav tabs (``nav=False``). The URL is
deep-linkable: /game/<chapter-id>.
"""
from __future__ import annotations

import dash
import pandas as pd
from dash import dcc, html

import data
from components import content_card, empty_state, page_header

dash.register_page(
    __name__,
    path_template="/game/<chapter_id>",
    name="Game",
    title="Game — Chess Stats",
    nav=False,  # reached by clicking a Game, not from the nav tabs
)


def embed_url(chapter_url: str) -> str:
    """
    The Lichess embed URL for a Chapter.

    ChapterURL is https://lichess.org/study/{study}/{chapter}; the embeddable
    board lives at https://lichess.org/study/embed/{study}/{chapter}.
    """
    return chapter_url.replace("lichess.org/study/", "lichess.org/study/embed/")


def _find_game(chapter_id: str | None) -> pd.Series | None:
    """Look up a Game by the chapter-id segment of its ChapterURL."""
    df = data.get_df()
    if df.empty or not chapter_id:
        return None
    matches = df[df["ChapterURL"].str.endswith(f"/{chapter_id}")]
    return matches.iloc[0] if len(matches) else None


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _meta_row(label: str, value) -> html.Div | None:
    if value is None or str(value).strip() in ("", "?"):
        return None
    return html.Div(className="meta-row", children=[
        html.Span(label, className="meta-label"),
        html.Span(str(value), className="meta-value"),
    ])


def _metadata_card(game: pd.Series) -> html.Div:
    eco_opening = " · ".join(
        x for x in [str(game.get("ECO", "")), str(game.get("Opening", ""))] if x.strip()
    )
    rows = [
        _meta_row("Date", game.get("Date")),
        _meta_row("Event", game.get("Event")),
        _meta_row("Round", game.get("Round")),
        _meta_row("You played", game.get("Color")),
        _meta_row("Result", game.get("Result")),
        _meta_row("Termination", game.get("Termination")),
        _meta_row("Your rating", game.get("PlayerRating")),
        _meta_row("Opponent rating", game.get("OpponentRating")),
        _meta_row("Opening", eco_opening),
        _meta_row("Moves", game.get("FullMoves")),
        _meta_row("Study", game.get("StudyName")),
    ]
    return content_card("Game", *[r for r in rows if r is not None])


def _lessons_card(game: pd.Series) -> html.Div:
    lessons = game.get("Lessons") or []
    if lessons:
        body: list = [
            html.Div(className="lesson-quote", children=[
                html.Span("💡", className="lesson-bulb"),
                html.Span(lesson, className="lesson-text"),
            ])
            for lesson in lessons
        ]
    else:
        # Games without a Lesson render gracefully (acceptance criterion) and
        # teach the convention (ADR 0002).
        body = [html.Div(className="lesson-empty", children=[
            html.Div("No Lesson written for this game yet.", className="lesson-empty-title"),
            html.Div([
                "Add a comment starting with ", html.Code("Lesson:"),
                " to the Chapter on Lichess and it will appear here after the next Sync.",
            ], className="lesson-empty-hint"),
        ])]

    tags = game.get("Tags") or []
    if tags:
        body.append(html.Div(className="tag-strip", children=[
            html.Span(f"#{t}", className="tag-chip") for t in tags
        ]))

    n = len(lessons)
    title = "Lesson" if n <= 1 else f"Lessons ({n})"
    return content_card(title, *body)


def layout(chapter_id: str | None = None, **kwargs) -> html.Div:
    game = _find_game(chapter_id)

    if game is None:
        return html.Div(className="page", children=[
            page_header("Game not found"),
            empty_state(
                "♚",
                "This Game is not in your archive",
                "It may have been removed from its Study, or your data may be out of date.",
                "Try a Sync, or head back to the Games page.",
            ),
            dcc.Link("← Back to all games", href="/games", className="back-link"),
        ])

    opponent = str(game.get("Opponent") or "Unknown opponent")
    outcome = str(game.get("Outcome") or "")
    subtitle_bits = [str(game.get("Event") or ""),
                     f"Round {game['Round']}" if str(game.get("Round") or "").strip() else "",
                     str(game.get("Date") or "")]
    subtitle = "  ·  ".join(x for x in subtitle_bits if x)

    return html.Div(className="page", children=[
        dcc.Link("← All games", href="/games", className="back-link"),

        html.Div(className="game-detail-header", children=[
            page_header(f"vs {opponent}", subtitle),
            html.Div(outcome, className=f"outcome-badge {outcome.lower()}"),
        ]),

        html.Div(className="game-detail-grid", children=[
            # The Chapter's interactive board — annotations and variations
            # playable in place
            html.Div(className="game-board-card", children=[
                html.Iframe(
                    src=embed_url(str(game["ChapterURL"])),
                    className="lichess-embed",
                    allow="fullscreen",
                ),
            ]),

            # Everything known about the Game, alongside the board
            html.Div(className="game-detail-side", children=[
                _lessons_card(game),
                _metadata_card(game),
                html.A(
                    [html.I(className="bi bi-box-arrow-up-right"), " Open on Lichess"],
                    href=str(game["ChapterURL"]), target="_blank",
                    className="lichess-open-btn",
                ),
            ]),
        ]),
    ])
