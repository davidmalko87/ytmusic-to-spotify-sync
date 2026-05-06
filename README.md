# ytmusic-to-spotify-sync

> Automatically sync your YouTube Music playlists to Spotify — with smart track matching, diff-based updates, and full metadata enrichment.

[![CI](https://github.com/davidmalko87/ytmusic-to-spotify-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/davidmalko87/ytmusic-to-spotify-sync/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.7.1-blue)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#requirements)
[![Last Commit](https://img.shields.io/github/last-commit/davidmalko87/ytmusic-to-spotify-sync)](https://github.com/davidmalko87/ytmusic-to-spotify-sync/commits/master)
[![Open Issues](https://img.shields.io/github/issues/davidmalko87/ytmusic-to-spotify-sync)](https://github.com/davidmalko87/ytmusic-to-spotify-sync/issues)

---

## Why?

YouTube Music and Spotify don't talk to each other. If you curate playlists on one platform you either listen on two platforms or manually rebuild them. This tool automates the bridge: it reads your YT Music playlist, finds the matching tracks on Spotify, and keeps the two in sync. Only changes since the last run are processed — no full re-scan, no duplicates.

---

## Features

| Feature | Description |
|---------|-------------|
| **Live YT Music API** | Fetches your playlist directly via `ytmusicapi` — no manual export required |
| **3-pass smart matching** | ISRC exact match → normalised title + artist → fuzzy fallback with duration validation |
| **Diff-based sync** | JSON snapshots track playlist state; only added/removed tracks are touched each run |
| **Spotify playlist management** | Adds new matches and removes deleted tracks automatically |
| **Audio features enrichment** | Schema for danceability, energy, valence, tempo, key, and 7 more *(populated only if your Spotify app has audio-features access; see Known limitations)* |
| **Metadata enrichment** | Captures ISRC, explicit flag, album release date, album type, track number, and Spotify popularity |
| **Last.fm artist tags** | Pulls play counts, listeners, and dense genre tags via `artist.getInfo` (typical ~93 % coverage on niche libraries) |
| **Local genre + mood classification** | Derives `primary_genre` (17 buckets) and `mood` (13 labels) from the tag pool — no API calls |
| **CSV fallback** | Works from a CSV export if you prefer not to use the live API |
| **Resume after rate limits** | Match progress is cached every 25 tracks; re-running continues where you left off |
| **Quota-friendly `--limit`** | Cap new tracks per run to stay within Spotify's daily API quota |
| **Interactive menu** | Run without arguments for a guided, looping step-by-step experience |
| **Dry-run mode** | Preview every change before it is applied |
| **Unmatched tracking** | Saves failed matches to `data/unmatched.csv` for manual review or later retry |

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/davidmalko87/ytmusic-to-spotify-sync.git
cd ytmusic-to-spotify-sync
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

| Variable | Where to get it |
|----------|----------------|
| `SPOTIPY_CLIENT_ID` | [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) → Create App |
| `SPOTIPY_CLIENT_SECRET` | Same app page, under "Settings" |
| `SPOTIPY_REDIRECT_URI` | Set to `http://127.0.0.1:8888/callback` and add it in the Spotify app settings |
| `SPOTIFY_PLAYLIST_ID` | Create a playlist on Spotify; the ID is the last segment of its URL |
| `YTMUSIC_PLAYLIST_ID` | From your YT Music playlist URL: `...playlist?list=<ID>` |

### 3. Authenticate YT Music

```bash
python playlist_sync.py setup-ytmusic
```

One-time setup — paste request headers from browser DevTools. Auth is valid for ~2 years.

<details>
<summary>How to get the request headers</summary>

1. Open [music.youtube.com](https://music.youtube.com) in your browser (logged in)
2. Press **F12** to open DevTools
3. Go to the **Network** tab and type `/browse` in the filter bar
4. Click on any playlist or page in YT Music to trigger a request
5. Find a **POST** request to `browse` with **status 200**
6. Click it → **Headers** tab → **Request Headers**
7. Copy all the request headers and paste them into the terminal when prompted

Both the Chrome two-line format and the standard `key: value` format are accepted.

</details>

### 4. Run your first sync

```bash
# Preview what would happen (no changes made)
python playlist_sync.py sync --dry-run

# Run the actual sync
python playlist_sync.py sync
```

---

## Configuration reference

All options are set via environment variables (`.env` file or shell environment).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SPOTIPY_CLIENT_ID` | Yes | — | Spotify app client ID |
| `SPOTIPY_CLIENT_SECRET` | Yes | — | Spotify app client secret |
| `SPOTIPY_REDIRECT_URI` | No | `http://127.0.0.1:8888/callback` | OAuth redirect URI |
| `SPOTIFY_PLAYLIST_ID` | Yes | — | ID of the target Spotify playlist |
| `YTMUSIC_PLAYLIST_ID` | Yes* | — | ID of the source YT Music playlist (*not needed with `--from-csv`) |
| `YTMUSIC_AUTH_FILE` | No | `browser.json` | Path to the YT Music auth JSON file |
| `SOURCE_CSV` | No | — | Path to a CSV export to use instead of the live API |
| `LASTFM_API_KEY` | No | — | Free key from [last.fm/api/account/create](https://www.last.fm/api/account/create) — enables play-count, listener, and artist-tag enrichment |

---

## Usage

### Interactive mode

```bash
python playlist_sync.py
```

```
==================================================
  Playlist Sync: YT Music -> Spotify
==================================================

  [1]  Setup YT Music auth
  [2]  Import from CSV
  [3]  Snapshot YT Music playlist
  [4]  Show diff (changes)
  [5]  Full sync to Spotify
  [6]  Sync from CSV file
  [7]  Retry unmatched tracks
  [8]  Enrich with Last.fm
  [9]  Classify genre + mood from tags
  [10] Sync Liked Songs (YTM <-> Spotify)
  [11] Export enriched data to JSON
  [12] Re-push to recreated Spotify playlist
  [13] Show status
  [0]  Exit
```

### Command-line mode

```bash
python playlist_sync.py setup-ytmusic          # One-time browser auth setup
python playlist_sync.py snapshot               # Save current playlist state
python playlist_sync.py diff                   # Show changes since last snapshot
python playlist_sync.py sync                   # Full sync (YT Music API → Spotify)
python playlist_sync.py sync --from-csv        # Sync from CSV export instead
python playlist_sync.py sync --dry-run         # Preview without making changes
python playlist_sync.py sync --limit 50        # Match at most 50 new tracks this run
python playlist_sync.py sync --retry-unmatched # Also retry tracks that previously failed
python playlist_sync.py retry-unmatched        # Standalone retry of previously failed matches
python playlist_sync.py lastfm                 # Re-run Last.fm enrichment on the existing CSV
python playlist_sync.py classify               # Re-derive primary_genre and mood from tags
python playlist_sync.py classify --force       # Re-classify even rows that already have values
python playlist_sync.py sync-likes             # Mirror YT Music liked songs to Spotify Liked Songs
python playlist_sync.py sync-likes --dry-run   # Preview likes-sync changes
python playlist_sync.py export                 # Export enriched CSV as JSON (default: data/playlist_enriched.json)
python playlist_sync.py export -o my_data.json # Custom output path
python playlist_sync.py repush                 # Re-push all matched URIs to the current Spotify playlist
python playlist_sync.py repush --dry-run       # Preview without pushing
python playlist_sync.py status                 # Show sync statistics
```

All commands accept `--verbose` / `-v` for debug-level log output.

---

## How it works

```
YT Music Playlist ──(API or CSV)──> Current State
                                        │
                                    Diff Engine  ←── Previous Snapshot
                                    ╱         ╲
                            Added Tracks   Removed Tracks
                                │               │
                            Matcher         Spotify Remove
                            ╱       ╲
                    Matched     Unmatched
                        │           │
                Spotify Add    unmatched.csv
                        │
                    Enricher  (audio features + metadata)
                        │
              playlist_enriched.csv
```

### Track matching strategy

The matcher runs three passes in order of reliability:

1. **ISRC match** — searches Spotify by `isrc:` query; highest accuracy, ~95 % confidence
2. **Title + Artist** — normalised search (`feat.` stripped, HTML decoded), validated by duration ± 5 s
3. **Relaxed** — title-only search with fuzzy artist matching; catches live versions and alternate releases

Unmatched tracks are written to `data/unmatched.csv` and can be retried later with `retry-unmatched`.

---

## Project structure

```
ytmusic-to-spotify-sync/
├── playlist_sync.py           # Entry point
├── playlist_sync/
│   ├── __init__.py            # Package version (canonical version source)
│   ├── cli.py                 # Commands and interactive menu
│   ├── config.py              # Environment variables and paths
│   ├── models.py              # Track, MatchResult, DiffResult dataclasses
│   ├── utils.py               # Text normalisation and logging setup
│   ├── csv_manager.py         # CSV I/O (BOM-aware)
│   ├── ytmusic_client.py      # YT Music API wrapper
│   ├── spotify_client.py      # Spotify API wrapper with rate-limit handling
│   ├── matcher.py             # 3-pass track matching engine
│   ├── differ.py              # Snapshot diff engine
│   ├── enricher.py            # Metadata, audio features, classification
│   └── lastfm_client.py       # Last.fm API wrapper (track + artist endpoints)
├── data/                      # Created at runtime
│   ├── snapshots/             # JSON snapshots (latest.json + timestamped)
│   ├── playlist_enriched.csv  # Full enriched output
│   └── unmatched.csv          # Tracks that could not be matched
├── .env.example               # Credential template
├── requirements.txt
├── CHANGELOG.md
├── CONTRIBUTING.md
└── SETUP.md                   # Detailed step-by-step setup guide
```

---

## Output: enriched CSV

The sync produces `data/playlist_enriched.csv` with **50 columns**:

| Column | Source |
|--------|--------|
| `title`, `artist`, `album` | YT Music |
| `trackId`, `url`, `duration` | YT Music |
| `spotify_uri`, `spotify_url`, `spotify_duration_ms` | Spotify match |
| `isrc`, `isrc_enriched`, `explicit`, `album_release_date` | Spotify metadata |
| `popularity` | Spotify track popularity (0–100) — only with extended-access apps |
| `artist_genres` | Primary artist genre tags from Spotify `/artists` — only with extended-access apps |
| `album_type` | Album type (`album` / `single` / `compilation`) |
| `track_number` | Track position within the album |
| `danceability`, `energy`, `valence` | Spotify audio features — only with extended-access apps |
| `tempo`, `key`, `mode`, `loudness` | Spotify audio features |
| `speechiness`, `acousticness` | Spotify audio features |
| `instrumentalness`, `liveness`, `time_signature` | Spotify audio features |
| `audio_features_fetched` | Skip-flag — audio features endpoint already attempted |
| `lastfm_playcount`, `lastfm_listeners`, `lastfm_tags` | Last.fm `track.getInfo` |
| **`artist_tags`** | Last.fm `artist.getInfo` — much denser than track tags |
| `tag_source` | Which source filled `artist_tags` (`lastfm_artist`, …) |
| `lastfm_attempted`, `lastfm_track_attempted` | Skip-flags — Last.fm endpoints already attempted |
| `spotify_metadata_attempted`, `spotify_genres_attempted` | Skip-flags — Spotify endpoints already attempted |
| `skip_reason` | Why the matcher pre-filtered this track (e.g. `no_album` for YT Music tracks lacking album metadata) — set means no Spotify search was attempted |
| `match_attempted` | `true` once the matcher has run on this track. Tracks with `match_attempted=true AND no spotify_uri` are skipped on subsequent syncs unless `--retry-unmatched` is passed (or `retry-unmatched` is run standalone) |
| **`primary_genre`** | Single broad genre bucket (`electronic`, `rock`, `soundtrack`, …) — derived locally from tags |
| **`mood`** | Multi-label mood (`chill`, `epic`, `cinematic`, …) — derived locally from tags |
| `match_method`, `match_confidence` | Matching diagnostics |
| `first_synced`, `last_synced` | Sync timestamps |

### Genre & mood classification

`primary_genre` and `mood` are **derived locally** from the tag pool (`artist_tags` + `lastfm_tags`) — no API calls. This runs automatically at the end of every `sync` and `lastfm` command, and can be triggered standalone with `python playlist_sync.py classify` (or `--force` to re-bucket rows that already have values).

The classifier uses word-boundary tokenisation, so compound tags like `deep house` map to the `electronic` bucket via `house`, and `post-rock` correctly maps to `rock` (not `soundtrack` via `ost`).

Genre buckets, in priority order: `soundtrack`, `classical`, `jazz`, `hip hop`, `metal`, `punk`, `country`, `blues`, `reggae`, `folk`, `electronic`, `ambient`, `rock`, `pop`, `rnb`, `indie`, `world`.

Mood labels: `chill`, `energetic`, `dark`, `sad`, `happy`, `epic`, `romantic`, `dreamy`, `aggressive`, `nostalgic`, `cinematic`, `ambient`, `instrumental`.

### Audio features reference

| Feature | Range | Meaning |
|---------|-------|---------|
| `danceability` | 0.0 – 1.0 | Suitability for dancing (tempo, rhythm, beat strength) |
| `energy` | 0.0 – 1.0 | Intensity and activity (loud, fast, noisy = high) |
| `valence` | 0.0 – 1.0 | Musical positiveness (happy = high, sad/angry = low) |
| `tempo` | BPM | Estimated beats per minute |
| `speechiness` | 0.0 – 1.0 | Presence of spoken words |
| `acousticness` | 0.0 – 1.0 | Confidence the track is acoustic |
| `instrumentalness` | 0.0 – 1.0 | Likelihood of no vocal content |
| `liveness` | 0.0 – 1.0 | Presence of a live audience |
| `loudness` | dB | Overall loudness (typically −60 to 0) |
| `key` | 0 – 11 | Pitch class (0 = C, 1 = C♯, …, 11 = B) |
| `mode` | 0 or 1 | Modality (0 = minor, 1 = major) |
| `time_signature` | int | Estimated beats per bar |

---

## Liked songs sync

```bash
python playlist_sync.py sync-likes
```

Mirrors your YT Music **Liked Songs** to Spotify's **Liked Songs** library (`/me/tracks`). Uses the same 3-pass matcher and reuses Spotify URIs already discovered during regular playlist sync, so a track present in both places is matched only once.

Maintains its own snapshot under `data/snapshots/likes/` so likes-diff state never collides with playlist-diff state. Outputs:
- `data/likes_enriched.csv` — matched + unmatched likes with the same 49-column schema
- `data/likes_unmatched.csv` — likes that couldn't be matched

**One-time re-authorization required** on first launch after upgrading to 0.7.0 — the new scope (`user-library-modify`) needs your consent. spotipy refreshes the cached token automatically.

## Recreated the Spotify playlist? Use `repush`

If you delete and recreate your Spotify playlist (new ID in `.env`), `sync` won't repopulate it — `sync` only pushes *newly-matched* tracks since the last snapshot, and an empty destination playlist isn't a "new match". Run:

```bash
python playlist_sync.py repush             # push every matched URI to the current SPOTIFY_PLAYLIST_ID
python playlist_sync.py repush --dry-run   # preview the push
```

Reads every `spotify_uri` from `data/playlist_enriched.csv` and adds them all in batches of 100. **No Spotify search calls** — uses the URIs already on disk, so it's fast.

## JSON export

```bash
python playlist_sync.py export                       # writes data/playlist_enriched.json
python playlist_sync.py export -o /tmp/my_data.json  # custom path
```

Produces a structured JSON document — `{ exported_at, track_count, tracks: [{...}, ...] }` — with all 49 enrichment fields per track. Easier to feed into `jq`, dashboards, or other programmatic tools than the CSV.

## Skip filter & debug stats

- **Skip filter**: YT Music tracks with no album metadata (typically YouTube uploads, fan edits, mixes) are filtered out before Spotify search and written to `data/skipped.csv` with `skip_reason=no_album`. Saves API time and keeps `unmatched.csv` focused on tracks that genuinely should match but didn't.
- **Debug stats**: every `sync` run writes `data/debug/run_<timestamp>.json` (and a rolling `latest.json`) with totals, diff deltas, match rate, method distribution, average confidence, skip-reason histogram, and a few unmatched/skipped examples. Useful for graphing sync quality over time or feeding into a monitoring dashboard.

---

## Known limitations

- **Spotify Developer Mode** limits search to 10 results per request and imposes a daily quota. Use `--limit N` to spread large initial syncs over multiple days.
- **`/v1/audio-features`, `/v1/tracks`, `/v1/artists` blocked** — Spotify returns 403 on these endpoints for most standard (non-extended-access) app types since late 2024. Affected columns (`danceability`, `energy`, `valence`, `tempo`, `popularity`, `artist_genres`, …) stay empty unless your app passes Spotify's Extended Quota Mode review. The tool detects each 403, marks the relevant skip-flag, and stops retrying. **Sync still works** — Last.fm picks up the slack for genre/mood data.
- **YT Music-exclusive tracks** (unreleased, region-locked, user uploads) will not have Spotify matches — these are tracked in `data/unmatched.csv`.
- **ytmusicapi OAuth is broken** in v1.11.x — the tool uses browser-based authentication instead (stable, valid ~2 years).
- **Last.fm tag coverage on niche music** — track-level tags (`lastfm_tags`) are user-submitted and sparse for game OSTs, regional uploads, and remix edits. Artist-level tags (`artist_tags`) are far denser; the tool prefers them and falls back to track-level only for play-count/listener data.

---

## Requirements

- Python 3.10+
- [Spotify Developer account](https://developer.spotify.com/dashboard) (free)
- YouTube Music account

```
pandas>=2.0.0
spotipy>=2.24.0
ytmusicapi>=1.8.0
python-dotenv>=1.0.0
tqdm>=4.66.0
```

---

## Changelog & Contributing

- [CHANGELOG.md](CHANGELOG.md) — full version history
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup, semver policy, two-file update rule

---

## License

[MIT](LICENSE)
