# USCF data is enrichment, never a dependency

USCF data is fetched from the MUIR ratings API (`ratings-api.uschess.org/api/v1/...`) for a single configured member ID, and is layered *onto* the Games as enrichment. The Studies on Lichess remain the source of truth for what Games exist (ADR 0001 is unchanged). If the USCF API is unreachable, changed, or removed, every existing dashboard feature keeps working; only USCF-specific panels degrade to cached data plus a warning.

The MUIR API is undocumented and unofficial — its routes were discovered by reading the ratings website's JavaScript bundle, not from a published spec. US Chess could rename, restrict, or break it at any time without notice. Building it in as a hard dependency would let a third party's silent change take down the whole dashboard; treating it as enrichment caps the blast radius at "USCF cards go stale."

Two consequences follow. First, a Sync that reaches Lichess but not USCF is a *successful* Sync. Second, USCF responses that can never change once written (crosstables of rated events, past monthly supplements) are cached aggressively, so a routine Sync makes only a handful of USCF calls — both politeness toward an API we were not invited to use, and resilience for when it disappears.

The alternative of scraping the legacy MSA pages (uschess.org/msa) was rejected: that system is frozen ("no new events or ratings will be posted") and the member's entire career exists only in MUIR.
