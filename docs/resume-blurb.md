# Resume / portfolio blurb

Copy-paste variants for resume, LinkedIn, and portfolio pages. Keep numbers in sync with reality (test count: `make test`, page count: `ls pages/`).

## Resume (2 lines)

> **Chess Dashboard** — Python/Plotly Dash analytics app that syncs OTB games from the Lichess API, reconciles them against the undocumented USCF ratings API, and layers on engine analysis, LLM game summaries, and coach reviews. 10 pages, ~20 test modules with CI, five ADRs, multi-user auth with per-user data isolation, deployed on Fly.io.

## LinkedIn / portfolio (short paragraph)

> I built a full analytics product for my over-the-board chess career: games archived in Lichess Studies sync in via API and get enriched with official USCF ratings, tournament crosstables, Stockfish evaluations, AI-generated game summaries, and my coach's review chapters — matched to games by the moves played. The interesting engineering is in the data honesty: two unreliable sources are reconciled with every disagreement surfaced rather than silently resolved, and every enrichment layer degrades gracefully when its API is down. Python, Plotly Dash, Pandas; tested, typed, CI'd, and deployed on Fly.io.

## One-liner (for a projects list)

> Chess analytics dashboard reconciling Lichess and USCF data with graceful-degradation enrichment layers — Python/Dash, deployed on Fly.io.
