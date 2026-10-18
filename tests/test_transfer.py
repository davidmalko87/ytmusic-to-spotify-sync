"""
Offline tests for YT Music export/import and the JSON copies of enriched data.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0

A fake YT Music account stands in for ytmusicapi; nothing touches the network.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from playlist_sync import cli, menu, yt_transfer
from playlist_sync import config as cfg
from playlist_sync.csv_manager import read_source_csv, write_enriched_csv
from playlist_sync.models import Track


def _track(n: int) -> Track:
    vid = f"vid{n:08d}"  # 11 characters, like a real YouTube video ID
    return Track(title=f"Song {n}", artist="Артист", album="Album", platform="ytmusic",
                 track_id=vid, duration=180.0, url=f"https://music.youtube.com/watch?v={vid}")


class FakeYT:
    """In-memory YT Music account: library, liked songs, owned playlists, writes."""

    def __init__(self) -> None:
        self.playlists: dict[str, dict] = {
            "PLmine": {"title": "Mine", "owned": True, "tracks": [_track(1), _track(2)]},
            "PLtheirs": {"title": "Theirs", "owned": False, "tracks": [_track(3)]},
        }
        self.liked = [_track(10), _track(11)]
        self.add_calls: list[list[str]] = []
        self.refuse: set[str] = set()
        self.created: list[tuple[str, str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        m = "playlist_sync.yt_transfer."
        monkeypatch.setattr(m + "fetch_liked_tracks", lambda auth: list(self.liked))
        monkeypatch.setattr(m + "fetch_playlist_tracks",
                            lambda auth, pid: list(self.playlists[pid]["tracks"]))
        monkeypatch.setattr(m + "get_playlist_info",
                            lambda auth, pid: {"id": pid, "title": self.playlists[pid]["title"]})
        monkeypatch.setattr(m + "list_library_playlists", lambda auth: [
            {"id": "LM", "title": "Liked Music", "count": "2", "owned": ""},
            *({"id": pid, "title": p["title"], "count": str(len(p["tracks"])),
               "owned": "yes" if p["owned"] else ""} for pid, p in self.playlists.items()),
        ])
        monkeypatch.setattr(m + "get_playlist_video_ids", lambda auth, pid: (
            [t.track_id for t in self.playlists[pid]["tracks"]], self.playlists[pid]["owned"]))
        monkeypatch.setattr(m + "create_yt_playlist", self._create)
        monkeypatch.setattr(m + "add_yt_playlist_items", self._add)
        monkeypatch.setattr(yt_transfer, "IMPORT_DELAY_SEC", 0)

    def _create(self, auth: str, title: str, description: str, privacy: str) -> str:
        pid = f"PLnew{len(self.created)}"
        self.created.append((title, privacy))
        self.playlists[pid] = {"title": title, "owned": True, "tracks": []}
        return pid

    def _add(self, auth: str, pid: str, video_ids: list[str]) -> None:
        self.add_calls.append(list(video_ids))
        if self.refuse & set(video_ids):
            raise RuntimeError("STATUS_FAILED")
        self.playlists[pid]["tracks"] += [Track(title="", artist="", track_id=v) for v in video_ids]


@pytest.fixture
def yt(monkeypatch: pytest.MonkeyPatch, home: Path) -> FakeYT:
    fake = FakeYT()
    fake.install(monkeypatch)
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)  # keep the real log clean
    (home / "browser.json").write_text("{}", encoding="utf-8")
    return fake


def test_export_liked_songs_writes_reimportable_json_and_csv(yt: FakeYT, home: Path) -> None:
    json_path, csv_path, count = yt_transfer.export_playlist("auth", "liked")
    assert count == 2 and json_path.parent == cfg.DATA_DIR / "exports"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["format"] == yt_transfer.EXPORT_FORMAT
    assert data["source"] == {"service": "ytmusic", "playlist_id": "LM", "title": "Liked Songs"}
    assert data["tracks"][0]["artist"] == "Артист"
    # the CSV is a normal playlist CSV export, usable with "Use a CSV export"
    assert [t.track_id for t in read_source_csv(csv_path)] == ["vid00000010", "vid00000011"]
    # and the JSON round-trips into an import source
    title, ids, without = yt_transfer.collect_source("auth", str(json_path))
    assert (title, ids, without) == ("Liked Songs", ["vid00000010", "vid00000011"], 0)


def test_export_library_keeps_going_after_one_failure(
    yt: FakeYT, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = yt_transfer.fetch_playlist_tracks

    def flaky(auth: str, pid: str) -> list[Track]:
        if pid == "PLtheirs":
            raise RuntimeError("boom")
        return real(auth, pid)

    monkeypatch.setattr(yt_transfer, "fetch_playlist_tracks", flaky)
    folder, written, failed = yt_transfer.export_library("auth", say=lambda text: None)
    assert written == 2 and failed == ["Theirs"]
    assert len(list(folder.glob("*.json"))) == 2 and len(list(folder.glob("*.csv"))) == 2


def test_import_into_new_playlist_in_batches(yt: FakeYT, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yt_transfer, "IMPORT_BATCH", 2)
    ids = [f"vid{n:08d}" for n in range(5)] + ["vid00000001"]  # one duplicate
    summary = yt_transfer.import_to_playlist("auth", ids, new_name="Copy <1>", privacy="unlisted",
                                             say=lambda text: None)
    assert summary["added"] == 5 and summary["duplicates_in_source"] == 1
    assert yt.created == [("Copy <1>", "UNLISTED")]
    assert [len(c) for c in yt.add_calls] == [2, 2, 1]


def test_import_into_own_playlist_skips_tracks_already_there(yt: FakeYT) -> None:
    summary = yt_transfer.import_to_playlist("auth", ["vid00000001", "vid00000005"],
                                             target_id="PLmine", say=lambda text: None)
    assert summary["already_there"] == 1 and summary["added"] == 1
    assert yt.add_calls == [["vid00000005"]]
    # running it again adds nothing: interrupted imports can simply be repeated
    again = yt_transfer.import_to_playlist("auth", ["vid00000001", "vid00000005"],
                                           target_id="PLmine", say=lambda text: None)
    assert again["to_add"] == 0 and len(yt.add_calls) == 1


def test_import_refuses_someone_elses_playlist(yt: FakeYT) -> None:
    with pytest.raises(yt_transfer.TransferError):
        yt_transfer.import_to_playlist("auth", ["vid00000005"], target_id="PLtheirs")


def test_unavailable_video_does_not_sink_the_whole_batch(yt: FakeYT) -> None:
    yt.refuse = {"vid00000007"}
    summary = yt_transfer.import_to_playlist(
        "auth", ["vid00000006", "vid00000007", "vid00000008"], target_id="PLmine",
        say=lambda text: None)
    assert summary["added"] == 2 and summary["failed"] == ["vid00000007"]


def test_dry_run_and_empty_source_never_write(yt: FakeYT) -> None:
    summary = yt_transfer.import_to_playlist("auth", ["vid00000006"], new_name="X", dry_run=True)
    assert summary["to_add"] == 1 and yt.created == [] and yt.add_calls == []
    yt_transfer.import_to_playlist("auth", [], new_name="Empty")
    assert yt.created == []


@pytest.mark.parametrize(("row", "expected"), [
    ({"videoId": "abcdefghijk"}, "abcdefghijk"),
    ({"trackId": "abcdefghijk"}, "abcdefghijk"),
    ({"url": "https://music.youtube.com/watch?v=abcdefghijk&list=PL1"}, "abcdefghijk"),
    ({"trackId": "4uLU6hMCjMI75M1A2tKUQC"}, ""),  # a Spotify ID is not a YouTube video
    ({}, ""),
])
def test_video_id_from_rows(row: dict, expected: str) -> None:
    assert yt_transfer.video_id_from(row) == expected


def test_cli_copy_playlist_straight_into_a_new_one(
    yt: FakeYT, capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(command="import-yt", verbose=False, source="PLmine", to=None,
                              new_name=None, privacy="private", dry_run=False)
    cli.dispatch(args)
    out = capsys.readouterr().out
    assert "Added 2 track(s)" in out
    assert yt.created == [("Mine", "PRIVATE")]


def test_cli_export_without_any_playlist_explains_options(
    yt: FakeYT, capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(command="export-yt", verbose=False, playlist=None, all=False,
                              output=None, dry_run=False)
    with pytest.raises(SystemExit):
        cli.dispatch(args)
    assert "--playlist liked" in capsys.readouterr().out


def test_enriched_csv_always_gets_a_json_copy(home: Path) -> None:
    target = cfg.DATA_DIR / "likes_enriched.csv"
    write_enriched_csv([_track(1), _track(2)], path=target)
    data = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["track_count"] == 2
    assert data["tracks"][1]["trackId"] == "vid00000002"


def test_menu_export_liked_songs(
    yt: FakeYT, monkeypatch: pytest.MonkeyPatch,
    keyboard: Callable[[list[str]], list[str]],
) -> None:
    dispatched: list[argparse.Namespace] = []
    monkeypatch.setattr(menu, "list_library_playlists", lambda auth: [])
    monkeypatch.setattr(cli, "dispatch", dispatched.append)
    keyboard(["l"])
    menu.action_export_yt()
    assert [(a.command, a.playlist) for a in dispatched] == [("export-yt", "LM")]


def test_menu_import_latest_export_into_new_private_playlist(
    yt: FakeYT, monkeypatch: pytest.MonkeyPatch,
    keyboard: Callable[[list[str]], list[str]],
) -> None:
    json_path, _, _ = yt_transfer.export_playlist("auth", "liked")
    dispatched: list[argparse.Namespace] = []
    monkeypatch.setattr(menu, "list_library_playlists", lambda auth: [])
    monkeypatch.setattr(cli, "dispatch", dispatched.append)
    # file 1, Enter = new playlist, keep name, Enter = private, Enter = real run
    keyboard(["1", "", "", "", ""])
    menu.action_import_yt()
    (args,) = dispatched
    assert (args.command, args.source, args.new_name, args.privacy, args.dry_run) == (
        "import-yt", str(json_path), "Liked Songs", "private", False)


def test_library_backup_keeps_playlists_that_share_a_title(yt: FakeYT) -> None:
    yt.playlists["PLtwin"] = {"title": "Mine", "owned": True, "tracks": [_track(7)]}
    yt.playlists["PLcase"] = {"title": "mine", "owned": True, "tracks": [_track(8)]}
    folder, written, failed = yt_transfer.export_library("auth", say=lambda text: None)
    assert (written, failed) == (5, [])
    assert len(list(folder.glob("*.json"))) == 5  # nothing overwritten, even case-only twins


def test_import_reads_bom_json_and_semicolon_csv(yt: FakeYT, home: Path) -> None:
    bom = home / "edited.json"
    bom.write_text(json.dumps({"tracks": [{"videoId": "abcdefghijk"}]}), encoding="utf-8-sig")
    assert yt_transfer.collect_source("auth", str(bom))[1] == ["abcdefghijk"]

    excel = home / "excel.csv"
    excel.write_bytes("title;artist;trackId\nCaf\xe9;X;abcdefghijk\n".encode("cp1252"))
    title, ids, without = yt_transfer.collect_source("auth", str(excel))
    assert ids == ["abcdefghijk"] and without == 0


def test_account_wide_refusal_stops_instead_of_blaming_every_track(yt: FakeYT) -> None:
    yt.refuse = {f"vid{n:08d}" for n in range(20, 40)}
    with pytest.raises(yt_transfer.TransferError, match="refused every add"):
        yt_transfer.import_to_playlist("auth", sorted(yt.refuse), target_id="PLmine",
                                       say=lambda text: None)
    assert len(yt.add_calls) == 3  # one batch, then two singles - not 20 requests
