# Playlist Sync — Setup Guide

Sync your YT Music playlist to Spotify with metadata enrichment.

## Prerequisites

- Python 3.10+
- A YT Music account with a playlist
- A Spotify account (free or Premium)

## Step 1: Install dependencies

```bash
pip install pandas spotipy ytmusicapi python-dotenv tqdm
```

Or from the project:
```bash
pip install -r requirements.txt
```

## Step 2: Create a Spotify app

1. Go to https://developer.spotify.com/dashboard
2. Click **Create App**
3. Set redirect URI to `http://localhost:8888/callback`
4. Note your **Client ID** and **Client Secret**

## Step 3: Create a Spotify playlist

1. Open Spotify and create a new empty playlist (this will be the sync target)
2. Copy the playlist ID from the URL: `https://open.spotify.com/playlist/PLAYLIST_ID`

## Step 4: Configure .env

```bash
cp .env.example .env
```

Edit `.env` and fill in:
```
SPOTIPY_CLIENT_ID=<your Spotify client ID>
SPOTIPY_CLIENT_SECRET=<your Spotify client secret>
SPOTIFY_PLAYLIST_ID=<your target Spotify playlist ID>
YTMUSIC_PLAYLIST_ID=<your YT Music playlist ID>
```

Your YT Music playlist ID is in the URL:
`https://music.youtube.com/playlist?list=PLd1XLAKnZQaXUPjf1khkyTNhg-KqHofWe`
→ ID is `PLd1XLAKnZQaXUPjf1khkyTNhg-KqHofWe`

## Step 5: Set up YT Music auth

```bash
python playlist_sync.py setup-ytmusic
```

This is a one-time setup (valid ~2 years). You'll need to:

1. Open https://music.youtube.com in your browser (logged in)
2. Open DevTools: **F12** (or **Ctrl+Shift+I**)
3. Go to **Network** tab
4. In the filter bar, type `/browse`
5. Click on any page/playlist in YT Music to generate requests
6. Find a **POST** request to `browse` with **status 200**
7. Click it, go to **Headers** tab
8. Scroll to **Request Headers** and copy ALL of them
9. Paste into the terminal prompt

## Step 6: Run your first sync

Preview what would happen (no changes made):
```bash
python playlist_sync.py sync --dry-run
```

Run the actual sync:
```bash
python playlist_sync.py sync
```

On first run, all tracks are "new" — the tool will search Spotify for each
track and add matches to your Spotify playlist. This takes ~5 minutes for
~3000 tracks.

## Step 7: Check status

```bash
python playlist_sync.py status
```

Shows match rate, method breakdown, and unmatched count.

## Daily usage

Just run whenever you want to sync changes:
```bash
python playlist_sync.py sync
```

Or use the interactive menu:
```bash
python playlist_sync.py
```

The tool diffs against the last snapshot, so only new/removed tracks are
processed on subsequent runs.

## Alternative: Sync from CSV export

If you have a CSV export of your playlist (e.g., from a browser extension):

```bash
# Set the CSV path in .env
SOURCE_CSV=my_playlist.csv

# Sync from it
python playlist_sync.py sync --from-csv
```

Expected CSV columns: `title, artist, album, trackId, duration, url`
(at minimum `title` and `artist` are required).

## Troubleshooting

**"YT Music auth file not found"**
→ Run `python playlist_sync.py setup-ytmusic`

**"Missing required env vars"**
→ Copy `.env.example` to `.env` and fill in credentials

**Spotify opens a browser for auth**
→ Normal on first run. Approve the permissions, it will redirect to
  `localhost:8888/callback`. The token is cached for future runs.

**Many unmatched tracks**
→ Check `data/unmatched.csv` for the list. Common reasons:
  - YT Music-exclusive tracks not on Spotify
  - Title/artist formatting differences
  - Run `python playlist_sync.py retry-unmatched` to retry

**Rate limiting**
→ Built-in delays handle this. If you hit 429 errors, spotipy retries
  automatically.
