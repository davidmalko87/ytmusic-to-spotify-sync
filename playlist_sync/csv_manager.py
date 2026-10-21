"""
CSV I/O for playlist data files (BOM-aware), plus JSON copies of the enriched data.
Author: David
Date: 2026-03-22
Version: 0.8.0
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from playlist_sync.config import (
    ENRICHED_COLUMNS,
    ENRICHED_CSV,
    SKIPPED_CSV,
    UNMATCHED_CSV,
)
from playlist_sync.models import Track

logger = logging.getLogger("playlist_sync")


class CsvFormatError(ValueError):
    """A CSV cell the tool cannot read; the message names the file, row and value."""


def _tracks_from_csv(path: Path) -> list[Track]:
    """Parse every row; a bad cell becomes a CsvFormatError that names its row."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    tracks: list[Track] = []
    # Spreadsheet numbering: row 1 is the header, so the first track is row 2
    for row_number, (_, row) in enumerate(df.iterrows(), start=2):
        try:
            tracks.append(Track.from_csv_row(row.to_dict()))
        except ValueError as e:
            message = f"{path.name}, row {row_number}: {e}"
            logger.debug("Cannot read %s: %s", path, message)  # the caller prints it
            raise CsvFormatError(message) from e
    return tracks


def read_source_csv(path: Path) -> list[Track]:
    """Read a playlist CSV export into Track objects (CsvFormatError on a bad cell)."""
    logger.info("Reading source CSV: %s", path)

    tracks = _tracks_from_csv(path)

    logger.info("Loaded %d tracks from source CSV", len(tracks))
    return tracks


def read_enriched_csv(path: Path | None = None) -> list[Track]:
    """Read the enriched CSV (if it exists) into Track objects."""
    path = path or ENRICHED_CSV
    if not path.exists():
        logger.debug("Enriched CSV not found: %s", path)
        return []

    logger.info("Reading enriched CSV: %s", path)
    tracks = _tracks_from_csv(path)

    logger.info("Loaded %d tracks from enriched CSV", len(tracks))
    return tracks


def _safe_csv_write(df: pd.DataFrame, path: Path) -> None:
    """Write DataFrame to CSV with fallback on PermissionError (e.g. file open in Excel)."""
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
    except PermissionError:
        fallback = path.with_stem(f"{path.stem}_{datetime.now().strftime('%H%M%S')}")
        df.to_csv(fallback, index=False, encoding="utf-8-sig")
        logger.warning("Could not write to %s (file locked). Saved to: %s", path, fallback)
        print(f"WARNING: {path.name} is locked (open in Excel?). Saved to: {fallback.name}")


def write_tracks_json(tracks: list[Track], path: Path) -> Path:
    """Write tracks as {exported_at, track_count, tracks: [...]} JSON.

    Same fields as the CSV, but typed: numbers, true/false, and null for empty cells.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "track_count": len(tracks),
        "tracks": [t.to_json_dict() for t in tracks],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_enriched_csv(tracks: list[Track], path: Path | None = None) -> None:
    """Write tracks to the enriched CSV and a JSON copy beside it (same name, .json)."""
    path = path or ENRICHED_CSV
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = [t.to_csv_row() for t in tracks]
    df = pd.DataFrame(rows, columns=ENRICHED_COLUMNS)
    _safe_csv_write(df, path)
    logger.info("Wrote %d tracks to enriched CSV: %s", len(tracks), path)

    # The JSON copy is a convenience: a failure must not lose the sync result
    json_path = path.with_suffix(".json")
    try:
        write_tracks_json(tracks, json_path)
        logger.info("Wrote JSON copy: %s", json_path)
    except OSError as e:
        logger.warning("Could not write JSON copy %s: %s", json_path, e)
        print(f"WARNING: could not write {json_path.name} ({e}); the CSV is up to date.")


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
    _safe_csv_write(df, path)

    logger.info("Wrote %d unmatched tracks to: %s", len(tracks), path)


def write_skipped_csv(tracks: list[Track], path: Path | None = None) -> None:
    """Write tracks that were filtered out before matching (skip_reason set).

    These are tracks the matcher decided not to send to Spotify search at
    all (e.g. YT Music tracks missing album metadata, typically YouTube
    uploads that wouldn't have a real Spotify equivalent).
    """
    path = path or SKIPPED_CSV
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        {
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "trackId": t.track_id,
            "url": t.url,
            "platform": t.platform,
            "skip_reason": t.skip_reason,
        }
        for t in tracks
    ]
    df = pd.DataFrame(rows)
    _safe_csv_write(df, path)

    logger.info("Wrote %d skipped tracks to: %s", len(tracks), path)
