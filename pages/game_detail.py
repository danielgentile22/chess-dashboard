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
from components import (
    USCF_RATING_SYSTEM_LABELS,
    content_card,
    empty_state,
    page_header,
    uscf_member_url,
)

dash.register_page(
    __name__,
    path_template="/game/<chapter_id>",
    name="Game",
    title="Game — Chess Stats",
    nav=False,  # reached by clicking a Game, not from the nav tabs
)


def embed_url(chapter_url: str) -> str:
    """
    The dark-theme Lichess embed URL for a Chapter (issue #43 [E9]).

    ChapterURL is https://lichess.org/study/{study}/{chapter}; the embeddable
    board lives at https://lichess.org/study/embed/{study}/{chapter}. We append
    Lichess's ``bg=dark`` background parameter so the board matches the dark
    dashboard instead of flashbanging the viewer with a light board.

    A blank ChapterURL yields ``""`` so the caller can skip the iframe.
    """
    if not chapter_url or not chapter_url.strip():
        return ""
    embed = chapter_url.replace("lichess.org/study/", "lichess.org/study/embed/")
    # Append the dark-background param, respecting any query string already there.
    separator = "&" if "?" in embed else "?"
    return f"{embed}{separator}bg=dark"


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


def _uscf_facts_card(game: pd.Series) -> html.Div | None:
    """
    The USCF half of a matched Game (issue #28): what USCF officially recorded
    — Rated Event, Section, rating system, and the opponent as USCF knows them,
    linking to their page on the ratings site.

    None for unmatched Games: no USCF facts are ever invented (ADR 0003).
    """
    if not game.get("UscfMatched"):
        return None

    system_code = str(game.get("UscfRatingSystem") or "")
    opponent_id = str(game.get("UscfOpponentId") or "")
    # How the match was made (issue #29): name matches deserve an eyeball
    matched_by = {"id": "opponent ID", "name": "opponent name"}.get(
        str(game.get("UscfMatchedBy") or ""), ""
    )
    rows = [
        _meta_row("Rated Event", game.get("UscfEventName")),
        _meta_row("Section", game.get("UscfSection")),
        _meta_row("Rating system",
                  USCF_RATING_SYSTEM_LABELS.get(system_code, system_code)),
        _meta_row("Matched by", matched_by),
    ]

    # The sources disagree (issue #30): badge it and point at Reconciliation.
    # The page itself keeps displaying the Lichess version of every fact.
    conflict_badge = None
    if game.get("UscfColorConflict"):
        conflict_badge = dcc.Link(
            className="uscf-conflict-badge", href="/reconciliation", children=[
                html.Span("⚠", className="uscf-conflict-icon"),
                html.Span("USCF disagrees about this game — review it in "
                          "Reconciliation"),
            ],
        )

    return content_card(
        "USCF record",
        conflict_badge,
        *[r for r in rows if r is not None],
        html.Div(className="meta-row", children=[
            html.Span("Opponent", className="meta-label"),
            html.Span(className="meta-value", children=[
                html.A(
                    [str(game.get("UscfOpponentName") or ""),
                     html.Span(f" #{opponent_id}", className="uscf-member-id")],
                    href=uscf_member_url(opponent_id),
                    target="_blank", className="uscf-opponent-link",
                ),
            ]),
        ]),
    )


def _forfeit_tag(game: pd.Series) -> html.Div | None:
    """
    The visible Forfeit tag (issue #29): this Chapter exists, but no game was
    played — the opponent never showed, so USCF never rated it.
    """
    if not game.get("Forfeit"):
        return None
    return html.Div(className="forfeit-tag", children=[
        html.Span("Forfeit", className="forfeit-tag-label"),
        html.Span(
            " — opponent no-show; USCF never rated this game. It counts toward "
            "the event score but not toward win rate, streaks, or opening stats.",
            className="forfeit-tag-hint",
        ),
    ])


def _critical_moment_section(game: pd.Series) -> html.Div | None:
    """
    The Game's critical-moment headline (issue #57 [F1]) — the single biggest
    win-probability swing, framed for Daniel, shown alongside the board.

    An analysed Game shows the verdict ("Won after your opponent's blunder on
    move 16 (Bd4)"); an un-analysed Chapter shows a quiet awaiting-analysis
    hint that teaches the one click, so the page degrades cleanly and never
    blanks (ADR 0004).  A Game with no Chapter at all shows nothing.
    """
    chapter_url = str(game.get("ChapterURL") or "")
    analysis = data.get_game_analysis(chapter_url)
    moment = analysis.critical_moment

    if analysis.analyzed and moment is not None:
        tone = "player" if moment.by_player else "opponent"
        move = next((m for m in analysis.moves if m.ply == moment.ply), None)
        detail = [f"eval {moment.eval_before:+.2f} → {moment.eval_after:+.2f}"]
        if move is not None and move.best_move:
            detail.append(f"engine preferred {move.best_move}")
        return html.Div(className=f"critical-moment-banner {tone}", children=[
            html.Span("Critical moment", className="critical-moment-label"),
            html.Span(moment.headline, className="critical-moment-headline"),
            html.Span(" · ".join(detail), className="critical-moment-detail"),
        ])

    # Not analysed — never blank; teach the one click (PRD #54).
    if not chapter_url.strip():
        return None
    return html.Div(className="awaiting-analysis-hint", children=[
        html.Span("Awaiting analysis", className="awaiting-analysis-label"),
        html.Span(
            " — request computer analysis on this Chapter on Lichess and the "
            "critical-moment verdict appears here after the next Sync.",
            className="awaiting-analysis-text",
        ),
    ])


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

    chapter_url = str(game.get("ChapterURL") or "")
    board_embed = embed_url(chapter_url)
    if board_embed:
        board_card = html.Div(className="game-board-card", children=[
            html.Iframe(
                src=board_embed,
                className="lichess-embed",
                allow="fullscreen",
            ),
        ])
    else:
        # No ChapterURL — render the board card gracefully, never a broken iframe.
        board_card = html.Div(className="game-board-card", children=[
            empty_state(
                "♟",
                "No board for this game",
                "This Game has no Lichess Chapter, so there's nothing to embed.",
            ),
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

        # No game was actually played (issue #29) — say so prominently
        _forfeit_tag(game),

        # The why-I-won/lost verdict in context with the board (issue #57)
        _critical_moment_section(game),

        html.Div(className="game-detail-grid", children=[
            # The Chapter's interactive board — annotations and variations
            # playable in place, in dark theme (issue #43)
            board_card,

            # Everything known about the Game, alongside the board
            html.Div(className="game-detail-side", children=[
                _lessons_card(game),
                _metadata_card(game),
                # The USCF half, when this Game is matched (issue #28)
                _uscf_facts_card(game),
                html.A(
                    [html.I(className="bi bi-box-arrow-up-right"), " Open on Lichess"],
                    href=chapter_url, target="_blank",
                    className="lichess-open-btn",
                ) if chapter_url else None,
            ]),
        ]),
    ])
