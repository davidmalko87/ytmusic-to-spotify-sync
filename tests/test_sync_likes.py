"""
Offline tests for sync-likes state: cumulative likes CSV, unmatched-likes retry,
the full Liked Songs fetch and the mass-unsave guard (fake YT Music and Spotify).
Author: David Malko
Date: 2026-10-21
Version: 0.8.0
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from playlist_sync import cli, ytmusic_client
from playlist_sync import config as cfg
from playlist_sync.csv_manager import read_enriched_csv, write_enriched_csv
from playlist_sync.models import MatchResult, Track


def like(n: int) -> Track:
    return Track(title=f"Song {n}", artist=f"Artist {n}", album="Album", platform="ytmusic",
                 track_id=f"vid{n:08d}", duration=200.0)


class FakeLikes:
    """YT Music likes plus a Spotify Liked Songs library that records every change."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        self.yt = [like(n) for n in range(1, 4)]
        self.saved: list[str] = []
        self.unsaved: list[str] = []
        self.searched: list[str] = []
        self.unmatchable: set[str] = set()

        (home / "browser.json").write_text("{}", encoding="utf-8")
        cfg.save_setting("SPOTIPY_CLIENT_ID", "a" * 32)
        cfg.save_setting("SPOTIPY_CLIENT_SECRET", "b" * 32)

        monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
        monkeypatch.setattr(cli, "fetch_liked_tracks", lambda auth: [
            Track(**{f: getattr(t, f) for f in ("title", "artist", "album", "platform",
                                                 "track_id", "duration")}) for t in self.yt])
        monkeypatch.setattr(cli, "get_spotify_client", lambda config: object())
        monkeypatch.setattr(cli, "match_track", self._match)
        monkeypatch.setattr(cli, "add_saved_tracks", self._save)
        monkeypatch.setattr(cli, "remove_saved_tracks", self._unsave)

    def _match(self, sp: object, track: Track) -> MatchResult:
        self.searched.append(track.track_id)
        if track.track_id in self.unmatchable:
            return MatchResult(source_track=track)
        return MatchResult(source_track=track, spotify_uri=f"spotify:track:{track.track_id}",
                           method="title_artist", confidence=0.9)

    def _save(self, sp: object, ids: list[str], dry_run: bool = False) -> int:
        if not dry_run:
            self.saved += [i for i in ids if i not in self.saved]
        return len(ids)

    def _unsave(self, sp: object, ids: list[str], dry_run: bool = False) -> int:
        if not dry_run:
            self.unsaved += ids
            self.saved = [i for i in self.saved if i not in ids]
        return len(ids)


@pytest.fixture
def likes(monkeypatch: pytest.MonkeyPatch, home: Path) -> FakeLikes:
    return FakeLikes(monkeypatch, home)


def sync_likes(**options: object) -> None:
    args = argparse.Namespace(command="sync-likes", verbose=False, dry_run=False,
                              retry_unmatched=False, force=False)
    for key, value in options.items():
        setattr(args, key, value)
    cli.dispatch(args)


def likes_rows() -> dict[str, Track]:
    return {t.track_id: t for t in read_enriched_csv(cfg.DATA_DIR / "likes_enriched.csv")}


# -- B-10: cumulative likes CSV ------------------------------------------------

def test_likes_csv_keeps_rows_from_earlier_runs(likes: FakeLikes) -> None:
    sync_likes()
    likes.yt.append(like(4))
    sync_likes()
    assert sorted(likes_rows()) == [f"vid{n:08d}" for n in range(1, 5)]


def test_unlike_of_a_song_synced_two_runs_ago_is_mirrored(likes: FakeLikes) -> None:
    sync_likes()
    likes.yt.append(like(4))
    sync_likes()
    likes.yt = [t for t in likes.yt if t.track_id != "vid00000001"]
    searches = len(likes.searched)
    sync_likes()
    assert likes.unsaved == ["vid00000001"]
    assert len(likes.searched) == searches  # nothing searched again
    assert "vid00000001" not in likes_rows()  # un-liked rows leave the CSV


def test_unmatched_likes_are_recorded_and_retried_on_request(likes: FakeLikes) -> None:
    likes.unmatchable = {"vid00000002"}
    sync_likes()
    row = likes_rows()["vid00000002"]
    assert row.match_attempted and not row.spotify_uri
    unmatched_csv = cfg.DATA_DIR / "likes_unmatched.csv"
    assert unmatched_csv.exists()

    likes.unmatchable = set()
    sync_likes()  # plain run: no quota spent on old failures
    assert likes.searched.count("vid00000002") == 1

    sync_likes(retry_unmatched=True)
    assert likes.searched.count("vid00000002") == 2
    assert "vid00000002" in likes.saved
    assert likes_rows()["vid00000002"].spotify_uri == "spotify:track:vid00000002"
    assert not unmatched_csv.exists()


def test_unmatched_like_reuses_a_later_playlist_match_for_free(likes: FakeLikes) -> None:
    likes.unmatchable = {"vid00000002"}
    sync_likes()
    playlist_row = like(2)
    playlist_row.spotify_uri, playlist_row.match_attempted = "spotify:track:fromplaylist", True
    write_enriched_csv([playlist_row])  # the playlist sync matched it meanwhile
    searches = len(likes.searched)
    sync_likes()
    assert len(likes.searched) == searches
    assert "fromplaylist" in likes.saved


def test_dry_run_writes_no_likes_state(likes: FakeLikes) -> None:
    sync_likes(dry_run=True)
    assert likes.saved == []
    assert not (cfg.DATA_DIR / "likes_enriched.csv").exists()
    assert not (cfg.LIKES_SNAPSHOTS_DIR / "latest.json").exists()


# -- B-30: full fetch and the mass-unsave guard ---------------------------------

def test_liked_songs_are_fetched_without_a_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeYTMusic:
        def get_liked_songs(self, limit: int | None = 100) -> dict:
            seen["limit"] = limit
            return {"tracks": []}

    monkeypatch.setattr(ytmusic_client, "get_ytmusic_client", lambda auth: FakeYTMusic())
    ytmusic_client.fetch_liked_tracks("browser.json")
    assert seen["limit"] is None


def test_a_sudden_drop_in_likes_unsaves_nothing(likes: FakeLikes) -> None:
    likes.yt = [like(n) for n in range(1, 201)]
    sync_likes()
    likes.yt = likes.yt[:100]  # a truncated answer: half the likes "vanish"
    sync_likes()
    assert likes.unsaved == []
    assert len(likes.saved) == 200
    assert len(likes_rows()) == 200  # their URIs are kept for a later, real unsave

    sync_likes(force=True)
    assert len(likes.unsaved) == 100


def test_a_normal_unlike_is_not_held_back(likes: FakeLikes) -> None:
    likes.yt = [like(n) for n in range(1, 201)]
    sync_likes()
    likes.yt = likes.yt[:-3]
    sync_likes()
    assert len(likes.unsaved) == 3
