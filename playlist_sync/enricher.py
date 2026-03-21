"""
Spotify metadata and audio features enrichment for matched tracks.
Author: David
Date: 2026-03-22
Version: 0.2.0

Enriches tracks with Spotify metadata (ISRC, release date, explicit)
and audio features (danceability, energy, valence, tempo, etc.).
"""

from __future__ import annotations

import logging
from datetime import datetime

import spotipy

from playlist_sync.models import MatchResult, Track
from playlist_sync.spotify_client import get_audio_features_batch

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


def enrich_with_audio_features(
    sp: spotipy.Spotify,
    tracks: list[Track],
) -> list[Track]:
    """Fetch and apply audio features for matched tracks that don't have them yet."""
    # Only fetch for tracks that have a Spotify match but no audio features
    needs_features = [
        t for t in tracks
        if t.has_spotify_match and t.danceability == 0.0 and t.energy == 0.0
    ]

    if not needs_features:
        logger.info("All matched tracks already have audio features")
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
            enriched_count += 1

    logger.info("Applied audio features to %d/%d tracks", enriched_count, len(track_id_map))
    return tracks


def enrich_tracks(
    tracks: list[Track],
    match_results: list[MatchResult],
) -> tuple[list[Track], list[Track]]:
    """Apply match results to tracks. Returns (enriched, unmatched) lists."""
    result_map = {r.source_track.fingerprint: r for r in match_results}

    enriched: list[Track] = []
    unmatched: list[Track] = []

    for track in tracks:
        result = result_map.get(track.fingerprint)
        if result and result.matched:
            enriched.append(apply_match_to_track(track, result))
        else:
            unmatched.append(track)

    matched_count = len(enriched)
    total = len(tracks)
    rate = (matched_count / total * 100) if total else 0
    logger.info(
        "Enrichment: %d/%d matched (%.1f%%), %d unmatched",
        matched_count, total, rate, len(unmatched),
    )

    return enriched, unmatched
