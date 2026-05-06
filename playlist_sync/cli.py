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
    DEBUG_DIR,
    ENRICHED_CSV,
    LIKES_ENRICHED_CSV,
    LIKES_UNMATCHED_CSV,
    MATCH_CACHE,
    SKIPPED_CSV,
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
    write_skipped_csv,
    write_unmatched_csv,
)
from playlist_sync.differ import diff_tracks
from playlist_sync.enricher import (
    apply_match_to_track,
    backfill_artist_genres,
    backfill_lastfm_artist_tags,
    backfill_lastfm_data,
    backfill_track_metadata,
    classify_all_tracks,
    enrich_with_audio_features,
)
from playlist_sync.matcher import match_track
from playlist_sync.models import Track
from playlist_sync.spotify_client import (
    RateLimitError,
    add_saved_tracks,
    add_tracks_to_playlist,
    get_spotify_client,
    remove_saved_tracks,
    remove_tracks_from_playlist,
)
from playlist_sync.utils import setup_logging
from playlist_sync.ytmusic_client import (
    fetch_liked_tracks,
    fetch_playlist_tracks,
    load_latest_likes_snapshot,
    load_latest_snapshot,
    save_likes_snapshot,
    save_snapshot,
    setup_browser_auth,
)


# ── Interactive menu ────────────────────────────────────────────────

MENU_OPTIONS = [
    ("1",  "Setup YT Music auth",                   "setup-ytmusic"),
    ("2",  "Import from CSV",                       "import-csv"),
    ("3",  "Snapshot YT Music playlist",            "snapshot"),
    ("4",  "Show diff (changes)",                   "diff"),
    ("5",  "Full sync to Spotify",                  "sync"),
    ("6",  "Sync from CSV file",                    "sync-csv"),
    ("7",  "Retry unmatched tracks",                "retry-unmatched"),
    ("8",  "Enrich with Last.fm",                   "lastfm"),
    ("9",  "Classify genre + mood from tags",       "classify"),
    ("10", "Sync Liked Songs (YTM <- -> Spotify)",  "sync-likes"),
    ("11", "Export enriched data to JSON",          "export"),
    ("12", "Show status",                           "status"),
    ("0",  "Exit",                                  "exit"),
]


def interactive_menu() -> None:
    """Show an interactive menu in a loop until user exits."""
    while True:
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
            break

        # Map choice to command
        cmd = None
        for key, _, command in MENU_OPTIONS:
            if choice == key:
                cmd = command
                break

        if cmd is None:
            print(f"Unknown option: {choice}")
            continue
        if cmd == "exit":
            break

        # Ask about dry-run for commands that support it
        dry_run = False
        if cmd in (
            "import-csv", "snapshot", "sync", "sync-csv",
            "retry-unmatched", "lastfm", "classify", "export", "sync-likes",
        ):
            try:
                dr = input("Dry run? (y/N): ").strip().lower()
                dry_run = dr in ("y", "yes")
            except (EOFError, KeyboardInterrupt):
                print()
                break

        # Build a fake namespace and dispatch
        args = argparse.Namespace(
            command=cmd,
            verbose=False,
            dry_run=dry_run,
            csv=None,
            from_csv=None,
            force=False,
            output=None,
            retry_unmatched=False,
        )

        if cmd == "sync-csv":
            args.command = "sync"
            args.from_csv = "default"

        try:
            dispatch(args)
        except SystemExit:
            pass
        except Exception as e:
            print(f"\nError: {e}")

        try:
            input("\nPress Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            print()
            break


# ── Debug stats writer ──────────────────────────────────────────────

def _write_debug_stats(
    current: list[Track],
    already_matched: list[Track],
    matched_results: list,
    unmatched: list[Track],
    skipped: list[Track],
    diff,
) -> None:
    """Write per-run sync stats as structured JSON for downstream analysis.

    Lands in `data/debug/run_<timestamp>.json`. Inspired by SyncDisBoi's
    debug mode but kept lightweight — captures match rate, method
    distribution, and the deltas from the diff engine. Useful for
    plotting sync quality over time or feeding into a dashboard.
    """
    import json
    from datetime import datetime
    ensure_dirs()

    methods: dict[str, int] = {}
    confidences: list[float] = []
    for r in matched_results:
        methods[r.method] = methods.get(r.method, 0) + 1
        confidences.append(r.confidence)

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "totals": {
            "current": len(current),
            "already_matched": len(already_matched),
            "newly_matched": len(matched_results),
            "unmatched": len(unmatched),
            "skipped": len(skipped),
        },
        "diff": {
            "added": len(diff.added),
            "removed": len(diff.removed),
            "unchanged": len(diff.unchanged),
        },
        "match": {
            "rate_pct": round(
                100.0 * (len(already_matched) + len(matched_results)) / max(len(current), 1),
                2,
            ),
            "methods": methods,
            "avg_confidence": (
                round(sum(confidences) / len(confidences), 3) if confidences else None
            ),
        },
        "skip_reasons": {},
        "unmatched_examples": [
            {"title": t.title, "artist": t.artist} for t in unmatched[:10]
        ],
        "skipped_examples": [
            {"title": t.title, "artist": t.artist, "reason": t.skip_reason}
            for t in skipped[:10]
        ],
    }

    # Group skip reasons (currently only "no_album", but extensible)
    for t in skipped:
        reason = t.skip_reason or "unknown"
        payload["skip_reasons"][reason] = payload["skip_reasons"].get(reason, 0) + 1

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out = DEBUG_DIR / f"run_{timestamp}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also keep a `latest.json` symlink-like file
    latest = DEBUG_DIR / "latest.json"
    latest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Debug stats: {out.name}")


# ── Match cache for resume after rate limits ────────────────────────

def _load_match_cache() -> dict[str, dict]:
    """Load cached match results from previous interrupted runs."""
    import json
    if MATCH_CACHE.exists():
        try:
            data = json.loads(MATCH_CACHE.read_text(encoding="utf-8"))
            print(f"Resuming: loaded {len(data)} cached matches from previous run")
            print(f"  (To start fresh, delete: {MATCH_CACHE})")
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
    setup_logging(args.verbose)
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
    setup_logging(args.verbose)
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
    setup_logging(args.verbose)
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
    setup_logging(args.verbose)
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
    skipped_tracks: list[Track] = []
    previously_failed: list[Track] = []   # tracks where matcher already gave up

    retry_unmatched_now = getattr(args, "retry_unmatched", False)

    for track in current:
        fp = track.fingerprint
        if fp in existing_map and existing_map[fp].has_spotify_match:
            enriched = existing_map[fp]
            enriched.last_synced = track.last_synced or enriched.last_synced
            already_matched.append(enriched)
        elif (
            fp in existing_map
            and existing_map[fp].match_attempted
            and not retry_unmatched_now
        ):
            # We've tried matching this track at least once and it failed —
            # don't waste Spotify search quota on it every sync. Use
            # `retry-unmatched` (option 7) or `sync --retry-unmatched` to
            # explicitly retry these.
            previously_failed.append(existing_map[fp])
        else:
            needs_matching.append(track)

    # Skip-filter: YT Music tracks without album metadata are typically
    # YouTube uploads, fan edits, mixes, or other content without a real
    # Spotify equivalent. Filtering them out before search saves ~3 s
    # per track in API time and keeps unmatched.csv focused on tracks
    # that genuinely *should* match but didn't.
    #
    # If a previously-skipped track now has album metadata (user edited
    # it in YT Music), the existing_map check above won't apply since
    # skipped tracks aren't in already_matched, so it'll be re-evaluated
    # here naturally.
    filtered: list[Track] = []
    for track in needs_matching:
        if track.platform == "ytmusic" and not track.album.strip():
            track.skip_reason = "no_album"
            skipped_tracks.append(track)
        else:
            filtered.append(track)
    needs_matching = filtered

    if skipped_tracks:
        print(f"Skipped {len(skipped_tracks)} track(s) with no album metadata "
              f"(YouTube uploads / fan edits) — written to skipped.csv")
    if previously_failed:
        print(f"Skipping {len(previously_failed)} previously-unmatched track(s) "
              f"(use 'retry-unmatched' or 'sync --retry-unmatched' to retry).")

    print(f"Already matched: {len(already_matched)}, needs matching: {len(needs_matching)}, "
          f"skipped: {len(skipped_tracks)}, previously-failed: {len(previously_failed)}")

    # Check if any matched tracks still need enrichment (backfill)
    needs_enrichment = any(
        not t.popularity or not t.artist_genres or not t.lastfm_playcount or not t.artist_tags
        for t in already_matched
        if t.has_spotify_match
    )

    # Step 5: Enrich already-matched tracks FIRST (doesn't need Spotify API)
    # This way enrichment data is saved even if matching hits rate limits.
    if already_matched and not args.dry_run:
        lastfm_key = config.get("LASTFM_API_KEY", "")
        if lastfm_key and needs_enrichment:
            print("\nFetching Last.fm track data...")
            backfill_lastfm_data(lastfm_key, already_matched)
            print("Fetching Last.fm artist tags...")
            backfill_lastfm_artist_tags(lastfm_key, already_matched)

    if not needs_matching and not diff.removed and not needs_enrichment:
        # Still save if we just enriched
        if not args.dry_run:
            all_tracks = already_matched + [
                t for t in current if not any(
                    t.fingerprint == m.fingerprint for m in already_matched
                )
            ]
            write_enriched_csv(already_matched)
            print(f"Enriched CSV updated: {ENRICHED_CSV}")
            save_snapshot(current)
        print("Nothing new to match.")
        return

    # Step 6: Match new tracks (with resume support)
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
                        isrc=cached.get("isrc", ""),
                        explicit=cached.get("explicit", False),
                        album_release_date=cached.get("album_release_date", ""),
                        spotify_duration_ms=cached.get("spotify_duration_ms", 0),
                        popularity=cached.get("popularity", 0),
                        album_type=cached.get("album_type", ""),
                        track_number=cached.get("track_number", 0),
                    )
                    enriched_track = apply_match_to_track(track, result)
                    already_matched.append(enriched_track)
                    # Do NOT append to matched_results here — these tracks are
                    # already in the Spotify playlist from a previous run.
                    # Only newly searched tracks should be added again.
                else:
                    unmatched_tracks.append(track)
            else:
                to_search.append(track)

        if to_search:
            cached_count = len(needs_matching) - len(to_search)
            if cached_count > 0:
                print(f"Restored {cached_count} matches from cache")

            limit = getattr(args, "limit", None)
            if limit and len(to_search) > limit:
                print(f"Limiting to {limit} tracks this run ({len(to_search) - limit} remaining for next run)")
                to_search = to_search[:limit]

            print(f"\nMatching {len(to_search)} tracks to Spotify...")

            try:
                for track in tqdm(to_search, desc="Matching", unit="track"):
                    result = match_track(sp, track)
                    fp = track.fingerprint
                    # Mark attempted regardless of outcome — failures go in
                    # unmatched.csv and won't be re-tried automatically next
                    # sync (use retry-unmatched to explicitly retry them).
                    track.match_attempted = True
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
                            "isrc": result.isrc,
                            "explicit": result.explicit,
                            "album_release_date": result.album_release_date,
                            "spotify_duration_ms": result.spotify_duration_ms,
                            "popularity": result.popularity,
                            "album_type": result.album_type,
                            "track_number": result.track_number,
                        }
                    else:
                        unmatched_tracks.append(track)
                        match_cache[fp] = {"matched": False}

                    # Save cache every 25 tracks for safety
                    if len(match_cache) % 25 == 0:
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

                # Save enriched CSV with whatever we have so far
                if not args.dry_run:
                    all_tracks = already_matched + unmatched_tracks
                    write_enriched_csv(all_tracks)
                    print(f"Enriched CSV saved: {ENRICHED_CSV}")
                    save_snapshot(current)
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

    # Step 8: Backfill Spotify metadata, genres, and audio features
    if already_matched and not args.dry_run:
        print("\nBackfilling Spotify metadata...")
        backfill_track_metadata(sp, already_matched)
        print("Fetching artist genres...")
        backfill_artist_genres(sp, already_matched)
        print("Fetching audio features...")
        enrich_with_audio_features(sp, already_matched)

        # Last.fm enrichment for newly matched tracks (already_matched from
        # previous runs were enriched in Step 5; this catches new matches)
        lastfm_key = config.get("LASTFM_API_KEY", "")
        if lastfm_key:
            print("Fetching Last.fm track data for new matches...")
            backfill_lastfm_data(lastfm_key, already_matched)
            print("Fetching Last.fm artist tags for new matches...")
            backfill_lastfm_artist_tags(lastfm_key, already_matched)
        else:
            print("Skipping Last.fm enrichment (no LASTFM_API_KEY in .env)")

    # Step 8: Classify tags into primary_genre + mood (no API calls)
    if not args.dry_run:
        # previously_failed tracks must be included in the CSV write so
        # their match_attempted=True flag survives the round-trip; otherwise
        # next sync would treat them as fresh again.
        all_tracks = already_matched + unmatched_tracks + skipped_tracks + previously_failed
        print("\nClassifying genre and mood from tags...")
        classify_all_tracks(all_tracks)

        write_enriched_csv(all_tracks)
        print(f"Enriched CSV updated: {ENRICHED_CSV}")

        # unmatched.csv reflects every track currently without a Spotify
        # match — both the freshly-failed batch and the long-tail of
        # previously-failed tracks — so the user always sees the full
        # backlog and can retry it with `retry-unmatched`.
        all_unmatched = unmatched_tracks + previously_failed
        if all_unmatched:
            write_unmatched_csv(all_unmatched)
            print(f"Unmatched tracks saved: {UNMATCHED_CSV} ({len(all_unmatched)} total)")

        if skipped_tracks:
            write_skipped_csv(skipped_tracks)
            print(f"Skipped tracks saved: {SKIPPED_CSV}")

        save_snapshot(current)
        print("Snapshot updated.")

        # Per-run debug stats — structured JSON for downstream analysis
        # (e.g. plotting match rate over time, monitoring sync health).
        _write_debug_stats(
            current=current,
            already_matched=already_matched,
            matched_results=matched_results,
            unmatched=all_unmatched,
            skipped=skipped_tracks,
            diff=diff,
        )

    _clear_match_cache()

    # Sync summary
    limit = getattr(args, "limit", None)
    total_still_pending = len(current) - len(already_matched) - len(matched_results) - len(unmatched_tracks)

    print("\n--- Sync Summary ---")
    print(f"  Total tracks:    {len(current)}")
    print(f"  Already matched: {len(already_matched)}")
    print(f"  Newly matched:   {len(matched_results)}")
    print(f"  Unmatched:       {len(unmatched_tracks)}")
    if matched_results:
        methods: dict[str, int] = {}
        for r in matched_results:
            methods[r.method] = methods.get(r.method, 0) + 1
        print(f"  Match methods:   {methods}")

    if limit and total_still_pending > 0:
        print(f"\nPartial sync complete. {total_still_pending} tracks still need matching.")
        print(f"Re-run tomorrow: python playlist_sync.py sync --limit {limit}")
    else:
        print("\nSync complete!")


def cmd_retry_unmatched(args: argparse.Namespace) -> None:
    """Re-attempt matching for previously unmatched tracks."""
    setup_logging(args.verbose)
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
        # Refresh the attempted flag — we just retried.
        track.match_attempted = True
        if result.matched:
            enriched = apply_match_to_track(track, result)
            newly_matched.append(enriched)
        else:
            still_unmatched.append(track)

    print(f"Newly matched: {len(newly_matched)}, Still unmatched: {len(still_unmatched)}")

    if not args.dry_run and newly_matched:
        uris = [t.spotify_uri for t in newly_matched]
        add_tracks_to_playlist(sp, config["SPOTIFY_PLAYLIST_ID"], uris)

        # Replace existing unmatched entries with the newly matched versions
        newly_matched_fps = {t.fingerprint for t in newly_matched}
        existing = [t for t in read_enriched_csv() if t.fingerprint not in newly_matched_fps]
        existing.extend(newly_matched)
        write_enriched_csv(existing)

        if still_unmatched:
            write_unmatched_csv(still_unmatched)
        elif UNMATCHED_CSV.exists():
            UNMATCHED_CSV.unlink()
            print("All tracks matched! Removed unmatched.csv")


def cmd_lastfm(args: argparse.Namespace) -> None:
    """Enrich tracks with Last.fm play counts, listeners, and tags.

    Works on the enriched CSV if it exists. If no enriched CSV but a
    snapshot exists, enriches all tracks from the latest snapshot
    (doesn't need Spotify matching — Last.fm matches by artist+title).
    """
    setup_logging(args.verbose)
    config = load_config()
    ensure_dirs()

    lastfm_key = config.get("LASTFM_API_KEY", "")
    if not lastfm_key:
        print("Error: LASTFM_API_KEY not set in .env")
        print("Get a free key at: https://www.last.fm/api/account/create")
        sys.exit(1)

    # Load tracks: prefer enriched CSV, fall back to snapshot
    if ENRICHED_CSV.exists():
        tracks = read_enriched_csv()
        print(f"Loaded {len(tracks)} tracks from enriched CSV")
    else:
        snapshot_tracks = load_latest_snapshot()
        if not snapshot_tracks:
            print("Error: no enriched CSV or snapshot found.")
            print("Run option 3 (Snapshot) or option 5 (Full sync) first.")
            sys.exit(1)
        tracks = snapshot_tracks
        print(f"Loaded {len(tracks)} tracks from latest snapshot")

    needs_track_data = [
        t for t in tracks
        if not t.lastfm_playcount and t.title and t.artist and not t.lastfm_track_attempted
    ]
    needs_artist_tags = [
        t for t in tracks
        if not t.artist_tags and t.artist and not t.lastfm_attempted
    ]
    print(f"Need Last.fm track data: {len(needs_track_data)}")
    print(f"Need Last.fm artist tags: {len(needs_artist_tags)}")

    if not needs_track_data and not needs_artist_tags:
        print("All tracks already have Last.fm data and artist tags.")
        return

    if args.dry_run:
        unique_artists = len({t.artist.split(",")[0].strip() for t in needs_artist_tags if t.artist})
        print(f"[DRY RUN] Would fetch Last.fm track data for {len(needs_track_data)} tracks")
        print(f"[DRY RUN] Would fetch Last.fm artist tags for {unique_artists} unique artists")
        return

    if needs_track_data:
        print("\nFetching Last.fm track data (playcount, listeners, track tags)...")
        backfill_lastfm_data(lastfm_key, tracks)

    if needs_artist_tags:
        print("\nFetching Last.fm artist tags (denser genre coverage)...")
        backfill_lastfm_artist_tags(lastfm_key, tracks)

    print("\nClassifying genre and mood from tag pool...")
    classify_all_tracks(tracks)

    write_enriched_csv(tracks)
    print(f"Enriched CSV updated: {ENRICHED_CSV}")

    track_enriched = sum(1 for t in tracks if t.lastfm_playcount)
    artist_enriched = sum(1 for t in tracks if t.artist_tags)
    genre_classified = sum(1 for t in tracks if t.primary_genre)
    mood_classified = sum(1 for t in tracks if t.mood)
    print("\nLast.fm enrichment complete:")
    print(f"  Track data:    {track_enriched}/{len(tracks)} ({100*track_enriched/len(tracks):.1f}%)")
    print(f"  Artist tags:   {artist_enriched}/{len(tracks)} ({100*artist_enriched/len(tracks):.1f}%)")
    print(f"  primary_genre: {genre_classified}/{len(tracks)} ({100*genre_classified/len(tracks):.1f}%)")
    print(f"  mood:          {mood_classified}/{len(tracks)} ({100*mood_classified/len(tracks):.1f}%)")


def cmd_classify(args: argparse.Namespace) -> None:
    """Re-derive primary_genre and mood columns from existing tags.

    Pure local computation — no API calls. Useful after you've gathered
    Last.fm tags and want to re-bucket them, or after the classifier
    keyword maps are updated.
    """
    setup_logging(args.verbose)
    ensure_dirs()

    if not ENRICHED_CSV.exists():
        print("Error: no enriched CSV found. Run sync or import-csv first.")
        sys.exit(1)

    tracks = read_enriched_csv()
    print(f"Loaded {len(tracks)} tracks from enriched CSV")

    # Force re-classification by clearing the existing values first
    if getattr(args, "force", False):
        for t in tracks:
            t.primary_genre = ""
            t.mood = ""
        print("Force mode: cleared existing primary_genre and mood values")

    classify_all_tracks(tracks)

    if args.dry_run:
        genre_n = sum(1 for t in tracks if t.primary_genre)
        mood_n = sum(1 for t in tracks if t.mood)
        print(f"[DRY RUN] Would classify {genre_n} primary_genre and {mood_n} mood values")
        return

    write_enriched_csv(tracks)
    print(f"Enriched CSV updated: {ENRICHED_CSV}")

    genre_n = sum(1 for t in tracks if t.primary_genre)
    mood_n = sum(1 for t in tracks if t.mood)
    print("\nClassification results:")
    print(f"  primary_genre: {genre_n}/{len(tracks)} ({100*genre_n/len(tracks):.1f}%)")
    print(f"  mood:          {mood_n}/{len(tracks)} ({100*mood_n/len(tracks):.1f}%)")

    # Show genre distribution
    from collections import Counter
    genres = Counter(t.primary_genre for t in tracks if t.primary_genre)
    print("\nGenre distribution:")
    for g, n in genres.most_common(10):
        print(f"  {g:15s}  {n}")


def cmd_export(args: argparse.Namespace) -> None:
    """Export the enriched CSV as portable JSON.

    Produces a structured JSON file that's easier to feed into other tools
    (jq, programmatic analysis, sharing) than the 48-column CSV. Each
    track becomes a JSON object with all enrichment fields.
    """
    setup_logging(args.verbose)
    ensure_dirs()

    if not ENRICHED_CSV.exists():
        print("Error: no enriched CSV found. Run sync first.")
        sys.exit(1)

    tracks = read_enriched_csv()
    print(f"Loaded {len(tracks)} tracks from enriched CSV")

    output_path = Path(args.output) if args.output else (ENRICHED_CSV.parent / "playlist_enriched.json")

    if args.dry_run:
        print(f"[DRY RUN] Would export {len(tracks)} tracks to {output_path}")
        return

    import json
    from datetime import datetime
    payload = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "track_count": len(tracks),
        "tracks": [t.to_csv_row() for t in tracks],
    }
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    size_kb = output_path.stat().st_size / 1024
    print(f"Exported {len(tracks)} tracks to {output_path} ({size_kb:.1f} KB)")


def cmd_sync_likes(args: argparse.Namespace) -> None:
    """Mirror YT Music liked songs into the user's Spotify Liked Songs library.

    Uses the same 3-pass matcher as the playlist sync but operates on YT
    Music's implicit "LM" playlist and Spotify's `/me/tracks` endpoint
    (saved tracks). Maintains a separate snapshot under
    `data/snapshots/likes/` so likes-diff state never collides with
    playlist-diff state.

    Adds tracks newly liked on YT Music; removes tracks unliked on YT
    Music (only if their Spotify match is known from a previous sync).
    """
    setup_logging(args.verbose)
    config = load_config()
    require_spotify_config(config)
    require_ytmusic_config(config)
    ensure_dirs()

    # Step 1: fetch current YTM likes
    print("Fetching YT Music liked songs...")
    current = fetch_liked_tracks(config["YTMUSIC_AUTH_FILE"])
    print(f"Fetched {len(current)} liked songs")

    # Step 2: load previous likes snapshot for diff
    previous = load_latest_likes_snapshot()
    diff = diff_tracks(current, previous)
    print(f"Likes diff: {diff.summary()}")

    if not diff.added and not diff.removed:
        if not args.dry_run:
            save_likes_snapshot(current)
        print("Nothing to do.")
        return

    # Step 3: load existing likes-enriched data + main enriched CSV.
    # The main enriched CSV is reused as a match cache: if a track is
    # already matched in the playlist sync, we don't need to re-match it
    # for likes — same Spotify URI applies.
    likes_existing = read_enriched_csv(LIKES_ENRICHED_CSV)
    main_existing = read_enriched_csv()
    fingerprint_to_uri: dict[str, str] = {}
    for t in likes_existing + main_existing:
        if t.has_spotify_match:
            fingerprint_to_uri[t.fingerprint] = t.spotify_uri

    # Step 4: classify added tracks — already matched (URI known) vs needs-search
    sp = get_spotify_client(config)
    matched_uris: list[str] = []
    matched_likes: list[Track] = []
    unmatched_likes: list[Track] = []

    needs_search: list[Track] = []
    for track in diff.added:
        uri = fingerprint_to_uri.get(track.fingerprint, "")
        if uri:
            track.spotify_uri = uri
            track.match_method = "reused_from_playlist"
            track.match_confidence = 1.0
            matched_uris.append(uri)
            matched_likes.append(track)
        else:
            needs_search.append(track)

    if matched_uris:
        print(f"Reused {len(matched_uris)} match(es) from existing enriched data")

    # Step 5: search Spotify for the rest
    if needs_search:
        # Skip-filter: same logic as cmd_sync
        searchable = []
        skipped = []
        for t in needs_search:
            if t.platform == "ytmusic" and not t.album.strip():
                t.skip_reason = "no_album"
                skipped.append(t)
            else:
                searchable.append(t)
        if skipped:
            print(f"Skipped {len(skipped)} track(s) with no album metadata")

        print(f"\nMatching {len(searchable)} new likes to Spotify...")
        try:
            for track in tqdm(searchable, desc="Matching", unit="track"):
                result = match_track(sp, track)
                if result.matched:
                    enriched = apply_match_to_track(track, result)
                    matched_likes.append(enriched)
                    matched_uris.append(result.spotify_uri)
                else:
                    unmatched_likes.append(track)
        except RateLimitError as e:
            print(f"Rate limited. Wait ~{e.retry_after / 3600:.1f}h and re-run.")
            return

    # Step 6: push to Spotify saved tracks
    if matched_uris:
        track_ids = [uri.split(":")[-1] for uri in matched_uris]
        added = add_saved_tracks(sp, track_ids, dry_run=args.dry_run)
        action = "[DRY RUN] Would save" if args.dry_run else "Saved"
        print(f"{action} {added} tracks to Spotify Liked Songs")

    # Step 7: remove tracks unliked on YTM (only if we know their Spotify ID)
    if diff.removed:
        remove_ids: list[str] = []
        for t in diff.removed:
            uri = fingerprint_to_uri.get(t.fingerprint, "")
            if uri:
                remove_ids.append(uri.split(":")[-1])
        if remove_ids:
            removed = remove_saved_tracks(sp, remove_ids, dry_run=args.dry_run)
            action = "[DRY RUN] Would unsave" if args.dry_run else "Unsaved"
            print(f"{action} {removed} tracks from Spotify Liked Songs")
        else:
            print(f"  ({len(diff.removed)} unliked on YTM but no Spotify match — skipping)")

    # Step 8: persist likes_enriched.csv + snapshot
    if not args.dry_run:
        all_likes = matched_likes + unmatched_likes
        if all_likes:
            write_enriched_csv(all_likes, path=LIKES_ENRICHED_CSV)
            print(f"Likes enriched CSV updated: {LIKES_ENRICHED_CSV}")
        if unmatched_likes:
            write_unmatched_csv(unmatched_likes, path=LIKES_UNMATCHED_CSV)
            print(f"Unmatched likes saved: {LIKES_UNMATCHED_CSV}")
        save_likes_snapshot(current)
        print("Likes snapshot updated.")

    print("\n--- Likes Sync Summary ---")
    print(f"  Total YTM likes: {len(current)}")
    print(f"  Newly added:     {len(diff.added)}")
    print(f"  Newly removed:   {len(diff.removed)}")
    print(f"  Matched:         {len(matched_likes)}")
    print(f"  Unmatched:       {len(unmatched_likes)}")
    print("\nLikes sync complete!")


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
        print("Source CSV:    not configured (set SOURCE_CSV in .env)")

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
        print("Enriched CSV: not yet created")

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
        print("Snapshots:    none")


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
    p_sync.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Max new tracks to match per run (use to stay within Spotify's daily API quota)",
    )
    p_sync.add_argument(
        "--retry-unmatched", action="store_true",
        help="Also retry tracks that previously failed matching (default: skip them)",
    )

    p_retry = sub.add_parser("retry-unmatched", help="Re-attempt matching for unmatched tracks")
    p_retry.add_argument("--dry-run", action="store_true", help="Preview without pushing")

    p_lastfm = sub.add_parser("lastfm", help="Enrich matched tracks with Last.fm data")
    p_lastfm.add_argument("--dry-run", action="store_true", help="Preview without writing")

    p_classify = sub.add_parser("classify", help="Derive primary_genre and mood from existing tags (no API calls)")
    p_classify.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p_classify.add_argument("--force", action="store_true", help="Re-classify even tracks that already have values")

    p_export = sub.add_parser("export", help="Export the enriched CSV as portable JSON")
    p_export.add_argument("--output", "-o", help="Output JSON path (default: data/playlist_enriched.json)")
    p_export.add_argument("--dry-run", action="store_true", help="Preview without writing")

    p_likes = sub.add_parser("sync-likes", help="Mirror YT Music liked songs to Spotify Liked Songs")
    p_likes.add_argument("--dry-run", action="store_true", help="Preview without writing")

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
        "lastfm": cmd_lastfm,
        "classify": cmd_classify,
        "export": cmd_export,
        "sync-likes": cmd_sync_likes,
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
