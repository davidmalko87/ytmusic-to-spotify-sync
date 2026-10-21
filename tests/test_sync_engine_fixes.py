"""
Offline regression tests for the sync-engine fixes (B-15, MS-M-4, ME-M-3, MS-M-2, B-12).
Author: David Malko
Date: 2026-10-21
Version: 0.8.0

Reuses the FakeWorld harness from test_sync.py: a fake YT Music playlist and a
Spotify playlist that records every add and remove.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import pytest

from playlist_sync import cli, matcher
from playlist_sync import config as cfg
from playlist_sync.csv_manager import read_enriched_csv
from playlist_sync.models import MatchResult, Track
from tests.test_sync import FakeWorld, sync, yt_track

URI = "spotify:track:vid{:08d}".format


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, home: Path) -> FakeWorld:
    return FakeWorld(monkeypatch, home)


def run(command: str, **options: object) -> None:
    args = argparse.Namespace(command=command, verbose=False, dry_run=False)
    for key, value in options.items():
        setattr(args, key, value)
    cli.dispatch(args)


# -- B-15: summary counts ------------------------------------------------------

def test_limited_sync_reports_the_tracks_still_pending(world: FakeWorld, capsys) -> None:
    world.yt = [yt_track(n) for n in range(1, 5)]
    sync(limit=2)
    out = capsys.readouterr().out
    assert "Already matched: 0" in out and "Newly matched:   2" in out
    assert "Partial sync complete. 2 tracks still need matching." in out
    assert "Sync complete!" not in out
    sync(limit=2)
    out = capsys.readouterr().out
    assert "Already matched: 2" in out and "Newly matched:   2" in out
    assert "Sync complete!" in out


def test_debug_stats_count_each_match_once(world: FakeWorld) -> None:
    sync()
    stats = json.loads((cfg.DEBUG_DIR / "latest.json").read_text(encoding="utf-8"))
    assert stats["totals"]["already_matched"] == 0
    assert stats["totals"]["newly_matched"] == 5
    assert stats["match"]["rate_pct"] == 100.0


# -- MS-M-4: removals survive 'snapshot' ---------------------------------------

def test_snapshot_command_does_not_swallow_a_removal(world: FakeWorld) -> None:
    sync()
    world.yt = world.yt[1:]  # song 1 deleted on YT Music, then 'snapshot' runs
    run("snapshot")
    sync()
    assert world.removed == [URI(1)]
    assert URI(1) not in world.playlist


def test_source_switch_after_snapshot_still_removes_nothing(world: FakeWorld, home) -> None:
    sync()
    export = home / "other.csv"
    export.write_text("title,artist,album,trackId,platform\nNew,Someone,Alb,vid00000099,ytmusic\n",
                      encoding="utf-8")
    sync(from_csv=str(export))
    assert world.removed == []


# -- ME-M-3: two YT entries, one Spotify track ----------------------------------

def shared_match(sp: object, track: Track) -> MatchResult:
    uri = "spotify:track:SHARED" if track.track_id in ("vid00000001", "vid00000002") \
        else URI(int(track.track_id[3:]))
    return MatchResult(source_track=track, spotify_uri=uri, method="isrc", confidence=1.0)


def test_shared_spotify_track_is_added_once_and_kept(world: FakeWorld, monkeypatch) -> None:
    monkeypatch.setattr(cli, "match_track", shared_match)
    sync()
    assert world.playlist.count("spotify:track:SHARED") == 1
    world.yt = [t for t in world.yt if t.track_id != "vid00000001"]
    sync()
    assert "spotify:track:SHARED" in world.playlist  # entry 2 still on YT Music
    world.yt = [t for t in world.yt if t.track_id != "vid00000002"]
    sync()
    assert "spotify:track:SHARED" not in world.playlist


def test_new_match_already_in_playlist_is_not_added_again(world: FakeWorld) -> None:
    world.playlist = [URI(3)]  # added by hand earlier
    sync()
    assert world.playlist.count(URI(3)) == 1 and len(world.playlist) == 5


# -- MS-M-2 / ME-M-2: fail early, fail loudly -----------------------------------

@pytest.mark.parametrize("status", [403, 404])
def test_unreachable_playlist_stops_before_any_search(world: FakeWorld, status: int) -> None:
    world.playlist_error = status
    with pytest.raises(SystemExit) as stop:
        sync()
    assert stop.value.code == 1 and world.match_calls == 0


def test_read_only_playlist_stops_before_any_search(world: FakeWorld) -> None:
    world.can_edit = False
    with pytest.raises(SystemExit):
        sync()
    assert world.match_calls == 0


def test_failed_push_exits_non_zero_and_the_next_sync_adds_it(world: FakeWorld,
                                                              monkeypatch) -> None:
    monkeypatch.setattr(cli, "add_tracks_to_playlist", lambda sp, pid, uris, dry_run=False: 0)
    with pytest.raises(SystemExit) as stop:
        sync()
    assert stop.value.code == 1
    monkeypatch.setattr(cli, "add_tracks_to_playlist", world._add)
    sync()
    assert sorted(world.playlist) == [URI(n) for n in range(1, 6)]


def test_failed_removal_exits_non_zero_and_is_retried(world: FakeWorld, monkeypatch) -> None:
    sync()
    world.yt = world.yt[1:]
    monkeypatch.setattr(cli, "remove_tracks_from_playlist",
                        lambda sp, pid, uris, dry_run=False: 0)
    with pytest.raises(SystemExit):
        sync()
    assert any(t.track_id == "vid00000001" for t in read_enriched_csv())  # URI kept
    monkeypatch.setattr(cli, "remove_tracks_from_playlist", world._remove)
    sync()
    assert URI(1) not in world.playlist


def test_retry_unmatched_push_failure_is_pushed_by_the_next_sync(world: FakeWorld,
                                                                  monkeypatch) -> None:
    monkeypatch.setattr(cli, "match_track", lambda sp, t: MatchResult(source_track=t))
    sync()
    monkeypatch.setattr(cli, "match_track", world._match)
    monkeypatch.setattr(cli, "add_tracks_to_playlist", lambda sp, pid, uris, dry_run=False: 0)
    with pytest.raises(SystemExit) as stop:
        run("retry-unmatched")
    assert stop.value.code == 1
    monkeypatch.setattr(cli, "add_tracks_to_playlist", world._add)
    sync()
    assert sorted(world.playlist) == [URI(n) for n in range(1, 6)]


def test_retry_unmatched_keeps_duration_and_lastfm_data(world: FakeWorld, monkeypatch) -> None:
    monkeypatch.setattr(cli, "match_track", lambda sp, t: MatchResult(source_track=t))
    sync()
    seen: list[float] = []

    def match(sp: object, track: Track) -> MatchResult:
        seen.append(track.duration)
        return world._match(sp, track)
    monkeypatch.setattr(cli, "match_track", match)
    run("retry-unmatched")
    assert seen == [200.0] * 5  # unmatched.csv has no duration column


# -- B-12: matcher guards -------------------------------------------------------

def cand(name: str, artists: list[str], seconds: float) -> dict:
    return {"name": name, "artists": [{"name": a, "id": "x"} for a in artists],
            "duration_ms": int(seconds * 1000), "uri": f"spotify:track:{name}{artists[0]}",
            "external_urls": {}, "album": {}}


def best(track: Track, candidates: list[dict], monkeypatch) -> MatchResult:
    monkeypatch.setattr(matcher, "search_track", lambda sp, q, limit=10: candidates)
    return matcher.match_track(None, track)


@pytest.mark.parametrize("candidate", [
    cand("Hello", ["Lionel Richie"], 250),        # same title, other artist, 45 s off
    cand("Other Tune", ["Adele"], 295),          # same artist, other song
    cand("Hello", ["Adele"], 340),                # right song, wrong (extended) version
    cand("Hello, Goodbye", ["The Beatles"], 295),  # longer title, other artist
])
def test_matcher_rejects_wrong_candidates(candidate: dict, monkeypatch) -> None:
    track = Track(title="Hello", artist="Adele", duration=295.0, platform="ytmusic")
    assert not best(track, [candidate], monkeypatch).matched


@pytest.mark.parametrize("track,candidate", [
    (Track(title="Numb", artist="Linkin Park", duration=187.0),
     cand("Numb", ["Linkin Park"], 185)),
    (Track(title="Song", artist="Band", duration=200.0),
     cand("Song - 2015 Remaster", ["Band"], 201)),
    (Track(title="Song", artist="Band", duration=200.0),
     cand("Song - Live - 2009", ["Band"], 204)),
    (Track(title="Song (2011 Remaster)", artist="Band", duration=200.0),
     cand("Song - Remastered 2011", ["Band"], 200)),
    (Track(title="Song", artist="Band", duration=200.0),
     cand("Song (Remastered)", ["Band"], 200)),
    (Track(title="Get Lucky", artist="Daft Punk, Pharrell Williams, Nile Rodgers",
           duration=248.0), cand("Get Lucky", ["Daft Punk"], 248)),
    (Track(title="Gruppa krovi", artist="KINO", duration=285.0),
     cand("Gruppa krovi", ["Kino"], 286)),
    (Track(title="Hello", artist="Adele", duration=0.0),   # duration unknown (CSV source)
     cand("Hello", ["Adele"], 295)),
])
def test_matcher_keeps_right_candidates(track: Track, candidate: dict, monkeypatch) -> None:
    assert best(track, [candidate], monkeypatch).matched


def test_transliterated_artist_matches_but_goes_to_review(monkeypatch) -> None:
    track = Track(title="Khochesh", artist="Zemfira Ramazanova", duration=230.0)
    result = best(track, [cand("Khochesh", ["Земфира"], 231)], monkeypatch)
    assert result.matched and result.confidence < cfg.REVIEW_CONFIDENCE


def test_low_confidence_matches_are_listed_in_review_csv(world: FakeWorld, monkeypatch) -> None:
    def match(sp: object, track: Track) -> MatchResult:
        confidence = 0.6 if track.track_id == "vid00000002" else 0.95
        return MatchResult(source_track=track, spotify_uri=URI(int(track.track_id[3:])),
                           method="relaxed", confidence=confidence)
    monkeypatch.setattr(cli, "match_track", match)
    sync()
    review = pd.read_csv(cfg.REVIEW_CSV, encoding="utf-8-sig", dtype=str)
    assert list(review["trackId"]) == ["vid00000002"]
    world.yt = [t for t in world.yt if t.track_id != "vid00000002"]
    sync()
    assert not cfg.REVIEW_CSV.exists()


def test_same_duration_cover_by_another_latin_artist_is_rejected(monkeypatch) -> None:
    # A low artist similarity is let through only for another alphabet; two
    # different Latin names are two different artists
    track = Track(title="Yesterday", artist="The Beatles", duration=125.0)
    assert not best(track, [cand("Yesterday", ["Boyz II Men"], 123)], monkeypatch).matched


@pytest.mark.parametrize("title,name", [
    ("Song", "Song - Extended Remix"),
    ("Song", "Song (Part 2)"),
])
def test_match_found_only_without_the_version_suffix_goes_to_review(
        title: str, name: str, monkeypatch) -> None:
    track = Track(title=title, artist="Band", duration=200.0)
    result = best(track, [cand(name, ["Band"], 203)], monkeypatch)
    assert result.matched and result.confidence < cfg.REVIEW_CONFIDENCE


def test_remaster_suffix_is_not_sent_to_review(monkeypatch) -> None:
    track = Track(title="Song", artist="Band", duration=200.0)
    result = best(track, [cand("Song - 2015 Remaster", ["Band"], 201)], monkeypatch)
    assert result.matched and result.confidence >= cfg.REVIEW_CONFIDENCE


# -- #29: a report file open in Excel -------------------------------------------

def test_open_review_csv_does_not_crash_the_sync(world: FakeWorld, monkeypatch, capsys) -> None:
    cfg.REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    cfg.REVIEW_CSV.write_text("stale\n", encoding="utf-8")
    real_unlink = Path.unlink

    def locked(self: Path, missing_ok: bool = False) -> None:
        if self.name == "review.csv":
            raise PermissionError(32, "being used by another process")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)
    sync()  # no low-confidence match: review.csv is due for deletion
    out = capsys.readouterr().out
    assert "review.csv is open in another program" in out and "Sync complete!" in out
    assert (cfg.SNAPSHOTS_DIR / "latest.json").exists()
