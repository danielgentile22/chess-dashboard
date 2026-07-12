"""
pages/game_detail.py
====================
The Game detail view (issue #11) — one Game, in full.

An interactive board rendered by Lichess's open-source pgn-viewer (a local
asset — issue #60 [F6]), behind a Game / My Analysis view switcher: Game is the
bare replay, My Analysis (only when Daniel annotated the Chapter himself) plays
his variations and comments in place.  Alongside it sit the Game's Lessons,
Tags, and metadata, plus an "Open on Lichess" button.

Reached by clicking a Game anywhere in the app (Games table, head-to-head
list, event detail) — never from the nav tabs (``nav=False``). The URL is
deep-linkable: /game/<chapter-id>.
"""
from __future__ import annotations

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html

import data
from components import (
    USCF_RATING_SYSTEM_LABELS,
    content_card,
    empty_state,
    page_header,
    tag_chips,
    uscf_member_url,
)
from engine_analysis_core import win_pct_from_cp
from pgn_stats_core import has_my_analysis, mainline_movetext
from styles import COLORS, apply_dark_theme, rgba

dash.register_page(
    __name__,
    path_template="/game/<chapter_id>",
    name="Game",
    title="Game — Chess Dashboard",
    nav=False,  # reached by clicking a Game, not from the nav tabs
)


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
        # Engine-emitted Tags (issue #62 [F4]) render distinguishably from the
        # ones Daniel hand-wrote, via the per-Game source map.
        body.append(html.Div(
            className="tag-strip",
            children=tag_chips(tags, game.get("TagSources")),
        ))

    n = len(lessons)
    title = "Lesson" if n <= 1 else f"Lessons ({n})"
    return content_card(title, *body)


def _game_pgn(game: pd.Series, movetext: str) -> str:
    """
    Assemble a self-contained PGN (headers + movetext) for the pgn-viewer.

    The board is rendered locally by Lichess's pgn-viewer (issue #60 [F6]), so
    it needs the Game as a standalone PGN rather than an embed URL.  The headers
    give the viewer the players, result, and date; *movetext* supplies the moves
    (clean for the Game view, annotated for My Analysis).
    """
    player = data.get_player()
    color = str(game.get("Color") or "")
    opponent = str(game.get("Opponent") or "?")
    white = player if color == "White" else opponent
    black = opponent if color == "White" else player
    headers = [
        ("Event", game.get("Event")),
        ("Date", game.get("Date")),
        ("Round", game.get("Round")),
        ("White", white),
        ("Black", black),
        ("Result", game.get("Result")),
    ]
    # A custom start position (Studies allow one) must ride in the headers or the
    # viewer replays the moves from the standard start.
    setup_fen = str(game.get("SetupFEN") or "").strip()
    if setup_fen:
        headers += [("FEN", setup_fen), ("SetUp", "1")]
    head = "".join(f'[{k} "{v}"]\n' for k, v in headers if str(v or "").strip())
    return f"{head}\n{movetext}".strip()


def _correction_row(move_eval) -> html.Div | None:
    """The engine's recommended correction for a judged move: the best move and
    the refutation line it leads to.  None when the engine named no better move
    (so a judged move without a correction simply shows its severity)."""
    if move_eval is None or not move_eval.best_move:
        return None
    children = [
        html.Span("Best", className="engine-correction-label"),
        html.Span(move_eval.best_move, className="engine-best-move"),
    ]
    # The refutation line beyond the best move itself, as the engine gave it.
    rest = move_eval.refutation_line[1:]
    if rest:
        children.append(html.Span(" ".join(rest), className="engine-refutation"))
    return html.Div(className="engine-correction", children=children)


def _eval_chart(analysis) -> dcc.Graph:
    """The engine's evaluation across the Game, as a win-probability advantage
    line (issue #63 [F7]).

    One point per played move: White's win% from that move's evaluation, so 50%
    is dead level, the line rising as White takes over and falling as Black does.
    Neutral systemBlue (gold stays reserved for achievements; win/red for
    outcomes) with a faint level line at 50%.
    """
    moves = analysis.moves
    xs = [m.ply / 2.0 for m in moves]
    ys = [win_pct_from_cp(m.eval_after * 100.0) for m in moves]

    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="lines",
        line=dict(color=COLORS["primary"], width=2, shape="spline"),
        fill="tozeroy", fillcolor=rgba(COLORS["primary"], 0.16),
        hovertemplate="move %{x:.0f} · White %{y:.0f}%<extra></extra>",
    ))
    apply_dark_theme(fig, title="Evaluation", xaxis_title="Move",
                     yaxis_title="White win %", show_legend=False)
    fig.update_yaxes(range=[0, 100])
    fig.add_hline(y=50, line=dict(color=COLORS["muted"], width=1, dash="dot"))

    return dcc.Graph(
        id="engine-eval-chart", figure=fig,
        config={"displayModeBar": False, "responsive": True},
        style={"height": "190px"},
    )


def _judgment_row(mistake, move_eval) -> html.Div:
    """One judged move: its severity (the F2 word), where it happened, and the
    engine's recommended correction (best move + refutation line)."""
    move_label = (f"{mistake.move_number}… {mistake.san}"
                  if mistake.ply % 2 == 0 else f"{mistake.move_number}. {mistake.san}")
    return html.Div(className=f"engine-judgment {mistake.severity}", children=[
        html.Div(className="engine-judgment-head", children=[
            html.Span(move_label, className="engine-move"),
            html.Span(mistake.severity.capitalize(),
                      className=f"engine-severity {mistake.severity}"),
        ]),
        _correction_row(move_eval),
    ])


def _engine_section(game: pd.Series) -> html.Div:
    """
    The Engine view (issue #63 [F7]) — the third board-switcher view, where
    Daniel reviews where he went wrong and what was better.

    Shows the engine's evaluation across the Game, his move judgments (the F2
    inaccuracy/mistake/blunder severities), and the recommended corrections —
    under the F5 AI-summary paragraph.  An unanalysed Game degrades to an
    awaiting-analysis state rather than breaking (ADR 0004).

    Hidden until the Engine switch reveals it (``assets/lpv-init.js``).
    """
    chapter_url = str(game.get("ChapterURL") or "")
    analysis = data.get_game_analysis(chapter_url)

    # An unanalysed Game has no evals to chart and no profile to judge — it
    # degrades to the same quiet awaiting-analysis hint shown above the board,
    # never an empty chart or a crash (ADR 0004).
    if not analysis.analyzed:
        return html.Div(
            className="lpv-engine", style={"display": "none"},
            children=[html.Div(className="awaiting-analysis-hint", children=[
                html.Span("Awaiting analysis", className="awaiting-analysis-label"),
                html.Span(
                    " — request computer analysis on this Chapter on Lichess and "
                    "the engine's evals, judgments, and corrections appear here "
                    "after the next Sync.",
                    className="awaiting-analysis-text",
                ),
            ])],
        )

    body: list = []

    # The F5 AI summary, on top — a plain-English verdict generated from engine
    # facts only.  It degrades cleanly to "" (no key / unanalysed / on failure),
    # in which case the paragraph is simply omitted (the summary is enrichment,
    # never a dependency; ADR 0004).
    summary = data.get_game_summary(chapter_url)
    if summary:
        body.append(html.P(summary, className="engine-summary"))

    # The engine's evaluation across the whole Game, charted.
    body.append(_eval_chart(analysis))

    # Each judged move is paired with its MoveEval (by ply) so the correction —
    # the best move + refutation line, carried on the MoveEval — sits with the
    # judgment.  Mirrors how the critical-moment section looks moves up by ply.
    moves_by_ply = {m.ply: m for m in analysis.moves}
    body.extend(
        _judgment_row(mistake, moves_by_ply.get(mistake.ply))
        for mistake in analysis.error_profile
    )

    return html.Div(
        className="lpv-engine", style={"display": "none"}, children=body,
    )


def _board_section(game: pd.Series) -> html.Div:
    """
    The Game's interactive board, rendered by Lichess's open-source pgn-viewer
    (a local asset — issue #60 [F6]), replacing the old iframe embed.

    The mount div carries the moves as a PGN; ``assets/lpv-init.js`` reads it
    and instantiates the viewer, themed via the shared ``--cs-*`` tokens.  A
    Game with no recorded moves degrades to a quiet empty state, never a broken
    board.
    """
    movetext = str(game.get("Movetext") or "")
    if not movetext.strip():
        return html.Div(className="game-board-card", children=[
            empty_state(
                "♟",
                "No board for this game",
                "This Game has no recorded moves to replay.",
            ),
        ])

    setup_fen = str(game.get("SetupFEN") or "")
    data_attrs = {
        "data-pgn-game": _game_pgn(game, mainline_movetext(movetext, setup_fen)),
        # Orient the board the way Daniel played it — his colour at the bottom.
        "data-orientation": str(game.get("Color") or "white").lower(),
    }

    # The view switcher: Game (the bare replay) is always the default.  My
    # Analysis is offered only when Daniel annotated this Chapter himself —
    # his variations or comments — so unannotated Games aren't cluttered with
    # an empty tab (issue #60 [F6]).
    switches = [
        html.Button("Game", className="lpv-switch active",
                    **{"data-view": "game"}),
    ]
    if has_my_analysis(movetext, setup_fen):
        data_attrs["data-pgn-analysis"] = _game_pgn(game, movetext)
        switches.append(
            html.Button("My Analysis", className="lpv-switch",
                        **{"data-view": "analysis"})
        )

    # The Engine view (issue #63 [F7]) is always offered: an analysed Game
    # shows its evals, judgments, and corrections; an unanalysed one degrades
    # to an awaiting-analysis state inside the panel.
    switches.append(
        html.Button("Engine", className="lpv-switch", **{"data-view": "engine"})
    )

    # The Coach view (issue #74 [G4]) is offered only when the coach reviewed
    # this Game — his annotated line, with all his variations and notes, played
    # in the same board.  A Game with no coach match simply has no Coach tab
    # (gracefully, never an error); coach material is private, so it renders
    # only behind the auth gate.
    chapter_url = str(game.get("ChapterURL") or "")
    coach_chapter = data.get_coach_chapter(chapter_url)
    if coach_chapter is not None:
        data_attrs["data-pgn-coach"] = _game_pgn(game, coach_chapter.movetext)
        switches.append(
            html.Button("Coach", className="lpv-switch", **{"data-view": "coach"})
        )

    mount = html.Div(className="lpv", **data_attrs)
    return html.Div(className="game-board-card", children=[
        html.Div(className="lpv-switcher", children=switches),
        mount,
        _engine_section(game),
    ])


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
    board_card = _board_section(game)

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
