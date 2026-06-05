"""
tests/test_game_detail_coach.py
===============================
The Coach view on Game detail (issue #74 [G4]).

The board switcher gains a fourth tab, Coach, rendered with the same
open-source pgn-viewer — but only when the coach reviewed that Game.  A Game
with no coach match simply has no Coach tab, gracefully rather than as an error.
Rendering stays smoke-tested per the existing UI suite.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import sync

SNAPSHOT_PGN = (
    Path(__file__).parent / "fixtures" / "uscf" / "lichess-study-snapshot.pgn"
).read_text()
COACH_PGN = (Path(__file__).parent / "fixtures" / "coach-study.pgn").read_text()

GEORGINA_ID = "GKSICQAY"          # the coach reviewed this Game
UNREVIEWED_ID = "RP1Age0G"        # a real Game the coach never reviewed


def _stub(**study_pgns):
    def fake(study_id, **kwargs):
        value = study_pgns[study_id]
        if isinstance(value, Exception):
            raise value
        return value
    return mock.patch.object(sync, "fetch_study_pgn", side_effect=fake)


def _button_labels(node) -> list[str]:
    """Every Button's text label anywhere in a rendered component tree."""
    labels: list[str] = []

    def walk(n):
        if n is None:
            return
        if isinstance(n, (list, tuple)):
            for item in n:
                walk(item)
            return
        children = getattr(n, "children", None)
        if type(n).__name__ == "Button" and isinstance(children, str):
            labels.append(children)
        if children is not None and not isinstance(children, str):
            walk(children)

    walk(node)
    return labels


def _record(coach_studies):
    from user_config import UserRecord, hash_password

    return UserRecord(username="daniel", password_hash=hash_password("pw"),
                      study_ids=("s-main",), coach_study_ids=coach_studies,
                      uscf_member_id=None, lichess_token="lip_x")


def _setup_daniel(coach_studies, tmp_path, **study_pgns):
    import data

    data.reset()
    with _stub(**study_pgns):
        data.register_users({"daniel": _record(coach_studies)},
                            data_dir=str(tmp_path))
        data.sync_user("daniel")
    data.activate("daniel")


class TestCoachTab:
    def test_coach_tab_appears_for_a_reviewed_game(self, ui_app, tmp_path):
        import data
        from pages import game_detail

        _setup_daniel(("s-coach",), tmp_path,
                      **{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN})
        try:
            layout = game_detail.layout(chapter_id=GEORGINA_ID)
            labels = _button_labels(layout)
            assert "Coach" in labels
            # the full four-view switcher
            assert "Game" in labels and "Engine" in labels
        finally:
            data.reset()

    def test_no_coach_tab_for_an_unreviewed_game(self, ui_app, tmp_path):
        import data
        from pages import game_detail

        _setup_daniel(("s-coach",), tmp_path,
                      **{"s-main": SNAPSHOT_PGN, "s-coach": COACH_PGN})
        try:
            layout = game_detail.layout(chapter_id=UNREVIEWED_ID)
            labels = _button_labels(layout)
            assert "Coach" not in labels       # graceful absence
            assert "Game" in labels            # the page still renders
        finally:
            data.reset()

    def test_no_coach_tab_when_no_coach_studies_configured(self, ui_app, tmp_path):
        import data
        from pages import game_detail

        _setup_daniel((), tmp_path, **{"s-main": SNAPSHOT_PGN})
        try:
            layout = game_detail.layout(chapter_id=GEORGINA_ID)
            assert "Coach" not in _button_labels(layout)
        finally:
            data.reset()
