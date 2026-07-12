"""
tests/test_anonymize_pgn.py
===========================
The demo-seed anonymizer (issue #89 [F5]).

Opponent names, real Event/Site names and game Dates are replaced with synthetic
values, while the chess itself — moves and the ``Lesson:`` comments — stays
byte-for-byte intact even when a real Event name is a common word.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "anonymize_pgn", Path(__file__).resolve().parent.parent / "scripts" / "anonymize_pgn.py"
)
anon = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(anon)


_PGN = '''[Event "Open"]
[Site "Realtown, VA"]
[Date "2025.06.20"]
[White "Daniel Gentile"]
[Black "Real Opponent"]
[WhiteElo "1500"]

1. e4 { Lesson: Open the position when ahead in development } e5 2. Nf3 1-0
'''


def test_event_and_site_headers_are_synthesised():
    out = anon.anonymize(_PGN)
    assert '[Event "Open"]' not in out
    assert "Realtown" not in out
    assert "Real Opponent" not in out


def test_lesson_comment_is_preserved_byte_for_byte():
    # The Event value "Open" also appears in the comment; a header-only rewrite
    # must not touch it, so the coaching note stays exactly as written.
    out = anon.anonymize(_PGN)
    assert "{ Lesson: Open the position when ahead in development }" in out


def test_self_player_is_kept_and_dates_shift():
    out = anon.anonymize(_PGN)
    assert "Daniel Gentile" in out          # the player's own dashboard
    assert '[Date "2025.06.20"]' not in out  # real date perturbed
    assert '[Date "' in out                  # but a (shifted) date remains


def test_offset_is_not_a_recoverable_constant():
    # Two different sources yield different offsets, so the shift can't be
    # reversed by reading this committed script.
    other = _PGN.replace("e5 2. Nf3", "c5 2. Nf3")
    assert anon._date_offset(_PGN) != anon._date_offset(other)
