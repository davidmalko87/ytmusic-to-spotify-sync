"""
Spotify API wrapper: search, playlist operations, batching.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import logging
import time

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from playlist_sync.config import (
    SEARCH_DELAY_SEC,
    SPOTIFY_PLAYLIST_BATCH,
    SPOTIFY_SEARCH_LIMIT,
)

logger = logging.getLogger("playlist_sync")


def get_spotify_client(config: dict[str, str]) -> spotipy.Spotify:
    """Create an authenticated Spotify client."""
    scope = "playlist-modify-public playlist-modify-private playlist-read-private"
    auth_manager = SpotifyOAuth(
        client_id=config["SPOTIPY_CLIENT_ID"],
        client_secret=config["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=config["SPOTIPY_REDIRECT_URI"],
        scope=scope,
    )
    sp = spotipy.Spotify(auth_manager=auth_manager, requests_timeout=30, retries=3)

    user = sp.current_user()
    logger.info("Authenticated as Spotify user: %s", user.get("display_name", user["id"]))
    return sp


def search_track(
    sp: spotipy.Spotify,
    query: str,
    limit: int = SPOTIFY_SEARCH_LIMIT,
) -> list[dict]:
    """Search Spotify for tracks. Returns list of track dicts."""
    if not query.strip():
        return []

    time.sleep(SEARCH_DELAY_SEC)

    try:
        results = sp.search(q=query, type="track", limit=limit)
        items = results.get("tracks", {}).get("items", [])
        logger.debug("Search '%s' -> %d results", query[:60], len(items))
        return items
    except spotipy.SpotifyException as e:
        logger.warning("Spotify search failed for '%s': %s", query[:60], e)
        return []


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
    results = sp.playlist_tracks(playlist_id)

    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if track:
                tracks.append(track)

        if results.get("next"):
            results = sp.next(results)
        else:
            break

    logger.info("Fetched %d tracks from Spotify playlist %s", len(tracks), playlist_id)
    return tracks


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
        try:
            sp.playlist_add_items(playlist_id, batch)
            added += len(batch)
            logger.debug("Added batch of %d tracks (%d/%d)", len(batch), added, len(uris))
        except spotipy.SpotifyException as e:
            logger.error("Failed to add batch at offset %d: %s", i, e)

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
        try:
            sp.playlist_remove_all_occurrences_of_items(playlist_id, batch)
            removed += len(batch)
            logger.debug("Removed batch of %d tracks (%d/%d)", len(batch), removed, len(uris))
        except spotipy.SpotifyException as e:
            logger.error("Failed to remove batch at offset %d: %s", i, e)

    logger.info("Removed %d/%d tracks from Spotify playlist", removed, len(uris))
    return removed
