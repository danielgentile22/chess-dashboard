# Pages — Dash Pages (the UI layer)

## Purpose
One module per route, registered via Dash Pages. A page owns its **layout** and
its **chart callbacks** only — it reads already-Synced, already-filtered data and
draws it. It never fetches, never mutates the store, and never computes stats
inline (that lives in `pgn_stats_core.py` / `uscf_core.py`). The persistent chrome
(header, nav, filter drawer, lens, Sync) is **not** here — it's in `../shell.py`.

## Routes

| Module | Path | What it shows |
|---|---|---|
| `overview.py` | `/` | KPIs, USCF profile card, last-20 streaks, W/D/L, milestones |
| `trends.py` | `/trends` | activity calendar, dual-line rating chart, time controls, upsets |
| `openings.py` | `/openings` | repertoire tree + ECO families |
| `opponents.py` | `/opponents` | Scouting Report, records, strength buckets |
| `events.py` | `/events` | Series → Rated Events, standings, crosstables |
| `games.py` | `/games` | full games table with USCF status |
| `lessons.py` | `/lessons` | Lessons + Tag filtering, review mode (`?review=1`) |
| `analysis.py` | `/analysis` | error-profile mistake-type distribution + trends (accuracy & type over time w/ rating, phase×type matrix, move histogram) + awaiting-analysis list |
| `reconciliation.py` | `/reconciliation` | Studies ↔ USCF disagreements |
| `game_detail.py` | `/game/<id>` | pgn-viewer board (Game / My Analysis / Engine switcher) + critical moment + metadata + USCF record (`nav=False`). The Engine view (F7) shows the AI summary, an eval chart, and his judged moves with corrections. |

## How a page is wired

Every page module, top to bottom:
1. Imports `data`, `components`, `styles`, the stats functions it needs, and
   `from filters import FILTER_INPUTS, get_filtered`.
2. `dash.register_page(__name__, path=..., name=..., order=...)` at import time.
   Add `nav=False` to keep it out of the header tabs (like `game_detail`).
   Registration happens when `app.py` imports the package — *after* the data
   store is initialized, so import-time code can assume data exists.
3. `def layout(**kwargs) -> html.Div:` — returns the static shell of the page.
   `kwargs` carries URL/query params (e.g. the game id, `review=1`).
4. `@callback(...)` functions that take `FILTER_INPUTS` and call
   `get_filtered(...)` to get the Games to draw.

## Contracts & invariants

- **Filter-driven charts declare `FILTER_INPUTS` and call `get_filtered()`** — the
  single shared input list + helper. Don't hand-roll filtering or read
  `data.get_df()` directly in a chart callback; `get_filtered()` applies both the
  global filters *and* the Official/Live rating lens in one place. (Non-filtered
  surfaces — e.g. the USCF profile card — follow Syncs via `sync-store` instead.)
- **Read-only, no mutation.** Treat the DataFrame from `get_filtered()`/`get_df()`
  as immutable; copy before assigning columns. The store is shared across
  concurrent Dash callbacks.
- **No stats math in pages.** Compute in `pgn_stats_core.py` / `uscf_core.py` (pure,
  unit-tested), import the function, call it here. Pages assemble figures, not
  statistics.
- **Enrichment columns always exist** — never guard on "is USCF configured" by
  checking for a column; an unmatched Game is the off/unavailable state.
- **Empty states are mandatory.** Use `styles.empty_fig()` (and the empty-state
  cards in `components.py`) so a page with no matching Games still renders cleanly.
- **Theme through the shared tokens.** Colors come from `styles` (`COLORS`,
  `WDL_COLOR_MAP`, `apply_dark_theme`), never hardcoded hex — they must match the
  CSS `:root` block generated from `styles.THEME`.
- **The one vendored front-end asset.** `game_detail.py` renders the board with
  Lichess's open-source pgn-viewer, bundled locally in `assets/`
  (`lichess-pgn-viewer.min.js` is an ES module, kept out of Dash's classic
  `<script>` bundle via `assets_ignore` and imported on demand by
  `assets/lpv-init.js`; `lichess-pgn-viewer.css` is self-contained — board,
  pieces, and fonts are data-URIs). The page emits a `.lpv` mount with the moves
  in `data-pgn-*` attributes and a `.lpv-switch` switcher; the init script does
  the rest. Themed through the viewer's own `--c-lpv-*` variables mapped to
  `--cs-*` tokens (`--board-color` ← `--cs-board`). It is the only client-side JS.
  The **Engine** view (F7) is a server-rendered `.lpv-engine` sibling panel, not
  a board: the init script toggles it against the board mount (and dispatches a
  `resize` so its Plotly eval chart redraws when revealed).

## Adding a page
1. New `pages/<name>.py` with `dash.register_page(...)` + `layout()`.
2. Build layout from `components.py` helpers; style via `styles`.
3. Chart callbacks take `FILTER_INPUTS`, call `get_filtered()`, return figures.
4. It appears in the nav automatically (unless `nav=False`).
5. Add a smoke assertion in `../tests/test_ui_smoke.py` (every page must boot,
   render, and wire up).

## Related context
- Store & accessors: `../data.py` — what data is reachable
- Filters, `FILTER_INPUTS`, lens application: `../filters.py`
- Persistent chrome & Sync machinery: `../shell.py`
- Pure stats: `../pgn_stats_core.py`, `../uscf_core.py`
- Domain vocabulary: `../CONTEXT.md`
