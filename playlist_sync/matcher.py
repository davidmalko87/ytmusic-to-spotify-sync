"""
Multi-pass track matcher: YT Music -> Spotify.
Author: David
Date: 2026-03-22
Version: 0.1.0

Matching strategy (in order of reliability):
  1. ISRC match -- search Spotify by ISRC code
  2. Title + Artist -- normalized search with duration validation
  3. Relaxed -- title-only search, best fuzzy match on artist
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

import spotipy

from playlist_sync.config import DURATION_TOLERANCE_SEC
from playlist_sync.models import MatchResult, Track
from playlist_sync.spotify_client import search_by_isrc, search_track
from playlist_sync.utils import (
    build_spotify_query,
    duration_close,
    normalize_text,
)

logger = logging.getLogger("playlist_sync")


def _similarity(a: str, b: str) -> float:
    """Fuzzy string similarity (0.0-1.0)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _extract_spotify_match_data(sp_track: dict) -> dict:
    """Extract relevant fields from a Spotify track result.

    Search results already contain full track data including popularity,
    album info, and artist IDs — no extra API calls needed.
    """
    external_ids = sp_track.get("external_ids", {})
    album = sp_track.get("album", {})
    artists = sp_track.get("artists", [])
    # Artist genres are NOT in search results (need /artists endpoint),
    # but artist IDs are — store the primary artist ID for later.
    primary_artist_id = artists[0].get("id", "") if artists else ""
    return {
        "spotify_uri": sp_track.get("uri", ""),
        "spotify_url": sp_track.get("external_urls", {}).get("spotify", ""),
        "spotify_duration_ms": sp_track.get("duration_ms", 0),
        "isrc": external_ids.get("isrc", ""),
        "explicit": sp_track.get("explicit", False),
        "album_release_date": album.get("release_date", ""),
        "popularity": sp_track.get("popularity", 0),
        "album_type": album.get("album_type", ""),
        "track_number": sp_track.get("track_number", 0),
        "primary_artist_id": primary_artist_id,
    }


def _score_candidate(track: Track, candidate: dict) -> float:
    """Score a Spotify candidate against a source track (0.0-1.0)."""
    sp_title = candidate.get("name", "")
    sp_artists = ", ".join(a.get("name", "") for a in candidate.get("artists", []))

    title_sim = _similarity(normalize_text(track.title), normalize_text(sp_title))
    artist_sim = _similarity(normalize_text(track.artist), normalize_text(sp_artists))

    dur_match = 1.0 if duration_close(
        track.duration, candidate.get("duration_ms", 0), DURATION_TOLERANCE_SEC
    ) else 0.5

    # Weighted: title matters most, then artist, then duration
    return (title_sim * 0.45) + (artist_sim * 0.40) + (dur_match * 0.15)


def match_by_isrc(sp: spotipy.Spotify, track: Track) -> MatchResult | None:
    """Pass 1: Match by ISRC code if available."""
    if not track.isrc:
        return None

    results = search_by_isrc(sp, track.isrc)
    if not results:
        return None

    data = _extract_spotify_match_data(results[0])
    confidence = _score_candidate(track, results[0])

    logger.debug("ISRC match for '%s': %s (%.2f)", track.title, data["spotify_uri"], confidence)
    return MatchResult(
        source_track=track,
        method="isrc",
        confidence=min(max(confidence, 0.95), 1.0),
        **data,
    )


def match_by_title_artist(sp: spotipy.Spotify, track: Track) -> MatchResult | None:
    """Pass 2: Search by normalized title + artist, validate duration."""
    query = build_spotify_query(track.title, track.artist)
    if not query:
        return None

    results = search_track(sp, query)
    if not results:
        return None

    best_score = 0.0
    best_candidate = None

    for candidate in results:
        score = _score_candidate(track, candidate)
        dur_ok = duration_close(
            track.duration, candidate.get("duration_ms", 0), DURATION_TOLERANCE_SEC
        )
        effective_score = min(score + (0.1 if dur_ok else 0.0), 1.0)

        if effective_score > best_score:
            best_score = effective_score
            best_candidate = candidate

    if best_candidate and best_score >= 0.5:
        data = _extract_spotify_match_data(best_candidate)
        logger.debug(
            "Title+Artist match for '%s': %s (%.2f)",
            track.title, data["spotify_uri"], best_score,
        )
        return MatchResult(
            source_track=track,
            method="title_artist",
            confidence=best_score,
            **data,
        )

    return None


def match_relaxed(sp: spotipy.Spotify, track: Track) -> MatchResult | None:
    """Pass 3: Title-only search, fuzzy match on artist."""
    norm_title = normalize_text(track.title)
    if not norm_title:
        return None

    results = search_track(sp, f"track:{norm_title}")
    if not results:
        return None

    best_score = 0.0
    best_candidate = None

    for candidate in results:
        score = _score_candidate(track, candidate)
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_candidate and best_score >= 0.4:
        data = _extract_spotify_match_data(best_candidate)
        logger.debug(
            "Relaxed match for '%s': %s (%.2f)",
            track.title, data["spotify_uri"], best_score,
        )
        return MatchResult(
            source_track=track,
            method="relaxed",
            confidence=best_score,
            **data,
        )

    return None


def match_track(sp: spotipy.Spotify, track: Track) -> MatchResult:
    """Run all matching passes for a single track."""
    # Pass 1: ISRC
    result = match_by_isrc(sp, track)
    if result and result.matched:
        return result

    # Pass 2: Title + Artist
    result = match_by_title_artist(sp, track)
    if result and result.matched:
        return result

    # Pass 3: Relaxed
    result = match_relaxed(sp, track)
    if result and result.matched:
        return result

    logger.debug("No match found for: '%s' by '%s'", track.title, track.artist)
    return MatchResult(
        source_track=track,
        error="No Spotify match found after all passes",
    )
