"""
YT Music API wrapper using ytmusicapi with browser auth.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from playlist_sync.config import SNAPSHOTS_DIR, ensure_dirs
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
    colon_lines = [l for l in lines if ": " in l and not l.startswith("//")]
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


def setup_browser_auth(output_path: str = "browser.json") -> None:
    """Interactive setup for browser-based authentication."""
    from ytmusicapi import YTMusic

    print("=" * 60)
    print("YT Music Browser Authentication Setup")
    print("=" * 60)
    print()
    print("Steps:")
    print("1. Open https://music.youtube.com in your browser (logged in)")
    print("2. Open DevTools (F12 or Ctrl+Shift+I)")
    print("3. Go to Network tab, filter by '/browse'")
    print("4. Click on any page in YT Music to trigger requests")
    print("5. Click a POST request with status 200")
    print("6. Go to Headers tab, scroll to 'Request Headers'")
    print("7. Copy ONLY the Request Headers section (not General/Response)")
    print("8. Paste below, then press Enter on an empty line")
    print()
    print("Note: Both Chrome two-line format and 'key: value' format work.")
    print()

    lines: list[str] = []
    print("Paste headers (empty line to finish):")
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)

    if not lines:
        print("No headers provided. Aborting.")
        return

    headers_raw = _normalize_chrome_headers(lines)
    import ytmusicapi
    ytmusicapi.setup(filepath=output_path, headers_raw=headers_raw)
    print(f"\nAuth saved to {output_path}")
    print("This is valid for ~2 years unless you log out of YT Music.")


def get_ytmusic_client(auth_file: str) -> "YTMusic":
    """Create an authenticated YTMusic client."""
    from ytmusicapi import YTMusic
    return YTMusic(auth_file)


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
            duration_sec = float(item["duration_seconds"])
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


def save_snapshot(tracks: list[Track]) -> Path:
    """Save current playlist state as a JSON snapshot for future diffing."""
    ensure_dirs()

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_path = SNAPSHOTS_DIR / f"snapshot_{timestamp}.json"

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

    latest_path = SNAPSHOTS_DIR / "latest.json"
    latest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Snapshot saved: %s (%d tracks)", snapshot_path.name, len(tracks))
    return snapshot_path


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
