"""
tests/conftest.py
=================
Shared fixtures for the chess stats test suite.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Sample PGN with 7 games covering many scenarios
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
[ECO "E04"]
[Opening "Catalan Opening: Open Defense"]
[Result "1-0"]
[Termination "win by resignation"]

1. d4 Nf6 2. c4 e6 3. g3 d5 4. Bg2 dxc4 5. Nf3 Be7 6. O-O O-O 1-0

[Event "Test Open"]
[Site "Springfield"]
[Date "2024.01.06"]
[Round "2"]
[White "Opponent B"]
[Black "Test Player"]
[WhiteElo "1750"]
[BlackElo "1800"]
[ECO "B12"]
[Opening "Caro-Kann Defense"]
[Result "1/2-1/2"]
[Termination "Normal"]

1. e4 c6 2. d4 d5 3. e5 Bf5 4. c3 e6 1/2-1/2

[Event "Test Open"]
[Site "Springfield"]
[Date "2024.01.07"]
[Round "3"]
[White "Test Player"]
[Black "Opponent C"]
[WhiteElo "1800"]
[BlackElo "2050"]
[ECO "A45"]
[Opening "Indian Game"]
[Result "0-1"]
[Termination "loss by resignation"]

1. d4 Nf6 2. e4 d6 3. Nc3 e5 0-1

[Event "Summer Cup"]
[Site "Shelbyville"]
[Date "2024.06.15"]
[Round "1"]
[White "Opponent A"]
[Black "Test Player"]
[WhiteElo "1930"]
[BlackElo "1810"]
[ECO "E60"]
[Opening "King's Indian Defense"]
[Result "0-1"]
[Termination "loss by checkmate"]

1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6 0-1

[Event "Summer Cup"]
[Site "Shelbyville"]
[Date "2024.06.15"]
[Round "2"]
[White "Test Player"]
[Black "Opponent D"]
[WhiteElo "1810"]
[BlackElo "1600"]
[ECO "C50"]
[Opening "Italian Game"]
[Result "1-0"]
[Termination "win by checkmate"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Nc3 Nf6 1-0

[Event "Summer Cup"]
[Site "Shelbyville"]
[Date "2024.06.16"]
[Round "3"]
[White "Opponent B"]
[Black "Test Player"]
[WhiteElo "1760"]
[BlackElo "1810"]
[ECO "B12"]
[Opening "Caro-Kann Defense"]
[Result "0-1"]
[Termination "win by resignation"]

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

1. d4 Nf6 2. c4 e6 3. g3 d5 1/2-1/2
"""


@pytest.fixture(scope="session")
def sample_pgn_path(tmp_path_factory) -> Path:
    """Write SAMPLE_PGN to a temp file and return its path."""
    p = tmp_path_factory.mktemp("pgn") / "test_games.pgn"
    p.write_text(SAMPLE_PGN, encoding="utf-8")
    return p


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
