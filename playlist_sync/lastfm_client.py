"""
Last.fm API client for track metadata enrichment.
Author: David
Date: 2026-04-14
Version: 0.1.0

Fetches play counts, listener counts, and genre/mood tags from Last.fm.
No pip dependencies — uses stdlib urllib only.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("playlist_sync")

LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

# Last.fm allows ~5 req/s; 0.25s delay is safe and fast
LASTFM_DELAY_SEC = 0.25


def get_track_info(
    api_key: str,
    artist: str,
    track: str,
) -> dict | None:
    """Fetch track info from Last.fm API.

    Returns dict with playcount, listeners, tags, or None on failure.
    """
    params = {
        "method": "track.getInfo",
        "api_key": api_key,
        "artist": artist,
        "track": track,
        "format": "json",
        "autocorrect": "1",
    }
    url = f"{LASTFM_BASE_URL}?{urllib.parse.urlencode(params)}"

    time.sleep(LASTFM_DELAY_SEC)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "YTMusicSync/0.5"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if "error" in data:
            logger.debug("Last.fm error for '%s - %s': %s", artist, track, data.get("message"))
            return None

        track_data = data.get("track", {})
        if not track_data:
            return None

        # Extract tags (top tags for this track)
        raw_tags = track_data.get("toptags", {}).get("tag", [])
        tags = [t.get("name", "") for t in raw_tags if t.get("name")]

        return {
            "playcount": int(track_data.get("playcount", 0)),
            "listeners": int(track_data.get("listeners", 0)),
            "tags": tags,
        }

    except (urllib.error.URLError, json.JSONDecodeError, ValueError, OSError) as e:
        logger.debug("Last.fm request failed for '%s - %s': %s", artist, track, e)
        return None


def get_tracks_info_batch(
    api_key: str,
    tracks: list[tuple[str, str]],
) -> dict[tuple[str, str], dict]:
    """Fetch Last.fm info for multiple (artist, track) pairs.

    Returns {(artist, track): info_dict} for successful lookups.
    Uses the first artist name only (split on comma) for better matching.
    """
    from tqdm import tqdm

    results: dict[tuple[str, str], dict] = {}
    consecutive_errors = 0

    for artist, track_name in tqdm(tracks, desc="Last.fm", unit="track"):
        # Use first artist only for better Last.fm matching
        first_artist = artist.split(",")[0].strip() if artist else ""
        if not first_artist or not track_name:
            continue

        info = get_track_info(api_key, first_artist, track_name)
        if info:
            results[(artist, track_name)] = info
            consecutive_errors = 0
        else:
            consecutive_errors += 1
            # If API key is invalid or service is down, stop early
            if consecutive_errors >= 50:
                logger.warning(
                    "Last.fm: %d consecutive failures — stopping. "
                    "Check your LASTFM_API_KEY in .env",
                    consecutive_errors,
                )
                break

    logger.info("Last.fm: fetched data for %d/%d tracks", len(results), len(tracks))
    return results
