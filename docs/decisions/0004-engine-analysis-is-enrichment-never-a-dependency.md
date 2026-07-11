---
status: accepted
---

# Engine analysis is enrichment, never a dependency

## Context and Problem Statement

Two facts forced the shape of engine analysis. First, Lichess does not permit triggering analysis through its API, so requesting it stays a manual click on the site and the dashboard can only ever *read* what is already there — making "analysis is optional enrichment" the only honest contract. Second, the computer analysis Lichess produces lands in the same Study export the app already trusts as the source of truth (ADR 0001): when you request analysis on a Chapter (the one extra click at the board), the export gains, per move, an `[%eval]` evaluation, a natural-language judgment ("Blunder. Nh5 was best."), and the recommended line as a variation.

## Considered Options

- Read the Lichess-computed analysis out of the existing Study PGN export, as enrichment
- Bundle an engine or call a third-party analysis service

## Decision Outcome

Chosen: "read Lichess's analysis from the Study export as enrichment", because it is the only contract the Lichess API honestly supports, and the source is the export the app already trusts — analysis is read, never authored in the app, so it cannot contradict the Games it annotates.

A pure module, `engine_analysis_core`, parses that movetext into a structured `GameAnalysis` — the per-move evals, the win-probability swings, and the **critical moment** (the Game's single biggest swing, attributed to whichever side made it). No engine is bundled and no third-party analysis service is called. This layers *onto* the Games exactly as USCF data does (ADR 0003), and the same blast-radius argument applies.

### Consequences

- Good, because a Sync that reaches Lichess succeeds whether or not any Game is analyzed. A Game with no requested analysis degrades cleanly to `analyzed=False` and an empty analysis — it is never an error, never excluded from the archive, and never blanks a page.
- Good, because the analysis-ingestion pass runs after the Studies are loaded, following the `uscf_core.enrich_games` pattern; its enrichment columns (`Analysis`, `Analyzed`) always exist, so pages never check for their presence. A single Chapter that fails to parse degrades to an empty analysis rather than failing the pass.
- Bad, because **OTB time-trouble cannot be auto-detected.** The Study export of an over-the-board Game carries no clock data — there is no `[%clk]` to mine — so the dashboard cannot tell when a mistake was made under time pressure. The manual `#time-trouble` Tag (ADR 0002) therefore remains the only signal for it, and the critical-moment / error-profile features make no attempt to infer it.

## More Information

Analyses are recomputed each Sync for now: parsing is pure, deterministic, and cheap for 63 Games. The disposable cache that avoids re-parsing (and re-billing the AI summary) lands with the AI-summary slice, with the same lifecycle as `uscf_cache.json` — never a source of truth, failures degrade to "no cache".
