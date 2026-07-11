---
status: accepted
---

# USCF Game Records match Games by member ID first, name second — and never guess

## Context and Problem Statement

A Game (a Lichess Chapter) and a USCF Game Record are the same over-the-board game seen by two systems, but the two share no common key. The matching engine (`uscf_core.match_games`) has to pair them so a Game can carry its USCF half — Rated Event, Section, official opponent identity, real round number — without ever attaching the *wrong* half. This strictness is load-bearing because a false match is not cosmetic: the USCF half feeds the Official/Live rating lens, upset margins, opponent-strength buckets, the fatigue chart's real round numbers, and Rated Event scores; a single wrong pairing silently corrupts all of them.

## Considered Options

- Two strict passes — opponent member ID first, narrow name matching second — that never guess
- Matching on Event-header text plus date
- Auto-resolving color or rating conflicts during matching

## Decision Outcome

Chosen: "member ID first, name second, never guess", with the bias made explicit: **prefer a missed match to an invented one** — missing a match is recoverable (type the FideId), inventing one is not.

Matching runs in two passes, in strict precedence order. The **primary pass** keys on opponent USCF member ID + result: the `WhiteFideId` / `BlackFideId` you type into a chapter, matched against the record's opponent ID and your outcome. The **fallback pass** keys on normalized opponent name + result + the Rated Event date window, and runs **only for chapters that have no typed FideId**. A chapter whose typed ID matched nothing never falls back to names — a wrong or stale ID is a discrepancy to surface in Reconciliation, not to quietly paper over with a name guess.

Color and the date window are **tiebreakers, never requirements**. Repeat opponents with identical results are disambiguated by color first, then by which record's event window the Game's date falls in. They break ties between otherwise-equal candidates; they are not gates. Color in particular is itself a fact the two sources can disagree on (ADR 0001: display the Lichess version, flag the disagreement), so requiring color agreement would throw away real matches. Name matching is deliberately narrow: case- and punctuation-insensitive, and it tolerates a first-name spelling variant only when the last name matches exactly and the first initial agrees ("Carter Clark" ↔ "Carver Clark").

The engine **never guesses**. The name pass matches a chapter only when exactly one record fits it *and* no other chapter fits that record; any ambiguity in either direction leaves the Game unmatched.

### Consequences

- Good, because unmatched Games and unmatched records are both exposed in the result, never silently dropped — they surface on the Reconciliation page.
- Good, because a Game with at most one move and no USCF record is classified a Forfeit (opponent no-show — USCF correctly never rated it); Forfeits count toward event scores but are excluded from win rate, streaks, opening stats, and upsets (issue #29).
- Bad, because some real matches go unmatched until a FideId is typed — the accepted price of never inventing one.

### Confirmation

The matcher is validated against a captured ground-truth fixture — `tests/data/uscf/games.json` (63 real Game Records) paired with the anonymized study snapshot — including the cases that are meant to be hard: an opponent with no typed FideId (name fallback), a spelling variant, repeat opponents disambiguated by color and date, an ID that matches nothing (must *not* fall back), and Forfeits. `test_uscf_core.py` asserts every intended pairing and every intended non-match. The trigger to tighten the name pass is a false positive in that fixture — a wrong pairing — not a false negative; missing a match is recoverable (type the FideId), inventing one is not.

## Pros and Cons of the Options

### Matching on Event-header text plus date

- Bad, because your PGN `Event` names and USCF's official event names are typed differently and unreliable — that is exactly why the two-level Series → Rated Event model exists.

### Auto-resolving color or rating conflicts during matching

- Bad, because that would violate ADR 0001 — the dashboard surfaces discrepancies, it does not correct them.
