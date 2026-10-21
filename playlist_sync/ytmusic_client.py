"""
YT Music API wrapper using ytmusicapi with browser auth.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ytmusicapi import YTMusic

from playlist_sync.config import (
    LIKES_SNAPSHOTS_DIR,
    SNAPSHOTS_DIR,
    ensure_dirs,
    make_private,
)
from playlist_sync.models import Track

logger = logging.getLogger("playlist_sync")


# Headers that ytmusicapi actually needs for browser auth
_REQUEST_HEADER_NAMES = {
    "cookie", "authorization", "origin", "user-agent",
    "x-goog-authuser", "x-goog-visitor-id", "x-origin",
    "x-youtube-client-name", "x-youtube-client-version",
    "content-type", "referer", "accept", "accept-language",
    "accept-encoding",
}

# Lines from Chrome DevTools that are NOT request headers
_SKIP_PREFIXES = {
    "request url", "request method", "status code", "remote address",
    "referrer policy", "alt-svc", "content-length", "date", "server",
    "vary", "x-content-type-options", "x-frame-options", "x-xss-protection",
    ":authority", ":method", ":path", ":scheme", "decoded:",
    "message clientvariations", "//", "repeated int32", "}",
    "priority", "sec-ch-ua", "sec-fetch", "x-browser-channel",
    "x-browser-copyright", "x-browser-validation", "x-browser-year",
    "x-client-data", "x-youtube-bootstrap",
}


def _normalize_chrome_headers(lines: list[str]) -> str:
    """Convert Chrome DevTools header format to 'key: value' format.

    Chrome shows headers as two separate lines:
        cookie
        VISITOR_INFO1_LIVE=abc; ...
    ytmusicapi expects:
        cookie: VISITOR_INFO1_LIVE=abc; ...
    """
    # First check if headers are already in "key: value" format
    colon_lines = [line for line in lines if ": " in line and not line.startswith("//")]
    if len(colon_lines) > len(lines) // 2:
        # Already in standard format, just pass through
        return "\n".join(lines)

    # Parse Chrome's two-line format: key on one line, value on the next
    headers: dict[str, str] = {}
    i = 0
    while i < len(lines):
        key = lines[i].strip().lower()

        # Skip empty lines and known non-request-header lines
        if not key or any(key.startswith(p) for p in _SKIP_PREFIXES):
            i += 1
            continue

        # Check if this is a known request header name
        if key in _REQUEST_HEADER_NAMES and i + 1 < len(lines):
            value = lines[i + 1].strip()
            # Make sure value doesn't look like another header name
            if value and value.lower() not in _REQUEST_HEADER_NAMES:
                headers[key] = value
                i += 2
                continue

        i += 1

    if not headers:
        # Fallback: return raw input and let ytmusicapi try to parse it
        return "\n".join(lines)

    # Build standard format
    result_lines = [f"{k}: {v}" for k, v in headers.items()]
    return "\n".join(result_lines)


# Header-copy instructions shown before the paste prompt (ASCII only: Windows consoles)
_HEADER_INSTRUCTIONS = """\
How to copy your YT Music login (about one minute):
  1. Open https://music.youtube.com in Chrome, Edge or Firefox and make sure
     you are logged in.
  2. Press F12 to open DevTools and switch to the "Network" tab.
  3. Type  browse  into the filter box, then click Home or Library in YT Music.
  4. Find a request named "browse?..." (method POST, status 200).
  5. Copy its request headers - any ONE of these works:
       Easiest (Chrome/Edge): right-click it > Copy > Copy as cURL (bash)
       Firefox:               right-click it > Copy Value > Copy Request Headers
       Any browser:           Headers tab > Request Headers > select ALL lines
                              (from the very first header to the last) > Ctrl+C
  6. Paste here, then press Enter on an empty line (Enter right away = go back).

The login is saved only on this computer, in {path}.
Treat that file like a password.
"""


def _parse_curl(text: str) -> dict[str, str] | None:
    """Extract headers from DevTools "Copy as cURL" output (bash or cmd flavour).

    Returns None when the text is not a cURL command. Cookies copied with
    -b/--cookie are folded into a "cookie" header, which ytmusicapi requires.
    """
    stripped = text.lstrip()
    if not stripped.lower().startswith("curl "):
        return None

    if '^"' in stripped:
        # cmd.exe flavour: ^ escapes the next character and ends continued lines
        stripped = re.sub(r"\^\r?\n", " ", stripped)
        stripped = re.sub(r"\^(.)", r"\1", stripped)
    else:
        stripped = re.sub(r"\\\r?\n", " ", stripped)

    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return None

    headers: dict[str, str] = {}
    for flag, value in itertools.pairwise(tokens):
        if flag in ("-H", "--header") and ":" in value:
            key, _, val = value.partition(":")
            headers[key.strip().lower()] = val.strip()
        elif flag in ("-b", "--cookie"):
            headers["cookie"] = value.strip()
    return headers


def headers_from_paste(lines: list[str]) -> str:
    """Turn any supported paste format into the 'key: value' text ytmusicapi expects."""
    curl_headers = _parse_curl("\n".join(lines))
    if curl_headers is not None:
        return "\n".join(f"{k}: {v}" for k, v in curl_headers.items())
    return _normalize_chrome_headers(lines)


def explain_header_problem(lines: list[str], headers_raw: str) -> str | None:
    """Return a plain-language reason why the pasted headers will not log in, or None."""
    keys = {
        line.split(":", 1)[0].strip().lower()
        for line in headers_raw.splitlines()
        if ":" in line
    }
    missing = [k for k in ("cookie", "x-goog-authuser", "authorization") if k not in keys]
    if not missing:
        return None

    first = lines[0].strip() if lines else ""
    if len(lines) == 1 and len(first) > 300:
        return (
            "Everything arrived on a single line, so the headers cannot be told apart.\n"
            "Use 'Copy as cURL (bash)' instead (right-click the request > Copy)."
        )
    if "=" in first and ";" in first and not first.lower().startswith(("cookie", "curl")):
        return (
            "The paste starts in the middle of the cookie value - the 'cookie' line\n"
            "itself was not selected. Select from the very first header line, or use\n"
            "'Copy as cURL (bash)' (right-click the request > Copy)."
        )
    if "cookie" in missing:
        return (
            "No 'cookie' header was found. Pick a request named 'browse' with method POST\n"
            "and status 200 while you are logged in, and copy ALL of its request headers\n"
            "(or use 'Copy as cURL (bash)')."
        )
    if "authorization" in missing:
        # Without it ytmusicapi mistakes the file for an OAuth login and fails later
        return (
            "The 'authorization' header (SAPISIDHASH ...) is missing - the selection was\n"
            "incomplete or the request was not a logged-in 'browse' POST. Copy ALL request\n"
            "headers again, or use 'Copy as cURL (bash)'."
        )
    return (
        "The 'x-goog-authuser' header is missing. Copy the headers of a 'browse' POST\n"
        "request on music.youtube.com while logged in (or use 'Copy as cURL (bash)')."
    )


def _read_pasted_block() -> list[str]:
    """Read pasted lines until an empty line or end of input.

    An empty line before anything was pasted means "go back".
    """
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return lines


def save_browser_auth(lines: list[str], output_path: str) -> str | None:
    """Validate pasted headers and write the auth file. Returns an error text or None."""
    import ytmusicapi
    from ytmusicapi.exceptions import YTMusicError

    headers_raw = headers_from_paste(lines)
    problem = explain_header_problem(lines, headers_raw)
    if problem:
        return problem
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        ytmusicapi.setup(filepath=output_path, headers_raw=headers_raw)
        make_private(Path(output_path))  # session cookies: owner-only on POSIX
    except YTMusicError as e:
        return f"YT Music could not read these headers: {e}"
    except OSError as e:
        return f"Could not write {output_path}: {e}"
    return None


def setup_browser_auth(output_path: str = "browser.json", display_path: str | None = None) -> bool:
    """Interactive setup for browser-based authentication.

    Keeps asking until the paste is usable or the user gives up, and explains
    what went wrong in plain words instead of surfacing a ytmusicapi traceback.
    display_path names the final login file when output_path is a temporary
    candidate. Returns True when the auth file was written.
    """
    print("=" * 60)
    print("YT Music login")
    print("=" * 60)
    print()
    print(_HEADER_INSTRUCTIONS.format(path=display_path or output_path))

    while True:
        print("Paste the headers below (press Enter on an empty line to finish):")
        lines = _read_pasted_block()
        if not lines:
            print("Nothing was pasted. YT Music login was not changed.")
            return False

        problem = save_browser_auth(lines, output_path)
        if problem is None:
            if display_path:
                print("\nHeaders accepted.")
            else:
                print(f"\nYT Music login saved to {output_path}")
            print("It stays valid for about 2 years unless you log out of YT Music in that browser.")
            return True

        print(f"\nThat did not work: {problem}\n")
        try:
            again = input("Try again? (Y/n): ").strip().lower()
        except EOFError:
            again = "n"
        if again in ("n", "no"):
            print("YT Music login was not changed.")
            return False


def get_ytmusic_client(auth_file: str) -> YTMusic:
    """Create an authenticated YTMusic client."""
    from ytmusicapi import YTMusic
    return YTMusic(auth_file)


def list_library_playlists(auth_file: str) -> list[dict[str, str]]:
    """Return the playlists in the user's YT Music library as {id, title, count}."""
    ytm = get_ytmusic_client(auth_file)
    playlists: list[dict[str, str]] = []
    for item in ytm.get_library_playlists(limit=None) or []:
        playlist_id = (item or {}).get("playlistId")
        if not playlist_id:
            continue
        playlists.append({
            "id": playlist_id,
            "title": item.get("title") or playlist_id,
            "count": str(item.get("count") or ""),
            # Only playlists you own can receive imported tracks
            "owned": "yes" if item.get("owned") else "",
        })
    return playlists


def get_playlist_info(auth_file: str, playlist_id: str) -> dict[str, str]:
    """Look up one playlist's title and size; raises if YT Music cannot open it."""
    ytm = get_ytmusic_client(auth_file)
    playlist = ytm.get_playlist(playlist_id, limit=1)
    return {
        "id": playlist_id,
        "title": playlist.get("title") or playlist_id,
        "count": str(playlist.get("trackCount") or ""),
    }


def get_playlist_video_ids(auth_file: str, playlist_id: str) -> tuple[list[str], bool]:
    """Return (videoIds already in the playlist, whether this account owns it)."""
    ytm = get_ytmusic_client(auth_file)
    playlist = ytm.get_playlist(playlist_id, limit=None)
    ids = [t["videoId"] for t in playlist.get("tracks") or [] if t and t.get("videoId")]
    return ids, bool(playlist.get("owned"))


def _write_succeeded(response: object) -> bool:
    return isinstance(response, dict) and "SUCCEEDED" in str(response.get("status", ""))


def create_yt_playlist(auth_file: str, title: str, description: str, privacy: str) -> str:
    """Create an empty YT Music playlist and return its ID; raises RuntimeError on refusal."""
    from ytmusicapi.exceptions import YTMusicError

    ytm = get_ytmusic_client(auth_file)
    # YT Music rejects '<' and '>' in titles and descriptions
    clean_title = title.replace("<", "(").replace(">", ")").strip() or "Imported playlist"
    try:
        result = ytm.create_playlist(clean_title, description.replace("<", "(").replace(">", ")"),
                                     privacy_status=privacy)
    except (YTMusicError, OSError, ValueError) as e:  # HTTP errors, network, bad replies
        raise RuntimeError(f"YT Music did not create the playlist: {e}") from e
    if isinstance(result, str) and result:
        return result
    raise RuntimeError(f"YT Music did not create the playlist: {result}")


def add_yt_playlist_items(auth_file: str, playlist_id: str, video_ids: list[str]) -> None:
    """Add videos to a YT Music playlist in one request; raises RuntimeError on refusal."""
    from ytmusicapi.exceptions import YTMusicError

    ytm = get_ytmusic_client(auth_file)
    try:
        response = ytm.add_playlist_items(playlist_id, video_ids, duplicates=False)
    except (YTMusicError, OSError, ValueError) as e:  # HTTP errors, network, bad replies
        raise RuntimeError(f"YT Music refused to add the tracks: {e}") from e
    if not _write_succeeded(response):
        raise RuntimeError(f"YT Music refused to add the tracks: {response}")


def fetch_playlist_tracks(
    auth_file: str,
    playlist_id: str,
) -> list[Track]:
    """Fetch all tracks from a YT Music playlist."""
    ytm = get_ytmusic_client(auth_file)

    logger.info("Fetching YT Music playlist: %s", playlist_id)
    playlist = ytm.get_playlist(playlist_id, limit=None)

    tracks: list[Track] = []
    for item in playlist.get("tracks", []):
        if item is None:
            continue

        artists = item.get("artists")
        if artists and isinstance(artists, list):
            artist_str = ", ".join(a.get("name", "") for a in artists if a)
        else:
            artist_str = ""

        album_info = item.get("album")
        album_str = album_info.get("name", "") if album_info else ""

        duration_sec = 0.0
        if item.get("duration_seconds"):
            try:
                duration_sec = float(item["duration_seconds"])
            except (ValueError, TypeError):
                logger.debug("Could not parse duration_seconds '%s' for '%s'",
                             item["duration_seconds"], item.get("title", ""))
        elif item.get("duration"):
            try:
                parts = str(item["duration"]).split(":")
                if len(parts) == 2:
                    duration_sec = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    duration_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except (ValueError, IndexError):
                logger.debug("Could not parse duration '%s' for track '%s'", item["duration"], item.get("title", ""))

        video_id = item.get("videoId", "")

        track = Track(
            title=item.get("title", ""),
            artist=artist_str,
            album=album_str,
            isrc=item.get("isrc", ""),
            platform="ytmusic",
            track_id=video_id,
            duration=duration_sec,
            url=f"https://music.youtube.com/watch?v={video_id}" if video_id else "",
        )
        tracks.append(track)

    logger.info("Fetched %d tracks from YT Music playlist", len(tracks))
    return tracks


def save_snapshot(tracks: list[Track], playlist_id: str = "") -> Path:
    """Save current playlist state as a JSON snapshot for future diffing.

    playlist_id records which YT Music playlist the snapshot describes, so a
    later sync of a different playlist is not diffed against it.
    """
    ensure_dirs()

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_path = SNAPSHOTS_DIR / f"snapshot_{timestamp}.json"

    data = {
        "timestamp": datetime.now().isoformat(),
        "playlist_id": playlist_id,
        "track_count": len(tracks),
        "tracks": [
            {
                "trackId": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "duration": t.duration,
            }
            for t in tracks
        ],
    }

    snapshot_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    latest_path = SNAPSHOTS_DIR / "latest.json"
    latest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Snapshot saved: %s (%d tracks)", snapshot_path.name, len(tracks))
    return snapshot_path


def latest_snapshot_playlist_id() -> str:
    """Return the playlist ID recorded in the latest snapshot ('' if unknown)."""
    latest_path = SNAPSHOTS_DIR / "latest.json"
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("playlist_id") or "") if isinstance(data, dict) else ""


def load_latest_snapshot() -> list[Track] | None:
    """Load the most recent snapshot, or None if no snapshots exist."""
    latest_path = SNAPSHOTS_DIR / "latest.json"
    if not latest_path.exists():
        logger.debug("No latest snapshot found")
        return None

    data = json.loads(latest_path.read_text(encoding="utf-8"))
    tracks = [
        Track(
            title=t["title"],
            artist=t["artist"],
            album=t.get("album", ""),
            track_id=t.get("trackId", ""),
            duration=t.get("duration", 0.0),
            platform="ytmusic",
        )
        for t in data["tracks"]
    ]

    logger.info("Loaded latest snapshot: %d tracks (from %s)", len(tracks), data.get("timestamp", "?"))
    return tracks


# ── Liked tracks (saved songs) ──────────────────────────────────────
#
# Mirrors the playlist functions but operates on YT Music's "liked songs"
# (the implicit `LM` playlist) and writes to a separate snapshots/likes/
# subdirectory so the diff state for likes never collides with the diff
# state for the user's main playlist.

class LikedSongsAuthError(RuntimeError):
    """YT Music returned an unauthenticated 'Sign in' page for the LM endpoint."""


def fetch_liked_tracks(auth_file: str) -> list[Track]:
    """Fetch all liked songs from YT Music (the implicit `LM` playlist).

    The Liked Music (`LM`) endpoint is gated more tightly than regular
    playlists by YT Music — it requires cookies that aren't always
    captured by `setup-ytmusic` even when the same browser session can
    fetch ordinary playlists fine. When that happens, the API returns
    an unauthenticated "Sign in" page (`singleColumnBrowseResultsRenderer`)
    and ytmusicapi crashes trying to parse it as a playlist.

    We catch that and raise a clear error with a recovery procedure so
    the user knows exactly what to do.
    """
    ytm = get_ytmusic_client(auth_file)

    logger.info("Fetching YT Music liked songs")
    # `get_liked_songs` returns the same shape as `get_playlist`
    try:
        playlist = ytm.get_liked_songs(limit=10000)
    except KeyError as e:
        # ytmusicapi raises a bare KeyError for the missing renderer key
        if "twoColumnBrowseResultsRenderer" in str(e):
            raise LikedSongsAuthError(
                "YT Music API returned an unauthenticated 'Sign in' page for "
                "the Liked Songs (LM) endpoint, even though your browser auth "
                "works for regular playlists.\n\n"
                "How to fix:\n"
                "  1. Open https://music.youtube.com in your browser (logged in)\n"
                "  2. Click 'Library' -> 'Songs' (your liked tracks). Make sure\n"
                "     they actually load on screen.\n"
                "  3. Press F12 -> Network tab -> filter for /browse\n"
                "  4. Reload the Liked Songs page; pick the most recent POST to\n"
                "     /browse with status 200\n"
                "  5. Copy the request headers and redo the YT Music login\n"
                "     (menu C > YT Music login, or the 'setup-ytmusic' command)\n"
                "  6. Re-run Sync Liked Songs (menu option 3)\n\n"
                "If it still fails, the LM endpoint may not be available for your\n"
                "account region/type. Regular playlist sync still works."
            ) from e
        raise

    tracks: list[Track] = []
    for item in playlist.get("tracks", []):
        if item is None:
            continue

        artists = item.get("artists")
        if artists and isinstance(artists, list):
            artist_str = ", ".join(a.get("name", "") for a in artists if a)
        else:
            artist_str = ""

        album_info = item.get("album")
        album_str = album_info.get("name", "") if album_info else ""

        duration_sec = 0.0
        if item.get("duration_seconds"):
            try:
                duration_sec = float(item["duration_seconds"])
            except (ValueError, TypeError):
                pass
        elif item.get("duration"):
            try:
                parts = str(item["duration"]).split(":")
                if len(parts) == 2:
                    duration_sec = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    duration_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except (ValueError, IndexError):
                pass

        video_id = item.get("videoId", "")

        tracks.append(Track(
            title=item.get("title", ""),
            artist=artist_str,
            album=album_str,
            isrc=item.get("isrc", ""),
            platform="ytmusic",
            track_id=video_id,
            duration=duration_sec,
            url=f"https://music.youtube.com/watch?v={video_id}" if video_id else "",
        ))

    logger.info("Fetched %d liked tracks from YT Music", len(tracks))
    return tracks


def save_likes_snapshot(tracks: list[Track]) -> Path:
    """Save current liked-songs state for future diffing."""
    ensure_dirs()

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_path = LIKES_SNAPSHOTS_DIR / f"likes_{timestamp}.json"

    data = {
        "timestamp": datetime.now().isoformat(),
        "track_count": len(tracks),
        "tracks": [
            {
                "trackId": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "duration": t.duration,
            }
            for t in tracks
        ],
    }

    snapshot_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    latest_path = LIKES_SNAPSHOTS_DIR / "latest.json"
    latest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Likes snapshot saved: %s (%d tracks)", snapshot_path.name, len(tracks))
    return snapshot_path


def load_latest_likes_snapshot() -> list[Track] | None:
    """Load the most recent likes snapshot, or None if no snapshots exist."""
    latest_path = LIKES_SNAPSHOTS_DIR / "latest.json"
    if not latest_path.exists():
        return None

    data = json.loads(latest_path.read_text(encoding="utf-8"))
    tracks = [
        Track(
            title=t["title"],
            artist=t["artist"],
            album=t.get("album", ""),
            track_id=t.get("trackId", ""),
            duration=t.get("duration", 0.0),
            platform="ytmusic",
        )
        for t in data["tracks"]
    ]

    logger.info("Loaded latest likes snapshot: %d tracks", len(tracks))
    return tracks
