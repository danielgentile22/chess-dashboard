# Lichess studies are the source of truth, designated by explicit study ID

Games are fetched from the Lichess study export API (`GET /api/study/{id}.pgn`) for an explicit, configured list of study IDs — not uploaded as PGN files, and not pulled by username.

Manual PGN export was already failing in practice (the local file was 5 games stale the day this was decided), and the 64-chapter study limit means the archive will span multiple studies, making manual merging worse over time. Pulling *all* studies by username (`/api/study/by/{user}/export.pgn`) was rejected because any future non-USCF study (opening prep, analysis of other players' games) would silently pollute the stats. An explicit ID list costs one config edit per new study (~once a year) and can never surprise.

A local PGN cache of the last successful sync is kept only as an offline fallback.
