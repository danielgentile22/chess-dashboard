# Design-review screenshots

Visual baseline for the **Phase E — Apple-style design overhaul** (see
`docs/prd-design-overhaul.md`).

## `before/` — post-Phase-D baseline

The original "before" screenshots of every page (1440px and 390px) were
captured during the design review, but they predate the Phase D merge
(Series → Rated Events) and were **never committed to this repo**. This
directory is the first design-review baseline in version control.

It re-captures the three surfaces that changed after that original capture,
against the real Study (`abcdWXYZ`, member `12345678`), at both widths:

| File | Surface | Shows |
| --- | --- | --- |
| `before-events-1440.png` / `before-events-390.png` | Events | A Series group expanded (ACC Friday Ladder) with one crosstable open |
| `before-scouting-1440.png` / `before-scouting-390.png` | Scouting Report | An opponent (Zane Baker) with the USCF identity block — official-page link + then-vs-now ratings |
| `before-overview-milestones-1440.png` / `before-overview-milestones-390.png` | Overview Milestones | The Milestones timeline with the gold USCF achievement rows |

## Naming convention

`<phase>-<surface>-<width>.png`, where `<phase>` is `before` or `after`.
When the overhaul lands, the matching after-shots go in an `after/` directory
using the same surface and width names, so each before/after pair lines up
one-to-one (e.g. `before-events-1440.png` ↔ `after-events-1440.png`).

Desktop = 1440×900, phone = 390×844. The Events and Scouting shots are
full-page captures; the Milestones shots are framed on the timeline.
