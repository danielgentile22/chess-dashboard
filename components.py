"""
components.py
=============
Shared UI building blocks for the multi-page Chess Dashboard.

Every page composes its layout from these helpers so the whole app keeps a
single visual language: page headers, chart cards, KPI cards, empty states,
form indicators, celebration banners, USCF cards, and the dark DataTable
styles.
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html, no_update

from styles import COLORS, FONT_SYSTEM, LOSS_WASH, WIN_WASH
from uscf_core import UscfProfile, UscfRating

# ---------------------------------------------------------------------------
# Dark DataTable styles (shared by every table in the app)
#
# Fonts and colors derive from the theme tokens in styles.py.  Numbers use
# the system font with tabular numerals (set in CSS) instead of a mono face;
# headers stay neutral (chrome never carries gold).
# ---------------------------------------------------------------------------

TABLE_CELL = dict(
    fontFamily=FONT_SYSTEM, fontSize="12px",
    padding="7px 10px", whiteSpace="normal", height="auto",
    minWidth="70px", maxWidth="200px",
    backgroundColor=COLORS["card"], color=COLORS["text"],
    border=f"1px solid {COLORS['border']}",
)
TABLE_HEADER = dict(
    fontFamily=FONT_SYSTEM,
    fontWeight="700", backgroundColor=COLORS["card2"],
    color=COLORS["muted"], border=f"1px solid {COLORS['border']}",
    fontSize="10px", letterSpacing="0.07em", textTransform="uppercase",
)
TABLE_DATA_COND = [
    {"if": {"filter_query": '{Outcome} = "Win"'},
     "backgroundColor": WIN_WASH, "color": COLORS["text"]},
    {"if": {"filter_query": '{Outcome} = "Loss"'},
     "backgroundColor": LOSS_WASH, "color": COLORS["text"]},
    {"if": {"row_index": "odd"}, "backgroundColor": COLORS["card2"]},
]


# ---------------------------------------------------------------------------
# Quiet-table treatment (shared — Games now, Events crosstables and Trends
# upset tables later).  Apple-calm tables: neutral headers (no gold, no
# uppercase shouting), left-aligned text, hairline row separators instead of a
# full grid, and the white "focused row" glitch fixed.
#
# A DataTable opts in by (1) wrapping it in a `.quiet-table` element and (2)
# passing these style dicts so the inline Dash styles agree with the CSS.  The
# visual rules that Dash can't express inline — the per-row hairline, the
# focused/selected-cell fix — live in the `.quiet-table` block in
# assets/custom.css so every page that adopts the class gets them for free.
# ---------------------------------------------------------------------------

QUIET_TABLE_CELL = dict(
    fontFamily=FONT_SYSTEM, fontSize="12px",
    padding="9px 12px", whiteSpace="normal", height="auto",
    minWidth="70px", maxWidth="240px",
    backgroundColor=COLORS["card"], color=COLORS["text"],
    textAlign="left", border="none",
)
QUIET_TABLE_HEADER = dict(
    fontFamily=FONT_SYSTEM,
    fontWeight="600", backgroundColor=COLORS["card"],
    color=COLORS["muted"], border="none",
    fontSize="12px", letterSpacing="normal", textTransform="none",
    textAlign="left",
)
# Outcome washes only — no zebra striping.  Hairline separators (in the CSS)
# carry the row rhythm so alternating fills aren't needed.
QUIET_TABLE_DATA_COND = [
    {"if": {"filter_query": '{Outcome} = "Win"'},
     "backgroundColor": WIN_WASH, "color": COLORS["text"]},
    {"if": {"filter_query": '{Outcome} = "Loss"'},
     "backgroundColor": LOSS_WASH, "color": COLORS["text"]},
]


def quiet_table(table, *, clickable: bool = False, scroll: bool = True) -> html.Div:
    """Wrap a DataTable in the shared quiet-table treatment.

    The returned ``Div`` carries the ``quiet-table`` class (neutral headers,
    left-aligned text, hairline row separators, focused-row fix) so any page —
    Games here, Events crosstables and Trends upset tables later — gets the
    same Apple-calm table by composing this helper.  ``clickable`` adds the
    row-pointer / hover treatment used by tables whose rows open a Game.
    """
    classes = "quiet-table" + (" clickable-rows" if clickable else "")
    style = {"flex": "1", "overflow": "auto"} if scroll else {}
    return html.Div(table, className=classes, style=style)


# ---------------------------------------------------------------------------
# Page scaffolding
# ---------------------------------------------------------------------------

def page_header(title: str, subtitle: str = "") -> html.Div:
    """The iOS large-title block at the top of every page."""
    children: list = [html.H1(title, className="page-title")]
    if subtitle:
        children.append(html.Div(subtitle, className="page-subtitle"))
    return html.Div(children, className="page-header")


def chart_card(title: str, graph_id: str, *, height: int = 380) -> html.Div:
    """A dark card holding one Plotly graph."""
    return html.Div(
        className="chart-card",
        style={"height": f"{height}px"},
        children=[
            html.Div(title, className="chart-title"),
            html.Div(className="chart-body", children=[
                dcc.Graph(
                    id=graph_id,
                    style={"height": "100%", "width": "100%"},
                    config={"displayModeBar": False, "responsive": True},
                )
            ]),
        ],
    )


def content_card(title: str, *children, height: int | None = None) -> html.Div:
    """A dark card holding arbitrary content (tables, stat grids, …).

    Carries the ``content-card`` marker in addition to the shared ``chart-card``
    surface styling.  Unlike a chart card (which holds a fixed-height Plotly
    graph), a content card sizes to its content — so "Last 20 games" and
    "Average game length" lose the dead zones they used to inherit from being
    stretched to a chart neighbour's height in a grid row.
    """
    style = {"height": f"{height}px"} if height else {}
    return html.Div(
        className="chart-card content-card",
        style=style,
        children=[html.Div(title, className="chart-title"), *children],
    )


def kpi_card(
    label: str, value_id: str, value_class: str = "", *, text: bool = False
) -> html.Div:
    """One KPI tile: an uppercase label over a big value with tabular figures.

    KPI values are neutral white by default — colour survives only where it is
    semantic (the win % is green, the loss % is red), passed via *value_class*.

    *text* opts the value into the text variant: a long string value (the
    favourite opening, "Italian Game: Scotch Gambit") wraps to two lines and
    shrinks slightly instead of truncating to "Italian Game: Scot…".
    """
    classes = ["kpi-value"]
    if value_class:
        classes.append(value_class)
    if text:
        classes.append("kpi-value-text")
    return html.Div(className="kpi-card", children=[
        html.Div(label, className="kpi-label"),
        html.Div("—", id=value_id, className=" ".join(classes)),
    ])


def form_indicator(form: dict) -> list:
    """
    Streak fire / cold + last-5 form dots (issue #10).

    Takes a ``current_form()`` dict and returns header-ready components:

      * win streak ≥ 2 → 🔥 scaled to the streak length, extra glow at 5+
      * loss streak ≥ 3 → 🧊 cold indicator
      * last 5 Games as colored dots, oldest → newest

    Reused anywhere recent form matters (the header now, opponent rows later).
    """
    children: list = []

    if form["win_streak"] >= 2:
        blazing = " blazing" if form["win_streak"] >= 5 else ""
        # The fire grows with the streak (capped so it never breaks the header)
        size = min(15 + form["win_streak"] * 1.5, 28)
        children.append(html.Span(
            ["🔥", html.Span(str(form["win_streak"]), className="streak-count")],
            className=f"streak-fire{blazing}",
            style={"fontSize": f"{size}px"},
            title=f"{form['win_streak']}-game win streak",
        ))
    elif form["loss_streak"] >= 3:
        children.append(html.Span(
            ["🧊", html.Span(str(form["loss_streak"]), className="streak-count")],
            className="streak-cold",
            title=f"{form['loss_streak']}-game losing streak — it turns around",
        ))

    if form["last_5"]:
        children.append(html.Span(
            # The outcome letter (W/D/L) inside each dot is the non-color
            # channel — colour alone can't carry win vs loss (issue #88).
            [html.Span(o[0], className=f"form-dot {o.lower()}", title=o) for o in form["last_5"]],
            className="form-dots",
            title="Last 5 games, oldest → newest",
        ))

    return children


# The source value that marks a Tag as the engine's, not Daniel's (issue #62).
ENGINE_TAG_SOURCE = "engine"


def tag_chips(tags, sources=None, *, base_class: str = "tag-chip") -> list:
    """A Game's Tags as chips, marking the engine-emitted ones (issue #62 [F4]).

    *sources* maps each Tag to ``"mine"`` or ``"engine"``; a Tag missing from it
    is treated as hand-written.  Engine-emitted Tags get an extra
    ``tag-chip-engine`` class and a small ⚙ marker so Daniel can always tell the
    computer's Tags from the ones he wrote himself — wherever Tags render.
    """
    sources = sources or {}
    chips = []
    for tag in tags:
        engine = sources.get(tag) == ENGINE_TAG_SOURCE
        children = [f"#{tag}"]
        if engine:
            children.append(html.Span("⚙", className="tag-chip-engine-mark"))
        chips.append(html.Span(
            children,
            className=base_class + (" tag-chip-engine" if engine else ""),
            title="Engine-emitted Tag" if engine else None,
        ))
    return chips


def lesson_card(row, *, show_opponent: bool = True) -> html.Div:
    """
    One Lesson as a quote card: the takeaway text, its source Game's context,
    Tags, and a link to the Game's detail view.

    *row* is a ``lessons_table()`` row (Series or dict).  Used by the Lessons
    page and inside Scouting Reports (where the opponent is implied, so
    ``show_opponent=False``).
    """
    outcome = str(row["Outcome"] or "")
    meta_bits = [
        f"vs {row['Opponent']}" if show_opponent and row["Opponent"] else "",
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
            html.Span(className="lesson-card-tags", children=tag_chips(
                row["Tags"], row.get("TagSources"), base_class="tag-chip tag-chip-small",
            )),
            dcc.Link("View game →", href=detail_path, className="lesson-game-link")
            if detail_path else None,
        ]),
    ])


def coach_note_card(note: dict) -> html.Div:
    """
    One coach's note as a card (issue #75 [G5]) — the coach's prose, where in
    the Game it was written, and a link to the Game.

    Deliberately distinct from ``lesson_card`` (the king glyph and the
    ``coach-note-*`` styling), so a coach's advice never reads as one of the
    user's own takeaways.  *note* is a ``data.get_coach_notes()`` row.
    """
    where = (f"move {note['move_number']}" if note.get("move_number")
             else "before the game")
    outcome = str(note.get("outcome") or "")
    meta_bits = [
        f"vs {note['opponent']}" if note.get("opponent") else "",
        outcome,
        str(note.get("date") or ""),
        where,
    ]
    href = f"/game/{note['chapter_id']}" if note.get("chapter_id") else ""

    return html.Div(className="coach-note-card", children=[
        html.Div(className="coach-note-quote", children=[
            html.Span("♚", className="coach-note-mark"),
            html.Span(note["text"], className="coach-note-text"),
        ]),
        html.Div(className="coach-note-footer", children=[
            html.Span(
                "  ·  ".join(b for b in meta_bits if b),
                className=f"coach-note-meta outcome-{outcome.lower()}",
            ),
            dcc.Link("View game →", href=href, className="coach-note-link")
            if href else None,
        ]),
    ])


def lichess_link(chapter_url: str) -> str:
    """Markdown 'Open on Lichess' link for a Game's ChapterURL ('' if none)."""
    return f"[Open ↗]({chapter_url})" if chapter_url else ""


def uscf_member_url(member_id: str) -> str:
    """The USCF ratings-site page for a member ('' if no ID)."""
    return f"https://ratings.uschess.org/members/{member_id}" if member_id else ""


# Display names for USCF rating-system codes (issue #28).
USCF_RATING_SYSTEM_LABELS = {
    "R": "Regular",
    "D": "Dual (Regular + Quick)",
    "Q": "Quick",
    "B": "Blitz",
    "OR": "Online Regular",
    "OQ": "Online Quick",
    "OB": "Online Blitz",
}


def uscf_status_label(matched_by: str, forfeit: bool, conflict: bool = False) -> str:
    """
    A Game's USCF status, one glanceable token (issues #28/#29/#30):
    ✓ matched by opponent ID · ≈ matched by name (eyeball these) ·
    ⚠ matched but the sources disagree (see Reconciliation) ·
    "Forfeit" for no-shows · blank for no USCF Game Record.
    """
    if forfeit:
        return "Forfeit"
    if conflict:
        return "⚠"
    return {"id": "✓", "name": "≈"}.get(matched_by, "")


def game_detail_path(chapter_url: str) -> str:
    """The in-app detail route for a Game ('' if it has no ChapterURL)."""
    if not chapter_url:
        return ""
    return f"/game/{chapter_url.rstrip('/').rsplit('/', 1)[-1]}"


# ---------------------------------------------------------------------------
# Mobile game cards (issue #48)
#
# On a phone the Games table's ~15 columns force a sideways scroll just to find
# who I played and whether I won.  At phone widths the page swaps the table for
# this card list: one tappable card per Game, the things that matter first —
# opponent, outcome, date, event.  Both presentations are fed by the *same*
# callback rows (the trimmed player-centric column set), so the card list is a
# second *rendering*, never a second data path.
# ---------------------------------------------------------------------------

def _game_card(row: dict) -> html.Div:
    """One Game as a tappable card: opponent, outcome, date, event.

    *row* is one record from the Games callback (the same dict the table is
    fed).  A Game with a ChapterURL is a link to its detail view; a Game
    without one (a Forfeit has a Chapter, but a row could lack the URL) renders
    as a plain, unlinked card — never an error.
    """
    outcome = str(row.get("Outcome") or "")
    opponent = str(row.get("Opponent") or "Unknown opponent")
    opp_rating = str(row.get("OpponentRating") or "")

    # The line under the opponent: outcome · date · event, blanks dropped.
    meta_bits = [b for b in (outcome, str(row.get("Date") or ""),
                             str(row.get("Event") or "")) if b]

    children = [
        html.Div(className="game-card-top", children=[
            html.Span(opponent, className="game-card-opponent"),
            html.Span(f"{opp_rating}" if opp_rating else "",
                      className="game-card-opp-rating"),
        ]),
        html.Div("  ·  ".join(meta_bits),
                 className=f"game-card-meta outcome-{outcome.lower()}"),
    ]

    detail = game_detail_path(row.get("ChapterURL", ""))
    if detail:
        return dcc.Link(children, href=detail, className="game-card")
    # No ChapterURL → a card with no link (and no crash).
    return html.Div(children, className="game-card game-card-nolink")


def game_cards(rows: list[dict]) -> html.Div:
    """The mobile Games card list: one :func:`_game_card` per Game row.

    Renders the *same* rows the Games table is fed, so the table and the cards
    can never disagree.  An empty archive (everything filtered out) renders an
    empty, harmless list rather than erroring.
    """
    return html.Div(
        [_game_card(row) for row in rows],
        className="game-card-list",
    )


def row_click_to_game(active_cell, viewport_rows):
    """
    Map a DataTable cell click to a Game detail path (issue #11).

    Returns the ``/game/<chapter-id>`` path to navigate to, or ``no_update``
    when the click shouldn't navigate: no cell, a click on the Lichess
    external-link column, or a row without a ChapterURL.
    """
    if not active_cell or not viewport_rows:
        return no_update
    if active_cell.get("column_id") == "Lichess":
        return no_update  # let the Open-on-Lichess link do its own thing
    row = active_cell.get("row")
    if row is None or row >= len(viewport_rows):
        return no_update
    path = game_detail_path(viewport_rows[row].get("ChapterURL", ""))
    return path or no_update


def register_game_navigation(table_id: str):
    """
    Register the click-a-row-to-open-the-game callback for one DataTable
    (issue #11) and return the callback (tests drive it directly).

    Every table whose rows carry a ChapterURL gets the exact same behavior —
    navigate to the Game's detail view and clear the selection so the same
    row can be clicked again — so the wiring lives in exactly one place.
    """
    @callback(
        Output("url", "href", allow_duplicate=True),
        Output(table_id, "active_cell"),
        Input(table_id, "active_cell"),
        State(table_id, "derived_viewport_data"),
        prevent_initial_call=True,
    )
    def navigate(active_cell, viewport_rows):
        return row_click_to_game(active_cell, viewport_rows), None

    return navigate


def empty_state(glyph: str, title: str, *lines) -> html.Div:
    """
    A deliberate empty state: a chess glyph, a serif heading, and explanation
    lines (used for placeholder pages and no-data situations).
    """
    return html.Div(className="empty-state", children=[
        html.Div(glyph, className="empty-state-glyph"),
        html.Div(title, className="empty-state-title"),
        *[html.Div(line, className="empty-state-line") for line in lines],
    ])


def weakness_callout(callout: dict, *, compact: bool = False) -> html.Div:
    """
    A recurring-weakness callout (issue #18): the Tag, the stat, the time
    window, and the Games behind it.

    The full form (Lessons page) links each Game; the compact form (Overview)
    is just the headline plus a pointer to the Lessons page.
    """
    children: list = [
        html.Div(className="weakness-headline", children=[
            html.Span("⚠", className="weakness-icon"),
            html.Span(callout["stat"], className="weakness-stat"),
            html.Span(callout["window"], className="weakness-window"),
        ]),
    ]
    if compact:
        children.append(
            dcc.Link("Review these lessons →", href="/lessons",
                     className="weakness-game-link")
        )
    else:
        linkable = [url for url in callout["chapter_urls"] if url]
        children.append(html.Div(className="weakness-games", children=[
            dcc.Link(f"Game {i} →", href=game_detail_path(url),
                     className="weakness-game-link")
            for i, url in enumerate(linkable, start=1)
        ]))
    return html.Div(children, className="weakness-callout" + (" compact" if compact else ""))


# ---------------------------------------------------------------------------
# USCF profile card (issue #25)
# ---------------------------------------------------------------------------

def _uscf_rating_note(entry: UscfRating | None) -> str:
    """The provisional/floor footnote under a rating value."""
    if entry is None or entry.rating is None:
        return "Unrated"
    if entry.is_provisional:
        games = f" · {entry.games_played} games" if entry.games_played else ""
        return f"Provisional{games}"
    floor = f" · floor {entry.floor}" if entry.floor else ""
    return f"Established{floor}"


def _uscf_rating_block(label: str, entry: UscfRating | None) -> html.Div:
    """One rating system's tile: big mono numeral + provisional/floor note."""
    value = str(entry.rating) if entry is not None and entry.rating is not None else "—"

    return html.Div(className="uscf-stat", children=[
        html.Div(label, className="uscf-stat-label"),
        html.Div(value, className="uscf-stat-value"),
        html.Div(_uscf_rating_note(entry), className="uscf-stat-note"),
    ])


def _uscf_regular_block(entry: UscfRating | None, live_rating: float | None) -> html.Div:
    """
    The Regular rating tile — the backbone rating (PRD #24).

    With a Live Rating available it shows both values side by side
    ("Official 1545 · Live 1571" — issue #27); without one it is a plain
    rating tile.  Ratings display as whole numbers — no decimal places.
    """
    if live_rating is None:
        return _uscf_rating_block("Regular", entry)

    official = str(entry.rating) if entry is not None and entry.rating is not None else "—"

    return html.Div(className="uscf-stat uscf-stat-regular", children=[
        html.Div("Regular", className="uscf-stat-label"),
        html.Div(className="uscf-dual-value", children=[
            html.Div([
                html.Div("Official", className="uscf-dual-label"),
                html.Div(official, className="uscf-stat-value"),
            ]),
            html.Div([
                html.Div("Live", className="uscf-dual-label"),
                html.Div(f"{live_rating:.0f}",
                         className="uscf-stat-value uscf-live-value"),
            ]),
        ]),
        html.Div(_uscf_rating_note(entry), className="uscf-stat-note"),
    ])


def _uscf_rank_block(label: str, rank: int | None) -> html.Div:
    """One rank tile: '#11,719' style."""
    value = f"#{rank:,}" if rank is not None else "—"
    return html.Div(className="uscf-stat", children=[
        html.Div(label, className="uscf-stat-label"),
        html.Div(value, className="uscf-stat-value"),
        html.Div("", className="uscf-stat-note"),
    ])


def uscf_profile_card(
    profile: UscfProfile,
    alert: str | None = None,
    stale: str | None = None,
    live_rating: float | None = None,
) -> html.Div:
    """
    The USCF profile card: the member's official identity at a glance.

    Regular / Quick / Online-Regular ratings (provisional ones labeled with
    game counts), national and state rank, rating floor, and membership —
    with a visible warning when the membership has lapsed or expires soon.

    *stale* is the degradation notice (ADR 0003): shown when the numbers come
    from the cache because USCF is currently unreachable.

    *live_rating* is the current Live Rating (issue #27): shown next to the
    Official Regular rating so the gap between the two is visible at a glance.
    """
    membership = profile.membership_status
    if profile.membership_expires:
        membership += f" · until {profile.membership_expires.isoformat()}"

    state_label = f"{profile.state} rank" if profile.state else "State rank"

    return html.Div(className="chart-card uscf-card", children=[
        html.Div(className="uscf-card-header", children=[
            html.Div([
                html.Div("US Chess Federation", className="chart-title"),
                html.Div(className="uscf-identity", children=[
                    html.Span(profile.name, className="uscf-name"),
                    html.Span(f"#{profile.member_id}", className="uscf-member-id"),
                ]),
            ]),
            html.Div(membership, className="uscf-membership"),
        ]),
        html.Div(stale, className="uscf-stale") if stale else None,
        html.Div(alert, className="uscf-alert") if alert else None,
        html.Div(
            className="uscf-stats" + (" uscf-stats-with-live" if live_rating else ""),
            children=[
                _uscf_regular_block(profile.rating("R"), live_rating),
                _uscf_rating_block("Quick", profile.rating("Q")),
                _uscf_rating_block("Online Regular", profile.rating("OR")),
                _uscf_rank_block("National rank", profile.national_rank),
                _uscf_rank_block(state_label, profile.state_rank),
            ],
        ),
    ])


def uscf_unavailable_card(reason: str) -> html.Div:
    """
    The USCF card's degraded state (ADR 0003): says USCF is unavailable and
    why, without pretending to have data.
    """
    return html.Div(className="chart-card uscf-card uscf-unavailable", children=[
        html.Div("US Chess Federation", className="chart-title"),
        html.Div(className="uscf-alert", children=[
            html.Span("USCF data unavailable", className="uscf-alert-headline"),
            html.Span(f" — {reason}" if reason else "", className="uscf-alert-reason"),
        ]),
        html.Div(
            "Your Lichess games are unaffected. The card will fill in on the next "
            "successful Sync.",
            className="uscf-unavailable-hint",
        ),
    ])


def celebration_banner(deltas: list[dict]) -> dbc.Alert:
    """
    The gold milestone celebration (issue #15): shown once after a Sync that
    set a personal best — or that USCF first recognized with a norm or award
    (issue #36) — dismissible, and gone for good once dismissed.

    Takes the ``milestone_deltas()`` list — one line per record broken —
    plus any new-achievement deltas (kind ``uscf_achievement``).
    """
    official = sum(1 for d in deltas if d["kind"] == "uscf_achievement")
    personal = len(deltas) - official
    if personal and official:
        # A Sync can bring both — name both, mislabel neither
        headline = "New personal bests and official USCF achievements!"
    elif official:
        # An official achievement isn't a "personal best" — say what it is
        headline = ("New official USCF achievement!" if official == 1
                    else f"{official} new official USCF achievements!")
    else:
        headline = ("New personal best!" if personal == 1
                    else f"{personal} new personal bests!")
    return dbc.Alert(
        [
            html.Div(className="celebration-headline", children=[
                html.Span("🏆", className="celebration-trophy"),
                html.Span(headline, className="celebration-title"),
            ]),
            html.Ul(className="celebration-list", children=[
                html.Li(d["description"]) for d in deltas
            ]),
        ],
        is_open=True,
        dismissable=True,
        className="celebration-banner",
    )
