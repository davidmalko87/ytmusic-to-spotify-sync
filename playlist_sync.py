#!/usr/bin/env python3
"""
Playlist Sync -- YT Music -> Spotify playlist sync with enrichment.
Author: David
Date: 2026-03-22
Version: 0.1.0

Usage:
    python playlist_sync.py                     # Interactive menu
    python playlist_sync.py setup-ytmusic       # Browser auth setup
    python playlist_sync.py import-csv          # Bootstrap from CSV
    python playlist_sync.py snapshot            # Snapshot YTM playlist
    python playlist_sync.py diff                # Show changes
    python playlist_sync.py sync                # Full sync (live API)
    python playlist_sync.py sync --from-csv     # Sync from CSV export
    python playlist_sync.py status              # Show stats
    python playlist_sync.py retry-unmatched     # Retry failed matches
"""

from playlist_sync.cli import main

if __name__ == "__main__":
    main()
