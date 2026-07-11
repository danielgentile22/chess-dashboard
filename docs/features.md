# Feature tour

The complete tour of every page and enrichment layer. For the short version, see the [README](../README.md#features).

### Pages

The dashboard is a multi-page app — each page loads only its own charts, so it stays fast on a phone:

| Page | What you get |
|---|---|
| **Overview** | 10 KPI cards · **USCF profile card** (all your ratings with Official · Live side by side, national/state rank, floor, membership warning) · last-20 streak badges · W/D/L donut · termination breakdown · milestone timeline (with your **USCF norms and awards** as gold official entries) · your top recurring weakness |
| **Trends** | GitHub-style activity calendar (one cell per day, colored by results) · **dual-line rating chart** (your Official Rating as a step line and your Live Rating per Rated Event, so you can watch them diverge and reconverge) · cumulative win rate · games per month · win rate by day of week · game-length distribution · results by time control · score by round (the fatigue check) · **upset tracker** (giant kills and upset losses by rating margin) |
| **Openings** | **Repertoire tree** (your games arranged move by move, branches that leak points flagged) · ECO family breakdown (A/B/C/D/E) · full opening detail table |
| **Opponents** | **Scouting Report** (search an opponent → their **USCF identity** with a link to ratings.uschess.org and **then-vs-now ratings**, score, rating gap, game timeline, their openings by your color, and every Lesson from facing them) · stacked W/D/L bar per opponent · outcome by rating bucket · outcome vs. rating scatter |
| **Events** | **Series → Rated Events**: each tournament as you name it expands into the events USCF actually rated — official names and dates, Section(s), your score, game count, live rating change (pre → post), per-event performance rating, your **final placement** ("Finished 5th of 116"), and the **full crosstable** (every player, scores, round-by-round results, your row highlighted) · plus the Rated Events you entered but never played |
| **Games** | Every game with Open-on-Lichess links, Lesson indicators (💡), Tags, and its **USCF status** (✓ matched by opponent ID · ≈ matched by name · ⚠ sources disagree · Forfeit) — click any row to open the game |
| **Lessons** | Every `Lesson:` you've written on Lichess, filterable by Tag and opponent · recurring-weakness callouts ("#time-trouble appears in 4 of your last 5 losses") · pre-game review mode |
| **Analysis** | Your **error profile** from the engine analysis Lichess embeds: the **tactical-vs-positional split** of your own mistakes across every analyzed game (the single biggest weakness at a glance) · a list of the Chapters still **awaiting analysis** (the one click at the board) |
| **Reconciliation** | Every disagreement between your Studies and USCF, grouped and actionable: color conflicts (both versions side by side) · USCF-rated games missing from your Studies · games USCF hasn't rated · chapters missing opponent IDs · typed ratings that don't match the Official Rating — each with fix-on-Lichess links and Dismiss |

Clicking a Game anywhere opens its **detail view**: an interactive board rendered by Lichess's open-source **pgn-viewer** (bundled as a local asset, not an iframe), behind a view switcher of up to four tabs — **Game / My Analysis / Engine / Coach** — each appearing only when it has content. Game is a clean replay; My Analysis plays your own variations and comments in place; Engine shows the AI summary, eval chart, and your judged moves with corrections; Coach shows your coach's review chapter. Alongside it sit the critical-moment headline, the Game's Lessons, Tags, metadata, and — when the Game is matched — its **USCF record** (Rated Event, Section, rating system, and the opponent as USCF registers them, linking to their page on ratings.uschess.org).

### Pre-game review mode

`/lessons?review=1` (the "Review before playing" button, or "Review before facing X" inside a Scouting Report) opens a full-screen, card-by-card walk through your most relevant Lessons — recurring weaknesses first, then the selected opponent's games, then everything else newest-first. Built for one hand and the five minutes before a round.

### Repertoire tree

The Openings page leads with your personal opening explorer: every game as White (or Black — toggleable) arranged move by move. Each branch shows how many games continued that way, a W/D/L bar, and its score; expanding a branch drills one move deeper, down to links into the exact games. Branches that score below your overall average for that color across 3+ games are flagged in red — that's where your repertoire is leaking points.

### USCF enrichment

Configure your USCF member ID and every Sync also pulls your official record from the USCF ratings API (ratings.uschess.org): all your ratings (with provisional game counts), national and state rank, rating floor, and membership expiration — shown on a profile card on Overview with your **Official Rating and Live Rating side by side** (the published monthly integer vs. where your rating really stands after your last event).

**The Official/Live lens** — an `[Official | Live]` switch in the header — picks which rating series powers every rating-derived number in the dashboard: KPI cards, upset margins, opponent-strength buckets, everything. It's a *lens*, not a filter: it never hides Games, it changes what "your rating" means. **Official** is the supplement in effect at each Game's Rated Event start date (the rating that determined your section and pairings), with opponents at their typed pairing-sheet values; **Live** is the pre-rating of the Section you played it in, with opponents at their **crosstable pre-ratings** — what they were really rated walking in, even when you never typed a rating for them. All ratings show as whole numbers. Games from before your first supplement show no Official value — the dashboard never invents a number. The Trends rating chart is the one place the lens hides nothing: both series always draw; the lens just picks which one leads.

**Opponent enrichment** — every matched opponent links to their page on ratings.uschess.org, and the Scouting Report shows **then vs now**: their rating when you last played (lens-aware) against their current rating, fetched politely (one call per unique opponent, refreshed at most weekly). "You beat them at 1433, they're 1400 now."

**The matching engine** pairs every USCF Game Record with its Game: by opponent member ID + result first (the `WhiteFideId`/`BlackFideId` headers you type into chapters), then by normalized opponent name + result + event date window for chapters without IDs. Repeat opponents with identical results are disambiguated by color and date — tiebreakers, never requirements, because color is itself a fact the sources can disagree on. Matched Games show their USCF half (Rated Event, Section, official opponent identity); games with at most one move and no USCF record are tagged **Forfeit** (opponent no-show) and excluded from win rate, streaks, opening stats, and upsets while still counting toward event scores.

Disagreements go to the **Reconciliation page** — conflicts get a ⚠ badge on the Game everywhere it appears, and the header shows a count of open items. The dashboard always displays the Lichess version of disputed facts; nothing is silently "corrected".

**The Events page is two-level** — *Series* (a tournament as you name it in the PGN Event header, like "ACC Friday Ladder") contain *Rated Events* (what USCF actually rated, like "ACC JUNE 2025" … "ACC MAY 2026"). Each Rated Event shows its official identity, Sections, your score (Forfeit wins count toward the score, never as games), the field size, and your live rating walking in → walking out. Games that matched no Rated Event stay visible under their Series; Rated Events you entered but never played get their own group.

**Standings and real round numbers** — each Rated Event carries your final placement and an expandable full crosstable: every player, their rating change, score, and round-by-round results, with your own row highlighted and your rounds linking to your Games. Crosstables of rated events never change, so each is fetched exactly once, ever, and cached permanently — a routine Sync makes zero crosstable calls. Matched Games also gain their **real round number** from the crosstable (you hand-type continuous ladder rounds; USCF knows which round of the Rated Event it really was), and the fatigue chart uses the real rounds.

**Norms and awards** join the Milestones timeline as official entries — gold, per the design language (gold is reserved for achievements). A norm or award appearing for the first time in a Sync gets the celebration banner, exactly like a personal best; ones the dashboard has already seen never re-celebrate, even across restarts.

USCF data is enrichment, never a dependency (`docs/decisions/0003`): a Sync that reaches Lichess but not USCF still succeeds, and USCF surfaces degrade to the last successful Sync's cached data with a clear "unavailable since" warning.

### Multi-user access & coach review

Set `USCF_DASHBOARD_USERS` and the whole dashboard goes behind a **login** — so it's no longer a public URL, and a coach's private study material can render safely behind the gate. The config is a JSON array, one record per allowed user: a username, a **hashed** password (`python -m user_config hash '<pw>'`), the Study IDs that hold their Games, the Study IDs that hold their coach's reviews, their USCF member ID, and their Lichess token. Adding a user is adding a record — there's no signup and no settings UI. Each user logs in and sees their **own** dashboard — their Games, USCF data, and analysis — fully isolated from everyone else's (`docs/decisions/0005`: the data store is a registry of per-user stores). Leave `USCF_DASHBOARD_USERS` empty and the dashboard runs single-user and ungated, exactly as before.

With login in place, **coach reviews come in as enrichment**, the same way USCF data does. On Sync the dashboard fetches each user's designated coach Studies (with their token for the private ones) and matches each coach Chapter to one of their Games **by the moves played** — robustly, even when names and dates are typed differently — automatically ignoring the coach's online games and teaching positions. The user's own main Study stays the source of truth (`docs/decisions/0001`): a coach Chapter only ever *enriches* a Game that already exists, never creates one.

Each matched Game's detail page gains a **Coach** view — the coach's chapter with all his variations and notes — completing the four-view switcher (Game / My Analysis / Engine / Coach), each tab appearing only when it has content. The Lessons page gains a **Coach's Notes** feed: the prose your coach wrote, newest first, each linking to its Game, kept visually distinct from your own Lessons. A coach Study being unreachable degrades to cached/empty and never fails a Sync (`docs/decisions/0003`); coach material is private, so it only ever renders behind the auth gate.

### Header

The sticky header celebrates current form: a 🔥 that grows with your win streak (extra glow at 5+), a 🧊 on cold streaks, and your last 5 games as colored dots. Plus the **Official/Live rating lens**, the Sync button, and a per-source freshness label ("Lichess synced X ago · USCF synced Y ago"). When a Sync sets a personal best — a new peak rating, a new longest win streak, or a win over the highest-rated opponent yet — or USCF recognizes something new (a norm, an award), a gold celebration banner appears until you dismiss it.

### Filters

A global filter drawer (right side on desktop, dropdown on phones) slices every chart on every page simultaneously — and the selection survives navigation:

| Filter | Options |
|---|---|
| **Presets** | All Games · Last 20 · This Year · White only · Black only · Wins only |
| **Color** | White / Black checkboxes |
| **Outcome** | Win / Draw / Loss checkboxes |
| **Termination** | Multi-select (Checkmate, Resignation, Timeout, …) |
| **Date range** | Calendar date picker |
| **Event** | Multi-select tournament picker |
| **Move count** | Range slider (e.g. games between 20–60 moves) |

All charts are dark-themed (GitHub-inspired palette) and update instantly when any filter changes.
