"""
CLI interface with argparse subcommands and interactive menu.
Author: David
Date: 2026-03-22
Version: 0.1.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tqdm import tqdm

from playlist_sync.config import (
    ENRICHED_CSV,
    MATCH_CACHE,
    SNAPSHOTS_DIR,
    UNMATCHED_CSV,
    ensure_dirs,
    get_source_csv,
    load_config,
    require_spotify_config,
    require_ytmusic_config,
)
from playlist_sync.csv_manager import (
    read_enriched_csv,
    read_source_csv,
    write_enriched_csv,
    write_unmatched_csv,
)
from playlist_sync.differ import diff_tracks
from playlist_sync.enricher import apply_match_to_track, enrich_with_audio_features
from playlist_sync.matcher import match_track
from playlist_sync.models import Track
from playlist_sync.spotify_client import (
    RateLimitError,
    add_tracks_to_playlist,
    get_spotify_client,
    remove_tracks_from_playlist,
)
from playlist_sync.utils import setup_logging
from playlist_sync.ytmusic_client import (
    fetch_playlist_tracks,
    load_latest_snapshot,
    save_snapshot,
    setup_browser_auth,
)


# ── Interactive menu ────────────────────────────────────────────────

MENU_OPTIONS = [
    ("1", "Setup YT Music auth",     "setup-ytmusic"),
    ("2", "Import from CSV",         "import-csv"),
    ("3", "Snapshot YT Music playlist", "snapshot"),
    ("4", "Show diff (changes)",     "diff"),
    ("5", "Full sync to Spotify",    "sync"),
    ("6", "Sync from CSV file",      "sync-csv"),
    ("7", "Retry unmatched tracks",  "retry-unmatched"),
    ("8", "Show status",             "status"),
    ("0", "Exit",                    "exit"),
]


def interactive_menu() -> None:
    """Show an interactive menu when no command is given."""
    print()
    print("=" * 50)
    print("  Playlist Sync: YT Music -> Spotify")
    print("=" * 50)
    print()

    for key, label, _ in MENU_OPTIONS:
        print(f"  [{key}] {label}")

    print()

    try:
        choice = input("Select an option: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    # Map choice to command
    cmd = None
    for key, _, command in MENU_OPTIONS:
        if choice == key:
            cmd = command
            break

    if cmd is None:
        print(f"Unknown option: {choice}")
        return
    if cmd == "exit":
        return

    # Ask about dry-run for commands that support it
    dry_run = False
    if cmd in ("import-csv", "snapshot", "sync", "sync-csv", "retry-unmatched"):
        try:
            dr = input("Dry run? (y/N): ").strip().lower()
            dry_run = dr in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
            return

    # Build a fake namespace and dispatch
    args = argparse.Namespace(
        command=cmd,
        verbose=False,
        dry_run=dry_run,
        csv=None,
        from_csv=None,
    )

    if cmd == "sync-csv":
        args.command = "sync"
        args.from_csv = "default"

    dispatch(args)


# ── Match cache for resume after rate limits ────────────────────────

def _load_match_cache() -> dict[str, dict]:
    """Load cached match results from previous interrupted runs."""
    import json
    if MATCH_CACHE.exists():
        try:
            data = json.loads(MATCH_CACHE.read_text(encoding="utf-8"))
            print(f"Resuming: loaded {len(data)} cached matches from previous run")
            return data
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


def _save_match_cache(cache: dict[str, dict]) -> None:
    """Save match results so we can resume after rate limits."""
    import json
    ensure_dirs()
    MATCH_CACHE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _clear_match_cache() -> None:
    """Remove match cache after successful sync."""
    if MATCH_CACHE.exists():
        MATCH_CACHE.unlink()


# ── Command handlers ────────────────────────────────────────────────

def cmd_setup_ytmusic(args: argparse.Namespace) -> None:
    """Interactive YT Music browser auth setup."""
    config = load_config()
    output = config["YTMUSIC_AUTH_FILE"]
    setup_browser_auth(output)


def cmd_import_csv(args: argparse.Namespace) -> None:
    """Bootstrap from existing playlist CSV export."""
    log = setup_logging(args.verbose)
    config = load_config()
    ensure_dirs()

    csv_path = Path(args.csv) if args.csv else get_source_csv(config)
    if csv_path is None:
        print("Error: no source CSV configured.")
        print("Set SOURCE_CSV in .env or pass --csv <path>")
        sys.exit(1)
    if not csv_path.exists():
        print(f"Error: CSV not found: {csv_path}")
        sys.exit(1)

    tracks = read_source_csv(csv_path)
    print(f"Loaded {len(tracks)} tracks from {csv_path.name}")

    if args.dry_run:
        print("[DRY RUN] Would save snapshot and prepare for sync")
        for t in tracks[:5]:
            print(f"  {t.title} -- {t.artist}")
        if len(tracks) > 5:
            print(f"  ... and {len(tracks) - 5} more")
        return

    snapshot_path = save_snapshot(tracks)
    print(f"Snapshot saved: {snapshot_path.name}")

    write_enriched_csv(tracks)
    print(f"Initial enriched CSV written: {ENRICHED_CSV}")
    print("\nNext: set up .env credentials, then run 'sync' to match tracks to Spotify")


def cmd_snapshot(args: argparse.Namespace) -> None:
    """Snapshot current YT Music playlist state via API."""
    log = setup_logging(args.verbose)
    config = load_config()
    require_ytmusic_config(config)
    ensure_dirs()

    tracks = fetch_playlist_tracks(config["YTMUSIC_AUTH_FILE"], config["YTMUSIC_PLAYLIST_ID"])
    print(f"Fetched {len(tracks)} tracks from YT Music")

    if args.dry_run:
        print("[DRY RUN] Would save snapshot")
        return

    snapshot_path = save_snapshot(tracks)
    print(f"Snapshot saved: {snapshot_path.name}")


def cmd_diff(args: argparse.Namespace) -> None:
    """Show changes since last snapshot."""
    log = setup_logging(args.verbose)
    config = load_config()
    require_ytmusic_config(config)

    print("Fetching current YT Music playlist...")
    current = fetch_playlist_tracks(config["YTMUSIC_AUTH_FILE"], config["YTMUSIC_PLAYLIST_ID"])
    previous = load_latest_snapshot()

    result = diff_tracks(current, previous)

    if not result.has_changes:
        print("No changes since last snapshot.")
        return

    print(f"\nDiff: {result.summary()}")

    if result.added:
        print(f"\n+++ Added ({len(result.added)}):")
        for t in result.added[:20]:
            print(f"  + {t.title} -- {t.artist}")
        if len(result.added) > 20:
            print(f"  ... and {len(result.added) - 20} more")

    if result.removed:
        print(f"\n--- Removed ({len(result.removed)}):")
        for t in result.removed[:20]:
            print(f"  - {t.title} -- {t.artist}")
        if len(result.removed) > 20:
            print(f"  ... and {len(result.removed) - 20} more")


def cmd_sync(args: argparse.Namespace) -> None:
    """Full sync: diff -> match -> push to Spotify + enrich."""
    log = setup_logging(args.verbose)
    config = load_config()
    require_spotify_config(config)
    ensure_dirs()

    # Step 1: Get current tracks
    if args.from_csv:
        csv_path = Path(args.from_csv) if args.from_csv != "default" else get_source_csv(config)
        if csv_path is None:
            print("Error: no source CSV configured. Set SOURCE_CSV in .env")
            sys.exit(1)
        current = read_source_csv(csv_path)
        print(f"Loaded {len(current)} tracks from CSV")
    else:
        require_ytmusic_config(config)
        print("Fetching current YT Music playlist...")
        current = fetch_playlist_tracks(config["YTMUSIC_AUTH_FILE"], config["YTMUSIC_PLAYLIST_ID"])
        print(f"Fetched {len(current)} tracks")

    # Step 2: Load existing enriched data to preserve previous matches
    existing = read_enriched_csv()
    existing_map: dict[str, Track] = {t.fingerprint: t for t in existing}

    # Step 3: Diff
    previous = load_latest_snapshot()
    diff = diff_tracks(current, previous)
    print(f"Diff: {diff.summary()}")

    # Step 4: Separate already-matched from needs-matching
    needs_matching: list[Track] = []
    already_matched: list[Track] = []

    for track in current:
        fp = track.fingerprint
        if fp in existing_map and existing_map[fp].has_spotify_match:
            enriched = existing_map[fp]
            enriched.last_synced = track.last_synced or enriched.last_synced
            already_matched.append(enriched)
        else:
            needs_matching.append(track)

    print(f"Already matched: {len(already_matched)}, needs matching: {len(needs_matching)}")

    if not needs_matching and not diff.removed:
        print("Nothing to do.")
        if not args.dry_run:
            save_snapshot(current)
        return

    # Step 5: Match new tracks (with resume support)
    sp = get_spotify_client(config)
    matched_results = []
    unmatched_tracks: list[Track] = []
    match_cache = _load_match_cache()

    if needs_matching:
        # Skip tracks already in cache from previous interrupted run
        to_search: list[Track] = []
        for track in needs_matching:
            fp = track.fingerprint
            if fp in match_cache:
                cached = match_cache[fp]
                if cached.get("matched"):
                    from playlist_sync.models import MatchResult
                    result = MatchResult(
                        source_track=track,
                        spotify_uri=cached["spotify_uri"],
                        spotify_url=cached.get("spotify_url", ""),
                        method=cached.get("method", "cached"),
                        confidence=cached.get("confidence", 0.0),
                    )
                    enriched_track = apply_match_to_track(track, result)
                    already_matched.append(enriched_track)
                    matched_results.append(result)
                else:
                    unmatched_tracks.append(track)
            else:
                to_search.append(track)

        if to_search:
            cached_count = len(needs_matching) - len(to_search)
            if cached_count > 0:
                print(f"Restored {cached_count} matches from cache")
            print(f"\nMatching {len(to_search)} tracks to Spotify...")

            try:
                for track in tqdm(to_search, desc="Matching", unit="track"):
                    result = match_track(sp, track)
                    fp = track.fingerprint
                    if result.matched:
                        enriched_track = apply_match_to_track(track, result)
                        already_matched.append(enriched_track)
                        matched_results.append(result)
                        match_cache[fp] = {
                            "matched": True,
                            "spotify_uri": result.spotify_uri,
                            "spotify_url": result.spotify_url,
                            "method": result.method,
                            "confidence": result.confidence,
                        }
                    else:
                        unmatched_tracks.append(track)
                        match_cache[fp] = {"matched": False}

                    # Save cache every 50 tracks for safety
                    if len(match_cache) % 50 == 0:
                        _save_match_cache(match_cache)

            except RateLimitError as e:
                _save_match_cache(match_cache)
                matched_so_far = len(matched_results)
                remaining = len(to_search) - matched_so_far
                hrs = e.retry_after / 3600
                print(f"\n\nRate limited! Progress saved ({matched_so_far} matched).")
                print(f"Remaining: {remaining} tracks")
                print(f"Wait ~{hrs:.1f}h then re-run: python playlist_sync.py sync")
                print("Your progress will be restored automatically.")
                return

        print(f"Matched: {len(matched_results)}, Unmatched: {len(unmatched_tracks)}")

    # Step 6: Push to Spotify
    new_uris = [r.spotify_uri for r in matched_results]
    if new_uris:
        added = add_tracks_to_playlist(
            sp, config["SPOTIFY_PLAYLIST_ID"], new_uris, dry_run=args.dry_run,
        )
        action = "[DRY RUN] Would add" if args.dry_run else "Added"
        print(f"{action} {added} tracks to Spotify playlist")

    if diff.removed:
        remove_uris = []
        for t in diff.removed:
            fp = t.fingerprint
            if fp in existing_map and existing_map[fp].spotify_uri:
                remove_uris.append(existing_map[fp].spotify_uri)
        if remove_uris:
            removed = remove_tracks_from_playlist(
                sp, config["SPOTIFY_PLAYLIST_ID"], remove_uris, dry_run=args.dry_run,
            )
            action = "[DRY RUN] Would remove" if args.dry_run else "Removed"
            print(f"{action} {removed} tracks from Spotify playlist")

    # Step 7: Fetch audio features for newly matched tracks
    if already_matched and not args.dry_run:
        print("\nFetching audio features...")
        enrich_with_audio_features(sp, already_matched)

    # Step 8: Save outputs
    if not args.dry_run:
        all_tracks = already_matched + unmatched_tracks
        write_enriched_csv(all_tracks)
        print(f"Enriched CSV updated: {ENRICHED_CSV}")

        if unmatched_tracks:
            write_unmatched_csv(unmatched_tracks)
            print(f"Unmatched tracks saved: {UNMATCHED_CSV}")

        save_snapshot(current)
        print("Snapshot updated.")

    _clear_match_cache()
    print("\nSync complete!")


def cmd_retry_unmatched(args: argparse.Namespace) -> None:
    """Re-attempt matching for previously unmatched tracks."""
    log = setup_logging(args.verbose)
    config = load_config()
    require_spotify_config(config)

    if not UNMATCHED_CSV.exists():
        print("No unmatched.csv found -- nothing to retry.")
        return

    import pandas as pd
    df = pd.read_csv(UNMATCHED_CSV, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    tracks = [Track.from_csv_row(row.to_dict()) for _, row in df.iterrows()]
    print(f"Loaded {len(tracks)} unmatched tracks")

    sp = get_spotify_client(config)
    newly_matched: list[Track] = []
    still_unmatched: list[Track] = []

    for track in tqdm(tracks, desc="Retrying", unit="track"):
        result = match_track(sp, track)
        if result.matched:
            enriched = apply_match_to_track(track, result)
            newly_matched.append(enriched)
        else:
            still_unmatched.append(track)

    print(f"Newly matched: {len(newly_matched)}, Still unmatched: {len(still_unmatched)}")

    if not args.dry_run and newly_matched:
        uris = [t.spotify_uri for t in newly_matched]
        add_tracks_to_playlist(sp, config["SPOTIFY_PLAYLIST_ID"], uris)

        existing = read_enriched_csv()
        existing.extend(newly_matched)
        write_enriched_csv(existing)

        if still_unmatched:
            write_unmatched_csv(still_unmatched)
        elif UNMATCHED_CSV.exists():
            UNMATCHED_CSV.unlink()
            print("All tracks matched! Removed unmatched.csv")


def cmd_status(args: argparse.Namespace) -> None:
    """Show sync statistics."""
    setup_logging(args.verbose)
    config = load_config()
    ensure_dirs()

    print("=== Playlist Sync Status ===\n")

    # Source CSV
    source = get_source_csv(config)
    if source and source.exists():
        tracks = read_source_csv(source)
        print(f"Source CSV:    {len(tracks)} tracks ({source.name})")
    else:
        print(f"Source CSV:    not configured (set SOURCE_CSV in .env)")

    # Enriched CSV
    if ENRICHED_CSV.exists():
        enriched = read_enriched_csv()
        matched = [t for t in enriched if t.has_spotify_match]
        print(f"Enriched CSV: {len(enriched)} tracks ({len(matched)} matched to Spotify)")

        if enriched:
            match_rate = len(matched) / len(enriched) * 100
            print(f"Match rate:   {match_rate:.1f}%")

            methods: dict[str, int] = {}
            for t in matched:
                m = t.match_method or "unknown"
                methods[m] = methods.get(m, 0) + 1
            if methods:
                print("Match methods:")
                for method, count in sorted(methods.items(), key=lambda x: -x[1]):
                    print(f"  {method}: {count}")
    else:
        print(f"Enriched CSV: not yet created")

    # Unmatched
    if UNMATCHED_CSV.exists():
        import pandas as pd
        df = pd.read_csv(UNMATCHED_CSV, encoding="utf-8-sig")
        print(f"Unmatched:    {len(df)} tracks")

    # Snapshots
    snapshots = sorted(SNAPSHOTS_DIR.glob("snapshot_*.json"))
    if snapshots:
        print(f"Snapshots:    {len(snapshots)} (latest: {snapshots[-1].name})")
    else:
        print(f"Snapshots:    none")


# ── CLI parser ──────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="playlist_sync",
        description="YT Music -> Spotify playlist sync with metadata enrichment",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("setup-ytmusic", help="Interactive YT Music browser auth setup")

    p_import = sub.add_parser("import-csv", help="Bootstrap from existing CSV export")
    p_import.add_argument("--csv", help="Path to CSV file")
    p_import.add_argument("--dry-run", action="store_true", help="Preview without writing")

    p_snap = sub.add_parser("snapshot", help="Snapshot current YT Music playlist")
    p_snap.add_argument("--dry-run", action="store_true", help="Preview without writing")

    sub.add_parser("diff", help="Show changes since last snapshot")

    p_sync = sub.add_parser("sync", help="Full sync: diff -> match -> Spotify push")
    p_sync.add_argument("--dry-run", action="store_true", help="Preview without pushing")
    p_sync.add_argument(
        "--from-csv", nargs="?", const="default",
        help="Sync from CSV instead of YT Music API",
    )

    p_retry = sub.add_parser("retry-unmatched", help="Re-attempt matching for unmatched tracks")
    p_retry.add_argument("--dry-run", action="store_true", help="Preview without pushing")

    sub.add_parser("status", help="Show sync statistics")

    return parser


def dispatch(args: argparse.Namespace) -> None:
    """Route to the correct command handler."""
    commands = {
        "setup-ytmusic": cmd_setup_ytmusic,
        "import-csv": cmd_import_csv,
        "snapshot": cmd_snapshot,
        "diff": cmd_diff,
        "sync": cmd_sync,
        "retry-unmatched": cmd_retry_unmatched,
        "status": cmd_status,
    }
    handler = commands.get(args.command)
    if handler:
        handler(args)


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        # No subcommand given -- show interactive menu
        interactive_menu()
    else:
        dispatch(args)
