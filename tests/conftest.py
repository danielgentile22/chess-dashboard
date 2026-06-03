"""
tests/conftest.py
=================
Shared fixtures for the chess stats test suite.
"""
from __future__ import annotations

import json
import socket
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

# Real USCF MUIR API responses captured live on 2026-06-02 (issue #25 / PRD #24).
# These are the canonical "real response shapes" the USCF tests run against.
USCF_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "uscf"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """
    The suite must never touch the network (PRD testing decision).
    Any unstubbed HTTP call fails loudly instead of silently hitting Lichess.
    """
    def guard(*args, **kwargs):
        raise RuntimeError(
            "Network access attempted during tests — stub the Lichess/USCF clients."
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
[WhiteFideId "99999999"]
[WhiteElo "1800"]
[BlackFideId "10000001"]
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
[WhiteFideId "10000002"]
[WhiteElo "1750"]
[BlackFideId "99999999"]
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
[WhiteFideId "99999999"]
[WhiteElo "1800"]
[BlackFideId "10000003"]
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
[WhiteFideId "10000001"]
[WhiteElo "1930"]
[BlackFideId "99999999"]
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
[WhiteFideId "99999999"]
[WhiteElo "1810"]
[BlackFideId "10000004"]
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
[WhiteFideId "10000002"]
[WhiteElo "1760"]
[BlackFideId "99999999"]
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


# ---------------------------------------------------------------------------
# USCF fixtures: real API response shapes (captured 2026-06-02)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def uscf_profile_json() -> dict:
    """A real /members/{id} response: 6 rating systems, ranks, floor, membership."""
    return json.loads((USCF_FIXTURES_DIR / "member-profile.json").read_text())


@pytest.fixture(scope="session")
def uscf_supplements_json() -> dict:
    """A real /members/{id}/rating-supplements response (10 monthly supplements)."""
    return json.loads((USCF_FIXTURES_DIR / "rating-supplements.json").read_text())


@pytest.fixture(scope="session")
def uscf_sections_json() -> dict:
    """A real /members/{id}/sections response: 24 Sections across 12 months,
    including dual-rated, Online-Regular, zero-change, and same-day Sections."""
    return json.loads((USCF_FIXTURES_DIR / "sections.json").read_text())


@pytest.fixture(scope="session")
def uscf_games_json() -> dict:
    """A real /members/{id}/games response: 63 USCF Game Records — every rated
    game with opponent member ID, color, outcome, Rated Event, and Section."""
    return json.loads((USCF_FIXTURES_DIR / "games.json").read_text())


@pytest.fixture(scope="session")
def uscf_norms_json() -> dict:
    """A real /members/{id}/norms response: the FourthCategory norm from the
    First Annual Oak Grove Open (issue #36).  Note: no pagination fields —
    the norms endpoint returns bare items."""
    return json.loads((USCF_FIXTURES_DIR / "norms.json").read_text())


@pytest.fixture(scope="session")
def uscf_awards_json() -> dict:
    """A real /members/{id}/awards response: the 25th-career-win WinMilestone
    award (issue #36)."""
    return json.loads((USCF_FIXTURES_DIR / "awards.json").read_text())


@pytest.fixture(scope="session")
def study_snapshot_df():
    """
    Daniel's real Study (63 chapters), captured the same day as games.json —
    the matching engine's ground-truth fixture pair (issue #28).

    Parsed through the real PGN loader so matching tests see exactly what a
    Sync produces.
    """
    from pgn_stats_core import load_games_from_text

    pgn_text = (USCF_FIXTURES_DIR / "lichess-study-snapshot.pgn").read_text()
    df, _player = load_games_from_text(pgn_text, player_name="Daniel Gentile")
    return df


# ---------------------------------------------------------------------------
# USCF Game Records that pair with SAMPLE_PGN (issues #28 / #29 / #30).
#
# Records 1–5 match SAMPLE_PGN games 1–5 by opponent member ID + result.
# Record 4 disagrees about the color (the chapter says Test Player was Black,
# USCF says White) → a Reconciliation conflict + ⚠ badge.
# Game 6 has an ID but no record (a Game USCF hasn't rated → Lichess-only).
# Game 7 has no FideId typed → matches record 6 by name (the fallback pass)
# and is listed as a missing-FideId entry.
# The last record matches no Game at all (USCF-only → Reconciliation).
# ---------------------------------------------------------------------------

def _sample_uscf_game(opponent_id, first, last, player_color, player_outcome,
                      event, event_id, start, end, section="OPEN",
                      rating_system="R"):
    opponent_color = "Black" if player_color == "White" else "White"
    opponent_outcome = {"Win": "Loss", "Loss": "Win", "Draw": "Draw"}[player_outcome]
    return {
        "section": {"id": f"section-{event_id}", "number": 1, "name": section},
        "event": {"id": event_id, "name": event,
                  "startDate": start, "endDate": end, "stateCode": "VA"},
        "ratingSystem": rating_system,
        "player": {"color": player_color, "outcome": player_outcome},
        "opponent": {"id": opponent_id, "firstName": first, "lastName": last,
                     "stateRep": "VA", "color": opponent_color,
                     "outcome": opponent_outcome},
    }


SAMPLE_USCF_GAMES = [
    # → game 1: Test Player (White) beat Opponent A in the January open
    _sample_uscf_game("10000001", "OPPONENT", "A", "White", "Win",
                      "TEST OPEN JANUARY", "202401070001", "2024-01-06", "2024-01-07"),
    # → game 2: drew Opponent B with Black
    _sample_uscf_game("10000002", "OPPONENT", "B", "Black", "Draw",
                      "TEST OPEN JANUARY", "202401070001", "2024-01-06", "2024-01-07"),
    # → game 3: lost to Opponent C with White
    _sample_uscf_game("10000003", "OPPONENT", "C", "White", "Loss",
                      "TEST OPEN JANUARY", "202401070001", "2024-01-06", "2024-01-07"),
    # → game 4: beat Opponent A again — but USCF says White where the chapter
    #   says Black: a color conflict (matched anyway; flagged in Reconciliation)
    _sample_uscf_game("10000001", "OPPONENT", "A", "White", "Win",
                      "SUMMER CUP 2024", "202406160002", "2024-06-15", "2024-06-16"),
    # → game 5: beat Opponent D with White
    _sample_uscf_game("10000004", "OPPONENT", "D", "White", "Win",
                      "SUMMER CUP 2024", "202406160002", "2024-06-15", "2024-06-16"),
    # → game 7 (no FideId typed): drew Opponent A — matches by name (issue #29)
    _sample_uscf_game("10000001", "OPPONENT", "A", "White", "Draw",
                      "SUMMER CUP 2024", "202406160002", "2024-06-15", "2024-06-16"),
    # → no Game: a rated game whose Chapter was never added (USCF-only)
    _sample_uscf_game("10000005", "EXTRA", "OPPONENT", "White", "Win",
                      "SUMMER CUP 2024", "202406160002", "2024-06-15", "2024-06-16"),
]


# ---------------------------------------------------------------------------
# Sample supplements + sections that pair with SAMPLE_PGN (issue #32).
#
# SAMPLE_PGN's games are dated 2024, while the real captured supplements and
# sections are 2025–26 — so without these, no sample Game would have any
# value under the rating lens.  The supplement values equal the typed
# ratings (1800 / 1810), so the Official lens shows the same numbers the
# typed values always showed and no Reconciliation rating-mismatch appears;
# the Live values carry decimals so the Live lens is visibly different.
# ---------------------------------------------------------------------------

SAMPLE_USCF_SUPPLEMENTS = [
    {"ratingSupplementDate": "2024-01-01",
     "ratings": [{"source": "R", "rating": 1800}]},
    {"ratingSupplementDate": "2024-06-01",
     "ratings": [{"source": "R", "rating": 1810}]},
]


def _sample_uscf_section(event_id, event_name, start, end, pre, post,
                         section="OPEN"):
    """One raw /members/{id}/sections item (the real endpoint shape)."""
    return {
        "ratingSystem": "R",
        "sectionName": section,
        "event": {"id": event_id, "name": event_name,
                  "startDate": start, "endDate": end},
        "ratingRecords": [{
            "ratingSource": "R",
            "preRating": round(pre), "preRatingDecimal": pre,
            "postRating": round(post), "postRatingDecimal": post,
        }],
    }


SAMPLE_USCF_SECTIONS = [
    # Games 1–3's Rated Event; pre-rating carries decimals for Live-lens tests
    _sample_uscf_section("202401070001", "TEST OPEN JANUARY",
                         "2024-01-06", "2024-01-07", pre=1782.5, post=1800.5),
    # Games 4–7's Rated Event; chains from the January Section
    _sample_uscf_section("202406160002", "SUMMER CUP 2024",
                         "2024-06-15", "2024-06-16", pre=1800.5, post=1812.44),
]


@pytest.fixture(scope="session")
def sample_uscf_games() -> list[dict]:
    """USCF Game Records that pair with SAMPLE_PGN (5 matches + 1 USCF-only)."""
    return SAMPLE_USCF_GAMES


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
# session (with the Lichess and USCF clients stubbed); ``ui_data``
# re-initializes the module-level data store before each UI test so tests
# stay isolated from whatever other test files did to it.
# ---------------------------------------------------------------------------

# The same real response shapes, available without requesting the session
# fixtures (ui_app builds the app at session scope).
_UI_USCF_PROFILE = json.loads((USCF_FIXTURES_DIR / "member-profile.json").read_text())
REAL_USCF_SUPPLEMENTS = json.loads(
    (USCF_FIXTURES_DIR / "rating-supplements.json").read_text()
)["items"]
REAL_USCF_SECTIONS = json.loads((USCF_FIXTURES_DIR / "sections.json").read_text())["items"]
REAL_USCF_NORMS = json.loads((USCF_FIXTURES_DIR / "norms.json").read_text())["items"]
REAL_USCF_AWARDS = json.loads((USCF_FIXTURES_DIR / "awards.json").read_text())["items"]

# What UI fixtures feed by default: the real 2025–26 career (so the profile
# card and the rating series are real) plus the 2024 sample items that cover
# SAMPLE_PGN's Games (so the rating lens has values for them — issue #32).
_UI_USCF_SUPPLEMENTS = SAMPLE_USCF_SUPPLEMENTS + REAL_USCF_SUPPLEMENTS
_UI_USCF_SECTIONS = SAMPLE_USCF_SECTIONS + REAL_USCF_SECTIONS


@contextmanager
def stub_ui_sources(pgn_text: str, uscf_profile: dict | Exception = None,
                    uscf_games: list | None = None,
                    uscf_supplements: list | None = None,
                    uscf_sections: list | None = None,
                    uscf_norms: list | None = None,
                    uscf_awards: list | None = None):
    """
    Patch both clients at sync's module boundary for UI fixtures/tests:
    Lichess returns *pgn_text*; USCF returns the real captured responses.
    Pass an Exception as *uscf_profile* to simulate USCF being down
    (every endpoint raises it).  *uscf_games* defaults to the records that
    pair with SAMPLE_PGN, so UI tests render against matched Games;
    *uscf_supplements* / *uscf_sections* default to the real career extended
    with the 2024 sample items that cover SAMPLE_PGN; *uscf_norms* /
    *uscf_awards* default to the real captured achievements (issue #36).
    """
    import sync

    if uscf_profile is None:
        uscf_profile = _UI_USCF_PROFILE
    if uscf_games is None:
        uscf_games = SAMPLE_USCF_GAMES
    if uscf_supplements is None:
        uscf_supplements = _UI_USCF_SUPPLEMENTS
    if uscf_sections is None:
        uscf_sections = _UI_USCF_SECTIONS
    if uscf_norms is None:
        uscf_norms = REAL_USCF_NORMS
    if uscf_awards is None:
        uscf_awards = REAL_USCF_AWARDS
    uscf_down = uscf_profile if isinstance(uscf_profile, Exception) else None

    def fake(value):
        def fetch(member_id, **kwargs):
            if uscf_down is not None:
                raise uscf_down
            return value
        return fetch

    with mock.patch.object(sync, "fetch_study_pgn", return_value=pgn_text), \
         mock.patch.object(sync, "fetch_member_profile",
                           side_effect=fake(uscf_profile)), \
         mock.patch.object(sync, "fetch_rating_supplements",
                           side_effect=fake(uscf_supplements)), \
         mock.patch.object(sync, "fetch_member_sections",
                           side_effect=fake(uscf_sections)), \
         mock.patch.object(sync, "fetch_member_games",
                           side_effect=fake(uscf_games)), \
         mock.patch.object(sync, "fetch_member_norms",
                           side_effect=fake(uscf_norms)), \
         mock.patch.object(sync, "fetch_member_awards",
                           side_effect=fake(uscf_awards)):
        yield


@pytest.fixture(scope="session")
def ui_app():
    """The real Dash app built once with fixture data (Lichess + USCF stubbed)."""
    import data

    data.reset()
    with stub_ui_sources(SAMPLE_PGN):
        from app import build_app
        dash_app, _server = build_app(
            ["teststudy"], player_name="Test Player", uscf_member_id="32487228"
        )
    return dash_app


@pytest.fixture()
def ui_data(sample_pgn_text):
    """A freshly initialized data store for each UI test (USCF available)."""
    import data

    data.reset()
    with stub_ui_sources(sample_pgn_text):
        data.initialize(
            ["teststudy"], player_name="Test Player", uscf_member_id="32487228"
        )
    yield
    data.reset()


@pytest.fixture()
def real_career_ui(ui_app):
    """
    The data store loaded with Daniel's real fixture pair: the 63-chapter
    Study snapshot matched against his real USCF record (issues #31/#32).

    Pure real data — no 2024 sample items — so series lengths and values
    match the captured career exactly.
    """
    import data

    pgn_text = (USCF_FIXTURES_DIR / "lichess-study-snapshot.pgn").read_text()
    games = json.loads((USCF_FIXTURES_DIR / "games.json").read_text())["items"]
    data.reset()
    with stub_ui_sources(pgn_text, uscf_games=games,
                         uscf_supplements=REAL_USCF_SUPPLEMENTS,
                         uscf_sections=REAL_USCF_SECTIONS):
        data.initialize(["realstudy"], player_name="Daniel Gentile",
                        uscf_member_id="32487228")
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
