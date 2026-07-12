#!/usr/bin/env python3
"""Anonymize a Lichess-exported PGN into a shareable demo seed.

Replaces every identifying value — opponent names, USCF member IDs
(`WhiteFideId`/`BlackFideId`), the Lichess study/chapter IDs in `ChapterURL`,
non-self annotator handles, and the quasi-identifiers that make a synthetic name
reversible via public USCF crosstables (real `Event`/`Site` names and the game
`Date`s, issue #89) — with stable synthetic equivalents, while leaving the chess
itself (moves, variations, `[%eval]`, NAGs, and the `Lesson:` comments)
byte-for-byte intact. The player themself is kept real: this is their dashboard.

Elos are left as-is: once the event and date are synthetic there is no
crosstable to look the rating up in, so it stops being a linkage handle.

Substitution is textual, never a parse-and-reserialize, so nothing in the
movetext can be silently dropped or reformatted.

    python scripts/anonymize_pgn.py real.pgn tests/data/demo-games.pgn

The mapping is derived from the input at runtime — no real name or ID is
baked into this file, so committing the script leaks nothing. Always run it
from the real source PGN: the date shift is applied on every pass, so re-running
it on an already-anonymized file would shift the dates a second time.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta

# The one identity kept real — it's the player's own dashboard. Their member ID
# maps to the value the README/tests already document for the demo.
SELF_NAME = "Daniel Gentile"
SELF_HANDLE = "danielgentile"  # Lichess handle in the Annotator header
DEMO_MEMBER_ID = "12345678"
DEMO_STUDY_ID = "abcdWXYZ"

# Synthetic-name pool. first × last gives 12×12 = 144 unique pairs, assigned in
# first-appearance order — plenty for one player's opponent history.
_FIRST = ["Alex", "Blair", "Casey", "Devon", "Emerson", "Finley",
          "Harper", "Jordan", "Kai", "Logan", "Morgan", "Quinn"]
_LAST = ["Archer", "Bennett", "Carter", "Dalton", "Ellis", "Foster",
         "Grant", "Hayes", "Ingram", "Jensen", "Keller", "Lawson"]


def _synthetic_name(i: int) -> str:
    if i >= len(_FIRST) * len(_LAST):
        raise ValueError(f"name pool exhausted at {i} opponents; widen _FIRST/_LAST")
    return f"{_FIRST[i % len(_FIRST)]} {_LAST[i // len(_FIRST)]}"


# Synthetic Event/Site pools, assigned in first-appearance order like the names.
# Whole real values are replaced (rating-class suffixes and venue cities dropped)
# so nothing survives to search a USCF crosstable on.
_EVENT_ADJ = ["Riverside", "Summit", "Lakeside", "Highland", "Fairview", "Cedar",
              "Granite", "Harbor", "Meadow", "Prairie", "Sunset", "Ironwood"]
_EVENT_NOUN = ["Open", "Classic", "Championship", "Invitational", "Challenge",
               "Masters", "Cup", "Festival", "Circuit", "Memorial"]
_SITE_POOL = ["Riverton", "Fairhaven", "Oakdale", "Millbrook", "Westford",
              "Kingsport", "Brookfield", "Ashford", "Clearwater", "Bridgeport",
              "Northgate", "Elmwood"]

# A fixed, uncommitted offset applied to every date; enough to break exact-date
# matching without disturbing the relative spacing the trends charts show.
_DATE_SHIFT = timedelta(days=57)
_DATE_LINE = re.compile(r'^\[(\w*Date)\s+"(\d{4})\.(\d{2})\.(\d{2})"\]', re.MULTILINE)


def _synthetic_event(i: int) -> str:
    if i >= len(_EVENT_ADJ) * len(_EVENT_NOUN):
        raise ValueError(f"event pool exhausted at {i}; widen _EVENT_ADJ/_EVENT_NOUN")
    return f"{_EVENT_ADJ[i % len(_EVENT_ADJ)]} {_EVENT_NOUN[i // len(_EVENT_ADJ)]}"


def _synthetic_site(i: int) -> str:
    if i >= len(_SITE_POOL):
        raise ValueError(f"site pool exhausted at {i}; widen _SITE_POOL")
    return _SITE_POOL[i]


def _shift_dates(text: str) -> str:
    """Shift every ``*Date`` header (Date/UTCDate/EventDate/EndDate) by a fixed
    offset; malformed/unknown dates (``????.??.??``) are left untouched."""
    def repl(m: re.Match) -> str:
        try:
            d = datetime(int(m[2]), int(m[3]), int(m[4])) + _DATE_SHIFT
        except ValueError:
            return m[0]
        return f'[{m[1]} "{d.year:04d}.{d.month:02d}.{d.day:02d}"]'
    return _DATE_LINE.sub(repl, text)


def anonymize(text: str) -> str:
    header = re.compile(r'^\[(\w+)\s+"(.*)"\]\s*$')

    # First pass: discover every real identity in first-appearance order.
    names: dict[str, str] = {}          # real name -> synthetic
    events: dict[str, str] = {}         # real event name -> synthetic
    sites: dict[str, str] = {}          # real venue -> synthetic
    member_ids: dict[str, str] = {}     # real USCF id -> synthetic
    chapters: dict[str, str] = {}       # real chapter hash -> synthetic
    study_ids: set[str] = set()
    self_member_ids: set[str] = set()

    # Track which side is the self-player per game so we can pin their member ID.
    cur = {"White": None, "Black": None}
    for line in text.splitlines():
        m = header.match(line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2)
        if tag in ("White", "Black"):
            cur[tag] = val
            if val != SELF_NAME and val not in names:
                names[val] = _synthetic_name(len(names))
        elif tag == "Event" and val and val not in events:
            events[val] = _synthetic_event(len(events))
        elif tag == "Site" and val and val not in sites:
            sites[val] = _synthetic_site(len(sites))
        elif tag in ("WhiteFideId", "BlackFideId"):
            side = tag[:5]  # "White" / "Black"
            if cur.get(side) == SELF_NAME:
                self_member_ids.add(val)
            elif val not in member_ids:
                member_ids[val] = f"{10_000_001 + len(member_ids)}"
        elif tag == "ChapterURL":
            um = re.search(r"study/([A-Za-z0-9]+)/([A-Za-z0-9]+)", val)
            if um:
                study_ids.add(um.group(1))
                ch = um.group(2)
                if ch not in chapters:
                    chapters[ch] = f"chap{len(chapters) + 1:04d}"

    for sid in self_member_ids:
        member_ids[sid] = DEMO_MEMBER_ID

    # Second pass: global textual replacement. Longest keys first so no real
    # value is a prefix of another (member IDs and names alike).
    replacements: dict[str, str] = {}
    replacements.update(names)
    replacements.update(events)
    replacements.update(sites)
    replacements.update(member_ids)
    replacements.update({s: DEMO_STUDY_ID for s in study_ids})
    replacements.update(chapters)

    out = text
    for real in sorted(replacements, key=len, reverse=True):
        out = re.sub(rf"(?<![\w]){re.escape(real)}(?![\w])", replacements[real], out)

    # Dates last, so the shift lands on the (unchanged) date headers.
    out = _shift_dates(out)

    # Non-self annotator handles (the coach) → a generic one. The self handle stays.
    out = re.sub(
        r'(\[Annotator "https://lichess\.org/@/)(?!' + re.escape(SELF_HANDLE) + r'")[^"/]+(")',
        r"\1ChessCoach\2",
        out,
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="real PGN to read")
    ap.add_argument("output", nargs="?", help="anonymized PGN to write (default: stdout)")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as fh:
        result = anonymize(fh.read())  # read fully before writing, so in==out is safe

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(result)
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
