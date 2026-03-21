"""
Track data models for Playlist Sync.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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
