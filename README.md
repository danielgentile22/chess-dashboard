# Chess Stats Dashboard

[![CI](https://github.com/danielgentile22/uscf-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/danielgentile22/uscf-dashboard/actions/workflows/ci.yml)

An interactive analytics dashboard for your over-the-board chess games, built with Python and Plotly Dash. Point it at the Lichess Study (or Studies) where you archive your games and explore your entire game history through a dark-themed, fully filterable interface — all in the browser, no database required.

Your designated Lichess Studies are the source of truth (see `docs/adr/0001`): the dashboard Syncs every Game directly from the Lichess API at startup — no manual PGN exports.

---

## Features

### Pages

The dashboard is a multi-page app — each page loads only its own charts, so it stays fast on a phone:

| Page | What you get |
|---|---|
| **Overview** | 10 KPI cards · last-20 streak badges · W/D/L donut · termination breakdown · milestone timeline · your top recurring weakness |
| **Trends** | GitHub-style activity calendar (one cell per day, colored by results) · rating over time with trend overlay · cumulative win rate · games per month · win rate by day of week · game-length distribution |
| **Openings** | ECO family breakdown (A/B/C/D/E) · full opening detail table |
| **Opponents** | **Scouting Report** (search an opponent → score, rating gap, game timeline, their openings by your color, and every Lesson from facing them) · stacked W/D/L bar per opponent · outcome by rating bucket · outcome vs. rating scatter |
| **Events** | Performance per tournament · selectable event table → per-event game list + performance rating |
| **Games** | Every game with Open-on-Lichess links, Lesson indicators (💡), and Tags — click any row to open the game |
| **Lessons** | Every `Lesson:` you've written on Lichess, filterable by Tag and opponent · recurring-weakness callouts ("#time-trouble appears in 4 of your last 5 losses") · pre-game review mode |

Clicking a Game anywhere opens its **detail view**: an embedded interactive Lichess board (your annotations and variations playable in place) alongside the Game's Lessons, Tags, and metadata.

### Pre-game review mode

`/lessons?review=1` (the "Review before playing" button, or "Review before facing X" inside a Scouting Report) opens a full-screen, card-by-card walk through your most relevant Lessons — recurring weaknesses first, then the selected opponent's games, then everything else newest-first. Built for one hand and the five minutes before a round.

### Header

The sticky header celebrates current form: a 🔥 that grows with your win streak (extra glow at 5+), a 🧊 on cold streaks, and your last 5 games as colored dots. Plus the Sync button and a "synced X ago" freshness label. When a Sync sets a personal best — a new peak rating, a new longest win streak, or a win over the highest-rated opponent yet — a gold celebration banner appears until you dismiss it.

### Filters

A global filter drawer (right side on desktop, dropdown on phones) slices every chart on every page simultaneously — and the selection survives navigation:

| Filter | Options |
|---|---|
| **Presets** | All Games · Last 20 · This Year · White only · Black only · Wins only |
| **Color** | White / Black checkboxes |
| **Outcome** | Win / Draw / Loss checkboxes |
| **Termination** | Multi-select (Checkmate, Resignation, Timeout, …) |
| **Date range** | Calendar date picker |
| **Event** | Multi-select tournament picker |
| **Move count** | Range slider (e.g. games between 20–60 moves) |

All charts are dark-themed (GitHub-inspired palette) and update instantly when any filter changes.

---

## Quick Start

### Prerequisites

- Python 3.10 or newer
- A Lichess Study containing your games (one Game per Chapter), e.g. `https://lichess.org/study/abcdWXYZ` → study ID `abcdWXYZ`

### Install with Make (recommended)

```bash
git clone https://github.com/danielgentile22/uscf-dashboard.git
cd uscf-dashboard
make install        # creates .venv and installs runtime deps
make run STUDY=abcdWXYZ
```

Then open [http://localhost:8050](http://localhost:8050).

### Install manually

```bash
git clone https://github.com/danielgentile22/uscf-dashboard.git
cd uscf-dashboard
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py --study abcdWXYZ
```

### CLI options

| Flag | Default | Description |
|---|---|---|
| `--study` | `$LICHESS_STUDY_IDS` | Lichess study ID to Sync games from (repeat for multiple Studies) |
| `--player` | auto-detected | Your name as it appears in Game headers |
| `--token` | `$LICHESS_API_TOKEN` | Lichess API token (only for private studies) |
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8050` | Port to listen on |
| `--debug` | off | Enable Dash hot-reload mode |

When your archive grows past Lichess's 64-chapter Study limit, designate the next Study too:

```bash
python app.py --study abcdWXYZ --study abcd1234
```

Games from all designated Studies are merged, deduplicated by chapter, and sorted by date.

If your name isn't auto-detected, pass it explicitly:

```bash
python app.py --study abcdWXYZ --player "Last, First"
```

### Environment variables

| Variable | Description |
|---|---|
| `LICHESS_STUDY_IDS` | Comma-separated Lichess study IDs to Sync from (e.g. `abcdWXYZ,abcd1234`) |
| `LICHESS_API_TOKEN` | Optional API token, only needed if a Study is private |
| `PLAYER_NAME` | Override player-name auto-detection |
| `CACHE_PATH` | PGN cache of the last successful Sync, used as offline fallback (default: `games.pgn`) |
| `HOST` / `PORT` / `DEBUG` | Server binding and debug mode |

### Offline resilience

Every successful Sync writes a local PGN cache. If Lichess is unreachable when the app starts, it boots from that cache and shows a "cached data" notice; if a Sync from the header button fails, the data you're looking at stays untouched and an error toast appears. The cache is disposable — the designated Studies on Lichess remain the only source of truth.

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
docker run -p 8050:8050 -e LICHESS_STUDY_IDS="abcdWXYZ" chess-stats
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
├── app.py                   # Entry point — Dash factory (use_pages), CLI, gunicorn server
├── config.py                # Environment variable config (LICHESS_STUDY_IDS, PORT, …)
├── data.py                  # Module-level data store (Synced from Lichess)
├── sync.py                  # Sync orchestrator: Studies → merged Games
├── lichess_client.py        # Lichess API client (the only module that talks HTTP)
├── shell.py                 # Persistent app chrome: header, nav tabs, sync machinery
├── filters.py               # Global filter drawer + shared FILTER_INPUTS
├── components.py            # Shared UI building blocks (cards, KPI tiles, form dots, …)
├── styles.py                # Color palette, dark-theme helpers, empty_fig()
├── pgn_stats_core.py        # PGN parsing, statistics, and insights functions
├── pages/                   # One module per page (Dash Pages)
│   ├── overview.py          #   /          KPIs, streaks, W/D/L, milestones
│   ├── trends.py            #   /trends    rating, win rate, activity, game length
│   ├── openings.py          #   /openings  ECO families + openings table
│   ├── opponents.py         #   /opponents records, head-to-head, strength
│   ├── events.py            #   /events    tournament performance
│   ├── games.py             #   /games     full games table
│   ├── lessons.py           #   /lessons   Lessons + Tag filtering
│   └── game_detail.py       #   /game/<id> embedded Lichess board + metadata
├── assets/
│   └── custom.css           # Dark theme, typography, component styles
├── docs/adr/                # Architecture decision records
├── tests/
│   ├── conftest.py          # Shared fixtures (sample Studies, dataframe, UI app)
│   ├── test_pgn_stats_core.py  # Parser + stats + insights function tests
│   ├── test_lichess_client.py  # Lichess client tests (mocked HTTP)
│   ├── test_sync.py         # Sync orchestrator tests (stubbed client)
│   ├── test_config.py       # Config parsing tests
│   ├── test_data.py         # Data store tests (stubbed client)
│   ├── test_shell.py        # Shell + filter callback tests
│   └── test_ui_smoke.py     # UI smoke harness: every page boots, renders, wires up
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

**Multi-page via Dash Pages.** Each page is one module under `pages/` that registers its own route and callbacks. The shell (header, nav, filter drawer) never unmounts, so filter state survives navigation for free.

**One filter helper, many callbacks.** Every chart callback on every page shares the same `FILTER_INPUTS` list and `filters.get_filtered()` helper. Dash runs independent callbacks in parallel, so all charts update concurrently when you change a filter.

**Lessons live on Lichess** (ADR 0002). A Game's Lesson is a chapter comment starting with `Lesson:`; hashtags become Tags. The dashboard extracts both during Sync and never stores them itself — writing happens on Lichess only.

**FIDE performance rating.** Calculated as `PR = avg_opponent_rating + 400 × log10(p / (1 − p))`, capped at ±800 from the average, matching the standard FIDE formula.

---

## License

MIT — see [LICENSE](LICENSE) for details.
