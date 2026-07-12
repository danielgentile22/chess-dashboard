"""
analysis_cache.py
=================
The disposable cache for engine analysis (issue #59 [F5]).

Same lifecycle as ``uscf_cache.json`` (ADR 0003, extended by ADR 0004): a local
JSON file that is **never a source of truth**.  Every filesystem misfortune —
missing file, corrupt file, unwritable disk, no path configured — degrades to
"no cache", never to an error.

What it holds: the billable AI summaries, keyed by Game identity (the permanent
ChapterURL) together with a fingerprint of the facts that produced them.  An
*unchanged* Game (same facts → same fingerprint) is served from the cache and
never re-billed; a re-analysed Game whose facts moved misses and is summarised
afresh.  Parsing the analyses themselves is pure, deterministic, and cheap, so
those are recomputed each Sync (ADR 0004) rather than cached here.

The ``_read`` / ``_write`` pair intentionally mirrors ``sync.UscfCache``'s
atomic-write / degrade-never-raise I/O rather than sharing it. Both are ~10
trivial lines with no other callers, so a shared cache-I/O module would add an
import and an indirection for less code than it removes; the duplication is the
cheaper of the two. If a third disposable cache ever needs the same pattern,
that's the point to extract it.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AnalysisCache"]


class AnalysisCache:
    """The local cache of engine-analysis AI summaries.

    Disposable and best-effort: on a host without a readable/writable disk it
    simply caches nothing for this run.  ``path=None`` disables caching outright
    (the no-op the dashboard uses when no cache is configured).
    """

    def __init__(self, path: str | None):
        self._path = path
        self._data: dict[str, Any] = self._read()

    # -- AI summaries (keyed by Game identity + facts fingerprint) -----------

    def get_summary(self, chapter_url: str, fingerprint: str) -> str | None:
        """The cached summary for this Game *iff* its facts are unchanged.

        A fingerprint mismatch (the Game was re-analysed) is a miss, so the
        stale summary is never served."""
        entry = self._data.get("summaries", {}).get(chapter_url)
        if isinstance(entry, dict) and entry.get("fp") == fingerprint:
            return entry.get("text")
        return None

    def put_summary(self, chapter_url: str, fingerprint: str, text: str) -> None:
        """Remember *text* as the summary for this Game's current facts.

        Best-effort persistence: on a host without a writable disk the summary
        lasts for this run only."""
        summaries = self._data.setdefault("summaries", {})
        summaries[chapter_url] = {"fp": fingerprint, "text": text}
        self._write()

    # -- file I/O (failures degrade, never raise) ----------------------------

    def _read(self) -> dict[str, Any]:
        if not self._path or not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read analysis cache %r (starting empty): %s",
                           self._path, exc)
            return {}
        if not isinstance(data, dict):
            return {}
        # Drop a wrong-typed "summaries" section so get_summary's nested .get
        # never raises on a truncated / hand-edited file (issue #87 [7]).
        if not isinstance(data.get("summaries"), dict):
            data.pop("summaries", None)
        return data

    def _write(self) -> None:
        if not self._path:
            return
        try:
            tmp_path = f"{self._path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            logger.warning("Could not write analysis cache %r (continuing "
                           "without): %s", self._path, exc)
