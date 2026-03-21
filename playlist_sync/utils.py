"""
Utility functions: text normalization, logging setup.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata

from playlist_sync.config import LOG_FILE


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging to file + console."""
    logger = logging.getLogger("playlist_sync")
    logger.setLevel(logging.DEBUG)

    # Prevent duplicate handlers on repeat calls
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — always DEBUG
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler — INFO or DEBUG based on verbose
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def normalize_text(text: str) -> str:
    """Normalize track title/artist for search matching.

    - Decode HTML entities
    - Strip feat./ft. clauses
    - Remove parenthetical suffixes like (Radio Edit), (Remix)
    - Collapse whitespace
    - Lowercase
    """
    if not text:
        return ""

    text = html.unescape(text)
    text = unicodedata.normalize("NFC", text)

    # Strip feat./ft. clauses
    text = re.sub(r"\s*[\(\[]\s*(?:feat|ft)\.?\s+[^\)\]]+[\)\]]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:feat|ft)\.?\s+.+$", "", text, flags=re.IGNORECASE)

    # Strip common parenthetical suffixes for better search
    text = re.sub(
        r"\s*[\(\[]\s*(?:radio\s+edit|original\s+mix|official\s+(?:video|audio))\s*[\)\]]",
        "", text, flags=re.IGNORECASE,
    )

    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def normalize_for_search(title: str, artist: str) -> tuple[str, str]:
    """Return normalized (title, first_artist) for Spotify search."""
    norm_title = normalize_text(title)
    first_artist = artist.split(",")[0].strip() if artist else ""
    norm_artist = normalize_text(first_artist)
    return norm_title, norm_artist


def build_spotify_query(title: str, artist: str) -> str:
    """Build a Spotify search query string."""
    norm_title, norm_artist = normalize_for_search(title, artist)
    parts = []
    if norm_title:
        parts.append(f"track:{norm_title}")
    if norm_artist:
        parts.append(f"artist:{norm_artist}")
    return " ".join(parts)


def duration_close(dur_a_sec: float, dur_b_ms: int, tolerance_sec: float = 5.0) -> bool:
    """Check if two durations are within tolerance."""
    if dur_a_sec <= 0 or dur_b_ms <= 0:
        return True
    dur_b_sec = dur_b_ms / 1000.0
    return abs(dur_a_sec - dur_b_sec) <= tolerance_sec
