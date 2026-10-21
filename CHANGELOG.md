# Changelog

All notable changes to this project are documented here.

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

---

## [0.8.0] - 2026-10-19

### Added
- **Setup wizard** (`setup` command, `S` in the menu). It asks for the YT Music login, picks the source playlist from your library, takes the Spotify app keys, picks or creates the Spotify playlist, and takes the optional Last.fm key. Each value is checked live and saved to `.env` for you: no `cp .env.example .env`, no manual editing. Finished steps are skipped on later runs, and `Ctrl+C` keeps what was entered.
- **Redesigned interactive menu**. Setup status sits at the top with a "Next:" hint, and actions are grouped by task (Sync, Fix & maintain, Extras, YT Music backup, Settings). An action that needs missing setup offers to run only those wizard steps. Actions that change Spotify ask whether to run for real or as a dry run. `C` changes any single setting.
- **YT Music backup**, no Spotify needed. `export-yt` saves Liked Songs, one playlist or the whole library (`--all`) as JSON + CSV under `data/exports/`. `import-yt` adds a file, another playlist or Liked Songs to a new or owned YT Music playlist. It skips tracks already there, falls back to one-by-one adds for unavailable videos, and stops cleanly when YT Music refuses all writes. Menu options 12 / 13.
- **JSON copy of every enriched CSV** (`playlist_enriched.json`, `likes_enriched.json`), with typed values: numbers, `true`/`false`, and `null` where the CSV is empty (0.7's `export` wrote every value as a string). The `export` command / menu 9 still saves a copy wherever you choose.
- **Playlist links accepted everywhere**: full YT Music / YouTube / Spotify links, in the wizard or in `.env`, are reduced to IDs.
- **Friendlier YT Music login.** Accepts DevTools "Copy as cURL" (bash or cmd), the Chrome two-line format and raw headers. It explains in plain words why a paste failed (single line, missing `cookie` or `authorization`, selection started mid-cookie) and asks again. The new login is checked with YT Music before it replaces the old `browser.json`, and the temporary file is always removed.
- **`PLAYLIST_SYNC_HOME`** environment variable to choose the settings folder (created if missing).
- **`lastfm --retry-missing`** re-checks rows that older versions marked as attempted but that still have no Last.fm data (no `lastfm_playcount`, or no `artist_tags`). Before 0.8.0, failed requests, `+` artists and duplicate rows all got the skip-flag, so plain runs never retried them. The flag applies the Last.fm fixes below to an existing `playlist_enriched.csv` without rebuilding it, so its Spotify enrichment is kept, and rows that already have data are not touched. A retried row's flag reflects only this run's answer: found or "not found" sets it, and a failed request clears it so the next plain run retries the row. Plain `lastfm` runs print how many flagged rows still lack data and point to the flag. On a real 3,600-track library, `--retry-missing --dry-run` found 404 tracks for track data and 135 tracks (92 artists) for artist tags.
- **Offline pytest suite** (`tests/`: the wizard and menu driven by scripted keystrokes, end-to-end sync integrity with fake APIs, export/import, Last.fm encoding and error handling, backfill retry and duplicate fan-out, genre/mood classification on real-looking tag pools). Runs in CI together with a `--help` smoke test.

### Fixed
- **`.env.example` placeholder values are no longer treated as real.** `snapshot` / `diff` / `sync` with `YTMUSIC_PLAYLIST_ID=your_playlist_id_here` used to fail inside ytmusicapi with `Unable to find 'contents' ...`. They now stop with a clear message pointing to the setup wizard. The same goes for the Spotify keys, the Spotify playlist and the Last.fm key.
- **pip / PyPI installs** read `.env` and wrote `data/`, `browser.json` and the log into `site-packages`. Installed copies now use one per-user folder: `%APPDATA%\ytmusic-to-spotify-sync`, `~/Library/Application Support/...` or `~/.config/...`. Git clones keep using the repo folder. On first run, the wizard offers to copy settings an older install left in `site-packages`.
- **Sync data integrity**:
  - Matches found before a rate limit or `Ctrl+C` were saved as "matched" but never added to Spotify. They are now pushed on the next run, after checking the playlist so nothing is added twice.
  - An interrupted run kept the new snapshot, which lost pending YT removals. It also dropped previously-failed and not-yet-searched rows from the CSV.
  - A dry run left a match cache that the next real run treated as already pushed.
  - Switching the source (another YT playlist, or a CSV after a YT sync) diffed against the old snapshot and removed every old track from Spotify. Snapshots now record their source, and a different source counts as a first run. In the menu, switching the source offers to archive the old sync data.
  - Tracks searched for the first time lost the Last.fm data their row already had.
- **`pip install .`** from a checkout failed when a local `data/` folder existed (setuptools flat-layout discovery). Package discovery is now explicit.
- **Spotify token cache** moved from `.cache` (often an existing directory such as `~/.cache`) to `.spotify_token_cache` in the settings folder. 0.7.x tokens are adopted automatically, so nobody has to log in again.
- **Spotify login can be cancelled with `Ctrl+C` on Windows**: spotipy's redirect wait used to ignore it. A 403 now explains Spotify's Premium rule for developer apps.
- **Spotify 2026 API**: playlist entries named `item` instead of `track` are read correctly, so `repush` can't mistake a full playlist for an empty one.
- `sync-likes` no longer requires `SPOTIFY_PLAYLIST_ID`. After the YT Music login changes, the likes snapshot is reset, so another account's likes are never unsaved.
- The default redirect URI is `http://127.0.0.1:8888/callback` (Spotify rejects `localhost`), matching `.env.example`.
- A non-ASCII playlist title no longer crashes a run whose output is redirected (cron, Jenkins, Git Bash) or a menu prompt.
- `.env` and `browser.json` are owner-only on Linux/macOS after every write. The Spotify token cache is created owner-only (and tightened if an older spotipy left it world-readable).
- **Spotify rate limits no longer hang the sync.** spotipy's default session let urllib3 sleep out `Retry-After` on its own (up to 6 hours per retry, three times) and then report the 429 without its headers, so a long Spotify ban froze `sync` for hours and the "rate limited, progress saved" path never ran. The client now uses its own session: short waits are slept, a long ban stops the run with progress kept, and the next sync continues.
- **A failed Spotify search is no longer "no match".** Server errors and network failures used to return an empty result, so the track was recorded as unmatched and skipped forever. It now stays unsearched for the next run; five failures in a row stop the sync with progress saved. A rate limit during the final push keeps the matches and removals for the next run.
- `spotipy>=2.26.0` (older versions call endpoints Spotify removed in February 2026, so pushes and likes saves failed).
- A sync with nothing new to match crashed (`NameError`) when every track had already failed once.
- `retry-unmatched` retries the full rows (ISRC, duration and all enrichment kept) instead of rebuilding them from `unmatched.csv`; it pushes only what the playlist lacks and stops cleanly on a rate limit.
- **`sync-likes`** keeps a cumulative `likes_enriched.csv` (one row per current like), so un-liking an older song is mirrored. Unmatched likes are recorded, reuse a later playlist match for free and are searched again with the new `--retry-unmatched`. Liked Songs are fetched without the 10,000 cap, and a sudden mass drop (more than 50 and 20 %) unsaves nothing unless `--force`.
- **`import-csv`** refuses to replace existing sync data (every Spotify match) unless `--force`, which archives it first. Every sync push now skips URIs already in the playlist, so a re-import can't duplicate it.
- CSV `duration` accepts `m:ss` / `h:mm:ss`; a bad cell names the file and row instead of a traceback.
- `status` tells "SOURCE_CSV set but no file there" apart from "not configured", and `sync --from-csv` checks the file exists.
- **Most wrong Spotify matches are rejected.** The weighted score alone accepted a same-title song by another artist (`Hello` by Lionel Richie for Adele's, 45 s off), another song by the same artist, and versions 2 minutes longer. Title/artist candidates must now pass hard checks: title at least 60 % similar (version suffixes such as ` - 2015 Remaster` ignored), durations at most 15 s apart when both are known, and one artist at least 60 % similar. Several YT Music artists are compared one by one with Spotify's. An artist written in another alphabet passes only with a near-identical title and a duration within 5 s, and goes to `review.csv`; two different names in the same alphabet are rejected (a same-length cover). A match that passes only once a suffix such as ` - Remix` or `(Part 2)` is ignored also goes to `review.csv`. A sequel with a near-identical title by the same artist can still pass when the right track is not in Spotify's results.
- **`data/review.csv`** lists fuzzy matches below confidence 0.75 for a manual check. They are still pushed; the file is rebuilt on every sync.
- **Removals survive `snapshot`.** A track deleted on YT Music was never removed from Spotify when the `snapshot` command (menu 11) ran before the next sync, and its Spotify link was lost. Removals now come from the enriched CSV, so they no longer depend on the snapshot. `sync --from-csv` removals now also work for CSV exports without a `platform` column.
- **A Spotify track two YT Music entries match** is added once, and stays while either entry is still on YT Music (Spotify removes every copy of a track at once).
- **Failed pushes and removals are no longer silent.** `sync` checks that the target playlist exists and is yours before any search. It exits 1 when tracks could not be added or removed, keeps them for the next sync instead of dropping the rows, and no longer prints "Sync complete!" then. `retry-unmatched` checks the playlist the same way; when its push fails, the matches go into the match cache, the next sync adds them, and the command exits 1.
- The sync summary and `data/debug/latest.json` counted every new match twice (match rate 200 %), and `sync --limit` reported "Sync complete!" with tracks still unsearched.
- Docs: the ISRC pass only runs for CSV exports with an `isrc` column (the YT Music API has no ISRCs); the column table lists all 50 columns; README links work on pypi.org; the 0.7.1 migration note no longer claims an automatic `match_attempted` migration.
- **Last.fm enrichment accuracy**:
  - **Artists with `+` in the name now get Last.fm data.** Last.fm URL-decodes query values twice, so a correctly encoded `+` (`%2B`) arrived as a space: `Sultan + Shepard` was looked up as `Sultan   Shepard`, a ghost entry with no tags (372 listeners instead of 217,571). The client now encodes `+` once more (`%252B`) in every artist and track name. This affected every name with `+`, e.g. `Florence + The Machine`.
  - **Post-punk and hardcore no longer land in `punk`.** `post-punk` (and `post-punk revival`) moved to the `rock` bucket. A tag that is itself a bucket keyword now counts only for that bucket, so `post-punk` can't match `punk` through its words (SadSvit, Кино). Bare `hardcore` was dropped from `punk`: `hardcore punk` and `post-hardcore` stay, and `happy hardcore`, `hardstyle`, `gabber` and `speedcore` are `electronic`. A `punk` tag that ranks below a tag of an unrelated genre no longer decides the genre (Thylacine: `electronic, french, punk, ...` is `electronic`). On a real 3,600-track library, `punk` went from 91 tracks to 26.
  - **YouTube channel uploads no longer borrow a namesake's tags.** For YT Music tracks without an album, the "artist" is usually the uploading channel, and `artist.getInfo` returned whoever else has that name (`Arrakis` came back as metal, `Electronic Arts` as jazz). These tracks are no longer looked up, and their stored artist tags are ignored when classifying. The exception is an artist who also has album tracks in the library or is named in the title (`Guru Josh - Infinity 2012`).
  - **Failed Last.fm requests are retried instead of written off.** Network errors, HTTP failures, rate limits and other API errors used to set `lastfm_track_attempted` / `lastfm_attempted` as if Last.fm had said "not found". Only a real "not found" (Last.fm error 6) sets the flag now. A batch that stops early after 50 consecutive failures no longer marks the tracks it never queried.
  - **Duplicate tracks all get their play counts.** When the same artist + title appeared more than once, only one copy received the Last.fm data and the others were marked attempted with nothing (345 rows on a real 3,600-track library). One lookup now fills every copy, and duplicate pairs are queried once.
  - Existing non-zero `lastfm_playcount` / `lastfm_listeners` and non-empty `lastfm_tags` are never overwritten with `0` or empty values.

### Changed
- Menu numbers changed (see README).
- Relative `YTMUSIC_AUTH_FILE` / `SOURCE_CSV` paths resolve against the settings folder instead of the current directory.
- `.env` is re-read on every menu redraw, so hand edits show up; real environment variables still win over `.env`.
- `setup-ytmusic` exits with code 1 when no login was saved.
- `python-dotenv>=1.2.3` (earlier versions lost backslashes in values such as Windows paths when writing `.env`).
- **New mood label `acoustic`** (a character marker like `instrumental`) for tracks tagged `acoustic`, `unplugged`, `indie folk`, `chamber folk` or the whole tag `folk` (not `folk metal` / `folk punk`). Calm folk acts whose top tags carry no mood word (Daughter, Agnes Obel, Pomme, Of Monsters and Men, Low Roar) now get one. Also new: `dream pop` → `dreamy`; `peaceful`, `gentle`, `laid back`, `lullaby` → `chill`; `sadcore` → `sad`. On the real library, filled `mood` went from 702 to 1,037 tracks.
- `lastfm` command counts now match what is actually fetched (channel uploads are left out of the artist-tag count).

### Migration note
Rows enriched by earlier versions keep their old skip-flags and classifications. To apply the Last.fm fixes to an existing `playlist_enriched.csv`:
- **Genre / mood only:** run `classify --force` (no API calls). This re-buckets punk/post-punk and drops namesake-based genres for channel uploads.
- **Missing Last.fm data** (`+` artists, failed requests, duplicate rows): run `lastfm --retry-missing` (add `--dry-run` first to see the counts).

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
There is no automatic migration (an earlier version of this note said there was; corrected in 0.8.0). A `playlist_enriched.csv` written before 0.7.1 has no `match_attempted` column, so its unmatched rows load as not yet attempted: the first sync after upgrading searches them once more and flags the ones that still fail, and later syncs skip them. Already-matched tracks are not searched again. To spread that one-time search over several days, use `sync --limit N`.

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
