"""
pages/reconciliation.py
=======================
The Reconciliation page (issue #30) — where every disagreement between the
Studies and USCF becomes visible and actionable.

Five kinds of entries (see CONTEXT.md / uscf_core.reconcile): conflicts,
USCF-only games, Lichess-only games, missing FideIds, and typed-rating
mismatches.  Each shows both sources side by side and offers two actions:
fix-on-Lichess (a deep link to the chapter) and Dismiss ("USCF is wrong" /
"intentionally skipped").

Dismissals are the dashboard's first write interaction.  They persist in the
local USCF cache only (best-effort — ADR 0002's stateless deployment model),
so a redeploy may resurrect dismissed items; the page says so.
"""
from __future__ import annotations

import dash
from dash import ALL, Input, Output, State, callback, ctx, html, no_update

import data
from components import content_card, empty_state, page_header
from uscf_core import ReconciliationEntry

dash.register_page(
    __name__, path="/reconciliation", name="Reconciliation",
    title="Reconciliation — Chess Dashboard", order=8,
)

# The five entry kinds, in display order: what they are and what to do about them.
_KINDS = [
    ("conflict", "Conflicts",
     "Matched Games whose facts disagree. The dashboard displays the Lichess "
     "version everywhere; the disagreement is flagged here, never hidden."),
    ("uscf_only", "USCF only",
     "Rated games with no Chapter in your Studies. Add the Chapter on Lichess, "
     "or dismiss the ones you skip on purpose (online-rated games)."),
    ("lichess_only", "Lichess only",
     "Games USCF hasn't rated. Usually just rating lag — the next supplement "
     "clears these."),
    ("missing_fide_id", "Missing opponent IDs",
     "Chapters without the opponent's USCF member ID typed in. Matching found "
     "them by name this time; type the ID in to make it robust."),
    ("rating_mismatch", "Typed-rating mismatches",
     "Your hand-typed header rating disagrees with the Official Rating in "
     "effect for that Rated Event. Typed values power no stats — this is "
     "bookkeeping only."),
]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**kwargs) -> html.Div:
    return html.Div(className="page", children=[
        page_header("Reconciliation", "Where your Studies and USCF disagree"),
        html.Div(id="reconciliation-content"),
    ])


def _entry_card(entry: ReconciliationEntry) -> html.Div:
    """One disagreement: who and when, both versions side by side, actions."""
    sources = []
    if entry.lichess_says:
        sources.append(html.Div(className="reconcile-source", children=[
            html.Div("Lichess", className="reconcile-source-label"),
            html.Div(entry.lichess_says, className="reconcile-source-text"),
        ]))
    if entry.uscf_says:
        sources.append(html.Div(className="reconcile-source reconcile-source-uscf",
                                children=[
            html.Div("USCF", className="reconcile-source-label"),
            html.Div(entry.uscf_says, className="reconcile-source-text"),
        ]))

    actions: list = []
    if entry.chapter_url:
        actions.append(html.A(
            [html.I(className="bi bi-box-arrow-up-right"), " Fix on Lichess"],
            href=entry.chapter_url, target="_blank",
            className="reconcile-action reconcile-fix",
        ))
    actions.append(html.Button(
        "Dismiss",
        id={"type": "reconcile-dismiss", "index": entry.entry_id},
        className="reconcile-action reconcile-dismiss",
        title="USCF is wrong, or this difference is intentional — stop showing it",
    ))

    head = [html.Span(f"vs {entry.opponent}", className="reconcile-opponent")]
    if entry.date:
        head.append(html.Span(entry.date, className="reconcile-date"))

    return html.Div(className="reconcile-entry", children=[
        html.Div(head, className="reconcile-entry-head"),
        html.Div(sources, className="reconcile-sources"),
        html.Div(actions, className="reconcile-actions"),
    ])


def _persistence_note() -> html.Div:
    """The documented limitation: dismissals are best-effort local state."""
    return html.Div(
        "Dismissals are remembered in this dashboard's local cache only — "
        "after a redeploy or on a fresh machine, dismissed items may come back.",
        className="reconcile-persistence-note",
    )


def _coach_ambiguity_card(chapters: list[dict]) -> html.Div:
    """Coach reviews the matcher couldn't place (issue #92) — surfaced here so a
    review the user paid for never silently vanishes.  Each links back to its
    coach Study Chapter to check by eye which Game it belongs to."""
    rows = []
    for chapter in chapters:
        head = [html.Span(chapter["name"] or "Untitled chapter",
                          className="reconcile-opponent")]
        actions: list = []
        if chapter["url"]:
            actions.append(html.A(
                [html.I(className="bi bi-box-arrow-up-right"), " Open in coach Study"],
                href=chapter["url"], target="_blank",
                className="reconcile-action reconcile-fix",
            ))
        rows.append(html.Div(className="reconcile-entry", children=[
            html.Div(head, className="reconcile-entry-head"),
            html.Div(actions, className="reconcile-actions"),
        ]))
    return content_card(
        f"Coach reviews ({len(chapters)})",
        html.Div(
            "The coach reviewed a Game the matcher couldn't place unambiguously — "
            "its moves fit more than one of your Games, or a Game two of his "
            "Chapters both claim. Nothing is dropped silently; open the Chapter "
            "to see which Game it belongs to (typing the moves or ID in fixes it).",
            className="reconcile-explain",
        ),
        html.Div(rows, className="reconcile-entries"),
    )


def _render_entries(
    entries: list[ReconciliationEntry], coach_ambiguities: list[dict]
) -> html.Div:
    """The page body: USCF disagreements grouped by kind and any coach reviews
    the matcher couldn't place, or the all-clear empty state."""
    uscf_on = data.uscf_enabled()
    if not uscf_on and not coach_ambiguities:
        return html.Div([empty_state(
            "♔", "USCF is not configured",
            "Reconciliation compares your Studies against your USCF record.",
            "Set a USCF member ID (--uscf-member or USCF_MEMBER_ID) to use it.",
        )])

    sections: list = []
    if uscf_on and entries:
        sections.append(html.Div(
            f"{len(entries)} open item{'s' if len(entries) != 1 else ''}",
            className="reconcile-count",
        ))
        for kind, title, explanation in _KINDS:
            kind_entries = [e for e in entries if e.kind == kind]
            if not kind_entries:
                continue
            sections.append(content_card(
                f"{title} ({len(kind_entries)})",
                html.Div(explanation, className="reconcile-explain"),
                html.Div([_entry_card(e) for e in kind_entries],
                         className="reconcile-entries"),
            ))
    elif uscf_on and not coach_ambiguities:
        sections.append(empty_state(
            "✓", "Everything agrees",
            "Your Studies and USCF tell the same story — no conflicts, "
            "nothing missing on either side.",
        ))

    if coach_ambiguities:
        sections.append(_coach_ambiguity_card(coach_ambiguities))

    sections.append(_persistence_note())
    # The per-kind cards stack vertically; the shared card-stack rhythm gives
    # them the same gap a grid row would (spacing polish, issue #51).
    return html.Div(sections, className="card-stack")


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(Output("reconciliation-content", "children"), Input("sync-store", "data"))
def update_reconciliation(_sync):
    """The page follows Syncs — every Sync recomputes the disagreements."""
    return _render_entries(data.get_reconciliation(), data.get_coach_ambiguities())


@callback(
    Output("reconciliation-content", "children", allow_duplicate=True),
    Output("reconciliation-store", "data"),
    Input({"type": "reconcile-dismiss", "index": ALL}, "n_clicks"),
    State("reconciliation-store", "data"),
    prevent_initial_call=True,
)
def dismiss_entry(n_clicks, store):
    """
    Dismiss the clicked entry: it disappears now and stays dismissed across
    Syncs (best-effort persistence).  Bumps the reconciliation store so the
    header badge updates everywhere.
    """
    if not any(n for n in n_clicks if n):
        return no_update, no_update
    triggered = ctx.triggered_id
    if not triggered:
        return no_update, no_update

    data.dismiss_reconciliation_entry(triggered["index"])
    return (_render_entries(data.get_reconciliation(), data.get_coach_ambiguities()),
            (store or 0) + 1)
