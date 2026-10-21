# Playlist Sync — Setup Guide

Sync your YT Music playlist to Spotify with metadata enrichment. The setup
wizard asks for everything and saves it for you — you never have to edit
`.env` by hand.

## Prerequisites

- Python 3.10+
- A YT Music account with a playlist
- A Spotify account with **Premium** (Spotify runs developer apps only for Premium owners;
  the YT Music export/import features work without Spotify)

## Step 1: Install

From PyPI (recommended):

```bash
pip install ytmusic-to-spotify-sync
```

Or from a clone of the repo:

```bash
git clone https://github.com/davidmalko87/ytmusic-to-spotify-sync.git
cd ytmusic-to-spotify-sync
pip install -r requirements.txt
```

### Where your settings are kept

`.env`, `browser.json` (YT Music login), the Spotify token cache, `data/` and
the log file all live in one **settings folder**:

| How you run it | Settings folder |
|---|---|
| From a git clone (`python playlist_sync.py`) | the repo folder |
| Installed from PyPI on Windows | `%APPDATA%\ytmusic-to-spotify-sync` |
| Installed from PyPI on macOS | `~/Library/Application Support/ytmusic-to-spotify-sync` |
| Installed from PyPI on Linux | `~/.config/ytmusic-to-spotify-sync` |
| Any, with `PLAYLIST_SYNC_HOME` set | that folder |

The same folder is used whichever terminal or folder you start from. The menu
and `status` both print the folder in use. Coming from 0.7.x or older installed
with pip? The wizard finds the old settings and offers to copy them over.

## Step 2: Run the setup wizard

```bash
ytmusic-sync setup            # PyPI install
python playlist_sync.py setup # git clone
```

Running the tool with no arguments starts the interactive menu, which offers
the wizard automatically on the first run (or press `S`). The wizard walks
through five steps; finished steps are skipped on later runs unless you choose
to change them, and `Ctrl+C` stops it with everything entered so far saved.

1. **YT Music login** — copy the request headers of one YT Music page from
   your browser's DevTools (instructions below). The login is checked with
   YT Music before it is saved, and stays valid for about two years.
2. **YT Music playlist** — pick the source playlist from your library by
   number (or type part of its name to search), or paste its link.
3. **Spotify app keys** — Spotify only lets tools use your account through a
   free developer "app". The wizard shows the exact steps:
   1. Open https://developer.spotify.com/dashboard and log in.
   2. Click **Create app**. Any name and description will do.
   3. Redirect URI: paste exactly `http://127.0.0.1:8888/callback` and click
      **Add** (Spotify does not accept `localhost` here).
   4. Tick **Web API**, accept the terms and click **Save**.
   5. Open the app's **Settings**, copy the **Client ID**, then click
      **View client secret** and copy that too.

   Paste both into the wizard (the secret is hidden while you type). A browser
   window then opens once so you can allow access to your Spotify account.
   The app only works while its owner has Spotify Premium.
4. **Spotify playlist** — pick one of your playlists, paste a link, or let the
   wizard create a new private playlist named after the YT Music one.
5. **Last.fm key (optional)** — adds play counts, listeners and genre tags.
   Create a free key at https://www.last.fm/api/account/create (any
   application name; copy the **API key**, not the shared secret). The key is
   checked with Last.fm before it is saved.

### Copying the YT Music headers

1. Open https://music.youtube.com and make sure you are logged in.
2. Press **F12** and switch to the **Network** tab.
3. Type `browse` into the filter box, then click **Home** or **Library**.
4. Find a request named `browse?...` (method POST, status 200).
5. Copy its request headers — any one of these works:
   - **Chrome / Edge (easiest):** right-click it > **Copy** > **Copy as cURL (bash)**
   - **Firefox:** right-click it > **Copy Value** > **Copy Request Headers**
   - **Any browser:** **Headers** tab > **Request Headers** > select every line
     from the first header to the last > `Ctrl+C`
6. Paste into the terminal and press Enter on an empty line.

If something is missing (for example the paste started in the middle of the
cookie line), the wizard says what went wrong and lets you paste again.

## Step 3: Run your first sync

Choose **1 Sync playlist now** in the menu, or:

```bash
ytmusic-sync sync --dry-run   # same work, nothing changed on Spotify
ytmusic-sync sync             # the real thing
ytmusic-sync sync --limit 200 # big playlist: match 200 tracks per run
```

(With a git clone, use `python playlist_sync.py` instead of `ytmusic-sync`.)

On the first run every track is "new" — the tool searches Spotify for each
one (about 1.5 s per track) and adds the matches to your Spotify playlist.
If Spotify pauses the app for rate limits, progress is kept and the next run
continues where it stopped.

## Step 4: Check status

```bash
ytmusic-sync status
```

Shows the settings folder, match rate, method breakdown and unmatched count.

## Daily usage

Run whenever you want to sync changes:

```bash
ytmusic-sync sync
```

Or use the interactive menu:

```bash
ytmusic-sync
```

The tool diffs against the last snapshot, so only new/removed tracks are
processed on subsequent runs.

## Changing a setting later

Press **C** in the menu to change one setting (login, either playlist, the
Spotify keys, the Last.fm key, a CSV export file or the redirect URI), or run
the wizard again with **S** / `setup`. Editing `.env` by hand still works too —
playlist links pasted there are reduced to IDs automatically.

## Backing up YT Music playlists (no Spotify needed)

Menu **12 Export from YT Music** saves Liked Songs, one playlist, or everything
in your library as JSON + CSV under `data/exports/`. Menu **13 Import into YT
Music** adds the tracks of such a file — or of another YT Music playlist —
to a new or existing playlist of yours, skipping tracks already there.

```bash
ytmusic-sync export-yt --playlist liked
ytmusic-sync export-yt --all
ytmusic-sync import-yt data/exports/<file>.json --new-name "Restored"
```

## Alternative: Sync from CSV export

If you have a CSV export of your playlist (e.g., from a browser extension),
choose **10 Use a CSV export** in the menu: it asks for the file (you can drag
it into the terminal window) and then imports or syncs it. From the CLI:

```bash
ytmusic-sync import-csv --csv my_playlist.csv  # import as the starting point
ytmusic-sync sync --from-csv my_playlist.csv   # sync it to Spotify
```

Expected CSV columns: `title, artist, album, trackId, duration, url`
(at minimum `title` and `artist` are required). An optional `isrc` column turns on the exact
ISRC match, which the YT Music API cannot provide.

## Troubleshooting

**"not logged in to YT Music yet"** or **"no YT Music playlist chosen yet"**
→ Run the setup wizard (`setup`, or `S` in the menu).

**"these settings are not filled in yet: ..."**
→ The named settings are empty or still hold the `your_..._here` template
  value. Run the setup wizard.

**Spotify page says "INVALID_CLIENT: Invalid redirect URI"**
→ Add `http://127.0.0.1:8888/callback` to the app's Redirect URIs in the
  Spotify dashboard, click Save, then press `Ctrl+C` in the terminal and try
  the step again.

**Spotify opens a browser for auth**
→ Normal on first run. Approve the permissions; the token is cached in the
  settings folder for future runs.

**Many unmatched tracks**
→ Check `data/unmatched.csv` for the list. Common reasons:
  - YT Music-exclusive tracks not on Spotify
  - Title/artist formatting differences
  - Choose **5 Retry unmatched tracks** (or run `retry-unmatched`) to retry

**Rate limiting**
→ Built-in delays handle this. If you hit 429 errors, spotipy retries
  automatically; long pauses save progress so the next run resumes.
