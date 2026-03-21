"""
CSV I/O for playlist data files (BOM-aware).
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from playlist_sync.config import ENRICHED_COLUMNS, ENRICHED_CSV, UNMATCHED_CSV
from playlist_sync.models import Track

logger = logging.getLogger("playlist_sync")


def read_source_csv(path: Path) -> list[Track]:
    """Read a playlist CSV export into Track objects."""
    logger.info("Reading source CSV: %s", path)

    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    tracks: list[Track] = []
    for _, row in df.iterrows():
        tracks.append(Track.from_csv_row(row.to_dict()))

    logger.info("Loaded %d tracks from source CSV", len(tracks))
    return tracks


def read_enriched_csv(path: Path | None = None) -> list[Track]:
    """Read the enriched CSV (if it exists) into Track objects."""
    path = path or ENRICHED_CSV
    if not path.exists():
        logger.debug("Enriched CSV not found: %s", path)
        return []

    logger.info("Reading enriched CSV: %s", path)
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    tracks: list[Track] = []
    for _, row in df.iterrows():
        tracks.append(Track.from_csv_row(row.to_dict()))

    logger.info("Loaded %d tracks from enriched CSV", len(tracks))
    return tracks


def write_enriched_csv(tracks: list[Track], path: Path | None = None) -> None:
    """Write tracks to the enriched CSV."""
    path = path or ENRICHED_CSV
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = [t.to_csv_row() for t in tracks]
    df = pd.DataFrame(rows, columns=ENRICHED_COLUMNS)
    df.to_csv(path, index=False, encoding="utf-8-sig")

    logger.info("Wrote %d tracks to enriched CSV: %s", len(tracks), path)


def write_unmatched_csv(tracks: list[Track], path: Path | None = None) -> None:
    """Write unmatched tracks to a separate CSV for manual review."""
    path = path or UNMATCHED_CSV
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        {
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "trackId": t.track_id,
            "url": t.url,
            "platform": t.platform,
        }
        for t in tracks
    ]
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, encoding="utf-8-sig")

    logger.info("Wrote %d unmatched tracks to: %s", len(tracks), path)
