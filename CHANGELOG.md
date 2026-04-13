# Changelog

All notable changes to this project are documented here.

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

---

## [0.5.0] - 2026-04-13

### Added
- **Track popularity** (0–100) from Spotify `/tracks` endpoint for playlist analysis.
- **Artist genres** (comma-separated) from Spotify `/artists` endpoint — fetched per unique primary artist.
- **Album type** (`album`, `single`, `compilation`) and **track number** enrichment columns.
- `audio_features_fetched` flag — tracks whether audio features were already attempted, preventing wasteful retries on 403-restricted endpoints.
- `backfill_track_metadata()` — batch-fetches missing metadata (duration, ISRC, popularity, album info) for previously matched tracks using `/tracks` endpoint (batches of 50).
- `backfill_artist_genres()` — fetches genre tags for unique primary artists via `/artists` endpoint.
- `get_tracks_batch()` and `get_artists_batch()` batch helpers in `spotify_client.py`.
- tqdm progress bars for all batch enrichment operations (metadata, artists, audio features).
- Sync summary printed after completion: total tracks, matched, unmatched, breakdown by match method.
- Interactive menu now **loops** until user selects Exit (previously exited after one command).
- `PermissionError` fallback in CSV writer — if the file is locked (e.g. open in Excel), writes to a timestamped fallback file instead of crashing.
- Log rotation via `RotatingFileHandler`: 5 MB max with 3 backups (replaces unbounded `FileHandler`).

### Fixed
- Confidence scores could exceed 1.0 for ISRC and duration-boosted matches (now capped at 1.0).
- Audio features detection used `danceability == 0.0` which could be a legitimate value; now uses the `audio_features_fetched` flag.
- `duration_seconds` parsing crash when YT Music API returns malformed data (added `try/except`).
- Backfill filter now includes tracks missing ISRC (not just missing `spotify_duration_ms`), so title+artist matches also get their ISRC populated.
- Match cache resume message now shows path hint for clearing stale cache.

### Changed
- Enriched CSV now has 37 columns (was 32): added `audio_features_fetched`, `popularity`, `artist_genres`, `album_type`, `track_number`.
- `to_csv_row()` uses `audio_features_fetched` flag for audio feature column serialization (preserves legitimate 0.0 values).
- `from_csv_row()` gracefully handles old CSVs missing new columns (backward compatible).

---

## [0.4.1] - 2026-03-28

### Added
- `pyproject.toml` with full PyPI metadata, classifiers, and `ytmusic-sync` entry-point script.
- GitHub Actions workflow (`.github/workflows/publish.yml`) that builds and publishes the package to PyPI automatically on every `v*` tag push, using OIDC trusted publishing.

### Fixed
- Duplicate tracks on re-sync: tracks restored from the match cache were incorrectly appended to `matched_results`, causing them to be re-added to the Spotify playlist.
- Audio features 403 resilience: the enricher now detects three consecutive 403 responses from the `/audio-features` endpoint and stops retrying, avoiding wasted API calls on restricted app types.

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
