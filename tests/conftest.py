"""
tests/conftest.py
=================
Shared fixtures for the chess stats test suite.
"""
from __future__ import annotations

import socket
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """
    The suite must never touch the network (PRD testing decision).
    Any unstubbed HTTP call fails loudly instead of silently hitting Lichess.
    """
    def guard(*args, **kwargs):
        raise RuntimeError(
            "Network access attempted during tests — stub the Lichess client."
        )

    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)

# ---------------------------------------------------------------------------
# Sample PGN with 7 games covering many scenarios.
# Shaped like a Lichess Study export: each game is one Chapter with
# StudyName / ChapterName / ChapterURL headers and chapter comments.
#
# Comment coverage (ADR 0002 — Lessons / Tags):
#   game 1: chapter-level Lesson with a tag
#   game 2: no comments at all
#   game 3: comments with hashtags but no Lesson (mixed-case tag)
#   game 4: two Lessons (mixed-case prefixes), one inside a variation
#   game 5: tags spread across comments, duplicated tag, [%clk] noise
#   games 6-7: no comments
#
# Time control coverage (issue #17):
#   games 1-3: classical USCF multi-stage control ("40/80, SD30; +30")
#   games 4-6: rapid ("30+5")
#   game 7:    no TimeControl header at all
# ---------------------------------------------------------------------------

SAMPLE_PGN = """\
[Event "Test Open"]
[Site "Springfield"]
[Date "2024.01.06"]
[Round "1"]
[White "Test Player"]
[Black "Opponent A"]
[WhiteElo "1800"]
[BlackElo "1920"]
[TimeControl "40/80, SD30; +30"]
[ECO "E04"]
[Opening "Catalan Opening: Open Defense"]
[Result "1-0"]
[Termination "win by resignation"]
[StudyName "Test Study"]
[ChapterName "Test Player - Opponent A"]
[ChapterURL "https://lichess.org/study/teststudy/chap0001"]

{ Lesson: Keep the tension in the center instead of releasing it early. #strategy }
1. d4 Nf6 2. c4 e6 3. g3 d5 4. Bg2 dxc4 5. Nf3 Be7 6. O-O O-O 1-0

[Event "Test Open"]
[Site "Springfield"]
[Date "2024.01.06"]
[Round "2"]
[White "Opponent B"]
[Black "Test Player"]
[WhiteElo "1750"]
[BlackElo "1800"]
[TimeControl "40/80, SD30; +30"]
[ECO "B12"]
[Opening "Caro-Kann Defense"]
[Result "1/2-1/2"]
[Termination "Normal"]
[StudyName "Test Study"]
[ChapterName "Opponent B - Test Player"]
[ChapterURL "https://lichess.org/study/teststudy/chap0002"]

1. e4 c6 2. d4 d5 3. e5 Bf5 4. c3 e6 1/2-1/2

[Event "Test Open"]
[Site "Springfield"]
[Date "2024.01.07"]
[Round "3"]
[White "Test Player"]
[Black "Opponent C"]
[WhiteElo "1800"]
[BlackElo "2050"]
[TimeControl "40/80, SD30; +30"]
[ECO "A45"]
[Opening "Indian Game"]
[Result "0-1"]
[Termination "loss by resignation"]
[StudyName "Test Study"]
[ChapterName "Test Player - Opponent C"]
[ChapterURL "https://lichess.org/study/teststudy/chap0003"]

1. d4 Nf6 2. e4 { dubious move order } d6 3. Nc3 e5 { hung the bishop here #blunder #Tactics } 0-1

[Event "Summer Cup"]
[Site "Shelbyville"]
[Date "2024.06.15"]
[Round "1"]
[White "Opponent A"]
[Black "Test Player"]
[WhiteElo "1930"]
[BlackElo "1810"]
[TimeControl "30+5"]
[ECO "E60"]
[Opening "King's Indian Defense"]
[Result "0-1"]
[Termination "loss by checkmate"]
[StudyName "Test Study"]
[ChapterName "Opponent A - Test Player"]
[ChapterURL "https://lichess.org/study/teststudy/chap0004"]

{ LESSON: Don't grab pawns while behind in development. }
1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 (3... d5 { lesson: Castle before starting an attack. #opening }) 4. e4 d6 0-1

[Event "Summer Cup"]
[Site "Shelbyville"]
[Date "2024.06.15"]
[Round "2"]
[White "Test Player"]
[Black "Opponent D"]
[WhiteElo "1810"]
[BlackElo "1600"]
[TimeControl "30+5"]
[ECO "C50"]
[Opening "Italian Game"]
[Result "1-0"]
[Termination "win by checkmate"]
[StudyName "Test Study"]
[ChapterName "Test Player - Opponent D"]
[ChapterURL "https://lichess.org/study/teststudy/chap0005"]

1. e4 e5 2. Nf3 { [%clk 1:30:00] sharp position #tactics } Nc6 3. Bc4 Bc5 4. Nc3 { converted the attack #endgame #tactics } Nf6 1-0

[Event "Summer Cup"]
[Site "Shelbyville"]
[Date "2024.06.16"]
[Round "3"]
[White "Opponent B"]
[Black "Test Player"]
[WhiteElo "1760"]
[BlackElo "1810"]
[TimeControl "30+5"]
[ECO "B12"]
[Opening "Caro-Kann Defense"]
[Result "0-1"]
[Termination "win by resignation"]
[StudyName "Test Study"]
[ChapterName "Opponent B - Test Player"]
[ChapterURL "https://lichess.org/study/teststudy/chap0006"]

1. e4 c6 2. d4 d5 3. e5 Bf5 0-1

[Event "Summer Cup"]
[Site "Shelbyville"]
[Date "2024.06.16"]
[Round "4"]
[White "Test Player"]
[Black "Opponent A"]
[WhiteElo "1810"]
[BlackElo "1925"]
[ECO "E04"]
[Opening "Catalan Opening"]
[Result "1/2-1/2"]
[Termination "Normal"]
[StudyName "Test Study"]
[ChapterName "Test Player - Opponent A"]
[ChapterURL "https://lichess.org/study/teststudy/chap0007"]

1. d4 Nf6 2. c4 e6 3. g3 d5 1/2-1/2
"""


# ---------------------------------------------------------------------------
# A second Study (the archive spans multiple Studies once the 64-chapter
# limit is hit — ADR 0001). Dates interleave with the first Study's games,
# and the last game is an exact duplicate of SAMPLE_PGN's chap0007 to cover
# ChapterURL dedup.
# ---------------------------------------------------------------------------

SAMPLE_PGN_STUDY2 = """\
[Event "Spring Rapid"]
[Site "Springfield"]
[Date "2024.03.10"]
[Round "1"]
[White "Test Player"]
[Black "Opponent E"]
[WhiteElo "1805"]
[BlackElo "1700"]
[ECO "B01"]
[Opening "Scandinavian Defense"]
[Result "1-0"]
[Termination "win by resignation"]
[StudyName "Test Study 2"]
[ChapterName "Test Player - Opponent E"]
[ChapterURL "https://lichess.org/study/teststud2/chap0021"]

{ Lesson: Punish early queen development. #opening }
1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 4. d4 c6 1-0

[Event "Autumn Open"]
[Site "Shelbyville"]
[Date "2024.09.01"]
[Round "1"]
[White "Opponent A"]
[Black "Test Player"]
[WhiteElo "1940"]
[BlackElo "1815"]
[ECO "D02"]
[Opening "London System"]
[Result "1/2-1/2"]
[Termination "Normal"]
[StudyName "Test Study 2"]
[ChapterName "Opponent A - Test Player"]
[ChapterURL "https://lichess.org/study/teststud2/chap0022"]

1. d4 d5 2. Nf3 Nf6 3. Bf4 c5 1/2-1/2

[Event "Summer Cup"]
[Site "Shelbyville"]
[Date "2024.06.16"]
[Round "4"]
[White "Test Player"]
[Black "Opponent A"]
[WhiteElo "1810"]
[BlackElo "1925"]
[ECO "E04"]
[Opening "Catalan Opening"]
[Result "1/2-1/2"]
[Termination "Normal"]
[StudyName "Test Study"]
[ChapterName "Test Player - Opponent A"]
[ChapterURL "https://lichess.org/study/teststudy/chap0007"]

1. d4 Nf6 2. c4 e6 3. g3 d5 1/2-1/2
"""


@pytest.fixture(scope="session")
def sample_pgn_text() -> str:
    """The sample PGN as raw text (what the Lichess client returns)."""
    return SAMPLE_PGN


@pytest.fixture(scope="session")
def sample_pgn_study2_text() -> str:
    """A second Study's PGN: 2 new games + 1 duplicate of SAMPLE_PGN's chap0007."""
    return SAMPLE_PGN_STUDY2


@pytest.fixture(scope="session")
def sample_pgn_path(tmp_path_factory) -> Path:
    """Write SAMPLE_PGN to a temp file and return its path."""
    p = tmp_path_factory.mktemp("pgn") / "test_games.pgn"
    p.write_text(SAMPLE_PGN, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# UI fixtures (multi-page shell — Phase 2)
#
# Page modules call dash.register_page() at import, which Dash only allows
# after a Dash app exists.  ``ui_app`` therefore builds the real app once per
# session (with the Lichess client stubbed); ``ui_data`` re-initializes the
# module-level data store before each UI test so tests stay isolated from
# whatever other test files did to it.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ui_app():
    """The real Dash app built once with fixture data (Lichess stubbed)."""
    import data
    import sync

    data.reset()
    with mock.patch.object(sync, "fetch_study_pgn", return_value=SAMPLE_PGN):
        from app import build_app
        dash_app, _server = build_app(["teststudy"], player_name="Test Player")
    return dash_app


@pytest.fixture()
def ui_data(sample_pgn_text):
    """A freshly initialized data store for each UI test."""
    import data
    import sync

    data.reset()
    with mock.patch.object(sync, "fetch_study_pgn", return_value=sample_pgn_text):
        data.initialize(["teststudy"], player_name="Test Player")
    yield
    data.reset()


@pytest.fixture(scope="session")
def sample_df(sample_pgn_path) -> tuple[pd.DataFrame, str]:
    """Return (df, player_name) loaded from the sample PGN."""
    from pgn_stats_core import load_games_df
    return load_games_df(str(sample_pgn_path), player_name="Test Player")


@pytest.fixture(scope="session")
def df(sample_df) -> pd.DataFrame:
    return sample_df[0]


@pytest.fixture(scope="session")
def player(sample_df) -> str:
    return sample_df[1]
