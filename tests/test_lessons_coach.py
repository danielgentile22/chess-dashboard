"""
tests/test_lessons_coach.py
===========================
The Coach's Notes feed on the Lessons page (issue #75 [G5]).

A second feed beside "My Lessons": the coach's prose on matched Chapters,
newest first, each linking to its Game, kept visually distinct.  Only matched
Games contribute.  Rendering stays smoke-tested per the existing UI suite.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import sync

SNAPSHOT_PGN = (
    Path(__file__).parent / "data" / "uscf" / "lichess-study-snapshot.pgn"
).read_text()
COACH_PGN = (Path(__file__).parent / "data" / "coach-study.pgn").read_text()


def _stub(**study_pgns):
    def fake(study_id, **kwargs):
        value = study_pgns[study_id]
        if isinstance(value, Exception):
            raise value
        return value
    return mock.patch.object(sync, "fetch_study_pgn", side_effect=fake)


def _record(coach_studies):
    from user_config import UserRecord, hash_password

    return UserRecord(username="daniel", password_hash=hash_password("pw"),
                      study_ids=("s-main",), coach_study_ids=coach_studies,
                      uscf_member_id=None, lichess_token="lip_x")


def _setup(coach_studies, tmp_path, **study_pgns):
    import data

    data.reset()
    with _stub(**study_pgns):
        data.register_users({"daniel": _record(coach_studies)},
                            data_dir=str(tmp_path))
        data.sync_user("daniel")
    data.activate("daniel")


def _walk(node, classnames, hrefs, texts):
    if node is None:
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            _walk(item, classnames, hrefs, texts)
        return
    cls = getattr(node, "className", None)
    if isinstance(cls, str):
        classnames.add(cls)
    href = getattr(node, "href", None)
    if isinstance(href, str):
        hrefs.append(href)
    children = getattr(node, "children", None)
    if isinstance(children, str):
        texts.append(children)
    elif children is not None:
        _walk(children, classnames, hrefs, texts)


def _scan(layout):
    classnames: set[str] = set()
    hrefs: list[str] = []
    texts: list[str] = []
    _walk(layout, classnames, hrefs, texts)
    return classnames, hrefs, texts


class TestCoachNotesFeed:
    def test_feed_renders_distinct_with_links(self, ui_app, tmp_path):
        import data
        from pages import lessons

        _setup(("s-coach",), tmp_path,
               **{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN})
        try:
            classnames, hrefs, texts = _scan(lessons.layout())
            # the feed exists and is its own visually-distinct surface
            assert "coach-notes-feed" in classnames
            assert any("coach-note-card" in c for c in classnames)
            # the coach's prose is shown…
            assert any("Caro-Kann again" in t for t in texts)
            # …and each note links back to its Game
            assert any(h.startswith("/game/") for h in hrefs)
        finally:
            data.reset()

    def test_feed_absent_without_coach_content(self, ui_app, tmp_path):
        import data
        from pages import lessons

        _setup((), tmp_path, **{"s-main": SNAPSHOT_PGN})
        try:
            classnames, _hrefs, _texts = _scan(lessons.layout())
            assert "coach-notes-feed" not in classnames
        finally:
            data.reset()

    def test_only_matched_games_notes_appear(self, ui_app, tmp_path):
        import data
        from pages import lessons

        _setup(("s-coach",), tmp_path,
               **{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN})
        try:
            _classnames, _hrefs, texts = _scan(lessons.layout())
            blob = " ".join(texts)
            # the coach's teaching positions / online games never reach the feed
            assert "teaching joke" not in blob
            assert "Build the bridge" not in blob
        finally:
            data.reset()
