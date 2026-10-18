# Changelog

All notable changes to this project are documented here.

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

---

## [0.7.2] - 2026-10-18

### Fixed
- **Artists with `+` in the name now get Last.fm data.** Last.fm URL-decodes query values twice, so a correctly encoded `+` (`%2B`) arrived as a space: `Sultan + Shepard` was looked up as `Sultan   Shepard`, a ghost entry with no tags (372 listeners instead of 217,571). The client now encodes `+` once more (`%252B`) in every artist and track name. This affected every name containing `+`, e.g. `Florence + The Machine`.
- **Post-punk and hardcore no longer land in `punk`.** `post-punk` (and `post-punk revival`) moved to the `rock` bucket, and a tag that is itself a bucket keyword now counts only for that bucket, so `post-punk` can't match `punk` through its words (SadSvit, Кино). Bare `hardcore` was dropped from `punk`; `hardcore punk` and `post-hardcore` stay, and `happy hardcore`, `hardstyle`, `gabber`, `speedcore` are `electronic`. A `punk` tag that ranks below a tag of an unrelated genre no longer decides the genre (Thylacine: `electronic, french, punk, ...` is `electronic`). On a real 3,600-track library, `punk` went from 91 tracks to 26.
- **YouTube channel uploads no longer borrow a namesake's tags.** For YT Music tracks without an album (`skip_reason=no_album`, or no album at all), the "artist" is usually the uploading channel, and `artist.getInfo` returned whoever else has that name (`Arrakis` came back as metal, `Electronic Arts` as jazz). These tracks are no longer sent to `artist.getInfo`, and their stored artist tags are ignored when classifying, unless the same artist also has album tracks in the library or the title names them (`Guru Josh - Infinity 2012`).
- **Failed Last.fm requests are retried instead of written off.** Network errors, HTTP failures, rate limits and other API errors used to set `lastfm_track_attempted` / `lastfm_attempted` as if Last.fm had said "not found", so those tracks were never retried. Only a real "not found" answer (Last.fm error 6) sets the flag now. A batch that stops early after 50 consecutive failures no longer marks the tracks it never queried.
- **Duplicate tracks all get their play counts.** When the same artist + title appeared more than once in a playlist, only one copy received the Last.fm data and the others were marked attempted with nothing. On a real 3,600-track library that left 345 rows without counts. One lookup now fills every copy, and duplicate pairs are queried once.
- Existing non-zero `lastfm_playcount` / `lastfm_listeners` and non-empty `lastfm_tags` are never overwritten with `0` or empty values.

### Changed
- **New mood label `acoustic`** (a character marker like `instrumental`) for tracks tagged `acoustic`, `unplugged`, `indie folk`, `chamber folk` or the whole tag `folk` (not `folk metal` / `folk punk`). Calm folk acts whose top tags carry no mood word (Daughter, Agnes Obel, Pomme, Of Monsters and Men, Low Roar) now get one. Also new: `dream pop` → `dreamy`; `peaceful`, `gentle`, `laid back`, `lullaby` → `chill`; `sadcore` → `sad`. On the real library, filled `mood` went from 702 to 1,037 tracks.
- `lastfm` command counts now match what is actually fetched (channel uploads are left out of the artist-tag count).

### Added
- Offline `pytest` suite in `tests/` (`pyproject.toml` sets `pythonpath = ["."]`): Last.fm encoding and error handling, backfill retry and duplicate fan-out, genre/mood classification on real-looking tag pools.

### Migration note
Rows enriched by earlier versions keep their old skip-flags and classifications. To apply the fixes to an existing `playlist_enriched.csv`:
- **Genre / mood only:** run `classify --force` (no API calls). This re-buckets punk/post-punk and drops namesake-based genres for channel uploads.
- **Missing Last.fm data** (`+` artists, failed requests, duplicate rows): these rows are already flagged as attempted. If the CSV holds nothing but Last.fm enrichment (no Spotify matches yet), move it aside and re-run `lastfm`; it rebuilds from the latest snapshot.

---

## [0.7.1] - 2026-05-07

### Fixed
- **Sync no longer retries persistently-unmatchable tracks every run.** The matcher had no "I already tried this and it failed" flag, so the same ~9 niche tracks (regional uploads, weird formatting, fan edits) were re-searched on every sync forever. Each retry burned ~3 s of Spotify API time × N tracks, with zero gain.
- **Recreated-Spotify-playlist scenario.** When you delete and recreate the Spotify playlist (new ID in `.env`), `sync` previously did nothing — it only pushes *newly-matched* tracks since the last snapshot, and an empty destination playlist isn't a "new match". The new `repush` command (option `[12]` in the menu) reads every URI from the enriched CSV and pushes them all to the current `SPOTIFY_PLAYLIST_ID` in batches of 100. No Spotify search calls.

### Added
- **`Track.match_attempted`** flag — set to `True` after the matcher runs on a track, regardless of outcome. Survives in the enriched CSV.
- **`sync --retry-unmatched`** flag — opt-in one-shot retry of all previously-failed tracks. Equivalent to running the standalone `retry-unmatched` command but folded into the regular sync.
- New "previously-failed" line in the sync header showing how many tracks are being skipped because they already failed once.
- **`repush` command** — push every already-matched URI from the enriched CSV to the current Spotify playlist. **Idempotent** by default: fetches the current playlist contents first and only adds URIs that aren't already there, so running it twice in a row is safe (the second run is a no-op). Pass `--replace` to wipe the playlist completely first (useful for cleaning up duplicates from earlier non-idempotent versions). Supports `--dry-run`.
- **Friendly error for `sync-likes` auth failure** — when YT Music's Liked Music endpoint returns the unauthenticated "Sign in" page (which it sometimes does even when ordinary playlists work fine), the user now gets a clear step-by-step recovery procedure instead of a raw JSON dump from `ytmusicapi`. The fix is to refresh `browser.json` via option `[1]` after navigating to the Liked Songs page in YT Music so the captured cookies have the right scope.

### Changed
- Default `sync` behaviour now skips tracks with `match_attempted=True AND no spotify_uri`. Use the existing `retry-unmatched` command (option `[7]` in menu) or the new `--retry-unmatched` flag to retry them when Spotify's catalogue grows or you've cleaned up the source metadata.
- `unmatched.csv` now reflects the **full** unmatched backlog every run (not just the freshly-failed batch), so the user always sees the complete list available for retry.
- `cmd_retry_unmatched` refreshes `match_attempted` after each retry.
- Enriched CSV grew to **50 columns** (was 49): added `match_attempted`.

### Migration note
Existing CSVs are auto-migrated: every track currently in `playlist_enriched.csv` is treated as already-attempted, so the next sync will skip the persistent failures immediately. New tracks added to the YT Music playlist after upgrade start with `match_attempted=False` and get processed normally.

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
