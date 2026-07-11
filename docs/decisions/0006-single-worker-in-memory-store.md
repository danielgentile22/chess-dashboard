---
status: accepted
---

# The data store lives in worker memory, so the app runs a single worker

## Context and Problem Statement

A player's whole archive is a few hundred DataFrame rows. Where should it live so that every callback can read it fast, without adding infrastructure? Lichess is already the source of truth (ADR 0001), so a second durable store would only add a second thing to keep consistent.

## Considered Options

- A module-level in-memory store, with the app pinned to exactly one worker
- A database (or shared cache) behind a multi-worker pool
- A process- or container-per-user model

## Decision Outcome

Chosen: "in-memory store, single worker", because loading the archive once at startup lets every callback read the shared DataFrame with no serialization and no `dcc.Store` round-trips — which is what makes filtering feel instant — and a database would only add infrastructure and a second thing to keep consistent (this is the "No database" decision the README states).

Synced Games live in a module-level store in `data.py`, rebuilt atomically on each Sync. Keeping the store in process memory forces one deployment constraint: **the app runs exactly one Gunicorn worker** (`workers = 1` in `gunicorn.conf.py`), on a single always-on Fly machine (`min_machines_running = 1`, `auto_stop_machines = false`). This is a correctness requirement, not a tuning knob. The Sync button swaps the in-memory DataFrame inside whichever worker handled the request; with two workers only that worker would see the new Games while the others kept serving the old ones until their own restart — a split-brain the user would experience as "my Sync half-worked." `WEB_CONCURRENCY` used to let an env var override the worker count; it was removed so nobody can trip the constraint by accident.

Multi-user access (ADR 0005) rides on the same single process. The per-user registry is a dict in that one worker's memory, and the active user is a `threading.local` set per request. That is safe *because* one synchronous worker handles each request start-to-finish on a single thread: the gate resolves the request's user and `activate()`s their store before any page renders, on the same thread Flask is handling. A pre-forked multi-worker pool or an async worker class would break that thread-local activation and leak one user's store into another's request — so the single-worker choice is load-bearing for isolation, not just for the Sync swap.

### Consequences

- Good, because callbacks read the shared DataFrame directly — no serialization, no round-trips, instant filtering.
- Good, because there is no database to run, migrate, or keep consistent with Lichess.
- Bad, because the app cannot scale horizontally as it stands — acceptable, because it serves one player, or a coach and a handful of students, not a public multi-tenant audience. Vertically it has enormous headroom: the whole dataset fits in tens of megabytes on one shared-CPU / 512 MB machine.

## Pros and Cons of the Options

### A process- or container-per-user model

- Bad, for the same reason ADR 0005 rejected it: it multiplies memory and deploy complexity for a dashboard shared with a few people, when a thread-local active store inside one process already gives complete isolation.

## More Information

What would trigger revisiting this: enough concurrent users that one worker's request serialization becomes the real bottleneck, or a need for zero-downtime deploys across more than one machine. At that point the store moves behind a shared cache (Redis) or a real database and the worker/machine caps lift together — a deliberate re-architecture, never a config flip.
