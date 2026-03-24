"""
Configuration and environment variable loading.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Project root = directory containing this package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
ENRICHED_CSV = DATA_DIR / "playlist_enriched.csv"
UNMATCHED_CSV = DATA_DIR / "unmatched.csv"
LOG_FILE = PROJECT_ROOT / "playlist_sync.log"

# Enriched CSV columns (original + enrichment)
ENRICHED_COLUMNS = [
    "title", "artist", "album", "isrc", "platform", "trackId",
    "duration", "addedDate", "addedBy", "url",
    "spotify_uri", "spotify_url", "spotify_duration_ms",
    "isrc_enriched", "explicit", "album_release_date",
    "danceability", "energy", "valence", "tempo",
    "key", "mode", "loudness", "speechiness",
    "acousticness", "instrumentalness", "liveness", "time_signature",
    "match_method", "match_confidence", "first_synced", "last_synced",
]

# Duration tolerance for track matching (seconds)
DURATION_TOLERANCE_SEC = 5

# Spotify API batch limits
SPOTIFY_PLAYLIST_BATCH = 100
SPOTIFY_SEARCH_LIMIT = 10

# Rate limiting (seconds between Spotify searches)
# Spotify Dev Mode has strict per-minute and daily quotas.
# For large playlists (1000+ tracks) 0.5s is too aggressive and triggers
# 24-hour bans. 1.5s (~40 req/min) is a safe conservative rate.
SEARCH_DELAY_SEC = 1.5

# Match cache file for resuming after rate limits
MATCH_CACHE = DATA_DIR / "match_cache.json"


def load_config() -> dict[str, str]:
    """Load .env and return config dict."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    config = {
        "SPOTIPY_CLIENT_ID": os.getenv("SPOTIPY_CLIENT_ID", ""),
        "SPOTIPY_CLIENT_SECRET": os.getenv("SPOTIPY_CLIENT_SECRET", ""),
        "SPOTIPY_REDIRECT_URI": os.getenv("SPOTIPY_REDIRECT_URI", "http://localhost:8888/callback"),
        "SPOTIFY_PLAYLIST_ID": os.getenv("SPOTIFY_PLAYLIST_ID", ""),
        "YTMUSIC_PLAYLIST_ID": os.getenv("YTMUSIC_PLAYLIST_ID", ""),
        "YTMUSIC_AUTH_FILE": os.getenv("YTMUSIC_AUTH_FILE", str(PROJECT_ROOT / "browser.json")),
        "SOURCE_CSV": os.getenv("SOURCE_CSV", ""),
    }
    return config


def get_source_csv(config: dict[str, str]) -> Path | None:
    """Get the source CSV path from config, or None if not set."""
    csv_path = config.get("SOURCE_CSV", "")
    if csv_path:
        p = Path(csv_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p
    return None


def require_spotify_config(config: dict[str, str]) -> None:
    """Validate that Spotify credentials are present."""
    missing = [
        k for k in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIFY_PLAYLIST_ID")
        if not config.get(k)
    ]
    if missing:
        print(f"Error: missing required env vars: {', '.join(missing)}")
        print(f"Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)


def require_ytmusic_config(config: dict[str, str]) -> None:
    """Validate that YT Music auth file exists."""
    auth_file = Path(config["YTMUSIC_AUTH_FILE"])
    if not auth_file.exists():
        print(f"Error: YT Music auth file not found: {auth_file}")
        print("Run: python playlist_sync.py setup-ytmusic")
        sys.exit(1)


def ensure_dirs() -> None:
    """Create data directories if they don't exist."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
