# Changelog

All notable changes to this project are documented here.

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

---

## [0.7.0] - 2026-05-07

### Added
- **`sync-likes` command** (menu option `[10]`) — mirrors YT Music liked songs to the user's Spotify "Liked Songs" library. Uses the same 3-pass matcher; reuses Spotify URIs already discovered during playlist sync to avoid duplicate API calls. Maintains a separate snapshot under `data/snapshots/likes/` so likes-diff state never collides with playlist-diff state. Supports `--dry-run`.
- **`export` command** (menu option `[11]`) — exports the enriched CSV as portable JSON (`data/playlist_enriched.json` by default; `--output PATH` to override). One JSON object per track, all 49 enrichment fields included. Easier to feed into `jq`, dashboards, or other tools than the CSV.
- **Per-run debug stats JSON** — every `sync` run now writes `data/debug/run_<timestamp>.json` (and a rolling `latest.json`) with totals, diff deltas, match rate, method distribution, average confidence, skip-reason histogram, and a few unmatched/skipped examples. Useful for graphing sync quality over time.
- **`skip_reason` column** + `data/skipped.csv` — YT Music tracks without album metadata (typically YouTube uploads, fan edits, mixes) are now filtered out before search. Saves ~1.5 s × N skipped tracks of Spotify API time, removes noise from `unmatched.csv`, and keeps a per-track audit trail of why each was skipped.
- New Spotify endpoints: `get_saved_tracks`, `add_saved_tracks`, `remove_saved_tracks` (all batched at 50 IDs/call, with rate-limit retry on `add`).
- New YT Music endpoints: `fetch_liked_tracks`, `save_likes_snapshot`, `load_latest_likes_snapshot`.
- New CSV writer: `write_skipped_csv`.
- New config paths: `LIKES_SNAPSHOTS_DIR`, `DEBUG_DIR`, `SKIPPED_CSV`, `LIKES_ENRICHED_CSV`, `LIKES_UNMATCHED_CSV`.

### Changed
- **Spotify OAuth scope expanded** to include `user-library-read` and `user-library-modify` for likes sync. **One-time re-authorization** required on first launch after upgrade — spotipy refreshes the cached token automatically.
- Enriched CSV grew to **49 columns** (was 48): added `skip_reason`.
- Sync flow now writes `skipped.csv` alongside `unmatched.csv` for the new no-album skip filter.
- Interactive menu adds `[10] Sync Liked Songs`, `[11] Export to JSON`, and shifts `Show status` to `[12]`.

### Notes
- The skip-no-album filter only applies to YT Music sources. Tracks imported from CSV with empty album fields are still tried unless explicitly marked.
- Likes sync reuses match results from `playlist_enriched.csv` when fingerprints overlap, so a track already matched in the playlist sync doesn't need to be re-searched for likes.

---

## [0.6.0] - 2026-05-02

### Added
- **Last.fm artist tags** (`artist_tags` column) — switches from `track.getInfo` to `artist.getInfo` to lift useful tag coverage from ~14 % to **~93 %** on niche libraries. Cached by unique artist name (≈ 4× speedup vs per-track lookups).
- **`primary_genre` column** — derived locally from the tag pool. 17 genre buckets (electronic, rock, soundtrack, classical, jazz, hip hop, metal, folk, ambient, …). No API calls.
- **`mood` column** — multi-label mood detection from tag keywords (chill, ambient, cinematic, epic, energetic, sad, dark, dreamy, …).
- **`tag_source` column** — records where each row's tags came from (`lastfm_artist`, etc.) so future sources (MusicBrainz, Discogs) can be layered cleanly.
- **`classify` command** (option `[9]` in interactive menu) — re-derives `primary_genre` and `mood` from existing tags. Pure-local, no API calls. Also runs automatically at the end of every `sync` and `lastfm` run. Use `--force` to re-classify rows that already have values.
- **Skip-flags** to stop hammering dead endpoints on every sync:
  - `lastfm_attempted` — set after `artist.getInfo` is tried, so artists with no community tags are not re-queried.
  - `lastfm_track_attempted` — set after `track.getInfo` is tried, so tracks Last.fm doesn't know about are not re-queried.
  - `spotify_metadata_attempted` — set after the `/v1/tracks` endpoint is hit (often 403 for non-extended apps).
  - `spotify_genres_attempted` — same, for `/v1/artists`.
- New helpers in `lastfm_client.py`: `get_artist_info()`, `get_artists_info_batch()` (deduplicates input).
- New enricher functions: `backfill_lastfm_artist_tags()`, `classify_track()`, `classify_all_tracks()`.

### Fixed
- Pre-existing latent `NameError` in `cli.py` — the Last.fm "skip" branch referenced an undefined `logger`. Replaced with `print()` to match the file's style.
- Removed an extraneous `f` prefix on a string with no placeholders (`F541`).
- Word-boundary tokenisation in the genre classifier — earlier substring matching produced false hits like `post-rock` → `ost` → `soundtrack`. Now tags are split on commas (whole-tag set) and on spaces/hyphens (token set), matched exactly.
- `backfill_lastfm_data` now respects `lastfm_track_attempted` so already-failed track lookups are not retried on every sync (saves ~1 min/run for libraries with unmatchable tracks).

### Changed
- Enriched CSV grew to **48 columns** (was 37): added `artist_tags`, `tag_source`, `lastfm_attempted`, `lastfm_track_attempted`, `primary_genre`, `mood`, `spotify_metadata_attempted`, `spotify_genres_attempted`.
- Sync flow now runs Last.fm artist tags + classification **before** writing the CSV, so a single `sync` populates everything.
- Interactive menu adds `[9] Classify genre + mood from tags` and shifts `Show status` to `[10]`.

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
