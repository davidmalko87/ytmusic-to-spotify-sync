"""
Spotify API wrapper: search, playlist operations, batching.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import time
from pathlib import Path

import requests
import spotipy
from requests.adapters import HTTPAdapter
from spotipy.cache_handler import CacheFileHandler
from spotipy.exceptions import SpotifyOauthError
from spotipy.oauth2 import SpotifyOAuth
from tqdm import tqdm
from urllib3.util.retry import Retry

from playlist_sync.config import (
    LEGACY_TOKEN_CACHE,
    SEARCH_DELAY_SEC,
    SPOTIFY_PLAYLIST_BATCH,
    SPOTIFY_SEARCH_LIMIT,
    SPOTIFY_TOKEN_CACHE,
    make_private,
)

logger = logging.getLogger("playlist_sync")


def _adopt_legacy_token_cache() -> None:
    """Keep 0.7.x users logged in: their token lived in a file named '.cache'."""
    if SPOTIFY_TOKEN_CACHE.exists() or not LEGACY_TOKEN_CACHE.is_file():
        return
    try:
        token = json.loads(LEGACY_TOKEN_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # some other tool's '.cache' file, not a spotipy token
    if isinstance(token, dict) and "refresh_token" in token:
        shutil.copy2(LEGACY_TOKEN_CACHE, SPOTIFY_TOKEN_CACHE)
        make_private(SPOTIFY_TOKEN_CACHE)
        logger.info("Copied Spotify token cache %s -> %s", LEGACY_TOKEN_CACHE, SPOTIFY_TOKEN_CACHE)


def _spotify_session() -> requests.Session:
    """HTTP session that retries server errors but hands every 429 straight back.

    spotipy's own session lets urllib3 honour Retry-After on a 429 by itself:
    it sleeps up to 6 hours per retry, three times, then reports the 429
    without its headers, so a long Spotify ban hung the sync for hours. With
    respect_retry_after_header=False (status_forcelist alone is not enough -
    urllib3 retries any 429 that carries Retry-After) the tool sees the real
    wait and either sleeps a short one or stops with progress saved.
    """
    retry = Retry(
        total=3, connect=None, read=False, status=3, backoff_factor=0.3,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]),
        respect_retry_after_header=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class PrivateCacheFileHandler(CacheFileHandler):
    """spotipy's token file handler, but the file is owner-only from the moment it exists.

    spotipy < 2.25.1 wrote it with the umask (0644, CVE-2025-27154); newer
    versions chmod it after writing, which leaves a short world-readable window.
    Creating it with mode 0600 closes both.
    """

    def save_token_to_cache(self, token_info: dict) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(self.cache_path, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(token_info, cls=getattr(self, "encoder_cls", None)))
        except OSError as e:
            logger.warning("Could not write the Spotify token cache %s: %s", self.cache_path, e)
            return
        make_private(Path(self.cache_path))  # O_CREAT's mode is ignored for an existing file


def get_spotify_client(config: dict[str, str]) -> spotipy.Spotify:
    """Create an authenticated Spotify client.

    Note: scope now includes `user-library-read` and `user-library-modify`
    so the likes-sync command can read and update the user's saved-tracks
    library. Adding these scopes triggers a one-time re-authorization in
    the browser on first launch after upgrade — spotipy refreshes the
    cached token automatically.
    """
    scope = (
        "playlist-modify-public playlist-modify-private playlist-read-private "
        "user-library-read user-library-modify"
    )
    _adopt_legacy_token_cache()
    if SPOTIFY_TOKEN_CACHE.is_file():
        make_private(SPOTIFY_TOKEN_CACHE)  # may have been written 0644 by an older spotipy
    auth_manager = SpotifyOAuth(
        client_id=config["SPOTIPY_CLIENT_ID"],
        client_secret=config["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=config["SPOTIPY_REDIRECT_URI"],
        scope=scope,
        # Pin the token cache to the settings folder; spotipy's default is the CWD
        cache_handler=PrivateCacheFileHandler(cache_path=str(SPOTIFY_TOKEN_CACHE)),
    )
    sp = spotipy.Spotify(
        auth_manager=auth_manager, requests_session=_spotify_session(), requests_timeout=30,
    )

    user = sp.current_user()
    logger.info("Authenticated as Spotify user: %s", user.get("display_name", user["id"]))
    return sp


def list_my_playlists(sp: spotipy.Spotify) -> list[dict[str, str]]:
    """Return playlists the current user can add tracks to, as {id, name, count}."""
    me = sp.current_user()["id"]
    playlists: list[dict[str, str]] = []
    page = sp.current_user_playlists(limit=50)
    while page:
        for item in page.get("items") or []:
            if not item or not item.get("id"):
                continue
            owner = (item.get("owner") or {}).get("id")
            if owner != me and not item.get("collaborative"):
                continue  # followed playlists are read-only for us
            # Newer API responses describe the contents under "items" instead of "tracks"
            contents = item.get("tracks") or item.get("items") or {}
            total = contents.get("total") if isinstance(contents, dict) else None
            playlists.append({
                "id": item["id"],
                "name": item.get("name") or item["id"],
                "count": "" if total is None else str(total),
            })
        page = sp.next(page) if page.get("next") else None
    return playlists


def create_playlist(sp: spotipy.Spotify, name: str, description: str = "") -> dict[str, str]:
    """Create a private playlist for the current user and return {id, name}."""
    created = sp.current_user_playlist_create(name, public=False, description=description)
    return {"id": created["id"], "name": created.get("name") or name}


def get_playlist_access(sp: spotipy.Spotify, playlist_id: str) -> dict[str, object]:
    """Return {name, can_edit} for a playlist; raises spotipy.SpotifyException if unreachable.

    Only the owner (or anyone, for collaborative playlists) may add tracks, the
    same rule list_my_playlists applies.
    """
    info = sp.playlist(playlist_id, fields="name,owner(id),collaborative")
    me = sp.current_user()["id"]
    owner = (info.get("owner") or {}).get("id")
    return {
        "name": info.get("name") or playlist_id,
        "can_edit": owner == me or bool(info.get("collaborative")),
    }


class SearchUnavailable(Exception):
    """Spotify did not answer a search (server error, network); not the same as no results."""


class RateLimitError(Exception):
    """Raised when Spotify rate limit is hit and retry wait is too long."""
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s")


def _retry_after_seconds(e: spotipy.SpotifyException) -> int:
    """Seconds Spotify asked us to wait (60 when the header is missing or odd)."""
    try:
        return max(0, int(float((e.headers or {}).get("Retry-After", 60))))
    except (TypeError, ValueError):
        return 60


def _is_real_rate_limit(e: spotipy.SpotifyException) -> bool:
    """A genuine 429 carries Retry-After; spotipy's own 'Max Retries' 429 has no headers."""
    return e.http_status == 429 and bool(e.headers)


def _handle_rate_limit(e: spotipy.SpotifyException) -> None:
    """Handle 429 rate limit errors with short backoff or raise if too long."""
    retry_after = _retry_after_seconds(e)

    if retry_after <= 120:
        # Manageable wait — just pause and retry
        logger.warning("Rate limited, waiting %ds...", retry_after)
        time.sleep(retry_after + 1)
    else:
        # Multi-hour wait — save progress and stop
        raise RateLimitError(retry_after)


def search_track(
    sp: spotipy.Spotify,
    query: str,
    limit: int = SPOTIFY_SEARCH_LIMIT,
) -> list[dict]:
    """Search Spotify for tracks. Returns list of track dicts."""
    if not query.strip():
        return []

    time.sleep(SEARCH_DELAY_SEC)

    last_wait = 60
    for _attempt in range(3):
        try:
            results = sp.search(q=query, type="track", limit=limit)
            items = results.get("tracks", {}).get("items", [])
            logger.debug("Search '%s' -> %d results", query[:60], len(items))
            return items
        except spotipy.SpotifyException as e:
            if _is_real_rate_limit(e):
                _handle_rate_limit(e)  # sleeps a short wait, raises RateLimitError for a long one
                last_wait = _retry_after_seconds(e)
                continue
            if e.http_status == 429 or e.http_status >= 500:
                # Server errors after urllib3's retries: the search did not happen,
                # so the track must not be recorded as "no match"
                raise SearchUnavailable(f"Spotify search failed (HTTP {e.http_status})") from e
            logger.warning("Spotify search failed for '%s': %s", query[:60], e)
            return []
        except requests.exceptions.RequestException as e:  # timeouts, DNS, resets
            raise SearchUnavailable(f"could not reach Spotify ({e})") from e
        except SpotifyOauthError as e:
            # The hourly token refresh failed (accounts.spotify.com down or
            # rate-limited): no search happened, so this is not "no match"
            raise SearchUnavailable(f"could not refresh the Spotify login ({e})") from e

    # Still rate-limited after three short waits: stop like a long ban, progress kept
    raise RateLimitError(last_wait)


def search_by_isrc(sp: spotipy.Spotify, isrc: str) -> list[dict]:
    """Search Spotify by ISRC code."""
    return search_track(sp, f"isrc:{isrc}", limit=1)


def get_track_details(sp: spotipy.Spotify, track_id: str) -> dict | None:
    """Get full track details including external_ids (ISRC)."""
    try:
        return sp.track(track_id)
    except spotipy.SpotifyException as e:
        logger.warning("Failed to get track details for %s: %s", track_id, e)
        return None


def get_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    """Fetch all tracks from a Spotify playlist (handles pagination)."""
    tracks: list[dict] = []
    results = sp.playlist_items(playlist_id, additional_types=("track",))

    while results:
        for item in results.get("items", []):
            # Spotify's 2026 API renamed each entry's "track" to "item"; accept both
            # so repush never mistakes a full playlist for an empty one.
            track = item.get("item") or item.get("track")
            if track and track.get("type", "track") == "track":
                tracks.append(track)

        if results.get("next"):
            results = sp.next(results)
        else:
            break

    logger.info("Fetched %d tracks from Spotify playlist %s", len(tracks), playlist_id)
    return tracks


def _is_server_failure(e: spotipy.SpotifyException) -> bool:
    """A 5xx, or spotipy's header-less 429 that stands for 5xx retries used up."""
    return (e.http_status == 429 and not e.headers) or e.http_status >= 500


def _lookup_in_batches(
    call, ids: list[str], key: str, desc: str, failed: set[str] | None,
) -> dict[str, dict]:
    """Fetch objects for ids 50 at a time; {id: object} for every one Spotify returned.

    Server trouble stops the loop at once (no 61 s sleep per batch during an
    outage) and puts the ids not fetched into failed, so the caller can leave
    them for the next run. A long rate limit raises RateLimitError.
    """
    results: dict[str, dict] = {}
    consecutive_403s = 0
    total_batches = math.ceil(len(ids) / 50)
    for i in tqdm(range(0, len(ids), 50), desc=desc, unit="batch", total=total_batches):
        batch = ids[i:i + 50]
        time.sleep(SEARCH_DELAY_SEC)
        for attempt in range(2):
            try:
                response = call(batch)
            except spotipy.SpotifyException as e:
                if _is_real_rate_limit(e) and attempt == 0:
                    _handle_rate_limit(e)  # sleeps a short wait, raises for a long one
                    continue
                if e.http_status == 429 or _is_server_failure(e):
                    logger.warning("Spotify /%s failed at offset %d (HTTP %s); "
                                   "%d left for the next run", key, i, e.http_status, len(ids) - i)
                    if failed is not None:
                        failed.update(ids[i:])
                    return results
                if e.http_status == 403:
                    consecutive_403s += 1
                else:
                    consecutive_403s = 0
                    logger.warning("Failed to fetch %s batch at offset %d: %s", key, i, e)
                break
            for item in response.get(key, []):
                if item:
                    results[item["id"]] = item
            consecutive_403s = 0
            break
        if consecutive_403s >= 3:
            logger.warning(
                "/%s endpoint returned 403 three times in a row. "
                "Spotify has restricted this endpoint for your app type. Skipping.", key,
            )
            break
    return results


def get_tracks_batch(
    sp: spotipy.Spotify,
    track_ids: list[str],
    failed: set[str] | None = None,
) -> dict[str, dict]:
    """Fetch full track objects for multiple tracks in batches of 50.

    Returns {track_id: track_dict} for successfully fetched tracks.
    Uses the /tracks endpoint which supports up to 50 IDs per call.
    Stops early if the endpoint returns 403 (restricted in Dev Mode), and on
    server trouble, adding the ids not fetched to failed.
    """
    results = _lookup_in_batches(sp.tracks, track_ids, "tracks", "Metadata", failed)
    logger.info("Fetched metadata for %d/%d tracks", len(results), len(track_ids))
    return results


def get_artists_batch(
    sp: spotipy.Spotify,
    artist_ids: list[str],
    failed: set[str] | None = None,
) -> dict[str, dict]:
    """Fetch artist objects in batches of 50. Returns {artist_id: artist_dict}.

    Each artist dict includes 'genres' (list of strings) and other metadata.
    Server trouble stops early and adds the ids not fetched to failed.
    """
    results = _lookup_in_batches(sp.artists, artist_ids, "artists", "Artists", failed)
    logger.info("Fetched genre data for %d/%d artists", len(results), len(artist_ids))
    return results


def get_audio_features_batch(
    sp: spotipy.Spotify,
    track_ids: list[str],
) -> dict[str, dict]:
    """Fetch audio features for multiple tracks. Returns {track_id: features}.

    Uses the single-track endpoint since GET /audio-features (batch) was
    removed in Feb 2026. Rate-limited to avoid 429s.

    If the endpoint returns 403 (access removed for this app type), logs a
    warning and returns an empty dict immediately rather than retrying for
    every track.
    """
    features: dict[str, dict] = {}
    consecutive_403s = 0

    for track_id in tqdm(track_ids, desc="Audio features", unit="track"):
        time.sleep(SEARCH_DELAY_SEC)
        try:
            result = sp.audio_features([track_id])
            if result and result[0]:
                features[track_id] = result[0]
                logger.debug("Audio features for %s: tempo=%.1f, energy=%.2f",
                             track_id, result[0].get("tempo", 0), result[0].get("energy", 0))
            consecutive_403s = 0
        except spotipy.SpotifyException as e:
            if e.http_status == 403:
                consecutive_403s += 1
                if consecutive_403s >= 3:
                    logger.warning(
                        "Audio features endpoint returned 403 three times in a row. "
                        "Spotify has restricted this endpoint for your app type. Skipping."
                    )
                    break
            else:
                consecutive_403s = 0
            logger.warning("Failed to get audio features for %s: %s", track_id, e)

    logger.info("Fetched audio features for %d/%d tracks", len(features), len(track_ids))
    return features


def _with_rate_limit_retry(call, what: str) -> bool:
    """Run one playlist write; wait out short 429s, raise RateLimitError for long ones.

    Returns False (and logs) when Spotify refuses the write for another reason.
    """
    for _attempt in range(3):
        try:
            call()
            return True
        except spotipy.SpotifyException as e:
            if _is_real_rate_limit(e):
                _handle_rate_limit(e)
                continue
            logger.error("Failed to %s: %s", what, e)
            return False
    logger.error("Failed to %s: still rate-limited after 3 waits", what)
    return False


def add_tracks_to_playlist(
    sp: spotipy.Spotify,
    playlist_id: str,
    uris: list[str],
    dry_run: bool = False,
) -> int:
    """Add tracks to Spotify playlist in batches of 100."""
    if not uris:
        return 0

    if dry_run:
        logger.info("[DRY RUN] Would add %d tracks to playlist", len(uris))
        return len(uris)

    added = 0
    for i in range(0, len(uris), SPOTIFY_PLAYLIST_BATCH):
        batch = uris[i:i + SPOTIFY_PLAYLIST_BATCH]
        if _with_rate_limit_retry(lambda b=batch: sp.playlist_add_items(playlist_id, b),
                                  f"add batch at offset {i}"):
            added += len(batch)
            logger.debug("Added batch of %d tracks (%d/%d)", len(batch), added, len(uris))

    logger.info("Added %d/%d tracks to Spotify playlist", added, len(uris))
    return added


def remove_tracks_from_playlist(
    sp: spotipy.Spotify,
    playlist_id: str,
    uris: list[str],
    dry_run: bool = False,
) -> int:
    """Remove tracks from Spotify playlist in batches of 100."""
    if not uris:
        return 0

    if dry_run:
        logger.info("[DRY RUN] Would remove %d tracks from playlist", len(uris))
        return len(uris)

    removed = 0
    for i in range(0, len(uris), SPOTIFY_PLAYLIST_BATCH):
        batch = uris[i:i + SPOTIFY_PLAYLIST_BATCH]
        if _with_rate_limit_retry(
            lambda b=batch: sp.playlist_remove_all_occurrences_of_items(playlist_id, b),
            f"remove batch at offset {i}",
        ):
            removed += len(batch)
            logger.debug("Removed batch of %d tracks (%d/%d)", len(batch), removed, len(uris))

    logger.info("Removed %d/%d tracks from Spotify playlist", removed, len(uris))
    return removed


# ── Saved tracks (user library / "Liked Songs") ─────────────────────
#
# Endpoint limit: GET/PUT/DELETE /me/tracks accept up to 50 IDs per call.

SPOTIFY_SAVED_TRACKS_BATCH = 50


def get_saved_tracks(sp: spotipy.Spotify) -> list[dict]:
    """Fetch all tracks from the user's Liked Songs (saved tracks).

    Returns a list of full track dicts (the same shape as playlist items).
    """
    tracks: list[dict] = []
    offset = 0
    while True:
        results = sp.current_user_saved_tracks(limit=50, offset=offset)
        items = results.get("items", [])
        if not items:
            break
        for item in items:
            t = item.get("track")
            if t:
                tracks.append(t)
        if len(items) < 50:
            break
        offset += 50

    logger.info("Fetched %d saved tracks from Spotify library", len(tracks))
    return tracks


def add_saved_tracks(
    sp: spotipy.Spotify,
    track_ids: list[str],
    dry_run: bool = False,
    failed: list[str] | None = None,
) -> int:
    """Add tracks to the user's Liked Songs in batches of 50; failed gets the ids not saved.

    A long rate limit raises RateLimitError; network errors propagate.
    """
    if not track_ids:
        return 0

    if dry_run:
        logger.info("[DRY RUN] Would save %d tracks to library", len(track_ids))
        return len(track_ids)

    added = 0
    for i in range(0, len(track_ids), SPOTIFY_SAVED_TRACKS_BATCH):
        batch = track_ids[i:i + SPOTIFY_SAVED_TRACKS_BATCH]
        if _with_rate_limit_retry(lambda b=batch: sp.current_user_saved_tracks_add(b),
                                  f"save batch at offset {i}"):
            added += len(batch)
            logger.debug("Saved batch of %d tracks (%d/%d)", len(batch), added, len(track_ids))
        elif failed is not None:
            failed.extend(batch)

    logger.info("Added %d/%d tracks to Spotify library", added, len(track_ids))
    return added


def remove_saved_tracks(
    sp: spotipy.Spotify,
    track_ids: list[str],
    dry_run: bool = False,
    failed: list[str] | None = None,
) -> int:
    """Remove tracks from the user's Liked Songs in batches of 50; failed gets the ids kept.

    A long rate limit raises RateLimitError; network errors propagate.
    """
    if not track_ids:
        return 0

    if dry_run:
        logger.info("[DRY RUN] Would unsave %d tracks from library", len(track_ids))
        return len(track_ids)

    removed = 0
    for i in range(0, len(track_ids), SPOTIFY_SAVED_TRACKS_BATCH):
        batch = track_ids[i:i + SPOTIFY_SAVED_TRACKS_BATCH]
        if _with_rate_limit_retry(lambda b=batch: sp.current_user_saved_tracks_delete(b),
                                  f"unsave batch at offset {i}"):
            removed += len(batch)
        elif failed is not None:
            failed.extend(batch)

    logger.info("Removed %d/%d tracks from Spotify library", removed, len(track_ids))
    return removed
