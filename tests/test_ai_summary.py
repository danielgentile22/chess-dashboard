"""
tests/test_ai_summary.py
========================
The AI-summary boundary (issue #59 [F5]) — the *only* place the dashboard
touches the Anthropic API.

These are boundary tests, in the spirit of the USCF-client tests: the network
seam (``_call_anthropic``) is mocked exactly the way ``sync.fetch_member_*`` is
mocked, and we never assert the LLM's actual prose.  What we *do* assert is
everything around it — that the prompt is assembled from the engine's
precomputed facts (and never asks the model to evaluate chess), that the module
is a no-op without a key, that any failure degrades silently, and that an
unchanged Game is never re-billed.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import ai_summary
from ai_summary import build_prompt, summarize
from engine_analysis_core import analyze_game
from pgn_stats_core import load_games_from_text

DATA_DIR = Path(__file__).parent / "data"
ALICE_PGN = (DATA_DIR / "analyzed-alice-anderson.pgn").read_text()


def _alice():
    """The analysed Alice Anderson Game, parsed the way a Sync produces it."""
    df, _player = load_games_from_text(ALICE_PGN, player_name="Daniel Gentile")
    row = df.iloc[0]
    return analyze_game(
        row["Movetext"],
        player_color=row["Color"],
        player_outcome=row["Outcome"],
        chapter_url=row["ChapterURL"],
    )


class TestPromptAssembly:
    """The prompt is built from precomputed facts — the model summarises, it
    never evaluates the position itself (the hard boundary, issue #59)."""

    def test_prompt_carries_the_critical_moment_and_worst_error_facts(self):
        prompt = build_prompt(_alice())
        # The Game's headline fact: the move-16 Bd4 blunder that decided it.
        assert "16" in prompt
        assert "Bd4" in prompt
        # The player's own worst error, classified: g5, an inaccuracy.
        assert "g5" in prompt
        assert "inaccuracy" in prompt.lower()

    def test_prompt_forbids_evaluating_the_position(self):
        # It must summarise the given facts, never analyse the chess itself.
        prompt = build_prompt(_alice())
        assert "not" in prompt.lower()
        lowered = prompt.lower()
        assert "evaluate" in lowered or "analyse" in lowered or "analyze" in lowered


class TestSummarize:
    """``summarize`` runs the boundary on the assembled prompt and returns its
    text — the network seam is mocked exactly like the USCF client."""

    def test_calls_the_boundary_with_the_assembled_prompt_and_returns_its_text(self):
        ga = _alice()
        with mock.patch.object(
            ai_summary, "_call_anthropic", return_value="You won after a blunder."
        ) as seam:
            result = summarize(ga, api_key="sk-test")

        assert result == "You won after a blunder."
        seam.assert_called_once()
        # It is handed the facts-only prompt, nothing else.
        assert seam.call_args.args[0] == build_prompt(ga)
        assert seam.call_args.kwargs["api_key"] == "sk-test"


class TestNoOpWithoutKey:
    """No key → the module does nothing, gracefully: the dashboard runs without
    one (issue #59).  An unanalysed Game has no facts to summarise either."""

    def test_no_api_key_returns_empty_and_never_calls_the_boundary(self):
        with mock.patch.object(ai_summary, "_call_anthropic") as seam:
            assert summarize(_alice(), api_key=None) == ""
            assert summarize(_alice(), api_key="") == ""
        seam.assert_not_called()

    def test_unanalysed_game_returns_empty_and_never_calls_the_boundary(self):
        unanalysed = analyze_game("1. e4 e5 2. Nf3 Nc6 1-0",
                                  player_color="White", player_outcome="Win")
        assert unanalysed.analyzed is False
        with mock.patch.object(ai_summary, "_call_anthropic") as seam:
            assert summarize(unanalysed, api_key="sk-test") == ""
        seam.assert_not_called()


class TestSilentDegradation:
    """Any client failure degrades silently — a Sync that reached Lichess still
    succeeds (issue #59, ADR 0004)."""

    def test_client_error_returns_empty_instead_of_raising(self):
        with mock.patch.object(
            ai_summary, "_call_anthropic",
            side_effect=RuntimeError("Anthropic is down"),
        ):
            # Must not propagate — the summary is enrichment, never a dependency.
            assert summarize(_alice(), api_key="sk-test") == ""


class TestRateLimitRetry:
    """One bounded wait-and-retry rides out a transient 429/overload before
    degrading to '' (issue #87 [9])."""

    def test_retries_once_on_429_then_succeeds(self):
        rate_limited = mock.Mock(status_code=429, headers={"Retry-After": "0"})
        ok = mock.Mock(status_code=200, headers={})
        ok.json.return_value = {"content": [{"text": "Recovered."}]}
        with mock.patch.object(
            ai_summary, "_post", side_effect=[rate_limited, ok]
        ) as post, mock.patch.object(ai_summary.time, "sleep") as sleep:
            result = summarize(_alice(), api_key="sk-test")

        assert result == "Recovered."
        assert post.call_count == 2   # one retry, not a give-up
        sleep.assert_called_once()


class _FakeCache:
    """A minimal summary cache (the protocol the real AnalysisCache exposes):
    keyed by Game identity + a fingerprint of the facts."""

    def __init__(self):
        self._store: dict[str, tuple[str, str]] = {}

    def get_summary(self, chapter_url: str, fingerprint: str) -> str | None:
        entry = self._store.get(chapter_url)
        return entry[1] if entry and entry[0] == fingerprint else None

    def put_summary(self, chapter_url: str, fingerprint: str, text: str) -> None:
        self._store[chapter_url] = (fingerprint, text)


class TestCachingByGameIdentity:
    """An unchanged Game is served from the cache, never re-billed (issue #59)."""

    def test_second_summary_of_an_unchanged_game_does_not_call_the_boundary(self):
        ga = _alice()
        cache = _FakeCache()
        with mock.patch.object(
            ai_summary, "_call_anthropic", return_value="You won after a blunder."
        ) as seam:
            first = summarize(ga, api_key="sk-test", cache=cache)
            second = summarize(ga, api_key="sk-test", cache=cache)

        assert first == second == "You won after a blunder."
        seam.assert_called_once()  # billed once, then served from cache


class TestCacheFingerprintCoversModelAndSystem:
    """Bumping the model (or rewording the system prompt) must invalidate the
    cache so a stale summary isn't served forever (issue #91)."""

    def test_fingerprint_changes_with_the_model(self):
        a = ai_summary._fingerprint("model-old", "same prompt")
        b = ai_summary._fingerprint("model-new", "same prompt")
        assert a != b

    def test_changing_the_model_reruns_instead_of_serving_a_stale_summary(self):
        ga = _alice()
        cache = _FakeCache()
        with mock.patch.object(ai_summary, "_call_anthropic", return_value="Old."):
            summarize(ga, api_key="sk-test", model="model-old", cache=cache)
        with mock.patch.object(
            ai_summary, "_call_anthropic", return_value="New."
        ) as seam:
            again = summarize(ga, api_key="sk-test", model="model-new", cache=cache)
        assert again == "New."          # not the stale "Old." — the model change missed
        seam.assert_called_once()
