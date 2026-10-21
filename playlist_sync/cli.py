"""
CLI interface with argparse subcommands; the interactive menu lives in menu.py.
Author: David
Date: 2026-03-22
Version: 0.8.0
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import requests
from tqdm import tqdm

from playlist_sync.config import (
    DEBUG_DIR,
    ENRICHED_CSV,
    LIKES_ENRICHED_CSV,
    LIKES_UNMATCHED_CSV,
    MATCH_CACHE,
    PROJECT_ROOT,
    SKIPPED_CSV,
    SNAPSHOTS_DIR,
    UNMATCHED_CSV,
    cli_command,
    ensure_dirs,
    find_legacy_home,
    get_source_csv,
    is_unset,
    load_config,
    parse_ytmusic_playlist_id,
    require_spotify_config,
    require_ytmusic_config,
    require_ytmusic_playlist,
)
from playlist_sync.csv_manager import (
    read_enriched_csv,
    read_source_csv,
    write_enriched_csv,
    write_skipped_csv,
    write_tracks_json,
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
    lastfm_artist_tag_candidates,
    lastfm_track_candidates,
)
from playlist_sync.matcher import match_track
from playlist_sync.models import Track
from playlist_sync.spotify_client import (
    RateLimitError,
    SearchUnavailable,
    add_saved_tracks,
    add_tracks_to_playlist,
    get_playlist_tracks,
    get_spotify_client,
    remove_saved_tracks,
    remove_tracks_from_playlist,
)
from playlist_sync.utils import setup_logging
from playlist_sync.ytmusic_client import (
    LikedSongsAuthError,
    fetch_liked_tracks,
    fetch_playlist_tracks,
    latest_snapshot_playlist_id,
    load_latest_likes_snapshot,
    load_latest_snapshot,
    save_likes_snapshot,
    save_snapshot,
)

logger = logging.getLogger("playlist_sync")


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


def _missing_from_playlist(sp: object, config: dict[str, str], uris: list[str]) -> list[str]:
    """The URIs not yet in the target playlist (only fetched when there is something to check)."""
    if not uris:
        return []
    present = {t.get("uri") for t in get_playlist_tracks(sp, config["SPOTIFY_PLAYLIST_ID"])}
    return [u for u in dict.fromkeys(uris) if u not in present]


def _push_and_mark(sp: object, config: dict[str, str], uris: list[str],
                   match_cache: dict[str, dict], dry_run: bool) -> bool:
    """Add URIs to the playlist; mark them pushed in the match cache when all went in.

    Returns True when everything was added (or there was nothing to add).
    """
    if not uris:
        return True
    added = add_tracks_to_playlist(sp, config["SPOTIFY_PLAYLIST_ID"], uris, dry_run=dry_run)
    action = "[DRY RUN] Would add" if dry_run else "Added"
    print(f"{action} {added} tracks to Spotify playlist")
    if dry_run:
        return True
    if added < len(uris):
        print(f"{len(uris) - added} matched track(s) could not be added; run "
              f"'{cli_command()} repush' (menu 6) to add them.")
        return False
    pushed = set(uris)
    for entry in match_cache.values():
        if entry.get("spotify_uri") in pushed:
            entry["pushed"] = True
    return True


def _remove_deleted(sp: object, config: dict[str, str], diff: object,
                    existing_map: dict[str, Track], dry_run: bool) -> None:
    """Remove from the Spotify playlist the tracks deleted on YT Music since the last run."""
    remove_uris = [existing_map[t.fingerprint].spotify_uri for t in diff.removed
                   if t.fingerprint in existing_map and existing_map[t.fingerprint].spotify_uri]
    if remove_uris:
        removed = remove_tracks_from_playlist(sp, config["SPOTIFY_PLAYLIST_ID"], remove_uris,
                                              dry_run=dry_run)
        action = "[DRY RUN] Would remove" if dry_run else "Removed"
        print(f"{action} {removed} tracks from Spotify playlist")


def _clear_match_cache() -> None:
    """Remove match cache after successful sync."""
    if MATCH_CACHE.exists():
        MATCH_CACHE.unlink()


def _snapshot_for(playlist_id: str) -> list[Track] | None:
    """Latest snapshot, or None when it belongs to a different YT Music playlist.

    Without this, switching the source playlist would diff the new playlist
    against the old one and remove every old track from the Spotify playlist.
    """
    recorded = latest_snapshot_playlist_id()
    if recorded and playlist_id and recorded != playlist_id:
        print(f"Note: the last snapshot is of a different source ({recorded}).")
        print("Comparing as a first run: nothing is removed from Spotify this time.")
        return None
    return load_latest_snapshot()


# ── Command handlers ────────────────────────────────────────────────

def cmd_setup(args: argparse.Namespace) -> None:
    """Guided setup wizard: logins, playlists and API keys, saved to .env."""
    from playlist_sync.menu import Cancelled, run_setup_wizard

    setup_logging(args.verbose)
    try:
        run_setup_wizard()
    except (Cancelled, KeyboardInterrupt):
        print("\nSetup stopped. Finished steps are saved; run setup again to continue.")


def cmd_setup_ytmusic(args: argparse.Namespace) -> None:
    """Interactive YT Music browser auth setup (verified before it replaces the old login)."""
    from playlist_sync.menu import Cancelled, read_state, step_yt_login

    try:
        saved = step_yt_login(read_state())
    except (Cancelled, KeyboardInterrupt):
        print("\nYT Music login was not changed.")
        saved = False
    if not saved:
        sys.exit(1)


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
    print("\nNext: finish setup (S in the menu, or the 'setup' command), then sync.")


def cmd_snapshot(args: argparse.Namespace) -> None:
    """Snapshot current YT Music playlist state via API."""
    setup_logging(args.verbose)
    config = load_config()
    require_ytmusic_config(config)
    require_ytmusic_playlist(config)
    ensure_dirs()

    tracks = fetch_playlist_tracks(config["YTMUSIC_AUTH_FILE"], config["YTMUSIC_PLAYLIST_ID"])
    print(f"Fetched {len(tracks)} tracks from YT Music")

    if args.dry_run:
        print("[DRY RUN] Would save snapshot")
        return

    snapshot_path = save_snapshot(tracks, config["YTMUSIC_PLAYLIST_ID"])
    print(f"Snapshot saved: {snapshot_path.name}")


def cmd_diff(args: argparse.Namespace) -> None:
    """Show changes since last snapshot."""
    setup_logging(args.verbose)
    config = load_config()
    require_ytmusic_config(config)
    require_ytmusic_playlist(config)

    print("Fetching current YT Music playlist...")
    current = fetch_playlist_tracks(config["YTMUSIC_AUTH_FILE"], config["YTMUSIC_PLAYLIST_ID"])
    previous = _snapshot_for(config["YTMUSIC_PLAYLIST_ID"])

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
        require_ytmusic_playlist(config)
        print("Fetching current YT Music playlist...")
        current = fetch_playlist_tracks(config["YTMUSIC_AUTH_FILE"], config["YTMUSIC_PLAYLIST_ID"])
        print(f"Fetched {len(current)} tracks")

    # Step 2: Load existing enriched data to preserve previous matches
    existing = read_enriched_csv()
    existing_map: dict[str, Track] = {t.fingerprint: t for t in existing}

    # Step 3: Diff (only against a snapshot of this same playlist -- diffing
    # against another playlist would remove all of its tracks from Spotify)
    snapshot_id = f"csv:{csv_path.resolve()}" if args.from_csv else config["YTMUSIC_PLAYLIST_ID"]
    previous = _snapshot_for(snapshot_id)
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
            # `retry-unmatched` (menu option 5) or `sync --retry-unmatched` to
            # explicitly retry these.
            previously_failed.append(existing_map[fp])
        else:
            # Reuse the existing row so enrichment it already has (Last.fm
            # tags from a 'lastfm' run, say) survives the match
            needs_matching.append(existing_map.get(fp, track))

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

    def rows_to_keep(include_removed: bool = False) -> list[Track]:
        """Results so far plus every row not processed this run (limit, interruption).

        include_removed keeps the rows of tracks removed on YT Music, for exits
        that did not remove them from Spotify yet: the next run needs their URIs.
        """
        rows = already_matched + unmatched_tracks + skipped_tracks + previously_failed
        seen = {t.fingerprint for t in rows}
        leftovers = list(needs_matching)
        if include_removed:
            leftovers += [existing_map[t.fingerprint] for t in diff.removed
                          if t.fingerprint in existing_map]
        for t in leftovers:
            if t.fingerprint not in seen:
                rows.append(existing_map.get(t.fingerprint, t))
                seen.add(t.fingerprint)
        return rows

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
        if not is_unset(lastfm_key) and needs_enrichment:
            print("\nFetching Last.fm track data...")
            backfill_lastfm_data(lastfm_key, already_matched)
            print("Fetching Last.fm artist tags...")
            backfill_lastfm_artist_tags(lastfm_key, already_matched)

    # Matches an earlier interrupted run found but never pushed. They may already
    # be marked matched in the CSV, so they are taken from the cache directly.
    match_cache = _load_match_cache() if not args.dry_run else {}
    restored_unpushed = [
        e["spotify_uri"] for e in match_cache.values()
        if e.get("matched") and e.get("spotify_uri") and not e.get("pushed")
    ]

    if not needs_matching and not diff.removed and not needs_enrichment and not restored_unpushed:
        # Still save if we just enriched
        if not args.dry_run:
            write_enriched_csv(rows_to_keep())
            print(f"Enriched CSV updated: {ENRICHED_CSV}")
            save_snapshot(current, snapshot_id)
        print("Nothing new to match.")
        return

    # Step 6: Match new tracks (with resume support)
    sp = get_spotify_client(config)
    matched_results = []
    unmatched_tracks: list[Track] = []

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
                    # Not added to matched_results: unpushed ones are already in
                    # restored_unpushed and get pushed (if missing) below
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
                failures_in_a_row = 0
                for track in tqdm(to_search, desc="Matching", unit="track"):
                    try:
                        result = match_track(sp, track)
                    except SearchUnavailable as e:
                        # Not searched: leave match_attempted unset so the next
                        # run tries it again; give up after a few in a row
                        failures_in_a_row += 1
                        logger.warning("Search failed for '%s': %s", track.title, e)
                        if failures_in_a_row >= 5:
                            raise
                        continue
                    failures_in_a_row = 0
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
                            "pushed": False,
                        }
                    else:
                        unmatched_tracks.append(track)
                        match_cache[fp] = {"matched": False}

                    # Save cache every 25 tracks for safety (never for a dry run:
                    # its matches were not pushed and must not look resumable)
                    if not args.dry_run and len(match_cache) % 25 == 0:
                        _save_match_cache(match_cache)

            except (RateLimitError, KeyboardInterrupt, SearchUnavailable) as e:
                matched_so_far = len(matched_results)
                searched = sum(1 for t in to_search if t.match_attempted)
                if isinstance(e, RateLimitError):
                    hrs = e.retry_after / 3600
                    print(f"\n\nRate limited by Spotify! Progress saved ({matched_so_far} matched).")
                    print(f"Wait ~{hrs:.1f}h, then sync again: {cli_command()} sync")
                elif isinstance(e, SearchUnavailable):
                    print(f"\n\nSpotify is not answering searches ({e}). "
                          f"Progress saved ({matched_so_far} matched).")
                    print(f"Sync again later to continue: {cli_command()} sync")
                else:
                    print(f"\n\nStopped. Progress saved ({matched_so_far} matched).")
                    print(f"Sync again any time to continue: {cli_command()} sync")
                print(f"Still to search: {len(to_search) - searched} tracks. "
                      "They are picked up automatically next time.")

                # Push what already matched when the user stopped. After a rate
                # limit every Spotify call would wait out Retry-After (hours), so
                # the matches stay in the cache as unpushed for the next run.
                if isinstance(e, KeyboardInterrupt):
                    try:
                        push_uris = _missing_from_playlist(sp, config, restored_unpushed) + \
                            [r.spotify_uri for r in matched_results]
                        _push_and_mark(sp, config, push_uris, match_cache, args.dry_run)
                    except KeyboardInterrupt:
                        print("\nPush interrupted - the next sync adds the rest.")
                elif matched_results:
                    print(f"The {len(matched_results)} new match(es) are added to Spotify "
                          "by the next sync.")

                # Save every row and keep the old snapshot: removals on YT Music
                # were not applied yet, so the next run must still see them
                if not args.dry_run:
                    _save_match_cache(match_cache)
                    write_enriched_csv(rows_to_keep(include_removed=True))
                    print(f"Enriched CSV saved: {ENRICHED_CSV}")
                return

        print(f"Matched: {len(matched_results)}, Unmatched: {len(unmatched_tracks)}")

    # Step 6: Push to Spotify (plus cached matches an interrupted run never pushed).
    # A long rate limit or a dropped connection here must not lose the matches:
    # keep them in the cache as unpushed and the old snapshot for the removals.
    try:
        new_uris = _missing_from_playlist(sp, config, restored_unpushed) + \
            [r.spotify_uri for r in matched_results]
        fully_pushed = _push_and_mark(sp, config, new_uris, match_cache, args.dry_run)
        _remove_deleted(sp, config, diff, existing_map, args.dry_run)
    except (RateLimitError, requests.exceptions.RequestException) as e:
        print(f"\nSpotify stopped accepting changes ({e}). Nothing is lost: the next sync "
              "adds the matched tracks and applies the removals.")
        if not args.dry_run:
            _save_match_cache(match_cache)
            write_enriched_csv(rows_to_keep(include_removed=True))
        return

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
        if not is_unset(lastfm_key):
            print("Fetching Last.fm track data for new matches...")
            backfill_lastfm_data(lastfm_key, already_matched)
            print("Fetching Last.fm artist tags for new matches...")
            backfill_lastfm_artist_tags(lastfm_key, already_matched)
        else:
            print("Skipping Last.fm enrichment (optional - add a key with menu C > Last.fm key)")

    # Step 8: Classify tags into primary_genre + mood (no API calls)
    if not args.dry_run:
        # previously_failed tracks must be included in the CSV write so
        # their match_attempted=True flag survives the round-trip; otherwise
        # next sync would treat them as fresh again.
        all_tracks = rows_to_keep()
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

        save_snapshot(current, snapshot_id)
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

    if not args.dry_run:
        if fully_pushed:
            _clear_match_cache()
        else:
            _save_match_cache(match_cache)  # keep unpushed matches for the next run

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
        print(f"Re-run tomorrow: {cli_command()} sync --limit {limit}")
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
    if is_unset(lastfm_key):
        print("Error: no Last.fm API key yet (LASTFM_API_KEY).")
        print("Get a free key at https://www.last.fm/api/account/create and add it with")
        print("the setup wizard ('setup' command, or C in the interactive menu).")
        sys.exit(1)

    # Load tracks: prefer enriched CSV, fall back to snapshot
    if ENRICHED_CSV.exists():
        tracks = read_enriched_csv()
        print(f"Loaded {len(tracks)} tracks from enriched CSV")
    else:
        snapshot_tracks = load_latest_snapshot()
        if not snapshot_tracks:
            print("Error: no enriched CSV or snapshot found.")
            print("Run 'snapshot' or 'sync' first (menu options 11 / 1).")
            sys.exit(1)
        tracks = snapshot_tracks
        print(f"Loaded {len(tracks)} tracks from latest snapshot")

    # --retry-missing: rows flagged attempted by versions before 0.8.0 (failed
    # requests, '+' artists, duplicate rows) still have no data; give them
    # one fresh lookup.
    retry_missing = getattr(args, "retry_missing", False)
    needs_track_data = lastfm_track_candidates(tracks, ignore_attempted=retry_missing)
    needs_artist_tags = lastfm_artist_tag_candidates(tracks, ignore_attempted=retry_missing)
    print(f"Need Last.fm track data: {len(needs_track_data)}")
    print(f"Need Last.fm artist tags: {len(needs_artist_tags)}")

    if retry_missing:
        print(f"  incl. previously attempted (--retry-missing): "
              f"{sum(1 for t in needs_track_data if t.lastfm_track_attempted)} track data, "
              f"{sum(1 for t in needs_artist_tags if t.lastfm_attempted)} artist tags")
    else:
        flagged_tracks = (
            len(lastfm_track_candidates(tracks, ignore_attempted=True)) - len(needs_track_data)
        )
        flagged_artists = (
            len(lastfm_artist_tag_candidates(tracks, ignore_attempted=True))
            - len(needs_artist_tags)
        )
        if flagged_tracks or flagged_artists:
            print(f"Skipping {flagged_tracks} track-data and {flagged_artists} artist-tag row(s) "
                  f"already attempted without data (use 'lastfm --retry-missing' to retry).")

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
        backfill_lastfm_data(lastfm_key, tracks, ignore_attempted=retry_missing)

    if needs_artist_tags:
        print("\nFetching Last.fm artist tags (denser genre coverage)...")
        backfill_lastfm_artist_tags(lastfm_key, tracks, ignore_attempted=retry_missing)

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


def cmd_repush(args: argparse.Namespace) -> None:
    """Push every already-matched track to the current Spotify playlist.

    Use this when you've recreated the Spotify playlist (new ID in .env)
    and need to repopulate it from `playlist_enriched.csv`. The regular
    `sync` command only pushes *newly-matched* tracks since the last
    snapshot — it has no concept of "the destination playlist is empty
    but my CSV has 3000 matches", so it does nothing.

    Idempotent by default: fetches the current playlist contents first
    and only adds URIs that are missing. Running `repush` twice in a row
    is safe — the second run is a no-op. Pass `--replace` to wipe the
    playlist completely first (useful when the playlist has accumulated
    duplicates from older non-idempotent versions).
    """
    setup_logging(args.verbose)
    config = load_config()
    require_spotify_config(config)
    ensure_dirs()

    if not ENRICHED_CSV.exists():
        print("Error: no enriched CSV found. Run sync first to populate matches.")
        sys.exit(1)

    enriched = read_enriched_csv()
    matched = [t for t in enriched if t.has_spotify_match]
    print(f"Enriched CSV: {len(enriched)} total tracks, {len(matched)} have Spotify matches")

    if not matched:
        print("No matched tracks found. Nothing to push.")
        return

    desired_uris = [t.spotify_uri for t in matched if t.spotify_uri]
    print(f"Target playlist: {config['SPOTIFY_PLAYLIST_ID']}")

    sp = get_spotify_client(config)

    # Fetch what's currently in the playlist so we can be idempotent.
    print("Reading current playlist contents...")
    from playlist_sync.spotify_client import get_playlist_tracks
    current_tracks = get_playlist_tracks(sp, config["SPOTIFY_PLAYLIST_ID"])
    current_uri_counts: dict[str, int] = {}
    for t in current_tracks:
        if t and t.get("uri"):
            current_uri_counts[t["uri"]] = current_uri_counts.get(t["uri"], 0) + 1
    print(f"Currently in playlist: {len(current_tracks)} entries ({len(current_uri_counts)} unique URIs)")

    replace_mode = getattr(args, "replace", False)

    # If --replace OR there are duplicates from a previous non-idempotent run,
    # offer to clear the playlist first.
    has_dupes = any(c > 1 for c in current_uri_counts.values())
    if replace_mode or has_dupes:
        if has_dupes and not replace_mode:
            print(
                f"Detected duplicates in the playlist ({len(current_tracks) - len(current_uri_counts)} extras). "
                "Pass --replace to wipe the playlist first; otherwise idempotent add will leave duplicates as-is."
            )
        if replace_mode and current_tracks:
            print(f"--replace: removing all {len(current_uri_counts)} unique URIs from the playlist...")
            if args.dry_run:
                print(f"[DRY RUN] Would remove {len(current_uri_counts)} URIs and add {len(desired_uris)}")
                return
            remove_tracks_from_playlist(sp, config["SPOTIFY_PLAYLIST_ID"], list(current_uri_counts.keys()))
            current_uri_counts = {}

    # Idempotent add: only push URIs that aren't already in the playlist
    desired_set = set(desired_uris)
    already_present = desired_set & set(current_uri_counts.keys())
    to_add = [u for u in desired_uris if u not in current_uri_counts]
    print(f"Already in playlist: {len(already_present)} URIs")
    print(f"To add:              {len(to_add)} URIs")

    if not to_add:
        print("\nPlaylist already contains every matched track. Nothing to do.")
        return

    if args.dry_run:
        print(f"\n[DRY RUN] Would add {len(to_add)} tracks. First 5:")
        for uri in to_add[:5]:
            t = next((t for t in matched if t.spotify_uri == uri), None)
            if t:
                print(f"  {t.title[:40]:40s} -- {t.artist[:30]:30s}  {uri}")
        return

    added = add_tracks_to_playlist(sp, config["SPOTIFY_PLAYLIST_ID"], to_add)
    print(f"\nDone! Added {added}/{len(to_add)} tracks to the Spotify playlist.")
    if added < len(to_add):
        print(f"  ({len(to_add) - added} failed — check the log for details.)")


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

    write_tracks_json(tracks, output_path)
    size_kb = output_path.stat().st_size / 1024
    print(f"Exported {len(tracks)} tracks to {output_path} ({size_kb:.1f} KB)")


def cmd_export_yt(args: argparse.Namespace) -> None:
    """Export a YT Music playlist, Liked Songs or the whole library to JSON + CSV."""
    from playlist_sync import yt_transfer

    setup_logging(args.verbose)
    config = load_config()
    require_ytmusic_config(config)
    auth_file = config["YTMUSIC_AUTH_FILE"]

    if getattr(args, "all", False):
        if args.dry_run:
            print("[DRY RUN] Would export Liked Songs and every playlist in your library")
            return
        print("Exporting Liked Songs and every playlist in your library...")
        folder, written, failed = yt_transfer.export_library(auth_file)
        print(f"\nExported {written} playlist(s) to {folder}")
        if failed:
            print(f"Could not export {len(failed)}: {', '.join(failed)}")
            sys.exit(1)
        return

    raw = getattr(args, "playlist", None) or config["YTMUSIC_PLAYLIST_ID"]
    if yt_transfer.is_liked(raw):
        playlist_id = yt_transfer.LIKED_ID
    else:
        playlist_id = parse_ytmusic_playlist_id(raw)
        if not playlist_id:
            print("Error: say which playlist to export: --playlist <ID or link>, --playlist liked,")
            print("or --all (or choose a YT Music playlist with the setup wizard).")
            sys.exit(1)
    if args.dry_run:
        print(f"[DRY RUN] Would export YT Music playlist {playlist_id}")
        return
    try:
        json_path, csv_path, count = yt_transfer.export_playlist(auth_file, playlist_id, args.output)
    except LikedSongsAuthError as e:
        print(f"\n{e}\n")
        sys.exit(1)
    print(f"Exported {count} tracks:")
    print(f"  {json_path}")
    print(f"  {csv_path}")


def cmd_import_yt(args: argparse.Namespace) -> None:
    """Copy tracks from an export file or a YT Music playlist into a YT Music playlist."""
    from playlist_sync import yt_transfer

    setup_logging(args.verbose)
    config = load_config()
    require_ytmusic_config(config)
    auth_file = config["YTMUSIC_AUTH_FILE"]

    target_id = None
    if getattr(args, "to", None):
        target_id = yt_transfer.LIKED_ID if yt_transfer.is_liked(args.to) else \
            parse_ytmusic_playlist_id(args.to)
        if not target_id or target_id == yt_transfer.LIKED_ID:
            print("Error: --to must be one of your own YT Music playlists (ID or link).")
            sys.exit(1)

    try:
        title, video_ids, without_id = yt_transfer.collect_source(auth_file, args.source)
        print(f"Source: {title} - {len(video_ids)} tracks with a YouTube video ID")
        if without_id:
            print(f"  {without_id} row(s) have no YouTube video ID and are skipped "
                  "(e.g. tracks that came from Spotify).")
        summary = yt_transfer.import_to_playlist(
            auth_file, video_ids, target_id=target_id,
            new_name=getattr(args, "new_name", None) or title,
            privacy=getattr(args, "privacy", "private"), dry_run=args.dry_run,
        )
    except (yt_transfer.TransferError, LikedSongsAuthError, RuntimeError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    prefix = "[DRY RUN] Would add" if args.dry_run else "Added"
    added = summary["to_add"] if args.dry_run else summary["added"]
    print(f"{prefix} {added} track(s); {summary['already_there']} already in the playlist, "
          f"{summary['duplicates_in_source']} duplicate(s) in the source.")
    if summary["playlist_id"]:
        print(f"Playlist: https://music.youtube.com/playlist?list={summary['playlist_id']}")
    failed = summary["failed"]
    if failed:
        print(f"{len(failed)} track(s) could not be added (unavailable in your region or removed):")
        for video_id in failed[:20]:
            print(f"  https://music.youtube.com/watch?v={video_id}")
        sys.exit(1)


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
    require_spotify_config(config, need_playlist=False)
    require_ytmusic_config(config)
    ensure_dirs()

    # Step 1: fetch current YTM likes
    print("Fetching YT Music liked songs...")
    try:
        current = fetch_liked_tracks(config["YTMUSIC_AUTH_FILE"])
    except LikedSongsAuthError as e:
        # Clear, actionable error instead of dumping a wall of JSON
        print(f"\n{e}\n")
        sys.exit(1)
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
        except SearchUnavailable as e:
            print(f"Spotify is not answering searches ({e}). Nothing was changed; re-run later.")
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
    print(f"Settings:      {PROJECT_ROOT}")

    # Source CSV
    source = get_source_csv(config)
    if source and source.exists():
        tracks = read_source_csv(source)
        print(f"Source CSV:    {len(tracks)} tracks ({source.name})")
    else:
        print("Source CSV:    not configured (optional; set it with the setup wizard)")

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

    sub.add_parser(
        "setup", help="Guided setup: YT Music login, both playlists, Spotify and Last.fm keys",
    )
    sub.add_parser("setup-ytmusic", help="Only the YT Music login step of the setup wizard")

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
    p_lastfm.add_argument(
        "--retry-missing", action="store_true",
        help="Also retry tracks already marked attempted that still have no Last.fm data "
             "(one-off fix for CSVs enriched before 0.8.0)",
    )

    p_classify = sub.add_parser("classify", help="Derive primary_genre and mood from existing tags (no API calls)")
    p_classify.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p_classify.add_argument("--force", action="store_true", help="Re-classify even tracks that already have values")

    p_export = sub.add_parser("export", help="Export the enriched CSV as portable JSON")
    p_export.add_argument("--output", "-o", help="Output JSON path (default: data/playlist_enriched.json)")
    p_export.add_argument("--dry-run", action="store_true", help="Preview without writing")

    p_repush = sub.add_parser("repush", help="Re-push all matched URIs to the current Spotify playlist (use after recreating the playlist)")
    p_repush.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p_repush.add_argument("--replace", action="store_true", help="Wipe the playlist completely before adding (use to clean up duplicates)")

    p_likes = sub.add_parser("sync-likes", help="Mirror YT Music liked songs to Spotify Liked Songs")
    p_likes.add_argument("--dry-run", action="store_true", help="Preview without writing")

    p_xyt = sub.add_parser(
        "export-yt",
        help="Export a YT Music playlist or Liked Songs to JSON + CSV (no Spotify needed)",
    )
    p_xyt.add_argument(
        "--playlist", "-p",
        help="Playlist ID or link, or 'liked' for Liked Songs (default: YTMUSIC_PLAYLIST_ID)",
    )
    p_xyt.add_argument("--all", action="store_true",
                       help="Export Liked Songs and every playlist in your library")
    p_xyt.add_argument("--output", "-o", help="Output .json path (a .csv is written next to it)")
    p_xyt.add_argument("--dry-run", action="store_true", help="Preview without writing")

    p_iyt = sub.add_parser(
        "import-yt",
        help="Copy tracks from an export file or a YT Music playlist into a YT Music playlist",
    )
    p_iyt.add_argument(
        "source",
        help="Export file (.json/.csv) or a YT Music playlist ID/link ('liked' for Liked Songs)",
    )
    target = p_iyt.add_mutually_exclusive_group()
    target.add_argument("--to", help="One of your playlists (ID or link); missing tracks are added")
    target.add_argument("--new-name", help="Create a new playlist with this name (default: source name)")
    p_iyt.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private",
                       help="Visibility of a new playlist (default: private)")
    p_iyt.add_argument("--dry-run", action="store_true", help="Preview without writing")

    sub.add_parser("status", help="Show sync statistics")

    return parser


def dispatch(args: argparse.Namespace) -> None:
    """Route to the correct command handler."""
    commands = {
        "setup": cmd_setup,
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
        "export-yt": cmd_export_yt,
        "import-yt": cmd_import_yt,
        "repush": cmd_repush,
        "status": cmd_status,
    }
    handler = commands.get(args.command)
    if handler:
        handler(args)


def main() -> None:
    """CLI entry point."""
    # Redirected output on Windows uses a legacy code page; a Cyrillic or emoji
    # title must print as '?' rather than abort a backup halfway
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args()

    legacy = find_legacy_home()
    if legacy and args.command not in (None, "setup"):
        print(f"Note: settings from an older version were found in {legacy}.")
        print(f"Copy them into the new settings folder with: {cli_command()} setup")

    if not args.command:
        # No subcommand given -- show interactive menu (imported lazily: menu imports cli)
        from playlist_sync.menu import interactive_menu
        interactive_menu()
    else:
        dispatch(args)
