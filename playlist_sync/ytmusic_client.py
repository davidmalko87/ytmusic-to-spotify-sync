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
    print("3. Go to Network tab")
    print("4. Filter by '/browse'")
    print("5. Click any POST request with status 200")
    print("6. Copy ALL request headers")
    print("7. Paste below, then press Enter on an empty line")
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

    headers_raw = "\n".join(lines)
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
            parts = str(item["duration"]).split(":")
            if len(parts) == 2:
                duration_sec = int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                duration_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

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
