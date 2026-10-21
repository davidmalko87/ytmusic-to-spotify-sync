"""
YT Music export and import: playlists or Liked Songs to JSON + CSV files, and
files or playlists back into a YT Music playlist.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0

Exports need only the YT Music login (no Spotify). Imports add tracks by
YouTube video ID, skip ones the target playlist already has (so an interrupted
import can simply be run again), and fall back to one-by-one adds when a batch
is refused because of a single unavailable video.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from playlist_sync import config as cfg
from playlist_sync.models import Track
from playlist_sync.ytmusic_client import (
    add_yt_playlist_items,
    create_yt_playlist,
    fetch_liked_tracks,
    fetch_playlist_tracks,
    get_playlist_info,
    get_playlist_video_ids,
    list_library_playlists,
)

logger = logging.getLogger("playlist_sync")

EXPORT_FORMAT = "ytmusic-to-spotify-sync/yt-export"
EXPORT_VERSION = 1
LIKED_ID = "LM"
LIKED_TITLE = "Liked Songs"
# Same columns as a playlist CSV export, so the CSV also works with "Use a CSV export"
CSV_COLUMNS = ["title", "artist", "album", "isrc", "platform", "trackId", "duration",
               "addedDate", "addedBy", "url"]
IMPORT_BATCH = 50
IMPORT_DELAY_SEC = 0.5
PRIVACY_CHOICES = ("PRIVATE", "UNLISTED", "PUBLIC")

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIDEO_URL_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


class TransferError(RuntimeError):
    """A problem the user can act on (bad file, playlist not owned, ...)."""


def is_liked(playlist_id: str) -> bool:
    """True for the ways people refer to Liked Songs: LM, liked, likes."""
    return playlist_id.strip().upper() in ("LM", "LIKED", "LIKES", "LIKED SONGS")


def exports_dir() -> Path:
    return cfg.DATA_DIR / "exports"


def safe_filename(text: str) -> str:
    """A file name that works on Windows, macOS and Linux (keeps non-Latin letters)."""
    return _UNSAFE_FILENAME_RE.sub("_", text).strip(" ._")[:60] or "playlist"


def fetch_source(auth_file: str, playlist_id: str) -> tuple[str, list[Track]]:
    """Return (title, tracks) for a YT Music playlist or Liked Songs."""
    if is_liked(playlist_id):
        return LIKED_TITLE, fetch_liked_tracks(auth_file)
    info = get_playlist_info(auth_file, playlist_id)
    return info["title"], fetch_playlist_tracks(auth_file, playlist_id)


def export_paths(title: str, output: str | None = None, folder: Path | None = None,
                 playlist_id: str = "") -> tuple[Path, Path]:
    """Return the (json, csv) paths for an export; generated names never overwrite a file.

    Playlists may share a title (and Windows/macOS ignore case), so a library
    backup names files after title + playlist ID, and a numeric suffix is added
    whenever the name is taken.
    """
    if output:
        json_path = Path(output).expanduser()
        if json_path.suffix.lower() != ".json":
            json_path = json_path.with_suffix(".json")
        return json_path, json_path.with_suffix(".csv")
    if folder is not None:
        base = f"{safe_filename(title)}-{safe_filename(playlist_id or 'playlist')}"
    else:
        base = f"{safe_filename(title)}-{datetime.now():%Y%m%d-%H%M%S}"
    parent = folder or exports_dir()
    json_path, n = parent / f"{base}.json", 2
    while json_path.exists() or json_path.with_suffix(".csv").exists():
        json_path, n = parent / f"{base}-{n}.json", n + 1
    return json_path, json_path.with_suffix(".csv")


def write_export(title: str, playlist_id: str, tracks: list[Track],
                 json_path: Path, csv_path: Path) -> None:
    """Write the portable JSON (re-importable) and a spreadsheet-friendly CSV."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "source": {"service": "ytmusic", "playlist_id": playlist_id, "title": title},
        "track_count": len(tracks),
        "tracks": [
            {
                "videoId": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "duration": t.duration,
                "isrc": t.isrc,
                "url": t.url,
            }
            for t in tracks
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for track in tracks:
            writer.writerow(track.to_csv_row())


def export_playlist(auth_file: str, playlist_id: str, output: str | None = None,
                    folder: Path | None = None) -> tuple[Path, Path, int]:
    """Fetch one playlist (or Liked Songs) and write it; returns (json, csv, count)."""
    title, tracks = fetch_source(auth_file, playlist_id)
    source_id = LIKED_ID if is_liked(playlist_id) else playlist_id
    json_path, csv_path = export_paths(title, output, folder, source_id)
    write_export(title, source_id, tracks, json_path, csv_path)
    return json_path, csv_path, len(tracks)


def export_library(auth_file: str, say: Callable[[str], None] = print) -> tuple[Path, int, list[str]]:
    """Export Liked Songs and every library playlist into one dated folder.

    Returns (folder, files written, names that failed). One failing playlist
    does not stop the rest.
    """
    folder = exports_dir() / f"backup-{datetime.now():%Y%m%d-%H%M%S}"
    sources = [(LIKED_ID, LIKED_TITLE)]
    sources += [(p["id"], p["title"]) for p in list_library_playlists(auth_file) if p["id"] != LIKED_ID]
    written, failed = 0, []
    for number, (playlist_id, title) in enumerate(sources, 1):
        try:
            _, _, count = export_playlist(auth_file, playlist_id, folder=folder)
        except Exception as e:  # ytmusicapi raises many types; report and continue
            logger.debug("Export of %s failed", playlist_id, exc_info=True)
            say(f"  [{number}/{len(sources)}] {title}: FAILED ({e})")
            failed.append(title)
            continue
        written += 1
        say(f"  [{number}/{len(sources)}] {title}: {count} tracks")
    return folder, written, failed


def video_id_from(entry: dict[str, object]) -> str:
    """Pull a YouTube video ID out of an export/CSV row ('' when there is none)."""
    for key in ("videoId", "trackId"):
        value = str(entry.get(key) or "").strip()
        if _VIDEO_ID_RE.match(value):
            return value
    match = _VIDEO_URL_RE.search(str(entry.get("url") or ""))
    return match.group(1) if match else ""


def read_import_file(path: Path) -> tuple[str, list[dict[str, object]]]:
    """Return (title, rows) from an export JSON, an enriched JSON or a CSV."""
    if path.suffix.lower() == ".csv":
        # Excel re-saves CSVs with ';' (many locales) and in the ANSI code page
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            first_line = fh.readline()
            fh.seek(0)
            delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
            try:
                return path.stem, list(csv.DictReader(fh, delimiter=delimiter))
            except csv.Error as e:
                raise TransferError(f"{path.name} is not a readable CSV ({e})") from e
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate a BOM
    except ValueError as e:
        raise TransferError(f"{path.name} is not valid JSON ({e})") from e
    rows = data.get("tracks") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise TransferError(f"{path.name} has no list of tracks")
    title = ""
    if isinstance(data, dict) and isinstance(data.get("source"), dict):
        title = str(data["source"].get("title") or "")
    return title or path.stem, [r for r in rows if isinstance(r, dict)]


def collect_source(auth_file: str, source: str) -> tuple[str, list[str], int]:
    """Resolve an import source to (title, video IDs, rows without a video ID).

    The source is an existing file, a YT Music playlist ID/link, or 'liked'.
    """
    path = Path(source).expanduser()
    if not path.is_file():
        candidate = cfg.resolve_user_path(source)  # e.g. data/exports/... in the settings folder
        path = candidate if candidate.is_file() else path
    if path.is_file():
        title, rows = read_import_file(path)
        ids = [video_id_from(r) for r in rows]
        return title, [v for v in ids if v], sum(1 for v in ids if not v)

    playlist_id = LIKED_ID if is_liked(source) else cfg.parse_ytmusic_playlist_id(source)
    if not playlist_id:
        raise TransferError(f"'{source}' is neither a file nor a YT Music playlist ID/link")
    title, tracks = fetch_source(auth_file, playlist_id)
    ids = [t.track_id for t in tracks]
    return title, [v for v in ids if v], sum(1 for v in ids if not v)


def _unique(items: Iterable[str]) -> list[str]:
    """Drop repeats, keeping the first occurrence and the original order."""
    return list(dict.fromkeys(items))


def import_to_playlist(
    auth_file: str,
    video_ids: list[str],
    target_id: str | None = None,
    new_name: str = "",
    privacy: str = "PRIVATE",
    dry_run: bool = False,
    say: Callable[[str], None] = print,
) -> dict[str, object]:
    """Add video IDs to an owned playlist (or a new one), skipping ones already there."""
    unique = _unique(video_ids)
    if target_id:
        existing, owned = get_playlist_video_ids(auth_file, target_id)
        if not owned:
            raise TransferError("That playlist belongs to someone else; you can only add "
                                "tracks to your own playlists (or create a new one).")
        present = set(existing)
        to_add = [v for v in unique if v not in present]
    else:
        to_add = unique
    summary: dict[str, object] = {
        "playlist_id": target_id or "",
        "duplicates_in_source": len(video_ids) - len(unique),
        "already_there": len(unique) - len(to_add),
        "to_add": len(to_add),
        "added": 0,
        "failed": [],
    }
    if dry_run or not to_add:
        return summary  # nothing to add: never create an empty playlist

    if not target_id:
        privacy = privacy.upper() if privacy.upper() in PRIVACY_CHOICES else "PRIVATE"
        target_id = create_yt_playlist(
            auth_file, new_name or "Imported playlist",
            f"Imported by ytmusic-to-spotify-sync on {datetime.now():%Y-%m-%d}", privacy,
        )
        summary["playlist_id"] = target_id
        say(f"Created YT Music playlist {target_id} ({privacy.lower()}).")
        say("If this import gets interrupted, finish it with --to " + target_id)

    failed: list[str] = []
    for start in range(0, len(to_add), IMPORT_BATCH):
        batch = to_add[start:start + IMPORT_BATCH]
        try:
            add_yt_playlist_items(auth_file, target_id, batch)
            summary["added"] = int(summary["added"]) + len(batch)
        except RuntimeError as batch_error:
            # One unavailable video can fail a whole batch; retry them one by one.
            # If the first singles fail too, YT Music is refusing the playlist or
            # the account (gated write, full playlist, outage): stop instead of
            # sending hundreds of requests and blaming every track.
            succeeded = 0
            for position, video_id in enumerate(batch):
                try:
                    add_yt_playlist_items(auth_file, target_id, [video_id])
                    summary["added"] = int(summary["added"]) + 1
                    succeeded += 1
                except RuntimeError as e:
                    logger.debug("Could not add %s: %s", video_id, e)
                    failed.append(video_id)
                    if succeeded == 0 and position >= 1:
                        raise TransferError(
                            f"YT Music refused every add ({batch_error}). "
                            f"{summary['added']} added so far; try again later with "
                            f"--to {target_id} to finish this playlist."
                        ) from e
                time.sleep(IMPORT_DELAY_SEC / 5)
        say(f"  {min(start + IMPORT_BATCH, len(to_add))}/{len(to_add)} processed")
        time.sleep(IMPORT_DELAY_SEC)
    summary["failed"] = failed
    return summary
