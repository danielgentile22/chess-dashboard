# Chess Stats Dashboard

Analytics dashboard for Daniel's over-the-board USCF chess games. The games live in Lichess studies; the app turns them into stats, trends, and lessons. USCF's rating system (via ratings.uschess.org) enriches the Games with official data.

## Language

**Game**:
A single over-the-board USCF game played by Daniel. Each Game is stored as exactly one Chapter in a Study.
_Avoid_: match, round (a Round is a Game's position within an Event)

**Study**:
A Lichess study that Daniel has designated as part of his game archive, identified by its Lichess study ID. The set of designated Studies is the source of truth for all Games. Lichess caps a Study at 64 Chapters, so the archive spans multiple Studies over time.
_Avoid_: database, PGN file (those are exports/caches, not the source)

**Chapter**:
Lichess's term for one entry inside a Study. Each Chapter holds exactly one Game and has a stable URL (the ChapterURL) that deep-links to it on Lichess.

**Sync**:
Fetching every designated Study from the Lichess API and Daniel's USCF record from the USCF ratings API, then rebuilding the full set of Games and their enrichment. Happens at app startup and on demand via a Sync button; a successful Sync also refreshes the local cache. A Sync that reaches Lichess but not USCF still succeeds — USCF data is enrichment, never a dependency.

**Series**:
A tournament or ladder as Daniel experiences and names it (PGN `Event` header), e.g. "ACC Friday Ladder". A Series contains one or more Rated Events.
_Avoid_: Event (ambiguous — say Series or Rated Event)

**Rated Event**:
A USCF-rated tournament, identified by its USCF event ID, with official dates, Sections, standings, and a rating change — e.g. "ACC JUNE 2025". Daniel's monthly club ladder is one Series but twelve Rated Events per year.

**Section**:
The subdivision of a Rated Event that USCF actually rates (e.g. "U1600", "LADDER"). Rating changes happen per Section, never per game. Daniel can play more than one Section of the same Rated Event.

**USCF Game Record**:
USCF's official record of one rated game: opponent (with USCF member ID), color, result, and the Rated Event and Section it belongs to. Each USCF Game Record is matched to the Game (Chapter) that holds the moves.

**Official Rating**:
Daniel's USCF rating as published in the monthly rating supplement. An integer; the number that determines section eligibility, pairings, and prizes. Changes at most once a month.

**Live Rating**:
Daniel's USCF rating as recalculated after each Section he completes, carried to two decimals. Updates faster than the Official Rating and can differ from it by a large margin between supplements.
_Avoid_: current rating (ambiguous — official and live are both "current")

**Forfeit**:
A Game whose Chapter exists but where no game was actually played over the board (opponent no-show). It counts toward the tournament score but USCF never rates it, so it has no USCF Game Record.

**Reconciliation**:
The dashboard surface listing every disagreement between the Studies and USCF: matched Games whose facts conflict, USCF Game Records with no Chapter, Forfeits, and typed ratings that don't match the Official Rating.

**Lesson**:
The takeaway Daniel wrote for a Game — a Lichess chapter comment starting with `Lesson:`. A Game has zero or more Lessons; they are written on Lichess, never in the dashboard.
_Avoid_: note, annotation (an annotation is any chapter comment; a Lesson is the marked takeaway)

**Tag**:
A hashtag (e.g. `#endgame`, `#time-trouble`) appearing in any of a Game's chapter comments. Tags categorize what a Game taught and are filterable in the dashboard. The canonical taxonomy below is the default vocabulary; new freeform tags are allowed and surface on the Lessons page with counts so fragmentation stays visible.

**Streak**:
A run of consecutive Games with the same outcome, ordered by date. The current win streak drives the fire indicator; the longest win streak is a tracked personal best.

**Scouting Report**:
The pre-game dossier on one opponent: head-to-head score, the openings they've played against Daniel, how those Games ended, and the Lessons written after facing them.
_Avoid_: head-to-head (that's just the score; the Scouting Report includes Lessons and openings)

**Error Profile**:
A Game's classified mistakes by Daniel — the engine-judged non-best moves *he* played, recorded for every Game regardless of result so the improvement signal isn't biased by wins. Read from the computer analysis Lichess embeds in an analysed Chapter's export (see `docs/adr/0004`); a Game with no requested analysis has an empty profile. Each entry carries a Severity, a Phase, a Mistake Type, and the move number it happened on.
_Avoid_: weakness (a Tag-derived recurring theme on the Lessons page is a different thing)

**Severity**:
How bad a mistake was — `inaccuracy`, `mistake`, or `blunder` — recomputed from the move's win-probability drop at the 0.1 / 0.2 / 0.3 thresholds, not read from Lichess's text word. A swing below the inaccuracy line is no mistake at all and never enters the Error Profile.

**Phase**:
The part of the Game a mistake happened in — `opening`, `middlegame`, or `endgame` — from a per-position port of Lichess's open-source `Divider` (majors+minors ≤ 6 → endgame; ≤ 10, a sparse home rank, or high "mixedness" → middlegame).

**Mistake Type**:
What kind of error a mistake was — `tactical` (a forcing shot missed, or material dropped to a forcing sequence) or `positional` (a slow eval bleed with no forcing refutation) — by a deterministic heuristic over the moves, never an extra engine call.

## Tag taxonomy

| Tag | Means |
|---|---|
| `#opening` | Didn't know or misplayed the opening theory |
| `#tactics` | Missed a tactic or fell for one |
| `#calculation` | Miscalculated a concrete line |
| `#endgame` | Endgame technique error |
| `#time-trouble` | Clock management cost the game or quality |
| `#blunder` | A single move threw away the game |
| `#strategy` | Wrong plan or pawn-structure decision |

## Flagged ambiguities

- "PGN file" / "database": previously meant the source of truth (a manually exported file). Now means only a local cache of the last successful Sync. The source of truth is always the designated Studies on Lichess.
- "Event": previously the only event-like term; now ambiguous between Series (Daniel's grouping) and Rated Event (USCF's grouping). Say which one.
- "Rating" / "Elo": ambiguous between Official Rating and Live Rating. The ratings Daniel hand-types in chapter headers are his record of the Official Rating at event start — USCF's published supplement is the authority; typed values are cross-checked against it.

## Example dialogue

> **Dev:** A new game was played — what happens?
> **Daniel:** I add it as a new Chapter to the active Study on Lichess. Next time the dashboard Syncs, the Game shows up in every chart.
> **Dev:** What if the active Study is full?
> **Daniel:** I create a new Study on Lichess, designate it (add its ID to the dashboard config), and keep adding Chapters there. The old Study stays designated but frozen.
> **Dev:** Where do I find what you learned from a Game?
> **Daniel:** In its Lesson — a chapter comment starting with `Lesson:` that I wrote on Lichess. The Tags in my comments tell you *what kind* of mistake it was; the dashboard counts those to find my recurring weaknesses.
> **Dev:** And before you play someone you've faced before?
> **Daniel:** I open their Scouting Report on my phone — score, their openings against me, and my own Lessons from those Games.
> **Dev:** Your chart says you're 1545 but you said you're almost 1571 — which is it?
> **Daniel:** Both. 1545 is my Official Rating — the June supplement missed my last event. 1570.72 is my Live Rating after that event. The dashboard's Official/Live switch picks which one every stat uses.
> **Dev:** USCF lists a rated game I can't find a Chapter for.
> **Daniel:** Then it shows up in Reconciliation. Either I forgot to add the game to my Study, or it's one I'm skipping on purpose — like online-rated games, which aren't OTB.
