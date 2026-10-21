"""
Offline tests for CSV/JSON data formats: duration parsing, typed JSON copies, status.
Author: David Malko
Date: 2026-10-21
Version: 0.8.0
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pytest

from playlist_sync import cli, models
from playlist_sync import config as cfg
from playlist_sync.csv_manager import (
    CsvFormatError,
    read_enriched_csv,
    read_source_csv,
    write_enriched_csv,
    write_tracks_json,
)
from playlist_sync.models import Track, parse_duration

# -- A-13: duration cells ------------------------------------------------------

@pytest.mark.parametrize(("cell", "seconds"), [
    ("", 0.0), ("  ", 0.0), ("245", 245.0), ("245.5", 245.5), ("225.0", 225.0),
    ("3:45", 225.0), ("03:05", 185.0), ("0:59", 59.0), ("75:00", 4500.0),
    ("1:02:03", 3723.0), ("3:45.5", 225.5), (" 3:45 ", 225.0),
])
def test_parse_duration_accepts_seconds_and_clock_times(cell: str, seconds: float) -> None:
    assert parse_duration(cell) == seconds


@pytest.mark.parametrize("cell", ["abc", "3:4x", "1:2:3:4", "nan", "inf", "3,45", ":45"])
def test_parse_duration_rejects_garbage_with_the_value_in_the_message(cell: str) -> None:
    with pytest.raises(ValueError, match="duration"):
        parse_duration(cell)


def test_csv_round_trip_is_unchanged_for_plain_seconds() -> None:
    track = Track(title="t", artist="a", duration=201.0)
    assert Track.from_csv_row(track.to_csv_row()) == track


def test_source_csv_with_clock_durations_loads(home: Path) -> None:
    src = home / "export.csv"
    src.write_text("title,artist,duration\nA,B,3:45\nC,D,1:02:03\nE,F,\n", encoding="utf-8")
    assert [t.duration for t in read_source_csv(src)] == [225.0, 3723.0, 0.0]


def test_bad_cell_names_file_and_row(home: Path) -> None:
    src = home / "export.csv"
    src.write_text("title,artist,duration\nA,B,3:45\nC,D,soon\n", encoding="utf-8")
    with pytest.raises(CsvFormatError, match=r"export\.csv, row 3: duration 'soon'"):
        read_source_csv(src)


def _cli(command: str, monkeypatch: pytest.MonkeyPatch, **options: object) -> None:
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    args = argparse.Namespace(command=command, verbose=False, dry_run=False, csv=None)
    for key, value in options.items():
        setattr(args, key, value)
    cli.dispatch(args)


def test_import_csv_with_clock_durations_writes_seconds(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = home / "export.csv"
    src.write_text("title,artist,duration\nSong,Artist,3:45\n", encoding="utf-8")
    _cli("import-csv", monkeypatch, csv=str(src))
    assert [t.duration for t in read_enriched_csv()] == [225.0]


def test_import_csv_bad_cell_exits_cleanly_without_traceback(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    src = home / "export.csv"
    src.write_text("title,artist,duration\nSong,Artist,soon\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        _cli("import-csv", monkeypatch, csv=str(src))
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "export.csv, row 2" in out and "Traceback" not in out
    assert not cfg.ENRICHED_CSV.exists()


# -- ME-M-6: typed JSON copies -------------------------------------------------

def _rich_track() -> Track:
    return Track(
        title="Song", artist="Artist", album="Album", isrc="USRC17607839", platform="ytmusic",
        track_id="vid00000001", duration=201.0, spotify_uri="spotify:track:1",
        spotify_url="https://open.spotify.com/track/1", spotify_duration_ms=201000,
        explicit=False, album_release_date="2020-01-01", danceability=0.5123, energy=0.7,
        valence=0.25, tempo=120.04, key=0, mode=1, loudness=-5.5, speechiness=0.04,
        acousticness=0.1, instrumentalness=0.0, liveness=0.11, time_signature=4,
        audio_features_fetched=True, popularity=55, artist_genres="rock", album_type="album",
        track_number=3, lastfm_playcount=1234, lastfm_listeners=99, lastfm_tags="rock",
        artist_tags="rock, indie", tag_source="artist", lastfm_attempted=True,
        lastfm_track_attempted=True, primary_genre="rock", mood="happy",
        spotify_metadata_attempted=True, spotify_genres_attempted=True, match_attempted=True,
        match_method="isrc", match_confidence=0.95, first_synced="2026-10-01",
        last_synced="2026-10-02",
    )


def test_type_tables_name_real_columns() -> None:
    typed = models._JSON_INT_COLUMNS | models._JSON_FLOAT_COLUMNS | models._JSON_BOOL_COLUMNS
    assert typed <= set(cfg.ENRICHED_COLUMNS)  # a typo would silently stay a string
    assert list(_rich_track().to_json_dict()) == cfg.ENRICHED_COLUMNS


def test_json_copy_uses_numbers_booleans_and_null(home: Path) -> None:
    unmatched = Track(title="x", artist="y", platform="ytmusic", track_id="vid00000002")
    write_enriched_csv([_rich_track(), unmatched])
    rows = json.loads(cfg.ENRICHED_CSV.with_suffix(".json").read_text(encoding="utf-8"))["tracks"]
    rich, bare = rows
    assert rich["duration"] == 201.0 and rich["popularity"] == 55 and rich["key"] == 0
    assert rich["danceability"] == 0.5123 and rich["tempo"] == 120.0  # CSV rounding kept
    assert rich["explicit"] is False and rich["match_attempted"] is True
    assert rich["trackId"] == "vid00000001" and rich["isrc"] == "USRC17607839"
    assert bare["explicit"] is None and bare["popularity"] is None and bare["key"] is None
    assert bare["duration"] is None and bare["lastfm_attempted"] is False
    assert bare["spotify_uri"] == ""


def test_json_copy_carries_the_same_data_as_the_csv(home: Path) -> None:
    track = _rich_track()
    out = write_tracks_json([track], home / "copy.json")
    row = json.loads(out.read_text(encoding="utf-8"))["tracks"][0]
    as_text = {k: "true" if v is True else "" if v is None or v is False else str(v)
               for k, v in row.items()}
    assert Track.from_csv_row(as_text) == Track.from_csv_row(track.to_csv_row())


def test_json_copy_never_writes_nan(home: Path) -> None:
    track = Track(title="x", artist="y", audio_features_fetched=True, loudness=math.nan)
    out = write_tracks_json([track], home / "copy.json")
    text = out.read_text(encoding="utf-8")
    assert "NaN" not in text and json.loads(text)["tracks"][0]["loudness"] is None


def test_export_command_writes_typed_json(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_enriched_csv([_rich_track()])
    target = home / "out.json"
    _cli("export", monkeypatch, output=str(target))
    assert json.loads(target.read_text(encoding="utf-8"))["tracks"][0]["lastfm_playcount"] == 1234


# -- ME-M-4: status tells unset from missing -----------------------------------

def _status(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    _cli("status", monkeypatch)
    lines = [line for line in capsys.readouterr().out.splitlines() if "Source CSV" in line]
    assert len(lines) == 1
    return lines[0]


def test_status_without_source_csv_says_not_configured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    assert "not configured" in _status(monkeypatch, capsys)


def test_status_with_missing_source_csv_shows_the_resolved_path(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    cfg.save_setting("SOURCE_CSV", "exports/gone.csv")
    line = _status(monkeypatch, capsys)
    assert "not configured" not in line
    assert str(home / "exports" / "gone.csv") in line


def test_status_with_source_csv_folder_is_not_read(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    (home / "exports").mkdir()
    cfg.save_setting("SOURCE_CSV", "exports")
    assert "no file at" in _status(monkeypatch, capsys)


def test_status_with_unreadable_source_csv_keeps_going(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    src = home / "export.csv"
    src.write_text("title,artist,duration\nA,B,soon\n", encoding="utf-8")
    cfg.save_setting("SOURCE_CSV", str(src))
    _cli("status", monkeypatch)
    out = capsys.readouterr().out
    assert "cannot read it (export.csv, row 2" in out and "Snapshots:" in out


def test_status_with_working_source_csv_counts_tracks(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    src = home / "export.csv"
    src.write_text("title,artist,duration\nA,B,3:45\nC,D,200\n", encoding="utf-8")
    cfg.save_setting("SOURCE_CSV", str(src))
    assert "2 tracks (export.csv)" in _status(monkeypatch, capsys)
