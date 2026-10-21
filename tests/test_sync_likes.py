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
from playlist_sync.spotify_client import RateLimitError, SearchUnavailable


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
        self.uri_of: dict[str, str] = {}          # track_id -> Spotify ID (default: same)
        self.unavailable: set[str] = set()        # searches that fail (SearchUnavailable)
        self.search_error: BaseException | None = None
        self.save_error: BaseException | None = None
        self.refuse_save: set[str] = set()        # Spotify IDs whose save fails
        self.refuse_unsave: set[str] = set()

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
        if self.search_error is not None:
            raise self.search_error
        if track.track_id in self.unavailable:
            raise SearchUnavailable("HTTP 503")
        if track.track_id in self.unmatchable:
            return MatchResult(source_track=track)
        spotify_id = self.uri_of.get(track.track_id, track.track_id)
        return MatchResult(source_track=track, spotify_uri=f"spotify:track:{spotify_id}",
                           method="title_artist", confidence=0.9)

    def _save(self, sp: object, ids: list[str], dry_run: bool = False,
              failed: list[str] | None = None) -> int:
        if self.save_error is not None:
            raise self.save_error
        ok = [i for i in ids if i not in self.refuse_save]
        if failed is not None:
            failed += [i for i in ids if i in self.refuse_save]
        if not dry_run:
            self.saved += [i for i in ok if i not in self.saved]
        return len(ok)

    def _unsave(self, sp: object, ids: list[str], dry_run: bool = False,
                failed: list[str] | None = None) -> int:
        ok = [i for i in ids if i not in self.refuse_unsave]
        if failed is not None:
            failed += [i for i in ids if i in self.refuse_unsave]
        if not dry_run:
            self.unsaved += ok
            self.saved = [i for i in self.saved if i not in ok]
        return len(ok)


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


# -- #21: a Spotify track two liked entries map to ---------------------------------

def test_unlike_keeps_a_track_another_like_still_maps_to(likes: FakeLikes, capsys) -> None:
    likes.uri_of = {"vid00000001": "SHARED", "vid00000002": "SHARED"}  # song + music video
    sync_likes()
    likes.yt = [t for t in likes.yt if t.track_id != "vid00000001"]
    sync_likes()
    assert likes.unsaved == [] and "SHARED" in likes.saved
    assert "Kept on Spotify: 1" in capsys.readouterr().out
    likes.yt = [t for t in likes.yt if t.track_id != "vid00000002"]
    sync_likes()
    assert likes.unsaved == ["SHARED"]


# -- #11 / #23: a failed save is retried -------------------------------------------

def test_failed_save_exits_1_and_is_retried_without_a_search(likes: FakeLikes) -> None:
    likes.refuse_save = {"vid00000003"}
    with pytest.raises(SystemExit) as stop:
        sync_likes()
    assert stop.value.code == 1 and "vid00000003" not in likes.saved
    likes.refuse_save = set()
    searches = len(likes.searched)
    sync_likes()
    assert "vid00000003" in likes.saved
    assert len(likes.searched) == searches  # URI reused from likes_enriched.csv


# -- #16 / #24: a failed unsave is retried -----------------------------------------

def test_failed_unsave_exits_1_and_is_retried(likes: FakeLikes) -> None:
    sync_likes()
    likes.yt = likes.yt[1:]
    likes.refuse_unsave = {"vid00000001"}
    with pytest.raises(SystemExit) as stop:
        sync_likes()
    assert stop.value.code == 1 and "vid00000001" in likes.saved
    likes.refuse_unsave = set()
    sync_likes()
    assert likes.unsaved == ["vid00000001"]


# -- #18 / #22: failed searches and rate limits keep the run's work ----------------

def test_one_failed_search_skips_only_that_like(likes: FakeLikes) -> None:
    likes.unavailable = {"vid00000002"}
    sync_likes()  # exit 0, like cmd_sync
    assert sorted(likes.saved) == ["vid00000001", "vid00000003"]
    likes.unavailable = set()
    sync_likes()
    assert sorted(likes.saved) == ["vid00000001", "vid00000002", "vid00000003"]


def test_rate_limit_during_matching_keeps_the_matches_so_far(likes: FakeLikes,
                                                            monkeypatch) -> None:
    real_match = likes._match

    def limited_after_one(sp: object, track: Track) -> MatchResult:
        if likes.searched:
            likes.searched.append(track.track_id)
            raise RateLimitError(7200)
        return real_match(sp, track)
    monkeypatch.setattr(cli, "match_track", limited_after_one)
    sync_likes()
    assert likes.saved == []  # no Spotify writes during a ban
    assert likes_rows()["vid00000001"].spotify_uri == "spotify:track:vid00000001"

    monkeypatch.setattr(cli, "match_track", real_match)
    likes.searched = []
    sync_likes()
    assert sorted(likes.saved) == ["vid00000001", "vid00000002", "vid00000003"]
    assert sorted(likes.searched) == ["vid00000002", "vid00000003"]  # 1 reused


# -- #20 / #25: Spotify stops answering while saving --------------------------------

def test_network_error_while_saving_exits_1_and_keeps_the_matches(likes: FakeLikes,
                                                                  capsys) -> None:
    import requests

    likes.save_error = requests.exceptions.ConnectionError("network is down")
    with pytest.raises(SystemExit) as stop:
        sync_likes()
    assert stop.value.code == 1 and "stopped accepting changes" in capsys.readouterr().out
    assert len(likes_rows()) == 3
    likes.save_error = None
    searches = len(likes.searched)
    sync_likes()
    assert sorted(likes.saved) == ["vid00000001", "vid00000002", "vid00000003"]
    assert len(likes.searched) == searches


# -- #26: likes without a row (0.7 upgrade, locked CSV) -----------------------------

def test_likes_missing_from_the_csv_get_rows_and_their_unlike_is_mirrored(
        likes: FakeLikes) -> None:
    sync_likes()
    # 0.7 kept only the last run's likes in the CSV; the playlist sync knows song 1
    write_enriched_csv([likes_rows()["vid00000003"]], cfg.DATA_DIR / "likes_enriched.csv")
    playlist_row = like(1)
    playlist_row.spotify_uri, playlist_row.match_attempted = "spotify:track:vid00000001", True
    write_enriched_csv([playlist_row])
    sync_likes()
    rows = likes_rows()
    assert sorted(rows) == ["vid00000001", "vid00000002", "vid00000003"]
    assert rows["vid00000001"].spotify_uri and not rows["vid00000002"].match_attempted

    searches = len(likes.searched)
    likes.yt = [likes.yt[2]]  # un-like songs 1 and 2
    sync_likes()
    assert sorted(likes.unsaved) == ["vid00000001", "vid00000002"]
    assert likes.searched[searches:] == ["vid00000002"]  # no URI known: searched once


def lock_likes_csv(monkeypatch) -> None:
    from playlist_sync import csv_manager

    real_to_csv = csv_manager.pd.DataFrame.to_csv

    def locked(self, path, *a, **k):
        if str(path).endswith("likes_enriched.csv"):
            raise PermissionError(13, "locked")
        return real_to_csv(self, path, *a, **k)
    monkeypatch.setattr(csv_manager.pd.DataFrame, "to_csv", locked)


def test_locked_likes_csv_leaves_new_likes_for_the_next_run(likes: FakeLikes,
                                                           monkeypatch) -> None:
    with monkeypatch.context() as m:
        lock_likes_csv(m)
        with pytest.raises(SystemExit) as stop:
            sync_likes()
        assert stop.value.code == 1  # the CSV did not get this run's rows
    sync_likes()
    assert sorted(likes_rows()) == ["vid00000001", "vid00000002", "vid00000003"]


def test_relike_after_an_unsave_with_the_csv_locked_is_saved_again(likes: FakeLikes,
                                                                    monkeypatch) -> None:
    sync_likes()
    likes.yt = likes.yt[1:]  # un-like song 1 while likes_enriched.csv is open in Excel
    with monkeypatch.context() as m:
        lock_likes_csv(m)
        with pytest.raises(SystemExit):
            sync_likes()
    assert likes.unsaved == ["vid00000001"]
    likes.yt = [like(1)] + likes.yt  # liked again, Excel closed
    sync_likes()
    assert "vid00000001" in likes.saved


# -- #27: unsave the URI sync-likes saved -------------------------------------------

def test_unlike_unsaves_the_uri_sync_likes_saved(likes: FakeLikes) -> None:
    sync_likes()  # saved spotify:track:vid00000001
    playlist_row = like(1)
    playlist_row.spotify_uri, playlist_row.match_attempted = "spotify:track:OTHER", True
    write_enriched_csv([playlist_row])  # the playlist sync picked another release
    likes.yt = likes.yt[1:]
    sync_likes()
    assert likes.unsaved == ["vid00000001"]


# -- #28: the menu's yes covers only the likes it showed ----------------------------

def test_forced_run_unsaves_only_the_confirmed_likes(likes: FakeLikes) -> None:
    likes.yt = [like(n) for n in range(1, 201)]
    sync_likes()
    likes.yt = likes.yt[:100]
    args = argparse.Namespace(command="sync-likes", verbose=False, dry_run=False,
                              retry_unmatched=False, force=False)
    cli.dispatch(args)
    confirmed = args.held_back_fps
    assert len(confirmed) == 100
    likes.yt = likes.yt[:50]  # the second answer is truncated even more
    sync_likes(force=True, confirmed_fps=confirmed)
    assert len(likes.unsaved) == 100
    assert all(f"vid{n:08d}" in likes.saved for n in range(51, 101))


# -- #29: likes_unmatched.csv open in Excel -----------------------------------------

def test_open_likes_unmatched_csv_does_not_stop_the_snapshot(likes: FakeLikes,
                                                            monkeypatch) -> None:
    likes.unmatchable = {"vid00000002"}
    sync_likes()
    likes.unmatchable = set()
    real_unlink = Path.unlink

    def locked(self: Path, missing_ok: bool = False) -> None:
        if self.name == "likes_unmatched.csv":
            raise PermissionError(32, "being used by another process")
        real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", locked)
    sync_likes(retry_unmatched=True)
    assert "vid00000002" in likes.saved
    snapshot = (cfg.LIKES_SNAPSHOTS_DIR / "latest.json").read_text(encoding="utf-8")
    assert "vid00000002" in snapshot


# -- second review: un-likes, Ctrl+C while writing, recovered 0.7 likes ---------

def test_unlike_of_a_like_that_was_never_saved_unsaves_nothing(likes: FakeLikes) -> None:
    video = like(9)
    video.album = ""  # a liked music video: skipped, never saved
    likes.yt.append(video)
    likes.unmatchable = {"vid00000002"}
    sync_likes()
    likes.saved += ["vid00000009", "vid00000002"]  # liked on Spotify by the user directly
    searches = len(likes.searched)
    likes.yt = [likes.yt[0], likes.yt[2]]  # un-like song 2 and the video
    sync_likes()
    assert likes.unsaved == [] and len(likes.searched) == searches


def test_ctrl_c_while_saving_keeps_the_run(likes: FakeLikes, monkeypatch) -> None:
    def interrupted(sp, ids, dry_run=False, failed=None):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "add_saved_tracks", interrupted)
    try:
        sync_likes()
    except KeyboardInterrupt:  # a real one would abort the whole pytest session
        pytest.fail("Ctrl+C while saving escaped as a traceback")
    assert len(likes_rows()) == 3
    monkeypatch.setattr(cli, "add_saved_tracks", likes._save)
    searches = len(likes.searched)
    sync_likes()
    assert sorted(likes.saved) == ["vid00000001", "vid00000002", "vid00000003"]
    assert len(likes.searched) == searches  # saved from the kept rows


def test_recovered_0_7_like_with_a_playlist_match_is_saved(likes: FakeLikes) -> None:
    likes.unmatchable = {"vid00000002"}
    sync_likes()  # song 2: no match, never saved
    # 0.7 overwrote likes_enriched.csv with the last run only; the playlist sync
    # matched song 2 meanwhile
    write_enriched_csv([likes_rows()["vid00000003"]], cfg.DATA_DIR / "likes_enriched.csv")
    playlist_row = like(2)
    playlist_row.spotify_uri, playlist_row.match_attempted = "spotify:track:vid00000002", True
    write_enriched_csv([playlist_row])
    sync_likes()
    assert "vid00000002" in likes.saved
