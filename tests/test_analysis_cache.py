"""
tests/test_analysis_cache.py
============================
The disposable analysis cache (issue #59 [F5]).

Same lifecycle as ``uscf_cache.json`` (ADR 0003/0004): a local JSON file that is
never a source of truth.  Every filesystem misfortune — missing file, corrupt
file, unwritable disk, no path at all — degrades to "no cache", never to an
error.  It caches the billable AI summaries by Game identity + a fingerprint of
the facts, so an unchanged Game is never re-billed and a re-analysed one is.
"""
from __future__ import annotations

from analysis_cache import AnalysisCache

URL = "https://lichess.org/study/abc/chap01"


class TestSummaryRoundTrip:
    def test_a_summary_survives_a_restart(self, tmp_path):
        path = str(tmp_path / "analysis_cache.json")
        AnalysisCache(path).put_summary(URL, "fp-1", "You won after a blunder.")

        reopened = AnalysisCache(path)  # a fresh instance = an app restart
        assert reopened.get_summary(URL, "fp-1") == "You won after a blunder."

    def test_changed_facts_miss_so_a_stale_summary_is_never_served(self, tmp_path):
        path = str(tmp_path / "analysis_cache.json")
        cache = AnalysisCache(path)
        cache.put_summary(URL, "fp-old", "Old verdict.")
        # The Game was re-analysed: the fingerprint moved → a miss, not stale data.
        assert cache.get_summary(URL, "fp-new") is None

    def test_unknown_game_is_a_miss(self, tmp_path):
        cache = AnalysisCache(str(tmp_path / "analysis_cache.json"))
        assert cache.get_summary("https://lichess.org/study/x/never", "fp") is None


class TestDegradesNeverRaises:
    """Every filesystem misfortune degrades to "no cache" (ADR 0003/0004)."""

    def test_missing_file_is_just_empty(self, tmp_path):
        cache = AnalysisCache(str(tmp_path / "never-written.json"))
        assert cache.get_summary(URL, "fp") is None

    def test_corrupt_file_is_tolerated(self, tmp_path):
        path = tmp_path / "analysis_cache.json"
        path.write_text("{not valid json", encoding="utf-8")
        cache = AnalysisCache(str(path))  # must not raise
        assert cache.get_summary(URL, "fp") is None
        # ...and is still usable: a fresh write recovers it
        cache.put_summary(URL, "fp", "recovered")
        assert AnalysisCache(str(path)).get_summary(URL, "fp") == "recovered"

    def test_unwritable_path_fails_silently(self):
        cache = AnalysisCache("/nonexistent-dir/sub/analysis_cache.json")
        cache.put_summary(URL, "fp", "text")  # must not raise
        assert cache.get_summary(URL, "fp") == "text"  # still in memory this run

    def test_no_path_means_no_caching(self):
        cache = AnalysisCache(None)
        cache.put_summary(URL, "fp", "text")  # no-op, never raises
        assert cache.get_summary(URL, "fp") == "text"  # in memory only
