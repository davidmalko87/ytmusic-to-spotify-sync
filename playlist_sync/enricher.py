"""
Spotify metadata and audio features enrichment for matched tracks.
Author: David
Date: 2026-03-22
Version: 0.4.0

Enriches tracks with Spotify metadata (ISRC, release date, explicit,
popularity, artist genres), audio features (danceability, energy, etc.),
Last.fm data (play counts, listeners, genre/mood tags), and derived
classifications (primary_genre, mood) computed from the tag pool.

Includes skip-flags so dead Spotify endpoints (403) and empty Last.fm
artists are not retried on every sync run.
"""

from __future__ import annotations

import logging
from datetime import datetime

import spotipy

from playlist_sync.lastfm_client import (
    get_artists_info_batch,
    get_tracks_info_batch,
)
from playlist_sync.models import MatchResult, Track
from playlist_sync.spotify_client import (
    get_artists_batch,
    get_audio_features_batch,
    get_tracks_batch,
)

logger = logging.getLogger("playlist_sync")


# ── Genre and mood classification (no API calls) ─────────────────────
#
# These maps bucket the 700+ unique tags Last.fm uses into a smaller,
# usable set. Order matters: the first bucket whose keywords match wins
# `primary_genre`. Mood detection scans for any keyword and joins all
# matches.
#
# Keywords are matched as substrings against lower-cased tags (e.g.
# "deep house" matches the "electronic" bucket via "house").

GENRE_BUCKETS: list[tuple[str, set[str]]] = [
    # More specific buckets first so they win over broader fallbacks.
    # Soundtrack precedes classical because "orchestral" appears in both
    # contexts but if "soundtrack" or "score" is also present, that's
    # almost always the more useful label.
    ("soundtrack", {"soundtrack", "score", "ost", "film score", "video game music", "game soundtrack"}),
    ("classical", {"classical", "baroque", "orchestra", "orchestral", "opera", "symphony", "chamber music"}),
    ("jazz", {"jazz", "swing", "bebop", "neo-swing", "smooth jazz", "fusion"}),
    ("hip hop", {"hip hop", "hip-hop", "rap", "trap", "boom bap", "gangsta rap"}),
    ("metal", {"metal", "heavy metal", "death metal", "black metal", "metalcore", "doom metal", "thrash metal", "nu metal"}),
    ("punk", {"punk", "punk rock", "post-punk", "hardcore", "pop punk"}),
    ("country", {"country", "americana", "bluegrass"}),
    ("blues", {"blues", "delta blues", "rhythm and blues"}),
    ("reggae", {"reggae", "ska", "dub", "dancehall"}),
    ("folk", {"folk", "singer-songwriter", "acoustic"}),
    ("electronic", {
        "electronic", "electronica", "electro", "edm",
        "house", "deep house", "tech house", "progressive house", "electro house",
        "techno", "minimal", "trance", "psytrance", "uplifting trance",
        "dubstep", "drum and bass", "drum n bass", "dnb", "breakbeat",
        "synthwave", "synth", "synthpop", "vaporwave", "chillwave",
        "downtempo", "trip-hop", "trip hop", "idm",
        "dance", "club",
    }),
    ("ambient", {"ambient", "drone", "dark ambient", "new age", "atmospheric"}),
    ("rock", {
        "rock", "alternative rock", "indie rock", "classic rock",
        "hard rock", "soft rock", "psychedelic rock", "progressive rock",
        "post-rock", "shoegaze", "grunge", "garage rock",
    }),
    ("pop", {"pop", "indie pop", "synthpop", "electropop", "dream pop", "k-pop", "j-pop", "art pop"}),
    ("rnb", {"rnb", "r&b", "soul", "neo-soul", "contemporary r&b"}),
    ("indie", {"indie", "alternative", "lo-fi", "lofi"}),
    ("world", {"world", "world music", "latin", "afrobeat", "balkan"}),
]

# Mood keyword → display label. Substring matched against full tag pool.
MOOD_KEYWORDS: dict[str, list[str]] = {
    "chill":       ["chill", "chillout", "chillstep", "lounge", "easy listening", "relaxing", "calm", "mellow", "soothing"],
    "energetic":   ["energetic", "upbeat", "uplifting", "party", "high energy", "powerful"],
    "dark":        ["dark", "haunting", "sinister", "ominous"],
    "sad":         ["sad", "melancholic", "melancholy", "depressing", "sorrowful", "mournful"],
    "happy":       ["happy", "cheerful", "joyful", "feel good"],
    "epic":        ["epic", "heroic", "triumphant"],
    "romantic":    ["romantic", "love song", "sentimental"],
    "dreamy":      ["dreamy", "ethereal", "atmospheric"],
    "aggressive":  ["aggressive", "intense", "brutal", "heavy"],
    "nostalgic":   ["nostalgic", "nostalgia", "retro"],
    "cinematic":   ["cinematic", "epic score", "orchestral"],
    "ambient":     ["ambient"],   # also a genre — useful as a mood marker
    "instrumental":["instrumental"],
}


def _tokenize_tags(raw_pool: str) -> tuple[set[str], set[str]]:
    """Split a comma-joined tag string into both whole-tag and word-token sets.

    Returns (tag_set, token_set):
      - tag_set: each comma-separated tag as-is, lowercased.
        e.g. "deep house, post-rock" -> {"deep house", "post-rock"}
      - token_set: each word inside each tag.
        e.g. "deep house, post-rock" -> {"deep", "house", "post", "rock"}

    The token set lets multi-word tags ("deep house") match single-word
    bucket keywords ("house") without resorting to substring matching,
    which produced false hits like "post-rock" -> "ost".
    """
    raw_pool = raw_pool.lower()
    tag_set: set[str] = set()
    token_set: set[str] = set()
    for tag in raw_pool.split(","):
        t = tag.strip()
        if not t:
            continue
        tag_set.add(t)
        for word in t.replace("-", " ").replace("/", " ").split():
            w = word.strip()
            if w:
                token_set.add(w)
    return tag_set, token_set


def _bucket_matches(tag_set: set[str], token_set: set[str], keywords: set[str]) -> bool:
    """True if any keyword appears as a whole tag OR as a word in any tag."""
    return bool(tag_set & keywords) or bool(token_set & keywords)


def classify_track(track: Track) -> Track:
    """Compute primary_genre and mood from existing tag pool.

    Pure function — no API calls. Safe to run on every track on every
    sync. Sources tags from both `artist_tags` (denser) and `lastfm_tags`
    (track-level, sparser but sometimes has mood markers).
    """
    raw_pool = track.artist_tags + "," + track.lastfm_tags
    tag_set, token_set = _tokenize_tags(raw_pool)

    if not tag_set:
        return track

    # primary_genre: first matching bucket wins (buckets ordered by specificity)
    if not track.primary_genre:
        for genre_label, keywords in GENRE_BUCKETS:
            if _bucket_matches(tag_set, token_set, keywords):
                track.primary_genre = genre_label
                break

    # mood: collect all matching mood labels (multi-mood is fine)
    if not track.mood:
        moods: list[str] = []
        for mood_label, keywords in MOOD_KEYWORDS.items():
            kw_set = set(keywords)
            if _bucket_matches(tag_set, token_set, kw_set):
                moods.append(mood_label)
        track.mood = ", ".join(moods)

    return track


def classify_all_tracks(tracks: list[Track]) -> int:
    """Run classifier on every track. Returns count classified."""
    classified = 0
    for t in tracks:
        before_g = t.primary_genre
        before_m = t.mood
        classify_track(t)
        if t.primary_genre != before_g or t.mood != before_m:
            classified += 1
    logger.info("Classified %d/%d tracks (genre + mood)", classified, len(tracks))
    return classified


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
        if t.has_spotify_match
        and (not t.spotify_duration_ms or not t.isrc or not t.popularity)
        and not t.spotify_metadata_attempted
    ]

    if not needs_backfill:
        logger.info("All matched tracks already have Spotify metadata or were attempted")
        return tracks

    # Build {spotify_id: Track} map
    track_id_map: dict[str, Track] = {}
    for t in needs_backfill:
        sp_id = t.spotify_uri.split(":")[-1] if t.spotify_uri else ""
        if sp_id:
            track_id_map[sp_id] = t

    logger.info("Backfilling Spotify metadata for %d tracks...", len(track_id_map))
    sp_tracks = get_tracks_batch(sp, list(track_id_map.keys()))

    # Mark all attempted so we skip them on future runs even if /tracks 403'd
    for t in needs_backfill:
        t.spotify_metadata_attempted = True

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
        if t.has_spotify_match and not t.artist_genres and not t.spotify_genres_attempted
    ]

    if not needs_genres:
        logger.info("All matched tracks already have artist genres or were attempted")
        return tracks

    # Mark attempted up-front so a 403 lockout doesn't re-trigger next run
    for t in needs_genres:
        t.spotify_genres_attempted = True

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


def backfill_lastfm_artist_tags(
    api_key: str,
    tracks: list[Track],
) -> list[Track]:
    """Fetch and apply Last.fm artist-level tags.

    Track-level Last.fm tags are sparse (typically ~14% coverage on niche
    libraries) because tags are user-submitted per song. Artist-level tags
    are far denser (~85%+ coverage) because most artists have community
    genre tags even if individual tracks don't.

    Caches by unique first-artist name, so the API call count is roughly
    `unique_artists` rather than `tracks` (often a 4× speedup).

    Only fetches for tracks that don't already have artist_tags populated.
    """
    # Skip tracks we've already tried — even if they got no tags back.
    # This is the key efficiency win: ~150 obscure artists that returned
    # empty tag lists get re-queried on every sync without it.
    needs_artist_tags = [
        t for t in tracks
        if not t.artist_tags and t.artist and not t.lastfm_attempted
    ]

    if not needs_artist_tags:
        logger.info("All tracks already have artist tags or were attempted")
        return tracks

    # Map "first artist name" -> list of Track objects sharing that artist.
    # We split on comma and use the primary artist for the lookup, the same
    # way Last.fm's track endpoint does it.
    artist_to_tracks: dict[str, list[Track]] = {}
    for t in needs_artist_tags:
        first_artist = t.artist.split(",")[0].strip() if t.artist else ""
        if first_artist:
            artist_to_tracks.setdefault(first_artist, []).append(t)

    if not artist_to_tracks:
        logger.info("No artist names to look up")
        return tracks

    logger.info(
        "Fetching Last.fm artist tags for %d unique artists (covers %d tracks)...",
        len(artist_to_tracks), len(needs_artist_tags),
    )
    artist_data = get_artists_info_batch(api_key, list(artist_to_tracks.keys()))

    enriched_tracks = 0
    for artist_name, info in artist_data.items():
        tags = info.get("tags", [])
        # Take top 5 tags, comma-joined, lowercase for consistency
        tag_str = ", ".join(t.lower() for t in tags[:5]) if tags else ""
        for t in artist_to_tracks.get(artist_name, []):
            # Mark as attempted regardless of whether tags came back, so we
            # don't re-query empty artists on every future sync run
            t.lastfm_attempted = True
            if tag_str:
                t.artist_tags = tag_str
                if not t.tag_source:
                    t.tag_source = "lastfm_artist"
                enriched_tracks += 1

    # Also mark artists that didn't even respond (404 / network failure)
    # so we eventually stop hammering them. They can be retried by clearing
    # the lastfm_attempted column manually.
    for artist_name in artist_to_tracks:
        if artist_name not in artist_data:
            for t in artist_to_tracks[artist_name]:
                t.lastfm_attempted = True

    logger.info(
        "Applied Last.fm artist tags to %d/%d tracks (from %d/%d unique artists with tags)",
        enriched_tracks, len(needs_artist_tags),
        len([a for a, info in artist_data.items() if info.get("tags")]),
        len(artist_to_tracks),
    )
    return tracks
