# Lessons live on Lichess as comment conventions, not in an app database

A Game's lesson is a Lichess chapter comment starting with `Lesson:`; hashtags (`#endgame`, `#time-trouble`) anywhere in a chapter's comments become that Game's tags. The app extracts both during Sync and never stores them itself.

Two forces drove this. First, the dashboard is hosted on free-tier infrastructure with no persistent disk, so the app must stay stateless — anything it needs to remember has to live somewhere else. Second, Daniel already annotates games as Lichess chapter comments, with a board in front of him; an in-app lesson editor (backed by Supabase/Postgres) was rejected because it would split annotations across two homes and add infrastructure to a zero-maintenance app.

Consequence: the dashboard is read-only with respect to lessons. Writing or editing a lesson always happens on Lichess.
