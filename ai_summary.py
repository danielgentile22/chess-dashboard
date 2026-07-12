"""
ai_summary.py
=============
The AI-summary boundary (issue #59 [F5]) — the single, isolated place the
dashboard touches the Anthropic API.

Given a Game's already-computed ``GameAnalysis`` (its critical moment and the
player's classified error profile), this module produces one plain-English
paragraph via Claude Haiku.  It **summarises precomputed engine facts only** —
it is never asked to evaluate the position itself, so it cannot invent chess.
That is a hard boundary: ``build_prompt`` hands the model nothing but facts the
engine already established, and the system instruction forbids analysis.

Resilience is the point (ADR 0004, in the spirit of ADR 0003):

* with **no API key** configured the module is a no-op returning ``""`` — the
  dashboard runs perfectly without one;
* any client failure **degrades silently** to ``""`` — a Sync that reached
  Lichess still succeeds, because the summary is enrichment, never a dependency;
* output is **cached by Game identity** so an unchanged Game is never re-billed
  (the cache is any object exposing ``get_summary`` / ``put_summary`` — see
  ``analysis_cache.AnalysisCache``).

Mirrors ``uscf_client``/``lichess_client``: one thin ``requests`` seam
(``_call_anthropic``), mocked in tests exactly as the USCF client is.

Public API
----------
build_prompt   Assemble the user prompt from a GameAnalysis's facts.
summarize      One-paragraph summary, or "" (no key / unanalysed / on failure).
DEFAULT_MODEL  The Claude Haiku model the summaries run on.
"""
from __future__ import annotations

import hashlib
import logging
import time

import requests

from engine_analysis_core import GameAnalysis

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_MODEL", "build_prompt", "summarize"]

# Haiku: cheap (~$0.002/Game) and more than capable of restating given facts.
DEFAULT_MODEL = "claude-haiku-4-5"

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 400
_DEFAULT_TIMEOUT = 30.0
# One bounded wait-and-retry on a rate-limit / overload before degrading to ""
# (ADR 0004): enough to ride out a transient spike without stalling a Sync.
_RETRY_STATUS = {429, 529}
_MAX_RETRY_WAIT = 10.0

# The hard boundary, stated to the model: restate the findings, never analyse.
_SYSTEM = (
    "You are a concise chess coach writing one short paragraph for a player "
    "about a single game. You are given the engine's already-computed findings "
    "for that game. Summarise ONLY those findings in plain English — do NOT "
    "evaluate the position yourself, do NOT invent moves, lines, or "
    "assessments, and do NOT add any chess analysis beyond the facts provided. "
    "Write 2-4 sentences, no lists, no move numbers in parentheses."
)


def build_prompt(analysis: GameAnalysis) -> str:
    """Assemble the user prompt from *analysis*'s precomputed facts.

    Carries the Game's critical moment (the headline swing) and the player's
    own classified mistakes — and nothing the engine didn't already establish,
    so the model has only facts to restate.
    """
    lines: list[str] = ["Engine findings for one of my games:", ""]

    cm = analysis.critical_moment
    if cm is not None:
        lines.append(
            f"Critical moment: {cm.headline} "
            f"(move {cm.move_number}, {cm.side} played {cm.san}, "
            f"a {cm.win_pct_swing:.0f}-point win-probability swing)."
        )

    if analysis.error_profile:
        lines.append("")
        lines.append("My mistakes (worst first):")
        worst = sorted(
            analysis.error_profile, key=lambda m: m.win_pct_drop, reverse=True
        )
        for m in worst:
            lines.append(
                f"- move {m.move_number} {m.san}: {m.severity}, "
                f"{m.mistake_type}, in the {m.phase}"
            )
    else:
        lines.append("")
        lines.append("I made no inaccuracies, mistakes, or blunders in this game.")

    lines.append("")
    lines.append(
        "Write one short paragraph summarising these findings for me. "
        "Use only the facts above; do not evaluate the position yourself."
    )
    return "\n".join(lines)


def summarize(
    analysis: GameAnalysis,
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    cache=None,
) -> str:
    """One-paragraph summary of *analysis*, or ``""`` when unavailable.

    Returns ``""`` (a true no-op) when no *api_key* is configured or the Game
    is unanalysed; returns ``""`` and logs on any client failure.  When *cache*
    is given an unchanged Game is served from it rather than re-billed.
    """
    if not api_key or not analysis.analyzed:
        return ""

    prompt = build_prompt(analysis)
    fingerprint = _fingerprint(model, prompt)
    if cache is not None:
        cached = cache.get_summary(analysis.chapter_url, fingerprint)
        if cached is not None:
            return cached

    try:
        text = _call_anthropic(prompt, api_key=api_key, model=model)
    except Exception as exc:  # any failure degrades to "" — never a dependency
        logger.warning("AI summary unavailable for %s (degrading): %s",
                       analysis.chapter_url or "game", exc)
        return ""

    if cache is not None and text:
        cache.put_summary(analysis.chapter_url, fingerprint, text)
    return text


def _fingerprint(model: str, prompt: str) -> str:
    """A stable identity for a Game's summary inputs: a hash of the *model*, the
    system instruction, and the assembled *prompt*.

    Keying the cache on all three means an *unchanged* Game (same facts → same
    prompt) is served from the cache, while a re-analysed Game whose facts moved
    — or a bumped model, or a reworded ``_SYSTEM`` — misses and is summarised
    afresh, so a model/prompt change doesn't serve stale summaries forever."""
    return hashlib.sha256(f"{model}\n{_SYSTEM}\n{prompt}".encode()).hexdigest()


def _call_anthropic(prompt: str, *, api_key: str, model: str) -> str:
    """The one network seam — a thin POST to the Messages API.

    Mirrors ``uscf_client``/``lichess_client`` (plain ``requests``, no SDK), and
    is the single function the suite mocks the way it mocks the USCF client.
    Raises on any transport/HTTP/shape problem; ``summarize`` turns that into a
    silent ``""``.
    """
    response = _post(prompt, api_key=api_key, model=model)
    if response.status_code in _RETRY_STATUS:
        # ponytail: one retry rides out a transient spike; a batch-wide stop is
        # the upgrade if summaries are frequently blanked on first Syncs.
        time.sleep(_retry_after(response))
        response = _post(prompt, api_key=api_key, model=model)
    response.raise_for_status()
    blocks = response.json()["content"]
    return "".join(b.get("text", "") for b in blocks).strip()


def _post(prompt: str, *, api_key: str, model: str) -> requests.Response:
    return requests.post(
        _API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=_DEFAULT_TIMEOUT,
    )


def _retry_after(response: requests.Response) -> float:
    """Seconds to wait before the single retry, honouring Retry-After (bounded)."""
    try:
        wait = float(response.headers.get("Retry-After", 1))
    except (TypeError, ValueError):
        wait = 1.0
    return max(0.0, min(wait, _MAX_RETRY_WAIT))
