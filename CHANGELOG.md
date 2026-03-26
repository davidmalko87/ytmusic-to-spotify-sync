# Changelog

All notable changes to this project are documented here.

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

---

## [0.4.0] - 2026-03-24

### Added
- `--limit N` flag for the `sync` command to cap the number of new tracks matched per run, helping users stay within Spotify's daily API quota on large playlists.

---

## [0.3.1] - 2026-03-24

### Fixed
- Resolved three bugs identified in a master code review (malformed search queries, snapshot path handling, CSV encoding edge cases).
- Fixed Spotify rate limiting for large playlists: short waits (≤ 120 s) now sleep-and-retry automatically; longer waits raise a `RateLimitError` so the user is prompted to re-run rather than blocking indefinitely.

---

## [0.3.0] - 2026-03-22

### Added
- Rate-limit handling for the Spotify search API with exponential back-off and automatic retry on 429 responses.
- Resume capability: match progress is saved to `data/match_cache.json` every 25 tracks so an interrupted sync can continue where it left off.

---

## [0.2.1] - 2026-03-22

### Fixed
- Fixed redirect URI default from deprecated `localhost` to `127.0.0.1` to satisfy Spotify's OAuth requirements.
- Fixed Chrome DevTools header parsing: the tool now correctly handles the two-line `key / value` format as well as the standard `key: value` format.
- Fixed `ytmusicapi` auth setup call: use `ytmusicapi.setup()` instead of the removed `YTMusic.setup()` class method.

---

## [0.2.0] - 2026-03-22

### Added
- Spotify audio features enrichment: every matched track is annotated with `danceability`, `energy`, `valence`, `tempo`, `key`, `mode`, `loudness`, `speechiness`, `acousticness`, `instrumentalness`, `liveness`, and `time_signature`.

---

## [0.1.0] - 2026-03-22

### Added
- Initial release: command-line tool to sync a YouTube Music playlist to Spotify.
- Three-pass track matching: ISRC exact match → normalised title + artist search → fuzzy/relaxed fallback.
- Diff-based sync using JSON snapshots — only added/removed tracks are processed on each run.
- Interactive CLI menu (run without arguments).
- `--dry-run` flag to preview all changes before committing them.
- CSV import/export support as an alternative to the live YT Music API.
- Unmatched track tracking: failed matches are written to `data/unmatched.csv` and can be retried with `retry-unmatched`.
- Metadata enrichment: ISRC, explicit flag, and album release date captured for every matched track.
