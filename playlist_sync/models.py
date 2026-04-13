"""
Track data models for Playlist Sync.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Track:
    """Represents a single music track across platforms."""

    title: str
    artist: str
    album: str = ""
    isrc: str = ""
    platform: str = ""
    track_id: str = ""
    duration: float = 0.0
    added_date: str = ""
    added_by: str = ""
    url: str = ""

    # Spotify enrichment fields
    spotify_uri: str = ""
    spotify_url: str = ""
    spotify_duration_ms: int = 0
    explicit: bool = False
    album_release_date: str = ""

    # Audio features (from Spotify audio_features API)
    danceability: float = 0.0
    energy: float = 0.0
    valence: float = 0.0
    tempo: float = 0.0
    key: int = -1
    mode: int = -1
    loudness: float = 0.0
    speechiness: float = 0.0
    acousticness: float = 0.0
    instrumentalness: float = 0.0
    liveness: float = 0.0
    time_signature: int = 0
    audio_features_fetched: bool = False

    # Additional Spotify enrichment (from /tracks and /artists endpoints)
    popularity: int = 0
    artist_genres: str = ""
    album_type: str = ""
    track_number: int = 0

    # Last.fm enrichment
    lastfm_playcount: int = 0
    lastfm_listeners: int = 0
    lastfm_tags: str = ""

    # Matching metadata
    match_method: str = ""
    match_confidence: float = 0.0
    first_synced: str = ""
    last_synced: str = ""

    @property
    def has_spotify_match(self) -> bool:
        return bool(self.spotify_uri)

    @property
    def fingerprint(self) -> str:
        """Unique-ish key for deduplication and diffing."""
        if self.track_id:
            return f"{self.platform}:{self.track_id}"
        return f"{self.platform}:{self.title}:{self.artist}".lower()

    def to_csv_row(self) -> dict[str, str]:
        """Serialize to a flat dict for CSV output."""
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "isrc": self.isrc,
            "platform": self.platform,
            "trackId": self.track_id,
            "duration": str(self.duration) if self.duration else "",
            "addedDate": self.added_date,
            "addedBy": self.added_by,
            "url": self.url,
            "spotify_uri": self.spotify_uri,
            "spotify_url": self.spotify_url,
            "spotify_duration_ms": str(self.spotify_duration_ms) if self.spotify_duration_ms else "",
            "isrc_enriched": self.isrc if self.isrc else "",
            "explicit": str(self.explicit).lower() if self.has_spotify_match else "",
            "album_release_date": self.album_release_date,
            "danceability": f"{self.danceability:.4f}" if self.audio_features_fetched else "",
            "energy": f"{self.energy:.4f}" if self.audio_features_fetched else "",
            "valence": f"{self.valence:.4f}" if self.audio_features_fetched else "",
            "tempo": f"{self.tempo:.1f}" if self.audio_features_fetched else "",
            "key": str(self.key) if self.audio_features_fetched and self.key >= 0 else "",
            "mode": str(self.mode) if self.audio_features_fetched and self.mode >= 0 else "",
            "loudness": f"{self.loudness:.2f}" if self.audio_features_fetched else "",
            "speechiness": f"{self.speechiness:.4f}" if self.audio_features_fetched else "",
            "acousticness": f"{self.acousticness:.4f}" if self.audio_features_fetched else "",
            "instrumentalness": f"{self.instrumentalness:.4f}" if self.audio_features_fetched else "",
            "liveness": f"{self.liveness:.4f}" if self.audio_features_fetched else "",
            "time_signature": str(self.time_signature) if self.audio_features_fetched else "",
            "audio_features_fetched": "true" if self.audio_features_fetched else "",
            "popularity": str(self.popularity) if self.popularity else "",
            "artist_genres": self.artist_genres,
            "album_type": self.album_type,
            "track_number": str(self.track_number) if self.track_number else "",
            "lastfm_playcount": str(self.lastfm_playcount) if self.lastfm_playcount else "",
            "lastfm_listeners": str(self.lastfm_listeners) if self.lastfm_listeners else "",
            "lastfm_tags": self.lastfm_tags,
            "match_method": self.match_method,
            "match_confidence": f"{self.match_confidence:.2f}" if self.match_confidence else "",
            "first_synced": self.first_synced,
            "last_synced": self.last_synced,
        }

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> Track:
        """Deserialize from a CSV row dict."""
        return cls(
            title=row.get("title", ""),
            artist=row.get("artist", ""),
            album=row.get("album", ""),
            isrc=row.get("isrc", "") or row.get("isrc_enriched", ""),
            platform=row.get("platform", ""),
            track_id=row.get("trackId", ""),
            duration=float(row["duration"]) if row.get("duration") else 0.0,
            added_date=row.get("addedDate", ""),
            added_by=row.get("addedBy", ""),
            url=row.get("url", ""),
            spotify_uri=row.get("spotify_uri", ""),
            spotify_url=row.get("spotify_url", ""),
            spotify_duration_ms=int(row["spotify_duration_ms"]) if row.get("spotify_duration_ms") else 0,
            explicit=row.get("explicit", "").lower() == "true",
            album_release_date=row.get("album_release_date", ""),
            danceability=float(row["danceability"]) if row.get("danceability") else 0.0,
            energy=float(row["energy"]) if row.get("energy") else 0.0,
            valence=float(row["valence"]) if row.get("valence") else 0.0,
            tempo=float(row["tempo"]) if row.get("tempo") else 0.0,
            key=int(row["key"]) if row.get("key") else -1,
            mode=int(row["mode"]) if row.get("mode") else -1,
            loudness=float(row["loudness"]) if row.get("loudness") else 0.0,
            speechiness=float(row["speechiness"]) if row.get("speechiness") else 0.0,
            acousticness=float(row["acousticness"]) if row.get("acousticness") else 0.0,
            instrumentalness=float(row["instrumentalness"]) if row.get("instrumentalness") else 0.0,
            liveness=float(row["liveness"]) if row.get("liveness") else 0.0,
            time_signature=int(row["time_signature"]) if row.get("time_signature") else 0,
            audio_features_fetched=row.get("audio_features_fetched", "").lower() == "true",
            popularity=int(row["popularity"]) if row.get("popularity") else 0,
            artist_genres=row.get("artist_genres", ""),
            album_type=row.get("album_type", ""),
            track_number=int(row["track_number"]) if row.get("track_number") else 0,
            lastfm_playcount=int(row["lastfm_playcount"]) if row.get("lastfm_playcount") else 0,
            lastfm_listeners=int(row["lastfm_listeners"]) if row.get("lastfm_listeners") else 0,
            lastfm_tags=row.get("lastfm_tags", ""),
            match_method=row.get("match_method", ""),
            match_confidence=float(row["match_confidence"]) if row.get("match_confidence") else 0.0,
            first_synced=row.get("first_synced", ""),
            last_synced=row.get("last_synced", ""),
        )


@dataclass
class MatchResult:
    """Result of attempting to match a YTM track to Spotify."""

    source_track: Track
    spotify_uri: str = ""
    spotify_url: str = ""
    spotify_duration_ms: int = 0
    isrc: str = ""
    explicit: bool = False
    album_release_date: str = ""
    popularity: int = 0
    album_type: str = ""
    track_number: int = 0
    primary_artist_id: str = ""
    method: str = ""
    confidence: float = 0.0
    error: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.spotify_uri)


@dataclass
class DiffResult:
    """Result of diffing two playlist states."""

    added: list[Track] = field(default_factory=list)
    removed: list[Track] = field(default_factory=list)
    unchanged: list[Track] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"+{len(self.added)} added")
        if self.removed:
            parts.append(f"-{len(self.removed)} removed")
        parts.append(f"{len(self.unchanged)} unchanged")
        return ", ".join(parts)
