# Chess Stats Dashboard

Analytics dashboard for Daniel's over-the-board USCF chess games. The games live in Lichess studies; the app turns them into stats, trends, and lessons.

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
Fetching every designated Study from the Lichess API and rebuilding the full set of Games. Happens at app startup and on demand via a Sync button; a successful Sync also refreshes the local cache.

**Event**:
A tournament or ladder under which Games were played (PGN `Event` header), e.g. "ACC Friday Ladder".

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

## Example dialogue

> **Dev:** A new game was played — what happens?
> **Daniel:** I add it as a new Chapter to the active Study on Lichess. Next time the dashboard Syncs, the Game shows up in every chart.
> **Dev:** What if the active Study is full?
> **Daniel:** I create a new Study on Lichess, designate it (add its ID to the dashboard config), and keep adding Chapters there. The old Study stays designated but frozen.
> **Dev:** Where do I find what you learned from a Game?
> **Daniel:** In its Lesson — a chapter comment starting with `Lesson:` that I wrote on Lichess. The Tags in my comments tell you *what kind* of mistake it was; the dashboard counts those to find my recurring weaknesses.
> **Dev:** And before you play someone you've faced before?
> **Daniel:** I open their Scouting Report on my phone — score, their openings against me, and my own Lessons from those Games.
