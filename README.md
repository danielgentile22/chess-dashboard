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
| **Overview** | 10 KPI cards · **USCF profile card** (all your ratings with Official · Live side by side, national/state rank, floor, membership warning) · last-20 streak badges · W/D/L donut · termination breakdown · milestone timeline · your top recurring weakness |
| **Trends** | GitHub-style activity calendar (one cell per day, colored by results) · **dual-line rating chart** (your Official Rating as a step line and your Live Rating per Rated Event, so you can watch them diverge and reconverge) · cumulative win rate · games per month · win rate by day of week · game-length distribution · results by time control · score by round (the fatigue check) · **upset tracker** (giant kills and upset losses by rating margin) |
| **Openings** | **Repertoire tree** (your games arranged move by move, branches that leak points flagged) · ECO family breakdown (A/B/C/D/E) · full opening detail table |
| **Opponents** | **Scouting Report** (search an opponent → score, rating gap, game timeline, their openings by your color, and every Lesson from facing them) · stacked W/D/L bar per opponent · outcome by rating bucket · outcome vs. rating scatter |
| **Events** | Performance per tournament · selectable event table → per-event game list + performance rating |
| **Games** | Every game with Open-on-Lichess links, Lesson indicators (💡), Tags, and its **USCF status** (✓ matched by opponent ID · ≈ matched by name · ⚠ sources disagree · Forfeit) — click any row to open the game |
| **Lessons** | Every `Lesson:` you've written on Lichess, filterable by Tag and opponent · recurring-weakness callouts ("#time-trouble appears in 4 of your last 5 losses") · pre-game review mode |
| **Reconciliation** | Every disagreement between your Studies and USCF, grouped and actionable: color conflicts (both versions side by side) · USCF-rated games missing from your Studies · games USCF hasn't rated · chapters missing opponent IDs · typed ratings that don't match the Official Rating — each with fix-on-Lichess links and Dismiss |

Clicking a Game anywhere opens its **detail view**: an embedded interactive Lichess board (your annotations and variations playable in place) alongside the Game's Lessons, Tags, metadata, and — when the Game is matched — its **USCF record** (Rated Event, Section, rating system, and the opponent as USCF registers them, linking to their page on ratings.uschess.org).

### Pre-game review mode

`/lessons?review=1` (the "Review before playing" button, or "Review before facing X" inside a Scouting Report) opens a full-screen, card-by-card walk through your most relevant Lessons — recurring weaknesses first, then the selected opponent's games, then everything else newest-first. Built for one hand and the five minutes before a round.

### Repertoire tree

The Openings page leads with your personal opening explorer: every game as White (or Black — toggleable) arranged move by move. Each branch shows how many games continued that way, a W/D/L bar, and its score; expanding a branch drills one move deeper, down to links into the exact games. Branches that score below your overall average for that color across 3+ games are flagged in red — that's where your repertoire is leaking points.

### USCF enrichment

Configure your USCF member ID and every Sync also pulls your official record from the USCF ratings API (ratings.uschess.org): all your ratings (with provisional game counts), national and state rank, rating floor, and membership expiration — shown on a profile card on Overview with your **Official Rating and Live Rating side by side** (the published monthly integer vs. where your rating really stands after your last event).

**The Official/Live lens** — an `[Official | Live]` switch in the header — picks which rating series powers every rating-derived number in the dashboard: KPI cards, upset margins, opponent-strength buckets, everything. It's a *lens*, not a filter: it never hides Games, it changes what "your rating" means. **Official** is the supplement in effect at each Game's Rated Event start date (the rating that determined your section and pairings); **Live** is the pre-rating of the Section you played it in, decimals and all. Games from before your first supplement show no Official value — the dashboard never invents a number. Opponent ratings stay your typed values under both lenses until crosstable enrichment (Phase D) supplies their live ratings. The Trends rating chart is the one place the lens hides nothing: both series always draw; the lens just picks which one leads.

**The matching engine** pairs every USCF Game Record with its Game: by opponent member ID + result first (the `WhiteFideId`/`BlackFideId` headers you type into chapters), then by normalized opponent name + result + event date window for chapters without IDs. Repeat opponents with identical results are disambiguated by color and date — tiebreakers, never requirements, because color is itself a fact the sources can disagree on. Matched Games show their USCF half (Rated Event, Section, official opponent identity); games with at most one move and no USCF record are tagged **Forfeit** (opponent no-show) and excluded from win rate, streaks, and opening stats while still counting toward event scores.

Disagreements go to the **Reconciliation page** — conflicts get a ⚠ badge on the Game everywhere it appears, and the header shows a count of open items. The dashboard always displays the Lichess version of disputed facts; nothing is silently "corrected".

USCF data is enrichment, never a dependency (`docs/adr/0003`): a Sync that reaches Lichess but not USCF still succeeds, and USCF surfaces degrade to the last successful Sync's cached data with a clear "unavailable since" warning.

### Header

The sticky header celebrates current form: a 🔥 that grows with your win streak (extra glow at 5+), a 🧊 on cold streaks, and your last 5 games as colored dots. Plus the **Official/Live rating lens**, the Sync button, and a per-source freshness label ("Lichess synced X ago · USCF synced Y ago"). When a Sync sets a personal best — a new peak rating, a new longest win streak, or a win over the highest-rated opponent yet — a gold celebration banner appears until you dismiss it.

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
| `--study` | `$LICHESS_STUDY_IDS` | Lichess study ID to Sync games from (repeat for multiple Studies) |
| `--player` | auto-detected | Your name as it appears in Game headers |
| `--token` | `$LICHESS_API_TOKEN` | Lichess API token (only for private studies) |
| `--uscf-member` | `$USCF_MEMBER_ID` | USCF member ID whose record enriches the Games (omit to run Lichess-only) |
| `--uscf-cache` | `uscf_cache.json` | USCF response cache for offline fallback |
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8050` | Port to listen on |
| `--debug` | off | Enable Dash hot-reload mode |

When your archive grows past Lichess's 64-chapter Study limit, designate the next Study too:

```bash
python app.py --study 6jYtXHGp --study abcd1234
```

Games from all designated Studies are merged, deduplicated by chapter, and sorted by date.

If your name isn't auto-detected, pass it explicitly:

```bash
python app.py --study 6jYtXHGp --player "Last, First"
```

### Environment variables

| Variable | Description |
|---|---|
| `LICHESS_STUDY_IDS` | Comma-separated Lichess study IDs to Sync from (e.g. `6jYtXHGp,abcd1234`) |
| `LICHESS_API_TOKEN` | Optional API token, only needed if a Study is private |
| `PLAYER_NAME` | Override player-name auto-detection |
| `CACHE_PATH` | PGN cache of the last successful Sync, used as offline fallback (default: `games.pgn`) |
| `USCF_MEMBER_ID` | USCF member ID whose record enriches the Games (unset → Lichess-only) |
| `USCF_CACHE_PATH` | USCF response cache, used as fallback when USCF is unreachable (default: `uscf_cache.json`) |
| `HOST` / `PORT` / `DEBUG` | Server binding and debug mode |

### Offline resilience

Every successful Sync writes a local PGN cache. If Lichess is unreachable when the app starts, it boots from that cache and shows a "cached data" notice; if a Sync from the header button fails, the data you're looking at stays untouched and an error toast appears. The cache is disposable — the designated Studies on Lichess remain the only source of truth.

USCF gets the same treatment (`uscf_cache.json`): when the USCF API is unreachable, USCF surfaces show the last successful Sync's data with an "unavailable since" warning — and the Sync itself still succeeds.

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
| `TimeControl` | Optional | Results-by-time-control chart (`110+10`, `40/80, SD30; +30`, `G/30;d5`, …) |
| `Round` | Optional | Score-by-round fatigue chart |

---

## Project Structure

```
chess-stats-dashboard/
├── app.py                   # Entry point — Dash factory (use_pages), CLI, gunicorn server
├── config.py                # Environment variable config (LICHESS_STUDY_IDS, USCF_MEMBER_ID, …)
├── data.py                  # Module-level data store (Synced from Lichess + USCF)
├── sync.py                  # Sync orchestrator: Studies → merged Games, USCF → enrichment
├── lichess_client.py        # Lichess API client (the only module that talks HTTP to Lichess)
├── uscf_client.py           # USCF ratings API client (the only module that talks HTTP to USCF)
├── uscf_core.py             # Pure USCF interpretation: profile, rating series, matching engine, reconciliation
├── shell.py                 # Persistent app chrome: header, nav tabs, sync machinery
├── filters.py               # Global filter drawer + shared FILTER_INPUTS
├── components.py            # Shared UI building blocks (cards, KPI tiles, form dots, …)
├── styles.py                # Color palette, dark-theme helpers, empty_fig()
├── pgn_stats_core.py        # PGN parsing, statistics, and insights functions
├── pages/                   # One module per page (Dash Pages)
│   ├── overview.py          #   /          KPIs, streaks, W/D/L, milestones
│   ├── trends.py            #   /trends    rating, activity, time controls, upsets
│   ├── openings.py          #   /openings  repertoire tree + ECO families
│   ├── opponents.py         #   /opponents records, head-to-head, strength
│   ├── events.py            #   /events    tournament performance
│   ├── games.py             #   /games     full games table
│   ├── lessons.py           #   /lessons   Lessons + Tag filtering
│   ├── reconciliation.py    #   /reconciliation  Studies ↔ USCF disagreements
│   └── game_detail.py       #   /game/<id> embedded Lichess board + metadata + USCF record
├── assets/
│   └── custom.css           # Dark theme, typography, component styles
├── docs/adr/                # Architecture decision records
├── tests/
│   ├── conftest.py          # Shared fixtures (sample Studies, USCF responses, UI app)
│   ├── fixtures/uscf/       # Real captured USCF API response shapes
│   ├── test_pgn_stats_core.py  # Parser + stats + insights function tests
│   ├── test_lichess_client.py  # Lichess client tests (mocked HTTP)
│   ├── test_uscf_client.py  # USCF client tests (mocked HTTP, real response shapes)
│   ├── test_uscf_core.py    # Profile, rating series, matching engine, reconciliation tests
│   ├── test_sync.py         # Sync orchestrator tests (stubbed clients)
│   ├── test_config.py       # Config parsing tests
│   ├── test_data.py         # Data store tests (stubbed clients)
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

**Lichess Studies are the source of truth** (ADR 0001). Games are Synced from the designated Studies via the Lichess API; nothing is uploaded or exported by hand. `lichess_client.py` is the only module that talks HTTP to Lichess.

**USCF data is enrichment, never a dependency** (ADR 0003). The USCF ratings API is undocumented and unofficial, so it gets a strict blast-radius cap: a Sync that reaches Lichess but not USCF succeeds, USCF surfaces degrade to cached data plus a warning, and `uscf_client.py` is the only module that talks HTTP to USCF. Pure interpretation (profile parsing, the Official/Live rating series, the matching engine, reconciliation) lives in `uscf_core.py`, mirroring the `lichess_client` / `pgn_stats_core` split.

**Match & enrich.** The Game (Lichess Chapter) stays the central entity; USCF Game Records attach to Games as enrichment columns. Matching never filters, hides, or restructures Games, and disputed facts always display the Lichess version with the disagreement flagged in Reconciliation — the dashboard surfaces discrepancies, it never silently resolves them.

**`pgn_stats_core.py` is framework-agnostic.** Every statistics function takes a Pandas DataFrame and returns a DataFrame or dict. You can import and call them from a Jupyter notebook or any other frontend without touching any Dash code.

**No database.** Games are Synced once at startup and stored in a module-level store (`data.py`). All callbacks read from this shared DataFrame — no serialisation overhead, no `dcc.Store` round-trips, instant filter response.

**Multi-page via Dash Pages.** Each page is one module under `pages/` that registers its own route and callbacks. The shell (header, nav, filter drawer) never unmounts, so filter state survives navigation for free.

**One filter helper, many callbacks.** Every chart callback on every page shares the same `FILTER_INPUTS` list and `filters.get_filtered()` helper. Dash runs independent callbacks in parallel, so all charts update concurrently when you change a filter.

**Lessons live on Lichess** (ADR 0002). A Game's Lesson is a chapter comment starting with `Lesson:`; hashtags become Tags. The dashboard extracts both during Sync and never stores them itself — writing happens on Lichess only.

**FIDE performance rating.** Calculated as `PR = avg_opponent_rating + 400 × log10(p / (1 − p))`, capped at ±800 from the average, matching the standard FIDE formula.

---

## License

MIT — see [LICENSE](LICENSE) for details.
