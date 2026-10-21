# YouTube Music to Spotify Playlist Sync

[![CI](https://github.com/davidmalko87/ytmusic-to-spotify-sync/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/davidmalko87/ytmusic-to-spotify-sync/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/ytmusic-to-spotify-sync.svg?logo=pypi&logoColor=white)](https://pypi.org/project/ytmusic-to-spotify-sync/)
[![PyPI downloads](https://img.shields.io/pypi/dm/ytmusic-to-spotify-sync.svg)](https://pypi.org/project/ytmusic-to-spotify-sync/)
[![Python](https://img.shields.io/pypi/pyversions/ytmusic-to-spotify-sync.svg?logo=python&logoColor=white)](https://pypi.org/project/ytmusic-to-spotify-sync/)
[![YouTube Music](https://img.shields.io/badge/YouTube%20Music-source-FF0000.svg?logo=youtubemusic&logoColor=white)](https://music.youtube.com)
[![Spotify](https://img.shields.io/badge/Spotify-target-1DB954.svg?logo=spotify&logoColor=white)](https://developer.spotify.com/documentation/web-api)
[![Last.fm](https://img.shields.io/badge/Last.fm-enrichment-D51007.svg?logo=lastdotfm&logoColor=white)](https://www.last.fm/api)
[![Setup](https://img.shields.io/badge/setup-guided%20wizard-success.svg)](#1-configure--setup-wizard)
[![Source](https://img.shields.io/badge/source-YT%20Music%20API%20%7C%20CSV%20export-success.svg)](#configuration-reference)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/davidmalko87/ytmusic-to-spotify-sync/blob/master/LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#requirements)
[![Last commit](https://img.shields.io/github/last-commit/davidmalko87/ytmusic-to-spotify-sync.svg)](https://github.com/davidmalko87/ytmusic-to-spotify-sync/commits/master)
[![GitHub issues](https://img.shields.io/github/issues/davidmalko87/ytmusic-to-spotify-sync.svg)](https://github.com/davidmalko87/ytmusic-to-spotify-sync/issues)

Keep a **YouTube Music playlist in sync with Spotify**: the tool reads the playlist through the
**YT Music API**, finds each track on **Spotify** with **title + artist / fuzzy matching** (plus an
exact ISRC pass for CSV exports that carry ISRCs),
pushes only what changed, and enriches every track with **Spotify and Last.fm metadata**. It also
**exports and imports YT Music playlists and Liked Songs** (JSON + CSV) without Spotify.
Fully resumable, with a **guided setup wizard**, an interactive menu and CLI mode.

---

## Why?

YouTube Music and Spotify don't talk to each other. If you curate playlists on one platform you either listen on two platforms or manually rebuild them. This tool automates the bridge: it reads your YT Music playlist, finds the matching tracks on Spotify, and keeps the two in sync. Only changes since the last run are processed — no full re-scan, no duplicates.

---

## Features

| Feature | Description |
|---|---|
| **Guided setup wizard** | Asks for the YT Music login, both playlists, the Spotify app keys and the optional Last.fm key, checks each one live and saves it to `.env` — no manual file editing |
| **Playlist pickers** | Choose the YT Music source from your library and the Spotify target from your playlists (or let it create one); pasted links work too |
| **Live YT Music API** | Fetches your playlist directly via `ytmusicapi` — no manual export required |
| **3-pass smart matching** | Normalised title + artist → fuzzy fallback, both validated by duration; an exact ISRC match runs first for CSV exports with an `isrc` column (the YT Music API has no ISRCs) |
| **Diff-based sync** | JSON snapshots track playlist state; only added/removed tracks are touched each run |
| **Spotify playlist management** | Adds new matches and removes deleted tracks automatically |
| **Liked Songs sync** | Mirrors YT Music likes into Spotify **Liked Songs** |
| **YT Music backup** | Exports a playlist, Liked Songs or the whole library to JSON + CSV, and imports a file or another playlist into a new or existing YT Music playlist — no Spotify needed |
| **JSON + CSV output** | Every enriched CSV gets a matching `.json` copy, ready for `jq` and dashboards |
| **Metadata enrichment** | Captures ISRC, explicit flag, album release date, album type, track number, and Spotify popularity |
| **Audio features enrichment** | Schema for danceability, energy, valence, tempo, key, and 7 more *(populated only if your Spotify app has audio-features access; see Known Limitations)* |
| **Last.fm artist tags** | Pulls play counts, listeners, and dense genre tags via `artist.getInfo` (typical ~93 % coverage on niche libraries) |
| **Local genre + mood classification** | Derives `primary_genre` (17 buckets) and `mood` (14 labels) from the tag pool — no API calls |
| **Run anywhere** | Interactive menu for manual use **and** subcommands for cron/Jenkins — same codebase |
| **Resumable** | Match progress is cached every 25 tracks; after a rate limit or `Ctrl+C`, re-running continues where it stopped, and matches found before the stop still reach Spotify (checked against the playlist, so not twice) |
| **Quota-friendly `--limit`** | Cap new tracks per run to stay within Spotify's daily API quota |
| **Dry-run mode** | Every Spotify-changing command can run without changing anything |
| **CSV fallback** | Works from a CSV export if you prefer not to use the live API |
| **Unmatched tracking** | Saves failed matches to `data/unmatched.csv` for manual review or later retry |

---

## Install

**Via PyPI (recommended):**

```bash
pip install ytmusic-to-spotify-sync
```

```bash
pipx install ytmusic-to-spotify-sync     # isolated install, puts ytmusic-sync on PATH
pip install -U ytmusic-to-spotify-sync   # upgrade
```

This installs the `ytmusic-sync` command.

**Or clone for development:**

```bash
git clone https://github.com/davidmalko87/ytmusic-to-spotify-sync.git
cd ytmusic-to-spotify-sync
pip install -r requirements.txt          # or: pip install -e ".[dev]" for tests + ruff
```

With a clone, run `python playlist_sync.py` wherever this README says `ytmusic-sync`.

Requirements: **Python 3.10+**.

### Where your settings live

`.env`, `browser.json` (YT Music login), the Spotify token cache, `data/` and the log file are kept
together in one **settings folder**:

| How you run it | Settings folder |
|---|---|
| Git clone — `python playlist_sync.py` | the repo folder |
| PyPI install on Windows — `ytmusic-sync` | `%APPDATA%\ytmusic-to-spotify-sync` |
| PyPI install on macOS | `~/Library/Application Support/ytmusic-to-spotify-sync` |
| PyPI install on Linux | `~/.config/ytmusic-to-spotify-sync` (or `$XDG_CONFIG_HOME/...`) |
| Either, with `PLAYLIST_SYNC_HOME` set | that folder (handy for cron/Jenkins) |

The folder is created on first use and the same one is used from any terminal. The menu header and
`ytmusic-sync status` show the folder in use. Upgrading from 0.7.x or older via pip? Those versions
kept their settings inside `site-packages`; the setup wizard finds them and offers to copy them over.

---

## Quick Start

### 1. Configure — setup wizard

```bash
ytmusic-sync setup
```

The wizard walks through five steps and saves every answer to `.env` for you:

1. **YT Music login** — paste the request headers of one YT Music page from DevTools (see below).
   The login is checked with YT Music before it is saved; it stays valid for about two years.
2. **YT Music playlist** — pick it from your library by number, search by name, or paste its link.
3. **Spotify app keys** — create an app at the
   [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) with the redirect URI
   `http://127.0.0.1:8888/callback` and the **Web API** checkbox, then paste its Client ID and
   secret (the secret is hidden while you type). A browser window opens once to grant access.
   Spotify only runs developer apps while the account that created the app has **Premium**.
4. **Spotify playlist** — pick one of yours, paste a link, or let the wizard create a private
   playlist named after the YT Music one.
5. **Last.fm key** *(optional)* — free from [last.fm/api/account/create](https://www.last.fm/api/account/create);
   checked with Last.fm before it is saved.

Finished steps are skipped on later runs unless you choose to change them; `Ctrl+C` stops the
wizard with everything entered so far kept. Running `ytmusic-sync` with no arguments offers the
wizard automatically on first start.

<details>
<summary>How to copy the YT Music request headers</summary>

1. Open [music.youtube.com](https://music.youtube.com) and make sure you are logged in
2. Press **F12** and switch to the **Network** tab
3. Type `browse` into the filter box, then click **Home** or **Library**
4. Find a request named `browse?...` (method **POST**, status **200**)
5. Copy its request headers — any one of these works:
   - **Chrome / Edge (easiest):** right-click it → **Copy** → **Copy as cURL (bash)**
   - **Firefox:** right-click it → **Copy Value** → **Copy Request Headers**
   - **Any browser:** **Headers** tab → **Request Headers** → select every line from the first
     header to the last → `Ctrl+C`
6. Paste into the terminal and press Enter on an empty line

If the paste can't be used (single line, missing `cookie` or `authorization`, selection started
mid-cookie), the wizard explains what went wrong and asks again. The login is saved in `browser.json` in the
settings folder — treat it like a password.

</details>

### 2. Run — interactive menu

```bash
ytmusic-sync              # or, from a clone: python playlist_sync.py
```

```
==================================================================
  YT Music -> Spotify Sync   v0.8.0
==================================================================
  Settings folder: C:\Users\you\ytmusic-to-spotify-sync

  SETUP
    [ OK ] YT Music login     saved
    [ OK ] YT Music playlist  Road Trip Mix
    [ OK ] Spotify app keys   Client ID ******3f9c
    [ OK ] Spotify playlist   Road Trip Mix
    [ OK ] Last.fm key        ******71d2

  SYNC
    1  Sync playlist now            YT Music -> Spotify
    2  Preview changes              what changed on YT Music
    3  Sync Liked Songs             YT likes -> Spotify Liked Songs
    4  Status                       match rate, unmatched, snapshots

  FIX & MAINTAIN
    5  Retry unmatched tracks       search Spotify again
    6  Re-fill Spotify playlist     after recreating or emptying it

  EXTRAS
    7  Last.fm tags & play counts   needs a free Last.fm key
    8  Genre & mood labels          from tags, no API calls
    9  Save a JSON copy             of the synced data, where you choose
   10  Use a CSV export             import or sync from a CSV file
   11  Save a snapshot              record the YT playlist as it is now

  YT MUSIC BACKUP (no Spotify needed)
   12  Export from YT Music         playlist / Liked Songs -> JSON + CSV
   13  Import into YT Music         file or playlist -> YT playlist

  SETTINGS
    S  Setup wizard                 walk through every step
    C  Change one setting           playlists, keys, login
    Q  Quit
```

The **SETUP** block shows what is configured and **Next** suggests the next step. Any action that
needs missing setup offers to run just those wizard steps first, and actions that change Spotify
ask whether to run for real or as a dry run. **C** changes a single setting (login, either
playlist, Spotify keys, Last.fm key, CSV export file, redirect URI).

### 3. Run — CLI (automation)

```bash
ytmusic-sync setup                         # guided setup wizard (interactive)
ytmusic-sync setup-ytmusic                 # only the YT Music login step
ytmusic-sync snapshot                      # save the current YT Music playlist state
ytmusic-sync diff                          # show changes since the last snapshot
ytmusic-sync sync                          # full sync (YT Music API -> Spotify)
ytmusic-sync sync --dry-run                # same work, nothing changed on Spotify
ytmusic-sync sync --limit 50               # match at most 50 new tracks this run
ytmusic-sync sync --retry-unmatched        # also retry tracks that previously failed
ytmusic-sync sync --from-csv               # sync from SOURCE_CSV instead of the API
ytmusic-sync sync --from-csv my_export.csv # sync from a specific CSV export
ytmusic-sync import-csv --csv my_export.csv  # bootstrap local data from a CSV export
ytmusic-sync retry-unmatched               # standalone retry of previously failed matches
ytmusic-sync lastfm                        # Last.fm enrichment on the existing data
ytmusic-sync lastfm --retry-missing        # also retry rows flagged attempted that still have no data
ytmusic-sync classify                      # re-derive primary_genre and mood from tags
ytmusic-sync classify --force              # re-classify rows that already have values
ytmusic-sync sync-likes                    # mirror YT Music likes to Spotify Liked Songs
ytmusic-sync sync-likes --dry-run          # preview likes-sync changes
ytmusic-sync export -o my_data.json        # copy of the enriched data as JSON, anywhere
ytmusic-sync export-yt --playlist liked    # YT Music Liked Songs -> data/exports/*.json + .csv
ytmusic-sync export-yt --playlist <ID-or-link>  # one YT Music playlist -> JSON + CSV
ytmusic-sync export-yt --all               # Liked Songs + every library playlist, one dated folder
ytmusic-sync import-yt backup.json         # export file -> new private YT Music playlist
ytmusic-sync import-yt backup.json --to <ID-or-link>  # ... or into one of your playlists
ytmusic-sync import-yt <ID-or-link> --new-name "Copy" --privacy unlisted  # copy a playlist
ytmusic-sync import-yt liked --dry-run     # preview copying Liked Songs into a new playlist
ytmusic-sync repush                        # re-push all matches (idempotent, only adds missing)
ytmusic-sync repush --replace              # empty the playlist first, then add (cleans duplicates)
ytmusic-sync repush --dry-run              # preview without pushing
ytmusic-sync status                        # settings folder, match rate, snapshots
```

All commands accept `--verbose` / `-v` (before the command) for debug-level log output, e.g.
`ytmusic-sync -v sync`. For cron/Jenkins, set `PLAYLIST_SYNC_HOME` so the job always uses the same
settings folder:

```bash
PLAYLIST_SYNC_HOME=/srv/ytmusic-sync ytmusic-sync sync --limit 200
```

Run `setup` once interactively first — it also caches the Spotify login, which needs a browser the
first time. After that the other commands run unattended: when something is not configured they
print what is missing and exit with code 1 instead of asking.

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

The matcher runs up to three passes in order of reliability:

1. **ISRC match** — only for tracks that carry an ISRC, i.e. from a CSV export with an `isrc`
   column (`sync --from-csv`, or `import-csv` before the first sync). The YT Music API returns no
   ISRCs, so playlists and Liked Songs read from it start at pass 2. Searches Spotify by `isrc:`;
   highest accuracy, confidence 0.95–1.0
2. **Title + Artist** — normalised search (`feat.` stripped, HTML decoded), validated by duration ± 5 s
3. **Relaxed** — title-only search with fuzzy artist matching; catches live versions and alternate releases

Unmatched tracks are written to `data/unmatched.csv` and can be retried later with `retry-unmatched`.

---

## Output: enriched CSV

The sync produces `data/playlist_enriched.csv` with **50 columns**:

| Column | Source |
|---|---|
| `title`, `artist`, `album` | YT Music |
| `platform`, `trackId`, `url`, `duration` | YT Music (`platform` is `ytmusic`, or the CSV export's own value) |
| `addedDate`, `addedBy` | Copied from a CSV export that has them; empty for the YT Music API |
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
| `lastfm_attempted`, `lastfm_track_attempted` | Skip-flags — Last.fm already answered (found or not found); failed requests stay unset and are retried next run. `lastfm --retry-missing` re-checks flagged rows that still have no data (CSVs enriched before 0.8.0) |
| `spotify_metadata_attempted`, `spotify_genres_attempted` | Skip-flags — Spotify endpoints already attempted |
| `skip_reason` | Why the matcher pre-filtered this track (e.g. `no_album` for YT Music tracks lacking album metadata) — set means no Spotify search was attempted |
| `match_attempted` | `true` once the matcher has run on this track. Tracks with `match_attempted=true AND no spotify_uri` are skipped on subsequent syncs unless `--retry-unmatched` is passed (or `retry-unmatched` is run standalone) |
| **`primary_genre`** | Single broad genre bucket (`electronic`, `rock`, `soundtrack`, …) — derived locally from tags |
| **`mood`** | Multi-label mood (`chill`, `epic`, `cinematic`, …) — derived locally from tags |
| `match_method`, `match_confidence` | Matching diagnostics |
| `first_synced`, `last_synced` | Sync timestamps |

### Genre & mood classification

`primary_genre` and `mood` are **derived locally** from the tag pool (`artist_tags` + `lastfm_tags`) — no API calls. This runs automatically at the end of every `sync` and `lastfm` command, and can be triggered standalone with `ytmusic-sync classify` (or `--force` to re-bucket rows that already have values).

The classifier uses word-boundary tokenisation, so compound tags like `deep house` map to the `electronic` bucket via `house`, and `post-rock` correctly maps to `rock` (not `soundtrack` via `ost`).

A tag that is itself a bucket keyword counts only for that bucket, so `post-punk` is `rock` and never `punk`. Bare `hardcore` decides nothing (it tags hardcore techno as often as hardcore punk), and a `punk` tag loses to a higher-ranked tag of an unrelated genre (`electronic, french, punk` is `electronic`). For YouTube uploads without an album, whose "artist" is usually the uploading channel, artist tags are ignored unless that artist also has album tracks in the library or is named in the title.

Genre buckets, in priority order: `soundtrack`, `classical`, `jazz`, `hip hop`, `metal`, `punk`, `country`, `blues`, `reggae`, `folk`, `electronic`, `ambient`, `rock`, `pop`, `rnb`, `indie`, `world`.

Mood labels: `chill`, `energetic`, `dark`, `sad`, `happy`, `epic`, `romantic`, `dreamy`, `aggressive`, `nostalgic`, `cinematic`, `ambient`, `instrumental`, `acoustic`.

### Audio features reference

| Feature | Range | Meaning |
|---|---|---|
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

### Liked songs sync

```bash
ytmusic-sync sync-likes
```

Mirrors your YT Music **Liked Songs** to Spotify's **Liked Songs** library (`/me/tracks`). Uses the same 3-pass matcher and reuses Spotify URIs already discovered during regular playlist sync, so a track present in both places is matched only once. It needs the YT Music login and the Spotify app keys, but no Spotify playlist.

Maintains its own snapshot under `data/snapshots/likes/` so likes-diff state never collides with playlist-diff state. Outputs:
- `data/likes_enriched.csv` — matched + unmatched likes with the same column schema
- `data/likes_unmatched.csv` — likes that couldn't be matched

### Recreated the Spotify playlist? Use `repush`

If you delete and recreate your Spotify playlist, `sync` won't repopulate it — `sync` only pushes *newly-matched* tracks since the last snapshot, and an empty destination playlist isn't a "new match". Point the tool at the new playlist (menu **C** → Spotify playlist), then choose **6 Re-fill Spotify playlist** or run:

```bash
ytmusic-sync repush             # idempotent — only adds URIs missing from the playlist
ytmusic-sync repush --replace   # empty the playlist first, then add (cleans dupes)
ytmusic-sync repush --dry-run   # preview the push
```

Reads every `spotify_uri` from `data/playlist_enriched.csv` and adds the missing ones in batches of 100. **No Spotify search calls** — uses the URIs already on disk, so it's fast. Idempotent by default: running it twice does not duplicate tracks.

### JSON copies

Every time the enriched CSV is written, a JSON copy with the same name is written beside it:
`data/playlist_enriched.json` and, for likes, `data/likes_enriched.json`. Each is a structured
document — `{ exported_at, track_count, tracks: [{...}, ...] }` — with every enrichment field per
track, easier to feed into `jq`, dashboards or other tools than the CSV. To put a copy somewhere
else, use menu **9** or:

```bash
ytmusic-sync export -o /tmp/my_data.json
```

### YT Music backup: export & import

Works with only the YT Music login — no Spotify setup needed. Menu **12** exports, menu **13** imports.

```bash
ytmusic-sync export-yt --playlist liked     # Liked Songs
ytmusic-sync export-yt --playlist <ID-or-link>
ytmusic-sync export-yt --all                # Liked Songs + every playlist in your library
```

Each export is a JSON file (portable, re-importable) plus a CSV with the usual playlist columns, so
it also works with **10 Use a CSV export**. Files go to `data/exports/` in the settings folder;
`--all` writes one dated `backup-<date>` folder.

```bash
ytmusic-sync import-yt data/exports/<file>.json             # new private playlist, same name
ytmusic-sync import-yt <file>.json --to <your-playlist>     # add what is missing to a playlist you own
ytmusic-sync import-yt <ID-or-link> --new-name "My copy"    # copy a playlist directly, no file
```

Imports add tracks by YouTube video ID and skip ones the target already has, so an interrupted
import can simply be run again (with `--to` and the new playlist's ID, which is printed right after
it is created). New playlists are private unless you pass `--privacy unlisted|public`. To copy a
playlist into another YT Music account, export it, switch the login (menu **C** → YT Music login),
import, then switch back.

### Skip filter & debug stats

- **Skip filter**: YT Music tracks with no album metadata (typically YouTube uploads, fan edits, mixes) are filtered out before Spotify search and written to `data/skipped.csv` with `skip_reason=no_album`. Saves API time and keeps `unmatched.csv` focused on tracks that genuinely should match but didn't.
- **Debug stats**: every `sync` run writes `data/debug/run_<timestamp>.json` (and a rolling `latest.json`) with totals, diff deltas, match rate, method distribution, average confidence, skip-reason histogram, and a few unmatched/skipped examples. Useful for graphing sync quality over time or feeding into a monitoring dashboard.

---

## Known Limitations

These are YT Music, Spotify and Last.fm platform constraints — not tool limitations:

| Data | Status | Notes |
|---|---|---|
| Spotify developer app | Premium required | Since February 2026 Spotify runs Development Mode apps only while the app owner has Premium, and new apps allow up to 5 users |
| Spotify search | Rate-limited | Development Mode apps get 10 results per request and a daily quota. Use `--limit N` to spread a large first sync over several days |
| Audio features, popularity, artist genres | Usually empty | `/v1/audio-features`, `/v1/tracks` and `/v1/artists` return 403 for standard (non-extended-access) apps since late 2024. The tool detects each 403, marks the skip-flag and stops retrying; sync still works and Last.fm fills genre/mood |
| YT Music-exclusive tracks | Not matchable | Unreleased, region-locked and user-uploaded tracks have no Spotify equivalent; they are listed in `data/unmatched.csv` |
| YT Music login | Browser headers only | ytmusicapi OAuth is broken in v1.11.x, so the tool uses browser-header auth (valid ~2 years, ends if you log out of YT Music in that browser) |
| Liked Songs endpoint | Best-effort | YT Music sometimes serves a signed-out page for the Liked Music (`LM`) endpoint even when playlists work; the tool explains how to refresh the login |
| Last.fm track tags | Sparse on niche music | Track-level tags are user-submitted and thin for game OSTs, regional uploads and remix edits; artist-level tags are preferred |
| YT Music import | Video IDs only | Only rows with a YouTube video ID can be imported (rows that came from Spotify have none); unavailable or region-locked videos are reported, not added |

---

## Project Structure

```
ytmusic-to-spotify-sync/
├── playlist_sync.py           # Entry point for git clones (same as the ytmusic-sync command)
├── .env.example               # Settings template (the wizard writes .env for you; .env is gitignored)
├── requirements.txt           # Python dependencies
├── pyproject.toml             # Packaging; console script ytmusic-sync
│
├── playlist_sync/
│   ├── __init__.py            # Package version (canonical version source)
│   ├── cli.py                 # Subcommands (argparse) and console-script entry point
│   ├── menu.py                # Interactive menu and setup wizard
│   ├── yt_transfer.py         # YT Music export (JSON + CSV) and import
│   ├── config.py              # Settings folder, .env loading/writing, validation
│   ├── models.py              # Track, MatchResult, DiffResult dataclasses
│   ├── utils.py               # Text normalisation and logging setup
│   ├── csv_manager.py         # CSV I/O (BOM-aware)
│   ├── ytmusic_client.py      # YT Music API wrapper and login-header parsing
│   ├── spotify_client.py      # Spotify API wrapper with rate-limit handling
│   ├── matcher.py             # 3-pass track matching engine
│   ├── differ.py              # Snapshot diff engine
│   ├── enricher.py            # Metadata, audio features, classification
│   └── lastfm_client.py       # Last.fm API wrapper (track + artist endpoints)
│
├── tests/                     # Offline pytest suite (no network, fakes for all APIs)
│
└── data/                      # Created at runtime in the settings folder (gitignored)
    ├── snapshots/             # JSON snapshots
    ├── playlist_enriched.csv  # Full enriched output (+ playlist_enriched.json copy)
    ├── exports/               # YT Music exports (export-yt)
    └── unmatched.csv          # Tracks that could not be matched
```

---

## Configuration Reference

The setup wizard writes these to `.env` in the settings folder; you can also edit `.env` by hand
or set them as environment variables (real environment variables win over `.env`). Values still
reading `your_..._here` from the template count as not set.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SPOTIPY_CLIENT_ID` | Yes | — | Spotify app Client ID ([dashboard](https://developer.spotify.com/dashboard)) |
| `SPOTIPY_CLIENT_SECRET` | Yes | — | Spotify app client secret |
| `SPOTIPY_REDIRECT_URI` | No | `http://127.0.0.1:8888/callback` | OAuth redirect URI; must match the app settings exactly |
| `SPOTIFY_PLAYLIST_ID` | Yes* | — | Target Spotify playlist — ID or full `open.spotify.com` link (*not needed for `sync-likes`) |
| `YTMUSIC_PLAYLIST_ID` | Yes* | — | Source YT Music playlist — ID or full `...playlist?list=<ID>` link (*not needed with `--from-csv`) |
| `YTMUSIC_AUTH_FILE` | No | `browser.json` | YT Music login file; relative paths are inside the settings folder |
| `SOURCE_CSV` | No | — | CSV export to use instead of the live API; relative paths are inside the settings folder |
| `LASTFM_API_KEY` | No | — | Free key from [last.fm/api/account/create](https://www.last.fm/api/account/create) — play counts, listeners, artist tags |
| `PLAYLIST_SYNC_HOME` | No | see [Where your settings live](#where-your-settings-live) | Settings folder override; set it in the environment, not in `.env` (created if missing) |

---

## Requirements

- Python **3.10+**
- Spotify account with **Premium** for the developer app ([dashboard](https://developer.spotify.com/dashboard)); not needed for the YT Music backup features
- YouTube Music account
- [`pandas`](https://pypi.org/project/pandas/) >= 2.0, [`spotipy`](https://pypi.org/project/spotipy/) >= 2.26,
  [`ytmusicapi`](https://pypi.org/project/ytmusicapi/) >= 1.8,
  [`python-dotenv`](https://pypi.org/project/python-dotenv/) >= 1.2.3, [`tqdm`](https://pypi.org/project/tqdm/) >= 4.66

---

## Changelog

See [CHANGELOG.md](https://github.com/davidmalko87/ytmusic-to-spotify-sync/blob/master/CHANGELOG.md) for the full version history.

## Contributing

See [CONTRIBUTING.md](https://github.com/davidmalko87/ytmusic-to-spotify-sync/blob/master/CONTRIBUTING.md) for the versioning policy and development setup. In short:
no secrets in the repo, `ruff check .` and `pytest -q` must pass, ASCII-only console output,
test against your own non-production playlists.

## License

[MIT](https://github.com/davidmalko87/ytmusic-to-spotify-sync/blob/master/LICENSE)
