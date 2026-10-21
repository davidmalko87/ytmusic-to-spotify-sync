"""
Offline end-to-end tests for cmd_sync's data integrity (fake YT Music and Spotify).
Author: David Malko
Date: 2026-10-18
Version: 0.8.0

Covers what the reviews found: a source switch must not remove tracks, and a
stopped, rate-limited or dry run must neither lose rows nor leave matches that
never reach the Spotify playlist.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from playlist_sync import cli
from playlist_sync import config as cfg
from playlist_sync.csv_manager import read_enriched_csv, write_enriched_csv
from playlist_sync.models import MatchResult, Track
from playlist_sync.spotify_client import RateLimitError

YT_ID = "PLd1XLAKnZQaXUPjf1khkyTNhg-KqHofWe"


def yt_track(n: int) -> Track:
    return Track(title=f"Song {n}", artist=f"Artist {n}", album="Album", platform="ytmusic",
                 track_id=f"vid{n:08d}", duration=200.0)


class FakeWorld:
    """YT Music playlist contents plus a Spotify playlist that records every change."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        self.yt = [yt_track(n) for n in range(1, 6)]
        self.playlist: list[str] = []
        self.removed: list[str] = []
        self.match_calls = 0
        self.fail_on_match: int | None = None
        self.fail_with: BaseException | None = None

        (home / "browser.json").write_text("{}", encoding="utf-8")
        for key, value in (("SPOTIPY_CLIENT_ID", "a" * 32), ("SPOTIPY_CLIENT_SECRET", "b" * 32),
                           ("SPOTIFY_PLAYLIST_ID", "37i9dQZF1DXcBWIGoYBM5M"),
                           ("YTMUSIC_PLAYLIST_ID", YT_ID)):
            cfg.save_setting(key, value)

        monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
        monkeypatch.setattr(cli, "fetch_playlist_tracks", lambda auth, pid: [
            Track(**{f: getattr(t, f) for f in ("title", "artist", "album", "platform",
                                                 "track_id", "duration")}) for t in self.yt])
        monkeypatch.setattr(cli, "get_spotify_client", lambda config: object())
        monkeypatch.setattr(cli, "match_track", self._match)
        monkeypatch.setattr(cli, "add_tracks_to_playlist", self._add)
        monkeypatch.setattr(cli, "remove_tracks_from_playlist", self._remove)
        monkeypatch.setattr(cli, "get_playlist_tracks",
                            lambda sp, pid: [{"uri": u} for u in self.playlist])
        for name in ("backfill_track_metadata", "backfill_artist_genres",
                     "enrich_with_audio_features"):
            monkeypatch.setattr(cli, name, lambda sp, tracks: tracks)

    def _match(self, sp: object, track: Track) -> MatchResult:
        self.match_calls += 1
        if self.fail_on_match is not None and self.match_calls == self.fail_on_match:
            raise self.fail_with  # type: ignore[misc]
        return MatchResult(source_track=track, spotify_uri=f"spotify:track:{track.track_id}",
                           method="isrc", confidence=1.0)

    def _add(self, sp: object, pid: str, uris: list[str], dry_run: bool = False) -> int:
        if not dry_run:
            self.playlist += uris
        return len(uris)

    def _remove(self, sp: object, pid: str, uris: list[str], dry_run: bool = False) -> int:
        if not dry_run:
            self.removed += uris
            self.playlist = [u for u in self.playlist if u not in uris]
        return len(uris)


def sync(**options: object) -> None:
    args = argparse.Namespace(command="sync", verbose=False, dry_run=False, from_csv=None,
                              limit=None, retry_unmatched=False)
    for key, value in options.items():
        setattr(args, key, value)
    cli.dispatch(args)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, home: Path) -> FakeWorld:
    return FakeWorld(monkeypatch, home)


def test_full_sync_then_nothing_to_do(world: FakeWorld) -> None:
    sync()
    assert len(world.playlist) == 5
    sync()
    assert len(world.playlist) == 5 and world.removed == []


def test_csv_sync_after_yt_sync_removes_nothing(world: FakeWorld, home: Path) -> None:
    sync()
    export = home / "other.csv"
    export.write_text("title,artist,album,trackId,platform\nNew,Someone,Alb,vid00000099,ytmusic\n",
                      encoding="utf-8")
    sync(from_csv=str(export))
    assert world.removed == []
    assert "spotify:track:vid00000099" in world.playlist


def test_ctrl_c_mid_sync_pushes_matches_and_keeps_every_row(world: FakeWorld) -> None:
    world.fail_on_match, world.fail_with = 4, KeyboardInterrupt()
    sync()
    assert world.playlist == [f"spotify:track:vid{n:08d}" for n in range(1, 4)]
    assert len(read_enriched_csv()) == 5  # unsearched tracks are kept, not dropped

    world.fail_on_match = None
    sync()
    assert sorted(world.playlist) == sorted(f"spotify:track:vid{n:08d}" for n in range(1, 6))


def test_rate_limit_defers_the_push_to_the_next_run(world: FakeWorld) -> None:
    world.fail_on_match, world.fail_with = 3, RateLimitError(7200)
    sync()
    assert world.playlist == []  # no Spotify calls while rate-limited
    world.fail_on_match = None
    sync()
    assert sorted(world.playlist) == sorted(f"spotify:track:vid{n:08d}" for n in range(1, 6))
    assert len(world.playlist) == len(set(world.playlist))  # nothing added twice


def test_stopped_sync_does_not_lose_a_youtube_removal(world: FakeWorld) -> None:
    sync()
    world.yt = world.yt[1:] + [yt_track(6), yt_track(7)]  # song 1 removed, 6 and 7 added
    world.fail_on_match, world.fail_with = world.match_calls + 2, KeyboardInterrupt()
    sync()
    assert "spotify:track:vid00000001" in world.playlist  # not removed during the stop
    world.fail_on_match = None
    sync()
    assert "spotify:track:vid00000001" not in world.playlist  # removed on the next run


def test_dry_run_leaves_no_cache_and_pushes_nothing(world: FakeWorld) -> None:
    sync(dry_run=True)
    assert world.playlist == [] and not cfg.MATCH_CACHE.exists()
    sync()
    assert len(world.playlist) == 5


def test_limited_runs_keep_lastfm_data_of_unsearched_rows(world: FakeWorld) -> None:
    rows = [yt_track(n) for n in range(1, 6)]
    for row in rows:
        row.lastfm_listeners = 1234
    write_enriched_csv(rows)  # like a 'lastfm' run on the snapshot, before any sync
    sync(limit=2)
    kept = read_enriched_csv()
    assert len(kept) == 5
    assert all(t.lastfm_listeners == 1234 for t in kept)
    assert len(world.playlist) == 2
