---
status: accepted
---

# Lichess studies are the source of truth, designated by explicit study ID

## Context and Problem Statement

The dashboard needs an authoritative, low-maintenance source for the Games it analyzes. Manual PGN export was already failing in practice (the local file was 5 games stale the day this was decided), and the 64-chapter study limit means the archive will span multiple studies, making manual merging worse over time.

## Considered Options

- Fetch from the Lichess study export API for an explicit, configured list of study IDs
- Pull *all* studies by username (`/api/study/by/{user}/export.pgn`)
- Keep uploading manually exported PGN files

## Decision Outcome

Chosen: "explicit study ID list", because it can never surprise — no future study silently pollutes the stats — at the cost of one config edit per new study (~once a year).

Games are fetched from the Lichess study export API (`GET /api/study/{id}.pgn`) for an explicit, configured list of study IDs — not uploaded as PGN files, and not pulled by username. A local PGN cache of the last successful sync is kept only as an offline fallback.

### Consequences

- Good, because the set of Games contributing to the stats is always exactly what was configured.
- Bad / cost, because adding a new study requires a config edit (~once a year).

## Pros and Cons of the Options

### Pull all studies by username

- Good, because no config edit is ever needed.
- Bad, because any future non-USCF study (opening prep, analysis of other players' games) would silently pollute the stats.

### Manual PGN export

- Bad, because it was already failing in practice (stale local file), and the multi-study archive makes manual merging worse over time.
