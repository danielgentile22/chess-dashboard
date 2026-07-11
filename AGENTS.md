# Chess Dashboard — Agent Guide

Analytics dashboard for Daniel's over-the-board USCF games. Games live in Lichess
Studies (source of truth); the app Syncs them into a Pandas DataFrame and renders
stats, trends, and lessons with **Plotly Dash** (multi-page). USCF ratings data
*enriches* the Games but is never required.

**Read these first, in order:**
1. `CONTEXT.md` — the domain glossary (Game, Study, Sync, Series vs Rated Event,
   Official vs Live Rating, Forfeit, Reconciliation, Lesson, Tag…). Use these
   exact words; they have precise, non-interchangeable meanings.
2. `docs/decisions/000{1..7}-*.md` — the seven load-bearing decisions (below).
3. `README.md` — exhaustive feature tour, CLI flags, env vars, deployment.

## Intent Layer

**Before modifying code in a subdirectory, read its `AGENTS.md` first.**

- **Pages (UI)**: `pages/AGENTS.md` — the 10 Dash page modules and the
  filter/callback conventions every page follows.

Root-level modules (this directory) are mapped under *Module map* below.

## Global invariants

These are the hidden contracts. Violating one is almost always a bug, even if
tests pass:

- **One HTTP boundary per source.** `lichess_client.py` is the *only* module that
  talks HTTP to Lichess; `uscf_client.py` is the *only* one that talks HTTP to
  USCF; `ai_summary.py` is the *only* one that talks HTTP to Anthropic (the AI
  game summaries). Everything downstream works on already-fetched data. Never add
  a `requests`/`httpx` call anywhere else.
- **USCF is enrichment, never a dependency** (ADR 0003). A Sync that reaches
  Lichess but not USCF must still succeed. USCF surfaces degrade to cached data +
  an "unavailable since" warning. Enrichment columns *always* exist on the
  DataFrame (every Game simply unmatched when USCF is off), so pages never check
  for their presence — keep it that way.
- **Lichess Studies are the source of truth** (ADR 0001). Games come only from the
  explicitly-configured study IDs. Disputed facts always **display the Lichess
  version**; disagreements surface in Reconciliation — the app never silently
  "corrects" data.
- **Lessons & Tags live on Lichess** (ADR 0002). A Lesson is a chapter comment
  starting with `Lesson:`; `#hashtags` become Tags. The app extracts both during
  Sync and never writes them. There is no app-side editor and no app database.
- **Engine analysis is enrichment, never a dependency** (ADR 0004). It is *read*
  from the computer analysis Lichess already embedded in the Study export
  (`[%eval]` + judgments + variations) — no bundled engine, no analysis API. A
  Sync that reaches Lichess succeeds whether or not any Game is analyzed; an
  un-analyzed Game degrades to `analyzed=False`. The enrichment columns
  (`Analysis`, `Analyzed`, `Summary`) *always* exist, so pages never check for
  them. The plain-English **AI Summary** (Claude Haiku, via `ai_summary`) is the
  same kind of optional enrichment: it summarizes the engine's *already-computed*
  facts only — never asked to evaluate chess — and with no `ANTHROPIC_API_KEY`,
  or on any client failure, it degrades to `""` without failing the Sync. Cached
  in the disposable `analysis_cache.json` so an unchanged Game is never re-billed.
  OTB time-trouble can't be auto-detected (no clock data) — the manual
  `#time-trouble` Tag stays the only signal.
- **No database; one module-level store, one worker** (ADR 0006). `data.py` holds
  the Synced data at module scope — since ADR 0005 a registry of per-user stores,
  resolving to a single default store in single-user/ungated mode, so accessors
  keep their no-argument signatures. All callbacks read via `data.get_df()` and
  **never mutate** the result (`apply_filters` copies before filtering).
  `refresh()` swaps the store atomically — readers see the old or new dataset,
  never a mix. The store is worker memory, so the app runs exactly one Gunicorn
  worker on one machine; never raise `workers` above 1 or the Sync swap and the
  per-request `threading.local` user activation break.
- **Match, never guess** (ADR 0007). USCF Game Records attach to Games by member
  ID + result first, then a strictly-unambiguous name + date-window fallback for
  chapters without a typed FideId. Color/date are tiebreakers, never match
  requirements; any ambiguity leaves a Game unmatched rather than mis-attached.
  Unmatched Games/records surface in Reconciliation — the app never invents a
  pairing.
- **`pgn_stats_core.py` is framework-agnostic.** Every stats function takes a
  DataFrame and returns a DataFrame/dict — zero Dash imports. Keep it importable
  from a notebook. The same separation mirrors into USCF: `uscf_client` (HTTP) vs
  `uscf_core` (pure interpretation).
- **The Official/Live lens is a lens, not a filter.** It changes what "your
  rating" *means* (which rating series powers every rating-derived number); it
  never hides Games. It's applied in exactly one place — `filters.get_filtered()`
  — and rides `FILTER_INPUTS` so every page follows it for free.

## Module map (root directory)

| File | Owns |
|---|---|
| `app.py` | Entry point — Dash factory (`use_pages`), CLI args, module-level `server` for gunicorn, theme-token injection. Imports `pages/*` only *after* `data.initialize()`. |
| `config.py` | Env-var config (`LICHESS_STUDY_IDS`, `USCF_MEMBER_ID`, …). |
| `data.py` | The module-level data store + all `get_*()` accessors. `initialize()` at startup, `refresh()` on the Sync button. **Read this to understand what data pages can reach.** |
| `sync.py` | Sync orchestrator: Studies → merged/deduped Games; USCF → enrichment. PGN-cache offline fallback. |
| `lichess_client.py` | Lichess study-export HTTP client (the only Lichess HTTP). |
| `uscf_client.py` | USCF MUIR ratings-API HTTP client (the only USCF HTTP). |
| `uscf_core.py` | Pure USCF interpretation: profile, Official/Live series, **matching engine**, `enrich_games`, `reconcile`, `apply_rating_lens`, standings/round numbers, achievements. (~1.6k lines.) |
| `pgn_stats_core.py` | Pure PGN parsing + every statistics/insight function. Framework-agnostic. (~1.8k lines.) |
| `engine_analysis_core.py` | Pure engine-analysis interpretation (ADR 0004): one Game's movetext → `GameAnalysis` (per-move evals, win% swings, the critical moment, error profile, `player_accuracy`). `enrich_games_with_analysis` mirrors `uscf_core.enrich_games`. |
| `analysis_trends.py` | Pure Analysis-page aggregates over the error profile (ADR 0004), mirroring the Phase-4 analytics: accuracy trend, mistake-type trend (both rating-overlaid), phase × type matrix, mistake move-number histogram. DataFrame-in → data-out; awaiting-analysis Games excluded. |
| `ai_summary.py` | The AI-summary boundary (ADR 0004) — the *only* Anthropic HTTP. `build_prompt` (facts only) + `summarize` (no-op without a key, degrades silently, cache-aware). |
| `analysis_cache.py` | Disposable `analysis_cache.json`: AI Summaries keyed by Game identity + facts fingerprint, so an unchanged Game isn't re-billed. USCF-cache lifecycle (never a source of truth). |
| `auth.py` | Login gate for multi-user mode (ADR 0005): session cookie, per-request store activation. Ungated when `USCF_DASHBOARD_USERS` is empty. |
| `user_config.py` | Parses `USCF_DASHBOARD_USERS` records; `python -m user_config hash '<pw>'` hashes passwords. |
| `coach_match_core.py` | Pure coach-review matching: coach Chapters → the user's Games, by the moves played. Coach content is enrichment, never a dependency. |
| `shell.py` | Persistent chrome: header, nav tabs, lens toggle, Sync machinery (`sync-store`, toast, freshness). Never unmounts → filter state survives navigation. |
| `filters.py` | Global filter drawer + the shared `FILTER_INPUTS` list and `get_filtered()` helper. |
| `components.py` | Shared UI building blocks (cards, KPI tiles, form dots, profile card…). |
| `styles.py` | Color palette / `THEME`, dark-theme helpers, `empty_fig()`. Single source for both Plotly colors and the CSS `:root` block. |

## Build, test, run

```bash
make install-dev   # venv + runtime + dev deps (pytest, ruff, mypy)
make test          # pytest with coverage  (1000+ tests; keep them green)
make lint          # ruff (auto-fix)
make typecheck     # mypy on pgn_stats_core.py
make run-debug     # hot-reload dev server
python app.py --study <ID>   # run directly; needs a study ID or LICHESS_STUDY_IDS
```

`.env` holds the Lichess token and USCF member ID for local runs. CI is
lint → test → typecheck (`.github/workflows/ci.yml`).

## House rules

- Ratings display as **whole numbers** in the UI (Live series keeps decimals
  internally). Daniel asked for this explicitly.
- **Never `git add -A` in this repo** — large caches (`uscf_cache.json`,
  `games.pgn`, `.coverage`) and the venv live here. Stage files by name.
- Adding a stat? Put the pure function in `pgn_stats_core.py` (or `uscf_core.py`),
  unit-test it there, then wire it into a page callback — don't compute in the page.
