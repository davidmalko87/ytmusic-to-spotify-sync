"""
Snapshot-based diff engine for playlist state changes.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import logging

from playlist_sync.models import DiffResult, Track

logger = logging.getLogger("playlist_sync")


def diff_tracks(
    current: list[Track],
    previous: list[Track] | None,
) -> DiffResult:
    """Compare current playlist state against previous snapshot.

    On first run (no previous snapshot), all tracks are considered 'added'.
    """
    if previous is None:
        logger.info("No previous snapshot -- all %d tracks are new", len(current))
        return DiffResult(added=list(current), removed=[], unchanged=[])

    prev_map = {t.fingerprint: t for t in previous}
    curr_map = {t.fingerprint: t for t in current}

    prev_keys = set(prev_map.keys())
    curr_keys = set(curr_map.keys())

    return DiffResult(
        added=[curr_map[k] for k in curr_keys - prev_keys],
        removed=[prev_map[k] for k in prev_keys - curr_keys],
        unchanged=[curr_map[k] for k in curr_keys & prev_keys],
    )
