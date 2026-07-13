"""
tests/test_issue_93.py
=======================
Focused checks for issue #93: the filter-clamp helpers and the PGN header
escaping that keep the filter drawer and the pgn-viewer honest.
"""
from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# filters._clamp_moves / _clamp_date — a shrinking Sync must not strand the
# selection outside the new bounds (finding #8).
# ---------------------------------------------------------------------------

class TestClampHelpers:
    def test_moves_in_range_is_left_alone(self):
        from dash import no_update

        from filters import _clamp_moves
        assert _clamp_moves([5, 20], 1, 40) is no_update

    def test_moves_out_of_range_gets_clamped(self):
        from filters import _clamp_moves
        assert _clamp_moves([5, 60], 1, 40) == [5, 40]
        assert _clamp_moves([0, 60], 10, 40) == [10, 40]

    def test_date_in_range_is_left_alone(self):
        from dash import no_update

        from filters import _clamp_date
        assert _clamp_date("2024-06-15", "2024-01-01", "2024-12-31") is no_update

    def test_date_below_range_snaps_to_min(self):
        from filters import _clamp_date
        assert _clamp_date("2023-01-01", "2024-01-01", "2024-12-31") == "2024-01-01"

    def test_empty_date_is_left_alone(self):
        from dash import no_update

        from filters import _clamp_date
        assert _clamp_date(None, "2024-01-01", "2024-12-31") is no_update


# ---------------------------------------------------------------------------
# game_detail._game_pgn — a quote/backslash in a header value must be escaped
# or the generated PGN is malformed and breaks the board (finding #9).
# ---------------------------------------------------------------------------

class TestPgnHeaderEscaping:
    def test_quotes_and_backslashes_are_escaped(self, ui_app, ui_data):
        from pages import game_detail

        game = pd.Series({
            "Color": "White",
            "Opponent": r'Bob "Tal" \Smith',
            "Event": '2025 "Summer" Open',
            "Date": "2025.06.01",
            "Round": "1",
            "Result": "1-0",
            "SetupFEN": "",
        })
        pgn = game_detail._game_pgn(game, "1. e4 e5 1-0")

        assert r'[Event "2025 \"Summer\" Open"]' in pgn
        assert r'[Black "Bob \"Tal\" \\Smith"]' in pgn
        # No unescaped inner quote leaks through (only the tag delimiters remain).
        assert '2025 "Summer"' not in pgn
