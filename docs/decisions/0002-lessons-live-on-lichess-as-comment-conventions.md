---
status: accepted
---

# Lessons live on Lichess as comment conventions, not in an app database

## Context and Problem Statement

Two forces shaped where lessons should live. First, keeping the app stateless: the dashboard was built to deploy on ephemeral free-tier infrastructure with no guaranteed persistent disk, so anything it needs to remember has to live somewhere durable outside the process. (The app has since moved to Fly.io with a mounted volume, but that volume holds only disposable caches — the stateless-with-respect-to-lessons posture still holds, and re-adding an app-side store now would give up its main benefit for no real gain.) Second, Daniel already annotates games as Lichess chapter comments, with a board in front of him.

## Considered Options

- Lessons as Lichess comment conventions, extracted during Sync
- An in-app lesson editor backed by an app database (Supabase/Postgres)

## Decision Outcome

Chosen: "Lichess comment conventions", because an app-side store would split annotations across two homes and add infrastructure to a zero-maintenance app.

A Game's lesson is a Lichess chapter comment starting with `Lesson:`; hashtags (`#endgame`, `#time-trouble`) anywhere in a chapter's comments become that Game's tags. The app extracts both during Sync and never stores them itself.

### Consequences

- Good, because annotations stay in one durable home, alongside the board where they are written.
- Good, because the app stays stateless with respect to lessons — no database to run or migrate.
- Bad, because the dashboard is read-only with respect to lessons: writing or editing a lesson always happens on Lichess.
