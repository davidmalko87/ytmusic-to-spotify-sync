"""
Last.fm API client for track metadata enrichment.
Author: David
Date: 2026-10-18
Version: 0.3.0

Fetches play counts, listener counts, and genre/mood tags from Last.fm.
Provides both track-level (track.getInfo) and artist-level (artist.getInfo)
endpoints. Artist-level lookups are cached per unique artist for speed,
which is essential when tracks share artists (typical 4× speedup on
large playlists).

Lookups tell "Last.fm has no such artist/track" (returns None) apart from
"the request failed" (raises LastfmRequestError), so callers can retry
failures on the next run instead of writing them off.

No pip dependencies — uses stdlib urllib only.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("playlist_sync")

LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

# Error 6 ("Invalid parameters") is how artist.getInfo / track.getInfo say
# "no such artist / track". Every other error code (bad key, rate limit,
# backend failure, ...) says nothing about the artist and is worth retrying.
LASTFM_ERROR_NOT_FOUND = 6

# Stop a batch after this many failed requests in a row: the key is bad or
# Last.fm is down, and the remaining lookups would fail the same way.
MAX_CONSECUTIVE_FAILURES = 50

# Last.fm allows ~5 req/s; 0.25s delay is safe and fast
LASTFM_DELAY_SEC = 0.25


def get_track_info(
    api_key: str,
    artist: str,
    track: str,
) -> dict | None:
    """Fetch track info from Last.fm API.

    Returns dict with playcount, listeners, tags; None when Last.fm has no
    such track. Raises LastfmRequestError when the request itself failed.
    """
    data = _call_lastfm({
        "method": "track.getInfo",
        "api_key": api_key,
        "artist": artist,
        "track": track,
        "format": "json",
        "autocorrect": "1",
    })
    track_data = data.get("track") if data else None
    if not track_data:
        logger.debug("Last.fm has no track '%s - %s'", artist, track)
        return None

    # Extract tags (top tags for this track)
    raw_tags = track_data.get("toptags", {}).get("tag", [])
    tags = [t.get("name", "") for t in raw_tags if t.get("name")]

    return {
        "playcount": _to_int(track_data.get("playcount")),
        "listeners": _to_int(track_data.get("listeners")),
        "tags": tags,
    }


def get_tracks_info_batch(
    api_key: str,
    tracks: list[tuple[str, str]],
) -> dict[tuple[str, str], dict | None]:
    """Fetch Last.fm info for multiple (artist, track) pairs.

    Returns {(artist, track): info} for every pair Last.fm answered; info is
    None when Last.fm has no such track. Pairs whose request failed are left
    out, so the caller can retry them on the next run.
    Uses the first artist name only (split on comma) for better matching.
    """
    from tqdm import tqdm

    results: dict[tuple[str, str], dict | None] = {}
    failed = 0
    consecutive_errors = 0

    for artist, track_name in tqdm(tracks, desc="Last.fm", unit="track"):
        # Use first artist only for better Last.fm matching
        first_artist = artist.split(",")[0].strip() if artist else ""
        if not first_artist or not track_name:
            continue

        try:
            results[(artist, track_name)] = get_track_info(api_key, first_artist, track_name)
        except LastfmRequestError as e:
            logger.debug("Last.fm request failed for '%s - %s': %s", first_artist, track_name, e)
            failed += 1
            consecutive_errors += 1
            # If API key is invalid or service is down, stop early
            if consecutive_errors >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "Last.fm: %d consecutive failures — stopping. "
                    "Check your LASTFM_API_KEY in .env",
                    consecutive_errors,
                )
                break
        else:
            consecutive_errors = 0

    found = sum(1 for info in results.values() if info)
    logger.info(
        "Last.fm: fetched data for %d/%d tracks (%d not on Last.fm, %d failed, retried next run)",
        found, len(tracks), len(results) - found, failed,
    )
    return results


def get_artist_info(
    api_key: str,
    artist: str,
) -> dict | None:
    """Fetch artist info from Last.fm API.

    Returns dict with tags (top tags for the artist); None when Last.fm has
    no such artist. Raises LastfmRequestError when the request itself failed.
    Tags from artist-level lookups are far denser than track-level ones —
    typically 85%+ coverage vs ~14% for niche music libraries.
    """
    data = _call_lastfm({
        "method": "artist.getInfo",
        "api_key": api_key,
        "artist": artist,
        "format": "json",
        "autocorrect": "1",
    })
    artist_data = data.get("artist") if data else None
    if not artist_data:
        logger.debug("Last.fm has no artist '%s'", artist)
        return None

    raw_tags = artist_data.get("tags", {}).get("tag", [])
    tags = [t.get("name", "") for t in raw_tags if t.get("name")]

    return {
        "name": artist_data.get("name", artist),
        "tags": tags,
    }


def get_artists_info_batch(
    api_key: str,
    artists: list[str],
) -> dict[str, dict | None]:
    """Fetch Last.fm info for a list of artist names (deduplicated).

    Returns {artist_name: info} for every artist Last.fm answered; info is
    None when Last.fm has no such artist. Artists whose request failed are
    left out, so the caller can retry them on the next run. Each unique
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

    results: dict[str, dict | None] = {}
    failed = 0
    consecutive_errors = 0

    for artist in tqdm(unique_artists, desc="Last.fm artists", unit="artist"):
        try:
            results[artist] = get_artist_info(api_key, artist)
        except LastfmRequestError as e:
            logger.debug("Last.fm artist request failed for '%s': %s", artist, e)
            failed += 1
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "Last.fm: %d consecutive artist failures — stopping. "
                    "Check your LASTFM_API_KEY in .env",
                    consecutive_errors,
                )
                break
        else:
            consecutive_errors = 0

    found = sum(1 for info in results.values() if info)
    logger.info(
        "Last.fm: fetched data for %d/%d unique artists (%d not on Last.fm, %d failed, "
        "retried next run)",
        found, len(unique_artists), len(results) - found, failed,
    )
    return results


# ── Request plumbing ─────────────────────────────────────────────────


class LastfmRequestError(Exception):
    """A Last.fm request failed (network, HTTP, API error other than not-found)."""


def _encode_query(params: dict[str, str]) -> str:
    """URL-encode request params so Last.fm reads them back unchanged.

    Last.fm URL-decodes query values twice. A correctly encoded '+' (%2B)
    survives the first pass as '+' and the second pass turns it into a
    space, so 'Sultan + Shepard' was looked up as 'Sultan   Shepard' — a
    tagless ghost entry. Encoding '+' once more (%252B) makes it arrive
    intact; other characters are unaffected.
    """
    return urllib.parse.urlencode({k: v.replace("+", "%2B") for k, v in params.items()})


def _call_lastfm(params: dict[str, str]) -> dict | None:
    """GET one Last.fm API method and return the decoded JSON body.

    Returns None when Last.fm answers "not found" (error 6). Raises
    LastfmRequestError for network failures, non-JSON replies and any other
    API error, none of which say anything about the artist or track.
    """
    url = f"{LASTFM_BASE_URL}?{_encode_query(params)}"

    time.sleep(LASTFM_DELAY_SEC)

    req = urllib.request.Request(url, headers={"User-Agent": "YTMusicSync/0.5"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Last.fm sends some API errors with a 4xx status and a JSON body
        # naming the error code; anything else is a plain HTTP failure.
        data = _read_error_body(e)
        if data is None:
            raise LastfmRequestError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, ValueError, OSError) as e:
        raise LastfmRequestError(str(e)) from e

    if not isinstance(data, dict):
        raise LastfmRequestError("unexpected reply from Last.fm")
    if "error" in data:
        if _to_int(data.get("error")) == LASTFM_ERROR_NOT_FOUND:
            return None
        raise LastfmRequestError(f"Last.fm error {data.get('error')}: {data.get('message')}")
    return data


def _read_error_body(err: urllib.error.HTTPError) -> dict | None:
    """Return the JSON error body of an HTTP error reply, or None if there is none."""
    try:
        data = json.loads(err.read().decode("utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) and "error" in data else None


def _to_int(value: object) -> int:
    """Parse a Last.fm count (sent as a string); 0 when missing or malformed."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
