"""
Spotify metadata and audio features enrichment for matched tracks.
Author: David
Date: 2026-03-22
Version: 0.3.0

Enriches tracks with Spotify metadata (ISRC, release date, explicit,
popularity, artist genres), audio features (danceability, energy, etc.),
and Last.fm data (play counts, listeners, genre/mood tags).
"""

from __future__ import annotations

import logging
from datetime import datetime

import spotipy

from playlist_sync.lastfm_client import get_tracks_info_batch
from playlist_sync.models import MatchResult, Track
from playlist_sync.spotify_client import (
    get_artists_batch,
    get_audio_features_batch,
    get_tracks_batch,
)

logger = logging.getLogger("playlist_sync")


def apply_match_to_track(track: Track, result: MatchResult) -> Track:
    """Apply a successful match result to enrich a track."""
    if not result.matched:
        return track

    now = datetime.now().isoformat(timespec="seconds")

    track.spotify_uri = result.spotify_uri
    track.spotify_url = result.spotify_url
    track.spotify_duration_ms = result.spotify_duration_ms
    track.explicit = result.explicit
    track.album_release_date = result.album_release_date
    track.popularity = result.popularity
    track.album_type = result.album_type
    track.track_number = result.track_number
    track.match_method = result.method
    track.match_confidence = result.confidence
    track.last_synced = now

    if not track.first_synced:
        track.first_synced = now

    if result.isrc and not track.isrc:
        track.isrc = result.isrc

    return track


def apply_audio_features(track: Track, features: dict) -> Track:
    """Apply audio features data to a track."""
    track.danceability = features.get("danceability", 0.0)
    track.energy = features.get("energy", 0.0)
    track.valence = features.get("valence", 0.0)
    track.tempo = features.get("tempo", 0.0)
    track.key = features.get("key", -1)
    track.mode = features.get("mode", -1)
    track.loudness = features.get("loudness", 0.0)
    track.speechiness = features.get("speechiness", 0.0)
    track.acousticness = features.get("acousticness", 0.0)
    track.instrumentalness = features.get("instrumentalness", 0.0)
    track.liveness = features.get("liveness", 0.0)
    track.time_signature = features.get("time_signature", 0)
    return track


def backfill_track_metadata(
    sp: spotipy.Spotify,
    tracks: list[Track],
) -> list[Track]:
    """Backfill Spotify metadata for matched tracks that are missing it.

    Fetches duration_ms, ISRC, explicit, album release date, popularity,
    album type, and track number using the /tracks endpoint (batches of 50).
    """
    needs_backfill = [
        t for t in tracks
        if t.has_spotify_match and (not t.spotify_duration_ms or not t.isrc or not t.popularity)
    ]

    if not needs_backfill:
        logger.info("All matched tracks already have Spotify metadata")
        return tracks

    # Build {spotify_id: Track} map
    track_id_map: dict[str, Track] = {}
    for t in needs_backfill:
        sp_id = t.spotify_uri.split(":")[-1] if t.spotify_uri else ""
        if sp_id:
            track_id_map[sp_id] = t

    logger.info("Backfilling Spotify metadata for %d tracks...", len(track_id_map))
    sp_tracks = get_tracks_batch(sp, list(track_id_map.keys()))

    enriched_count = 0
    for sp_id, sp_data in sp_tracks.items():
        if sp_id in track_id_map:
            t = track_id_map[sp_id]
            t.spotify_duration_ms = sp_data.get("duration_ms", 0)
            t.explicit = sp_data.get("explicit", False)
            t.popularity = sp_data.get("popularity", 0)
            t.track_number = sp_data.get("track_number", 0)

            album = sp_data.get("album", {})
            if album.get("release_date"):
                t.album_release_date = album["release_date"]
            if album.get("album_type"):
                t.album_type = album["album_type"]

            isrc = sp_data.get("external_ids", {}).get("isrc", "")
            if isrc and not t.isrc:
                t.isrc = isrc

            enriched_count += 1

    logger.info("Backfilled metadata for %d/%d tracks", enriched_count, len(track_id_map))
    return tracks


def backfill_artist_genres(
    sp: spotipy.Spotify,
    tracks: list[Track],
) -> list[Track]:
    """Fetch and apply artist genre tags for matched tracks missing them.

    Collects unique primary artist IDs from the Spotify track data,
    fetches genres via /artists endpoint, and writes comma-joined genres.
    """
    needs_genres = [
        t for t in tracks
        if t.has_spotify_match and not t.artist_genres
    ]

    if not needs_genres:
        logger.info("All matched tracks already have artist genres")
        return tracks

    # We need artist IDs — get them from the track URIs via /tracks endpoint.
    # Build a map of spotify_track_id -> list of Track objects (for fan-out).
    track_id_to_tracks: dict[str, list[Track]] = {}
    for t in needs_genres:
        sp_id = t.spotify_uri.split(":")[-1] if t.spotify_uri else ""
        if sp_id:
            track_id_to_tracks.setdefault(sp_id, []).append(t)

    logger.info("Fetching track data for %d tracks to extract artist IDs...", len(track_id_to_tracks))
    sp_tracks = get_tracks_batch(sp, list(track_id_to_tracks.keys()))

    # Collect unique artist IDs and map artist_id -> set of Tracks
    artist_id_to_tracks: dict[str, list[Track]] = {}
    for sp_id, sp_data in sp_tracks.items():
        artists = sp_data.get("artists", [])
        if artists:
            # Use primary (first) artist
            primary_artist_id = artists[0].get("id", "")
            if primary_artist_id and sp_id in track_id_to_tracks:
                for t in track_id_to_tracks[sp_id]:
                    artist_id_to_tracks.setdefault(primary_artist_id, []).append(t)

    if not artist_id_to_tracks:
        logger.info("No artist IDs found to fetch genres")
        return tracks

    logger.info("Fetching genres for %d unique artists...", len(artist_id_to_tracks))
    artist_data = get_artists_batch(sp, list(artist_id_to_tracks.keys()))

    enriched_count = 0
    for artist_id, artist_info in artist_data.items():
        genres = artist_info.get("genres", [])
        genre_str = ", ".join(genres) if genres else ""
        if artist_id in artist_id_to_tracks:
            for t in artist_id_to_tracks[artist_id]:
                t.artist_genres = genre_str
                enriched_count += 1

    logger.info("Applied genres to %d tracks from %d artists", enriched_count, len(artist_data))
    return tracks


def enrich_with_audio_features(
    sp: spotipy.Spotify,
    tracks: list[Track],
) -> list[Track]:
    """Fetch and apply audio features for matched tracks that don't have them yet."""
    needs_features = [
        t for t in tracks
        if t.has_spotify_match and not t.audio_features_fetched
    ]

    if not needs_features:
        logger.info("All matched tracks already have audio features (or were attempted)")
        return tracks

    # Extract Spotify track IDs from URIs (spotify:track:XXXXX -> XXXXX)
    track_id_map: dict[str, Track] = {}
    for t in needs_features:
        sp_id = t.spotify_uri.split(":")[-1] if t.spotify_uri else ""
        if sp_id:
            track_id_map[sp_id] = t

    logger.info("Fetching audio features for %d tracks...", len(track_id_map))
    features = get_audio_features_batch(sp, list(track_id_map.keys()))

    enriched_count = 0
    for sp_id, feat in features.items():
        if sp_id in track_id_map:
            apply_audio_features(track_id_map[sp_id], feat)
            track_id_map[sp_id].audio_features_fetched = True
            enriched_count += 1

    # Mark all attempted tracks so we don't retry on 403
    for t in needs_features:
        t.audio_features_fetched = True

    logger.info("Applied audio features to %d/%d tracks", enriched_count, len(track_id_map))
    return tracks


def backfill_lastfm_data(
    api_key: str,
    tracks: list[Track],
) -> list[Track]:
    """Fetch and apply Last.fm play counts, listeners, and tags.

    Uses artist + track name matching (no Spotify IDs needed).
    Only fetches for tracks that don't already have Last.fm data.
    """
    needs_lastfm = [
        t for t in tracks
        if not t.lastfm_playcount and t.title and t.artist
    ]

    if not needs_lastfm:
        logger.info("All tracks already have Last.fm data")
        return tracks

    # Build lookup pairs
    lookup_pairs = [(t.artist, t.title) for t in needs_lastfm]
    pair_to_tracks: dict[tuple[str, str], Track] = {
        (t.artist, t.title): t for t in needs_lastfm
    }

    logger.info("Fetching Last.fm data for %d tracks...", len(lookup_pairs))
    results = get_tracks_info_batch(api_key, lookup_pairs)

    enriched_count = 0
    for (artist, title), info in results.items():
        key = (artist, title)
        if key in pair_to_tracks:
            t = pair_to_tracks[key]
            t.lastfm_playcount = info.get("playcount", 0)
            t.lastfm_listeners = info.get("listeners", 0)
            tags = info.get("tags", [])
            t.lastfm_tags = ", ".join(tags[:5]) if tags else ""
            enriched_count += 1

    logger.info("Applied Last.fm data to %d/%d tracks", enriched_count, len(needs_lastfm))
    return tracks
