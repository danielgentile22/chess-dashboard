# PRD: Apple-style design overhaul — clean, simple, mobile-first dashboard

> Status: ready-for-agent
> Produced from a full design review (code + live app screenshots at 1440px and 390px) on 2026-06-02.
> Updated 2026-06-02 after the Phase D merge (Series → Rated Events): every decision re-verified against the new Events page, the Scouting Report's USCF identity block, and the Milestones timeline.

## Problem Statement

My dashboard works, but its design grew feature-by-feature and it shows. When I open it — especially on my phone at the chess club — the chrome competes with my chess:

- **On my phone the header is literally broken**: the streak fire, form dots, reconciliation badge, and game count physically overlap each other. The one place I check most (current form, before a round) is the most damaged surface.
- **The Games page is unusable on a phone**: ~21 columns, and the first ones I see are Index / Date / Event — I have to scroll sideways past six columns to find out who I played and whether I won.
- **The embedded Lichess board renders in light theme** inside my dark dashboard. Opening a Game's detail view is a flashbang.
- **Color is everywhere, so it means nothing**: the KPI row alone uses four accent colors (green, red, gold, blue). Gold is supposed to be "reserved for achievements" but it's on table headers, the filter summary, the nav underline, baselines — and the brand-new Events page shipped with gold crosstable headers, gold expand arrows, and a gold wash on my own crosstable row. Charts are giant blocks of fully-saturated green and red.
- **Three typefaces** (a literary serif, a mono, a sans) plus uppercase tracked labels make every surface busier than the content needs.
- **Cards stretch to match their neighbors' heights**, leaving big dead zones ("Last 20 games", "Average game length").
- **Framework defaults leak through**: native blue checkboxes in the filter drawer, raw Dash pagination, a white "focused row" in the Games table, and a "Favourite Opening" KPI that truncates to "Italian Game: Scot…".
- **Rotated diagonal labels** on the Events and Opponents charts overlap into unreadability.

I like how Apple UIs look: calm, neutral surfaces; one accent at a time; the content is the interface. I want my dashboard to feel like that — on desktop and on the phone I actually bring to tournaments — with just enough motion to feel polished, and nothing that distracts from my games.

## Solution

A single coherent design system — Apple dark mode, applied to every page:

1. **Apple system typography everywhere** (SF Pro on my devices). The serif and the mono font go away; numerals use tabular figures so columns align.
2. **Apple dark palette with strict color discipline**: near-black background, borderless cards separated by fill, hairline separators inside lists. Color appears only where it carries meaning — win green, loss red, conflict orange, achievement gold (only on things I achieved: the streak fire, the celebration banner, milestone markers, official USCF achievements, and my Rated Event placements).
3. **A simplified header**: brand, streak fire + form dots, reconciliation badge, the Official/Live lens, Filters, Sync. The game count and date range move into the filter drawer; sync freshness moves to the Sync button's tooltip and the post-Sync toast. The mobile header lays out correctly.
4. **Tables rebuilt around me, not the PGN**: the Games table drops redundant White/Black columns for player-centric ones (opponent, ratings, my color, outcome); headers go quiet; text left-aligns. On a phone, Games render as cards instead of a table. The Events page's Series groups and crosstables get the same quiet treatment.
5. **Charts that read instantly**: horizontal bars wherever names are long (Events, Opponents), softer fills, thin gridlines, no rotated labels.
6. **The Lichess board embeds in dark theme.**
7. **Subtle motion**: cards fade up with a slight stagger on page load, the nav underline slides between tabs, cards lift gently on hover, the drawer uses Apple's sheet curve. Everything stops once the page settles, and all of it respects reduced-motion.

All features stay. Nothing is removed — a few things are relocated.

## User Stories

### On the phone (the chess-club view)

1. As Daniel, I want the header to lay out correctly on my phone, so that the streak fire, form dots, and reconciliation badge don't overlap each other before a round.
2. As Daniel, I want the Games page to show each Game as a card on my phone (opponent, result, date, event), so that I can find a game without scrolling sideways through 20 columns.
3. As Daniel, I want charts sized for a phone screen, so that I don't scroll through giant half-empty plots.
4. As Daniel, I want touch targets at least 44px on nav tabs, preset chips, and repertoire tree rows, so that taps land on the first try.
5. As Daniel, I want the filter drawer to keep dropping down from the header on my phone, so that filtering stays one thumb-reach away.
6. As Daniel, I want the Scouting Report readable on a phone, so that my pre-game prep works where I actually do it.
7. As Daniel, I want a Rated Event's full crosstable readable on my phone, so that I can check the standings and my placement at the club.

### Color that means something

8. As Daniel, I want KPI values in neutral white with color only on semantically meaningful numbers (win % green, loss % red), so that important numbers don't compete for attention.
9. As Daniel, I want gold reserved for actual achievements — the streak fire, the celebration banner, peak/milestone markers, official USCF achievements on the timeline, and my final placement in a Rated Event — so that when I see gold it means I did something.
10. As Daniel, I want chart fills toned down (large areas at reduced opacity), so that big green/red blocks don't dominate the page.
11. As Daniel, I want conflict warnings (orange), losses (red), and achievements (gold) to stay visually distinct, so that "something's wrong" never reads like "something's celebrated".
12. As Daniel, I want table headers, crosstable headers, expand markers, filter summaries, and navigation to stop using gold, so that the achievement color regains its meaning.

### Typography and surfaces

13. As Daniel, I want the dashboard to use the same font my Apple devices use, so that it feels native to where I read it.
14. As Daniel, I want numerals with tabular figures in KPIs and tables, so that columns of numbers align and read cleanly.
15. As Daniel, I want cards separated from the background by fill instead of borders, so that pages feel calm instead of gridded.
16. As Daniel, I want cards sized to their content, so that "Last 20 games" and "Average game length" don't have huge empty areas.
17. As Daniel, I want the favourite-opening KPI to wrap to two lines, so that "Italian Game: Scotch Gambit" isn't cut off mid-name.

### Header

18. As Daniel, I want a calm header — brand, my current form, the reconciliation badge, the Official/Live lens, Filters, Sync — so that chrome doesn't compete with content.
19. As Daniel, I want the game count and date range in the filter drawer where I'm already thinking about scope, so that the header carries only what I need on every page.
20. As Daniel, I want sync freshness on the Sync button (tooltip) and in the post-Sync toast, so that I can still check it without it staring at me from every page.
21. As Daniel, I want the win-streak fire and form dots to stay in the header, so that my current form is the first thing I see on any page.
22. As Daniel, I want the Official/Live lens to stay in the header on every page, so that I always know which world view my numbers are in.
23. As Daniel, I want the reconciliation badge to stay one glance away, so that data disagreements never hide.

### Tables

24. As Daniel, I want the Games table built around me — opponent, opponent rating, my rating, my color, outcome — so that I don't mentally decode White/Black columns to find myself in my own games.
25. As Daniel, I want quiet table headers (no gold, no uppercase shouting) and left-aligned text, so that the data stands out, not the chrome.
26. As Daniel, I want clicking any row to keep opening that Game's detail view, so that the redesign doesn't change how I navigate.
27. As Daniel, I want the Series groups and crosstables on the Events page to follow the same quiet table treatment — neutral headers, hairline separators, a subtle fill highlighting my own row — so that the newest page doesn't shout in gold.
28. As Daniel, I want the white "focused row" glitch in tables gone, so that every row stays readable.

### Charts

29. As Daniel, I want tournament names readable on the Events chart, so that I don't tilt my head to read rotated labels.
30. As Daniel, I want opponent names readable on the Opponents chart, so that I can see who each bar belongs to.
31. As Daniel, I want the W/D/L donut, activity calendar, rating chart, and repertoire tree to keep working exactly as they do, so that the redesign changes how things look, never what they tell me.

### Game detail

32. As Daniel, I want the embedded Lichess board in dark theme, so that opening a game doesn't flashbang me inside the dark app.

### Motion

33. As Daniel, I want cards to fade up with a subtle stagger when I open a page, so that the app feels considered without being showy.
34. As Daniel, I want the active nav underline to slide when I switch pages, so that navigation feels continuous.
35. As Daniel, I want cards to lift slightly on hover, so that what's interactive is discoverable.
36. As Daniel, I want all animation disabled when my system asks for reduced motion, so that accessibility is respected.
37. As Daniel, I want nothing to keep moving after a page settles, so that I can read my stats in peace.

### Form controls and consistency

38. As Daniel, I want filter checkboxes, preset chips, and dropdowns that match the design system, so that the filter drawer doesn't look like a default browser form.
39. As Daniel, I want the Scouting Report's opponent USCF identity — the link to their official page and their then-vs-now ratings — styled by the design system, so that the newest features feel native rather than bolted on.
40. As Daniel, I want chart colors and CSS colors to come from one definition, so that they can never silently drift apart.
41. As Daniel, I want every feature I have today preserved through the redesign — including the Series → Rated Event groups, crosstables, and opponent enrichment — so that nothing I rely on disappears.

## Implementation Decisions

### Decisions made with Daniel during planning

1. **Typography → full Apple system font.** Drop Fraunces (serif) and IBM Plex Mono (mono). Mono remains only for inline code snippets (the `Lesson:` convention hints). Remove the Google Fonts import entirely.
2. **Header → simplified.** Metadata (game count, date range, freshness) relocates; form/streak, reconciliation badge, lens, and action buttons stay.
3. **Motion → subtle only.** No number count-ups, no chart draw-ins.
4. **Theme tokens → single source of truth.** Tokens are defined once in the styles module and injected as CSS `:root` variables at app startup (via the Dash index template). Plotly charts and CSS consume the same definition.
5. **Tests → all four new test areas** (theme consistency, mobile game cards, dark embed URL, Games column set) plus required updates to existing shell/smoke tests.
6. **Phase D gold → placement stays gold, chrome goes neutral.** On the new Events page, gold survives only where it marks an achievement: the "Finished Nth of M" placement line and official USCF achievement entries on the Milestones timeline. Crosstable headers, Series/crosstable expand markers, and the own-row highlight are chrome — they go neutral (quiet headers, hairlines, a subtle fill for my row).

### The design system

**Color (Apple dark-mode palette):**

| Token | Current (GitHub-dark) | New (Apple dark) |
|---|---|---|
| background | `#0d1117` | `#0a0a0c` |
| card | `#161b22` + border | `#1c1c1e`, no border |
| nested card | `#1c2128` + border | `#2c2c2e`, no border |
| separators | `#30363d` borders everywhere | hairlines `rgba(84,84,88,.5)`, inside lists only |
| text | `#e6edf3` | `#ffffff` |
| secondary text | `#8b949e` | `rgba(235,235,245,.6)` |
| tertiary text | `#6e7681` | `rgba(235,235,245,.3)` |
| win | `#3fb950` | `#30d158` (systemGreen) |
| loss | `#f85149` | `#ff453a` (systemRed) |
| draw | `#6e7681` | `#8e8e93` (systemGray) |
| interactive | `#58a6ff` | `#0a84ff` (systemBlue) |
| warning/conflict | `#db6d28` | `#ff9f0a` (systemOrange) |
| achievement (gold) | `#d29922` | `#d9a13d` (softened gold) |

**Color discipline rules:**
- KPI values neutral by default; color only where semantic.
- Gold only on achievements: streak fire count, celebration banner, peak/milestone markers (including official USCF achievement entries), and Rated Event placement lines.
- Chrome never carries gold: table and crosstable headers, expand markers, navigation, filter summaries, and row highlights use neutral colors.
- Every gold tint (washes, glows, gradients) derives from the gold token — no hardcoded color values that can drift from it.
- Large chart fills at ~80% opacity; full saturation reserved for small elements (dots, lines, badges, text).
- Cards: 12px radius, no border, no shadow; separation by fill.

**Typography:**
- System stack (`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif`).
- Page titles: bold, large (28px desktop / 24px mobile), tight tracking — iOS large-title feel.
- Data values: `font-variant-numeric: tabular-nums`.
- Labels: small, medium weight, secondary color; uppercase tracking kept only on card titles.

**Motion (all CSS, interruptible, `prefers-reduced-motion` respected):**
- Page load: cards fade up with 40ms stagger, 350ms ease-out.
- Nav: active-tab underline slides between tabs; segmented-control thumb slides between options.
- Cards: hover lift (−1px translate, 200ms).
- Drawer/overlays: Apple sheet curve `cubic-bezier(0.32, 0.72, 0, 1)`.

### Module breakdown

- **Theme tokens (new, deep module).** Single definition of every color/font/radius token. Exposes: the token dict for Plotly chart code, and a generated CSS `:root` block injected at app startup. Interface rarely changes; a test asserts CSS and chart colors can't drift. All gold washes/glows/gradients derive from the token (no hardcoded near-gold values).
- **Chart theme (existing, modified).** The shared dark-theme applier every chart calls: system font, thinner/fainter gridlines, borderless legends, softer hover labels, ~80%-opacity fill variants for W/D/L.
- **Shared components (existing, modified).** Quiet table styles (neutral headers, left-aligned, hairline separators, focused-row fix), KPI card with a text variant (wraps instead of truncating), content-sized cards, and a **new mobile game-card-list component** that renders Game rows as tappable cards.
- **Shell (existing, modified).** Simplified header per the decision above; sliding nav indicator; correct mobile layout (this is the fix for the overlap bug). The relocated metadata wires into the filter drawer summary and the Sync flow.
- **Filter drawer (existing, modified).** Summary line gains "Showing all 63 games · Jun 2025 – May 2026"; controls restyled by tokens.
- **Pages (existing, modified).**
  - Games: display columns trimmed from ~21 to ~15 player-centric ones (drop Index, White, Black, WhiteRating, BlackRating, raw Result); phone widths get the card list, desktop keeps the table — both fed by the same callback data.
  - Events: performance chart becomes horizontal bars; the Series → Rated Event groups and expandable crosstables restyle with the quiet table treatment (neutral sticky headers, hairline separators, subtle own-row fill); placement lines keep gold; Forfeit tags and round-result chips re-tint from tokens.
  - Opponents: opponent W/D/L chart becomes horizontal bars; Scouting Report heading hierarchy moves to system font sizes; the opponent USCF identity block (official-page link, then-vs-now ratings) restyles to the token system.
  - Trends: "Average game length" collapses into a compact stat strip; upset tables get the new table style.
  - Overview: KPI color discipline; "Last 20 games" card sizes to content; the Milestones timeline keeps gold on official USCF achievement entries (they're achievements) while game-milestone rows stay neutral.
  - Game detail: Lichess embed URL gains the dark-background parameter.
  - Openings / Lessons / Reconciliation: inherit tokens; spacing polish only.

### Bugs fixed as part of this work

1. Mobile header element overlap (fixed by the shell restructure).
2. Lichess embed light theme (fixed by the embed URL parameter).
3. Games table white focused-row (fixed by the table style overrides).
4. KPI text truncation (fixed by the KPI text variant).

## Testing Decisions

**What makes a good test here:** assert external behavior — what components render, what values functions return — never CSS pixel values or visual appearance. Visual quality is verified by screenshot review, not unit tests.

**Prior art:** the UI smoke harness (every page boots, renders, and wires its callbacks against sample data) and the shell callback tests. New tests follow the same fixtures and style.

**New tests:**
1. **Theme consistency** — the Plotly chart colors and the injected CSS variables both come from the single token source; a drifted value fails the test.
2. **Mobile game cards** — given Game rows, the card-list component renders one card per Game with opponent, outcome class, date, and event; rows without a ChapterURL render without a link.
3. **Dark embed URL** — the embed URL builder returns the Lichess embed path with the dark-background parameter for any ChapterURL.
4. **Games column set** — the Games page exposes exactly the trimmed player-centric column set, guarding against redundant columns creeping back.

**Required updates:** the shell tests (header structure and callbacks change with the metadata relocation) and the UI smoke tests (every page must still boot and render after restyling — including the Events page tests that assert the Series → Rated Event structure and crosstable rendering).

## Out of Scope

- No feature additions or removals — every existing surface, stat, and interaction survives, including everything Phase D shipped (Series → Rated Event groups, crosstables, opponent enrichment, achievement milestones).
- No changes to statistics, matching, rating-lens, Sync, or API client logic.
- The Review mode and celebration banner keep their current behavior (they're restyled by tokens only).
- Roadmap features beyond what is merged today.
- Light mode / theme switching — the app stays dark-only.

## Further Notes

- "Before" screenshots of every page at 1440px and 390px were captured during the design review — but they predate the Phase D merge. The Events page (Series → Rated Events, crosstables), the Scouting Report, and the Milestones timeline must be re-screenshotted at both widths before implementation starts, so the comparison baseline covers the whole app.
- Implementation order that minimizes risk: theme tokens → global restyle → shell/header → page-by-page → mobile cards → motion pass. Each step leaves the app working and testable.
- Verification beyond unit tests: run the app against the real Study (`abcdWXYZ`) and re-screenshot all pages at both widths; specifically confirm the four bugs above are gone.
- This PRD can be broken into implementation issues (e.g. with `/to-issues`) when ready; the natural slices match the implementation order above.
