"""
Spotify metadata enrichment for matched tracks.
Author: David
Date: 2026-03-22
Version: 0.1.0

Note: Spotify audio_features API was deprecated in Feb 2026.
Enrichment uses available metadata: ISRC, release date, explicit flag.
"""

from __future__ import annotations

import logging
from datetime import datetime

from playlist_sync.models import MatchResult, Track

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
