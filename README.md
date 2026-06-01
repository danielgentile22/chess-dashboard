# Chess Stats Dashboard

[![CI](https://github.com/danielgentile22/uscf-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/danielgentile22/uscf-dashboard/actions/workflows/ci.yml)

An interactive analytics dashboard for your over-the-board chess games, built with Python and Plotly Dash. Point it at the Lichess Study (or Studies) where you archive your games and explore your entire game history through a dark-themed, fully filterable interface — all in the browser, no database required.

Your designated Lichess Studies are the source of truth (see `docs/adr/0001`): the dashboard Syncs every Game directly from the Lichess API at startup — no manual PGN exports.

---

## Features

### Filters
A persistent filter bar lets you slice every chart simultaneously:

| Filter | Options |
|---|---|
| **Presets** | All Games · Last 20 · This Year · White only · Black only · Wins only |
| **Color** | White / Black checkboxes |
| **Outcome** | Win / Draw / Loss checkboxes |
| **Termination** | Multi-select (Checkmate, Resignation, Timeout, …) |
| **Date range** | Calendar date picker |
| **Event** | Multi-select tournament picker |
| **Move count** | Range slider (e.g. games between 20–60 moves) |

### Dashboard Sections

| Section | What you get |
|---|---|
| **KPI Bar** | 10 live cards: games, win %, draw %, loss %, current rating, peak rating, FIDE performance rating, longest win streak, unique opponents, favourite opening |
| **Overview** | Streak badge visualiser (last 20 games as W/D/L icons) · WDL donut chart · Termination breakdown bar |
| **Timeline** | Cumulative win-rate line chart · Rating over time with linear trend overlay |
| **Openings** | ECO family breakdown (A/B/C/D/E) · Full opening detail table sortable by W/D/L |
| **Opponents** | Stacked W/D/L bar per opponent · Head-to-head deep-dive (select any opponent for a full stats card + game list) |
| **Strength** | Outcome by opponent rating bucket · Outcome vs. rating scatter plot |
| **Game Length** | Move-count histogram · Statistics card (mean, median, shortest, longest) |
| **Activity** | Games per month · Win rate by day of week |
| **Events** | Performance per tournament · Selectable event table → per-event game list + performance rating |
| **Milestones** | Chronological timeline of personal bests (rating highs, streak records, first win, etc.) |
| **All Games** | Full filterable, sortable DataTable of every game |

All charts are dark-themed (GitHub-inspired palette) and update instantly when any filter changes.

---

## Quick Start

### Prerequisites

- Python 3.10 or newer
- A Lichess Study containing your games (one Game per Chapter), e.g. `https://lichess.org/study/6jYtXHGp` → study ID `6jYtXHGp`

### Install with Make (recommended)

```bash
git clone https://github.com/danielgentile22/uscf-dashboard.git
cd uscf-dashboard
make install        # creates .venv and installs runtime deps
make run STUDY=6jYtXHGp
```

Then open [http://localhost:8050](http://localhost:8050).

### Install manually

```bash
git clone https://github.com/danielgentile22/uscf-dashboard.git
cd uscf-dashboard
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py --study 6jYtXHGp
```

### CLI options

| Flag | Default | Description |
|---|---|---|
| `--study` | `$LICHESS_STUDY_IDS` | Lichess study ID to Sync games from |
| `--player` | auto-detected | Your name as it appears in Game headers |
| `--token` | `$LICHESS_API_TOKEN` | Lichess API token (only for private studies) |
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8050` | Port to listen on |
| `--debug` | off | Enable Dash hot-reload mode |

If your name isn't auto-detected, pass it explicitly:

```bash
python app.py --study 6jYtXHGp --player "Last, First"
```

### Environment variables

| Variable | Description |
|---|---|
| `LICHESS_STUDY_IDS` | Lichess study ID to Sync from (used by gunicorn deployments) |
| `LICHESS_API_TOKEN` | Optional API token, only needed if the Study is private |
| `PLAYER_NAME` | Override player-name auto-detection |
| `HOST` / `PORT` / `DEBUG` | Server binding and debug mode |

---

## Developer Workflow

All common tasks are wrapped in the `Makefile`:

```bash
make help           # list all targets
make install-dev    # install runtime + dev deps (pytest, ruff, mypy)
make test           # run pytest with coverage report
make lint           # run ruff (auto-fix)
make typecheck      # run mypy on pgn_stats_core.py
make run-debug      # start with hot-reload
make docker-up      # build & run in Docker
```

---

## Running with Docker

```bash
# Build and run
docker compose up --build

# Or just build the image
docker build -t chess-stats .
docker run -p 8050:8050 -e LICHESS_STUDY_IDS="6jYtXHGp" chess-stats
```

---

## Deployment on Render

This repo includes `render.yaml` for one-click deployment on [Render](https://render.com).

1. **Fork** this repository.
2. Set `LICHESS_STUDY_IDS` in `render.yaml` (or in the Render dashboard) to your study ID.
3. Create a **New Web Service** on Render, connect your fork — Render auto-detects `render.yaml`.
4. Optionally set `PLAYER_NAME` in the Render dashboard if auto-detection is wrong.

The start command uses `gunicorn.conf.py` for configuration:

```
gunicorn app:server --config gunicorn.conf.py
```

### Railway / Heroku

A `Procfile` is included for platforms that use it:

```
web: gunicorn app:server --config gunicorn.conf.py
```

---

## Game / PGN Compatibility

Games are read from Lichess Study chapters (which Lichess serves as standard PGN). Any chapter that includes the headers below works:

| Header | Required? | Notes |
|---|---|---|
| `White` / `Black` | Yes | Used to identify you and your opponents |
| `Result` | Yes | `1-0`, `0-1`, or `1/2-1/2` |
| `Date` | Recommended | Enables timeline charts; format `YYYY.MM.DD` |
| `WhiteElo` / `BlackElo` | Optional | Rating progression and opponent strength charts |
| `Event` | Optional | Tournament performance section |
| `ECO` / `Opening` | Optional | Opening analysis section |
| `Termination` | Optional | Termination breakdown chart |

---

## Project Structure

```
chess-stats-dashboard/
├── app.py                   # Entry point — Dash factory, CLI, gunicorn server
├── config.py                # Environment variable config (LICHESS_STUDY_IDS, PORT, …)
├── data.py                  # Module-level data store (Synced from Lichess)
├── lichess_client.py        # Lichess API client (the only module that talks HTTP)
├── layout.py                # Full Dash layout (skeleton, KPI bar, all sections)
├── callbacks.py             # All Dash callbacks (20+ focused functions)
├── styles.py                # Color palette, dark-theme helpers, empty_fig()
├── pgn_stats_core.py        # PGN parsing and all statistics functions
├── assets/
│   └── custom.css           # Dark theme overrides, component styles
├── docs/adr/                # Architecture decision records
├── tests/
│   ├── conftest.py          # Shared fixtures (sample PGN, dataframe)
│   ├── test_pgn_stats_core.py  # Parser + stats function tests
│   ├── test_lichess_client.py  # Lichess client tests (mocked HTTP)
│   └── test_data.py         # Data store tests (stubbed client)
├── requirements.txt         # Runtime dependencies
├── requirements-dev.txt     # Dev dependencies (pytest, ruff, mypy)
├── pyproject.toml           # Project metadata + tool configuration
├── Makefile                 # Developer convenience targets
├── Dockerfile               # Production container (Python 3.11-slim + gunicorn)
├── docker-compose.yml       # Local Docker orchestration
├── gunicorn.conf.py         # Gunicorn worker/timeout/logging config
├── Procfile                 # Railway / Heroku start command
├── render.yaml              # Render deployment configuration
└── .github/
    └── workflows/
        └── ci.yml           # GitHub Actions: lint → test → typecheck
```

### Key design decisions

**Lichess Studies are the source of truth** (ADR 0001). Games are Synced from the designated Studies via the Lichess API; nothing is uploaded or exported by hand. `lichess_client.py` is the only module that talks HTTP.

**`pgn_stats_core.py` is framework-agnostic.** Every statistics function takes a Pandas DataFrame and returns a DataFrame or dict. You can import and call them from a Jupyter notebook or any other frontend without touching any Dash code.

**No database.** Games are Synced once at startup and stored in a module-level store (`data.py`). All callbacks read from this shared DataFrame — no serialisation overhead, no `dcc.Store` round-trips, instant filter response.

**One filter helper, many callbacks.** All 20+ callbacks share the same `FILTER_INPUTS` list and a single `_get_filtered()` helper. Dash runs independent callbacks in parallel, so all charts update concurrently when you change a filter.

**FIDE performance rating.** Calculated as `PR = avg_opponent_rating + 400 × log10(p / (1 − p))`, capped at ±800 from the average, matching the standard FIDE formula.

---

## License

MIT — see [LICENSE](LICENSE) for details.
