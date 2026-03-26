# ytmusic-to-spotify-sync

> Automatically sync your YouTube Music playlists to Spotify — with smart track matching, diff-based updates, and full metadata enrichment.

[![CI](https://github.com/davidmalko87/ytmusic-to-spotify-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/davidmalko87/ytmusic-to-spotify-sync/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.4.0-blue)](CHANGELOG.md)
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
| **Audio features enrichment** | Annotates every track with danceability, energy, valence, tempo, key, and 7 more |
| **Metadata enrichment** | Captures ISRC, explicit flag, and album release date from Spotify |
| **CSV fallback** | Works from a CSV export if you prefer not to use the live API |
| **Resume after rate limits** | Match progress is cached every 25 tracks; re-running continues where you left off |
| **Quota-friendly `--limit`** | Cap new tracks per run to stay within Spotify's daily API quota |
| **Interactive menu** | Run without arguments for a guided step-by-step experience |
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

  [1] Setup YT Music auth
  [2] Import from CSV
  [3] Snapshot YT Music playlist
  [4] Show diff (changes)
  [5] Full sync to Spotify
  [6] Sync from CSV file
  [7] Retry unmatched tracks
  [8] Show status
  [0] Exit
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
python playlist_sync.py retry-unmatched        # Retry previously failed matches
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
│   └── enricher.py            # Metadata and audio feature enrichment
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

The sync produces `data/playlist_enriched.csv` with 32 columns:

| Column | Source |
|--------|--------|
| `title`, `artist`, `album` | YT Music |
| `trackId`, `url`, `duration` | YT Music |
| `spotify_uri`, `spotify_url` | Spotify match |
| `isrc`, `explicit`, `album_release_date` | Spotify metadata |
| `danceability`, `energy`, `valence` | Spotify audio features |
| `tempo`, `key`, `mode`, `loudness` | Spotify audio features |
| `speechiness`, `acousticness` | Spotify audio features |
| `instrumentalness`, `liveness`, `time_signature` | Spotify audio features |
| `match_method`, `match_confidence` | Matching diagnostics |
| `first_synced`, `last_synced` | Sync timestamps |

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

## Known limitations

- **Spotify Developer Mode** limits search to 10 results per request and imposes a daily quota. Use `--limit N` to spread large initial syncs over multiple days.
- **YT Music-exclusive tracks** (unreleased, region-locked, user uploads) will not have Spotify matches — these are tracked in `data/unmatched.csv`.
- **ytmusicapi OAuth is broken** in v1.11.x — the tool uses browser-based authentication instead (stable, valid ~2 years).

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
