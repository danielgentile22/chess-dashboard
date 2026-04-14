# Chess Stats Dashboard

An interactive analytics dashboard for your over-the-board chess games, built with Python and Plotly Dash. Load any PGN file and explore your game history through filterable charts and sortable tables — all in the browser.

---

## Features

| Section | What you get |
|---|---|
| **Filters** | Slice by color (White/Black), outcome (Win/Draw/Loss), termination type, and date range |
| **Streaks** | Longest unbeaten run, longest win streak, and current streak |
| **Win/Draw/Loss** | Pie chart of outcomes for the filtered set |
| **Termination breakdown** | Bar chart of how your games ended (checkmate, resignation, timeout, …) |
| **Opponent analysis** | Stacked W/D/L bar for every opponent you've faced more than once |
| **Cumulative win rate** | Line chart showing how your win % has evolved game-by-game |
| **Rating over time** | Your rating as recorded in the PGN headers, one point per day |
| **Event performance** | Stacked W/D/L bar per tournament + a table with score, best/worst opponent |
| **All games table** | Filterable, sortable table of every game with all key columns |

All chart cards are **resizable** — drag the bottom-right corner to fit your screen.

---

## Quick Start

### Prerequisites

- Python 3.9 or newer
- A PGN export of your games (USCF, FIDE, Lichess, Chess.com, etc.)

### Install

```bash
git clone https://github.com/danielgentile22/uscf-dashboard.git
cd uscf-dashboard
pip install -r requirements.txt
```

### Run locally

```bash
python app.py --pgn "your-games.pgn"
```

Then open [http://localhost:8050](http://localhost:8050) in your browser.

If your name isn't automatically detected, pass it explicitly:

```bash
python app.py --pgn "your-games.pgn" --player "Last, First"
```

### CLI options

| Flag | Default | Description |
|---|---|---|
| `--pgn` | *(required)* | Path to your PGN file |
| `--player` | auto-detected | Your name as it appears in PGN headers |
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8050` | Port to listen on |
| `--debug` | off | Enable Dash debug/hot-reload mode |

---

## Deployment on Render

This repo includes a `render.yaml` that configures a [Render](https://render.com) Web Service.

1. **Fork** this repository.
2. Add your PGN file to the repo root (or update `PGN_PATH` in `render.yaml` to match the filename you use).
3. Create a **New Web Service** on Render and connect your GitHub repo.
4. Render will auto-detect `render.yaml` and configure the build and start commands.
5. Optionally set the `PLAYER_NAME` environment variable in the Render dashboard if auto-detection picks the wrong name.

The app exposes a module-level `server` (Flask WSGI) that gunicorn targets:

```
gunicorn app:server --bind 0.0.0.0:$PORT
```

---

## PGN Compatibility

Built primarily for **USCF over-the-board PGN exports**, but compatible with any standard PGN that includes:

| Header | Required? | Notes |
|---|---|---|
| `White` / `Black` | Yes | Used to identify you and your opponents |
| `Result` | Yes | `1-0`, `0-1`, or `1/2-1/2` |
| `Date` | Recommended | Enables timeline charts; format `YYYY.MM.DD` |
| `WhiteElo` / `BlackElo` | Optional | Rating progression and opponent ratings |
| `Event` | Optional | Tournament / event performance charts |
| `ECO` / `Opening` | Optional | Opening columns in the games table |
| `Termination` | Optional | Termination breakdown chart |

Also tested with Lichess and Chess.com exports.

---

## Project Structure

```
chess-stats-dashboard/
├── app.py               # Dash layout, callbacks, CLI entrypoint, gunicorn server
├── pgn_stats_core.py    # PGN parsing and all statistics functions
├── requirements.txt     # Python dependencies with minimum versions
├── render.yaml          # Render deployment configuration
├── .gitignore
└── README.md
```

### Key design decisions

- **`pgn_stats_core.py` is framework-agnostic.** All statistics live in plain functions that take a Pandas DataFrame and return a DataFrame or dict. You can import and use them from a notebook or any other frontend without touching the Dash code.
- **No database.** The entire game set is parsed at startup and stored in a Dash `dcc.Store` (in-browser JSON). Filters run as pure Pandas operations on each callback — fast for typical personal game collections (hundreds to a few thousand games).
- **Player auto-detection.** If you don't pass `--player`, the most frequent name across all `White`/`Black` headers is used. This is correct for a personal PGN where you appear in every game.

---

## License

MIT — see [LICENSE](LICENSE) for details.
