"""
Last.fm API client for track metadata enrichment.
Author: David
Date: 2026-04-14
Version: 0.2.0

Fetches play counts, listener counts, and genre/mood tags from Last.fm.
Provides both track-level (track.getInfo) and artist-level (artist.getInfo)
endpoints. Artist-level lookups are cached per unique artist for speed,
which is essential when tracks share artists (typical 4× speedup on
large playlists).

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


def get_artist_info(
    api_key: str,
    artist: str,
) -> dict | None:
    """Fetch artist info from Last.fm API.

    Returns dict with tags (top tags for the artist), or None on failure.
    Tags from artist-level lookups are far denser than track-level ones —
    typically 85%+ coverage vs ~14% for niche music libraries.
    """
    params = {
        "method": "artist.getInfo",
        "api_key": api_key,
        "artist": artist,
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
            logger.debug("Last.fm artist error for '%s': %s", artist, data.get("message"))
            return None

        artist_data = data.get("artist", {})
        if not artist_data:
            return None

        raw_tags = artist_data.get("tags", {}).get("tag", [])
        tags = [t.get("name", "") for t in raw_tags if t.get("name")]

        return {
            "name": artist_data.get("name", artist),
            "tags": tags,
        }

    except (urllib.error.URLError, json.JSONDecodeError, ValueError, OSError) as e:
        logger.debug("Last.fm artist request failed for '%s': %s", artist, e)
        return None


def get_artists_info_batch(
    api_key: str,
    artists: list[str],
) -> dict[str, dict]:
    """Fetch Last.fm info for a list of artist names (deduplicated).

    Returns {artist_name: info_dict} for successful lookups. Each unique
    artist is queried only once even if it appears many times in `artists`.
    """
    from tqdm import tqdm

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_artists: list[str] = []
    for a in artists:
        if a and a not in seen:
            seen.add(a)
            unique_artists.append(a)

    results: dict[str, dict] = {}
    consecutive_errors = 0

    for artist in tqdm(unique_artists, desc="Last.fm artists", unit="artist"):
        info = get_artist_info(api_key, artist)
        if info:
            results[artist] = info
            consecutive_errors = 0
        else:
            consecutive_errors += 1
            if consecutive_errors >= 50:
                logger.warning(
                    "Last.fm: %d consecutive artist failures — stopping. "
                    "Check your LASTFM_API_KEY in .env",
                    consecutive_errors,
                )
                break

    logger.info(
        "Last.fm: fetched data for %d/%d unique artists",
        len(results), len(unique_artists),
    )
    return results
