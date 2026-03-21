# ytmusic-to-spotify-sync

> Automatically sync your YouTube Music playlists to Spotify — with smart track matching, diff-based updates, and metadata enrichment.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A command-line tool that reads your YT Music playlist (via API or CSV export), finds matching tracks on Spotify, and keeps your Spotify playlist in sync. Only processes changes since the last run — no duplicates, no re-scanning your entire library every time.

## Features

- **Live YT Music API** — fetches your playlist directly, no manual export needed
- **Smart 3-pass matching** — ISRC → title+artist → fuzzy search with duration validation
- **Diff-based sync** — only processes added/removed tracks since the last snapshot
- **Spotify playlist sync** — adds matched tracks, removes deleted ones automatically
- **Metadata enrichment** — captures ISRC, release date, explicit flag for every track
- **CSV fallback** — works with CSV exports if you prefer not to use the YT Music API
- **Interactive menu** — run without arguments for a guided experience
- **Dry-run mode** — preview all changes before committing
- **Unmatched tracking** — saves failed matches for manual review or retry

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up credentials

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

| Variable | Where to get it |
|----------|----------------|
| `SPOTIPY_CLIENT_ID` | [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) → Create App |
| `SPOTIPY_CLIENT_SECRET` | Same app page |
| `SPOTIFY_PLAYLIST_ID` | Create a playlist on Spotify, copy ID from URL |
| `YTMUSIC_PLAYLIST_ID` | From your YT Music playlist URL: `...playlist?list=YOUR_ID` |

### 3. Authenticate YT Music

```bash
python playlist_sync.py setup-ytmusic
```

One-time setup — paste request headers from browser DevTools. Valid for ~2 years.

<details>
<summary>Step-by-step: how to get the headers</summary>

1. Open https://music.youtube.com in your browser (logged in)
2. Press **F12** to open DevTools
3. Go to the **Network** tab
4. Type `/browse` in the filter bar
5. Click on any playlist or page in YT Music
6. Find a **POST** request to `browse` with **status 200**
7. Click it → go to **Headers** tab → **Request Headers**
8. Copy all the headers
9. Paste into the terminal when prompted

</details>

### 4. Run your first sync

```bash
# Preview what would happen (safe, no changes)
python playlist_sync.py sync --dry-run

# Run the actual sync
python playlist_sync.py sync
```

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
python playlist_sync.py setup-ytmusic       # One-time browser auth setup
python playlist_sync.py snapshot            # Save current playlist state
python playlist_sync.py diff                # Show changes since last snapshot
python playlist_sync.py sync                # Full sync (YT Music API -> Spotify)
python playlist_sync.py sync --from-csv     # Sync from CSV export instead
python playlist_sync.py sync --dry-run      # Preview without making changes
python playlist_sync.py retry-unmatched     # Retry previously failed matches
python playlist_sync.py status              # Show sync statistics
```

All commands support `--verbose` (`-v`) for debug output.

## How It Works

```
YT Music Playlist ──(API or CSV)──> Current State
                                        │
                                    Diff Engine
                                    ╱         ╲
                            Added Tracks   Removed Tracks
                                │               │
                            Matcher         Spotify Remove
                            ╱       ╲
                    Matched     Unmatched
                        │           │
                Spotify Add    unmatched.csv
                        │
                    Enricher
                        │
              playlist_enriched.csv
```

### Track matching strategy

The matcher runs three passes (in order of reliability):

1. **ISRC match** — if the track has an ISRC code, search Spotify by `isrc:` query (most accurate)
2. **Title + Artist** — normalized search (`feat.` stripped, HTML decoded), validated by duration (±5 sec tolerance)
3. **Relaxed search** — title-only search with fuzzy artist/album matching

Unmatched tracks are saved to `data/unmatched.csv` and can be retried later.

## Project Structure

```
├── playlist_sync.py           # Entry point
├── playlist_sync/
│   ├── cli.py                 # Commands + interactive menu
│   ├── config.py              # Environment and paths
│   ├── models.py              # Track, MatchResult, DiffResult
│   ├── utils.py               # Text normalization, logging
│   ├── csv_manager.py         # CSV I/O (BOM-aware)
│   ├── ytmusic_client.py      # YT Music API wrapper
│   ├── spotify_client.py      # Spotify API wrapper
│   ├── matcher.py             # 3-pass track matching
│   ├── differ.py              # Snapshot diffing
│   └── enricher.py            # Metadata enrichment
├── data/
│   ├── snapshots/             # JSON snapshots for diffing
│   ├── playlist_enriched.csv  # Output with Spotify metadata
│   └── unmatched.csv          # Tracks that couldn't be matched
├── .env.example               # Credential template
├── requirements.txt
└── SETUP.md                   # Detailed setup guide
```

## Output: Enriched CSV

The sync produces `data/playlist_enriched.csv` with these columns:

| Column | Source |
|--------|--------|
| `title`, `artist`, `album` | YT Music |
| `trackId`, `url`, `duration` | YT Music |
| `spotify_uri`, `spotify_url` | Spotify match |
| `isrc`, `explicit`, `album_release_date` | Spotify metadata |
| `match_method`, `match_confidence` | Matching info |
| `first_synced`, `last_synced` | Sync timestamps |

## Known Limitations

- **Spotify audio features API is deprecated** (Feb 2026) — danceability, energy, tempo etc. are no longer available via API. The tool enriches with available metadata (ISRC, release date, explicit flag).
- **Spotify Dev Mode** limits search to 10 results per request. The tool works within this limit.
- **YT Music-exclusive tracks** (unreleased, region-locked, user uploads) won't have Spotify matches — these are tracked in `unmatched.csv`.
- **ytmusicapi OAuth is broken** in v1.11.x — the tool uses browser authentication instead (stable, valid ~2 years).

## Requirements

- Python 3.10+
- [Spotify Developer account](https://developer.spotify.com/dashboard) (free)
- YouTube Music account

### Python packages

```
pandas >= 2.0.0
spotipy >= 2.24.0
ytmusicapi >= 1.8.0
python-dotenv >= 1.0.0
tqdm >= 4.66.0
```

## Contributing

Contributions welcome! Please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE)
