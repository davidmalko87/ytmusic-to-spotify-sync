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
from playlist_sync.spotify_client import RateLimitError, SearchUnavailable

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
        self.unavailable_on: set[int] = set()   # match calls whose search "fails"
        self.push_error: BaseException | None = None

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
        if self.match_calls in self.unavailable_on:
            raise SearchUnavailable("HTTP 503")
        return MatchResult(source_track=track, spotify_uri=f"spotify:track:{track.track_id}",
                           method="isrc", confidence=1.0)

    def _add(self, sp: object, pid: str, uris: list[str], dry_run: bool = False) -> int:
        if self.push_error is not None:
            raise self.push_error
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


def test_failed_search_leaves_the_track_for_the_next_run(world: FakeWorld) -> None:
    world.unavailable_on = {2}  # song 2's search fails once
    sync()
    rows = {t.track_id: t for t in read_enriched_csv()}
    assert not rows["vid00000002"].match_attempted  # not written off as "no match"
    assert len(world.playlist) == 4
    sync()
    assert "spotify:track:vid00000002" in world.playlist


def test_five_failed_searches_in_a_row_stop_with_progress_saved(world: FakeWorld) -> None:
    world.unavailable_on = {1, 2, 3, 4, 5}  # every search in the run fails
    sync()
    assert world.playlist == []  # stopped like a rate limit: no push while Spotify is down
    assert len(read_enriched_csv()) == 5
    world.unavailable_on = set()
    sync()
    assert len(world.playlist) == 5 and len(set(world.playlist)) == 5


def test_rate_limited_push_loses_nothing(world: FakeWorld) -> None:
    world.push_error = RateLimitError(7200)
    sync()
    assert world.playlist == []
    assert cfg.MATCH_CACHE.exists()  # matches kept as unpushed
    world.push_error = None
    calls_before = world.match_calls
    sync()
    assert len(world.playlist) == 5
    assert world.match_calls == calls_before  # pushed from the cache, nothing searched again


def test_nothing_to_do_exit_keeps_every_row(world: FakeWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    # Every track fails once; the next sync has nothing to search and takes the
    # quiet "Nothing new to match" exit, which must still keep all rows.
    monkeypatch.setattr(cli, "match_track", lambda sp, track: MatchResult(source_track=track))
    sync()
    sync()
    rows = read_enriched_csv()
    assert len(rows) == 5 and all(t.match_attempted for t in rows)


def test_csv_from_before_0_7_1_is_searched_once_then_skipped(
    world: FakeWorld, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CHANGELOG 0.7.1 once claimed an automatic match_attempted migration; there
    # is none. Old rows are searched once, flagged, and skipped afterwards.
    import pandas as pd

    rows = [yt_track(n) for n in range(1, 4)]
    cfg.ENRICHED_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{k: v for k, v in t.to_csv_row().items() if k != "match_attempted"}
                  for t in rows]).to_csv(cfg.ENRICHED_CSV, index=False, encoding="utf-8-sig")
    world.yt = rows
    searched: list[str] = []

    def never_matches(sp: object, track: Track) -> MatchResult:
        searched.append(track.track_id)
        return MatchResult(source_track=track, error="No Spotify match found after all passes")

    monkeypatch.setattr(cli, "match_track", never_matches)
    assert not any(t.match_attempted for t in read_enriched_csv())
    sync()
    assert len(searched) == 3 and all(t.match_attempted for t in read_enriched_csv())
    sync()
    assert len(searched) == 3


def test_csv_sync_with_clock_durations_matches_and_pushes(world: FakeWorld, home: Path) -> None:
    export = home / "browser-extension.csv"
    export.write_text("title,artist,album,trackId,platform,duration\n"
                      "New,Someone,Alb,vid00000099,ytmusic,3:45\n", encoding="utf-8")
    sync(from_csv=str(export))
    assert "spotify:track:vid00000099" in world.playlist
    assert [t.duration for t in read_enriched_csv()] == [225.0]


def test_csv_sync_with_a_bad_cell_stops_before_touching_spotify(
    world: FakeWorld, home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    export = home / "browser-extension.csv"
    export.write_text("title,artist,trackId,duration\nNew,Someone,vid00000099,soon\n",
                      encoding="utf-8")
    with pytest.raises(SystemExit):
        sync(from_csv=str(export))
    assert "browser-extension.csv, row 2" in capsys.readouterr().out
    assert world.playlist == [] and world.match_calls == 0


def test_csv_sync_with_missing_source_csv_names_the_path(
    world: FakeWorld, home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    cfg.save_setting("SOURCE_CSV", "exports/gone.csv")
    with pytest.raises(SystemExit) as exit_info:
        sync(from_csv="default")
    assert exit_info.value.code == 1
    assert str(home / "exports" / "gone.csv") in capsys.readouterr().out
    assert world.match_calls == 0 and world.playlist == []


# -- retry-unmatched keeps full rows (B-9) ------------------------------------

def retry(**options: object) -> None:
    args = argparse.Namespace(command="retry-unmatched", verbose=False, dry_run=False)
    for key, value in options.items():
        setattr(args, key, value)
    cli.dispatch(args)


def test_retry_unmatched_keeps_the_full_enriched_row(world: FakeWorld,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    real_match = world._match
    monkeypatch.setattr(cli, "match_track", lambda sp, t: (
        MatchResult(source_track=t) if t.track_id == "vid00000003" else real_match(sp, t)))
    sync()
    rows = read_enriched_csv()
    row = next(t for t in rows if t.track_id == "vid00000003")
    assert row.match_attempted and not row.spotify_uri
    # Enrichment a 'lastfm' run (or the YT fetch) gave the unmatched row
    row.isrc, row.added_date = "GBUM71029604", "2026-01-02"
    row.lastfm_listeners, row.artist_tags, row.primary_genre = 1234, "rock, indie", "rock"
    write_enriched_csv(rows)

    seen: list[Track] = []

    def match_and_record(sp: object, t: Track) -> MatchResult:
        seen.append(Track(**vars(t)))
        return real_match(sp, t)

    monkeypatch.setattr(cli, "match_track", match_and_record)
    retry()

    assert [(t.isrc, t.duration) for t in seen] == [("GBUM71029604", 200.0)]  # full input
    after = {t.track_id: t for t in read_enriched_csv()}
    assert len(after) == 5
    fixed = after["vid00000003"]
    assert fixed.spotify_uri == "spotify:track:vid00000003"
    assert (fixed.lastfm_listeners, fixed.artist_tags) == (1234, "rock, indie")
    assert fixed.primary_genre == "rock"
    assert (fixed.added_date, fixed.duration) == ("2026-01-02", 200.0)
    assert world.playlist.count("spotify:track:vid00000003") == 1
    assert not cfg.UNMATCHED_CSV.exists()


def test_retry_unmatched_skips_rows_matched_since_and_rows_gone_from_the_data(
        world: FakeWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    sync()  # everything matched
    from playlist_sync.csv_manager import write_unmatched_csv
    # A stale unmatched.csv: one row matched since, one track no longer in the data
    write_unmatched_csv([yt_track(2), yt_track(99)])
    calls: list[str] = []
    monkeypatch.setattr(cli, "match_track", lambda sp, t: calls.append(t.track_id) or
                        MatchResult(source_track=t, spotify_uri=f"spotify:track:{t.track_id}"))
    retry()
    assert calls == []  # nothing re-searched, nothing added
    assert len(world.playlist) == 5


def test_retry_unmatched_dry_run_writes_nothing(world: FakeWorld,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    real_match = world._match
    monkeypatch.setattr(cli, "match_track", lambda sp, t: (
        MatchResult(source_track=t) if t.track_id == "vid00000003" else real_match(sp, t)))
    sync()
    before = cfg.ENRICHED_CSV.read_bytes()
    monkeypatch.setattr(cli, "match_track", real_match)
    retry(dry_run=True)
    assert cfg.ENRICHED_CSV.read_bytes() == before
    assert "spotify:track:vid00000003" not in world.playlist
    assert cfg.UNMATCHED_CSV.exists()


# -- import-csv does not wipe existing sync data (MS-M-5) ---------------------

def import_csv(**options: object) -> None:
    args = argparse.Namespace(command="import-csv", verbose=False, dry_run=False, csv=None,
                              force=False)
    for key, value in options.items():
        setattr(args, key, value)
    cli.dispatch(args)


def test_import_csv_refuses_to_replace_existing_matches(world: FakeWorld, home: Path) -> None:
    sync()
    before = cfg.ENRICHED_CSV.read_bytes()
    export = home / "export.csv"
    export.write_text("title,artist,album,trackId,platform\nSong 1,Artist 1,Album,vid00000001,"
                      "ytmusic\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        import_csv(csv=str(export))
    assert exc.value.code == 1
    assert cfg.ENRICHED_CSV.read_bytes() == before


def test_import_csv_force_archives_the_old_data_first(world: FakeWorld, home: Path) -> None:
    sync()
    before = cfg.ENRICHED_CSV.read_bytes()
    export = home / "export.csv"
    export.write_text("title,artist,album,trackId,platform\nSong 1,Artist 1,Album,vid00000001,"
                      "ytmusic\n", encoding="utf-8")
    import_csv(csv=str(export), force=True)
    archived = list((cfg.DATA_DIR / "archive").glob("*/playlist_enriched.csv"))
    assert len(archived) == 1 and archived[0].read_bytes() == before
    assert [t.track_id for t in read_enriched_csv()] == ["vid00000001"]


def test_import_csv_first_run_needs_no_force(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    export = home / "export.csv"
    export.write_text("title,artist,album,trackId,platform\nA,B,C,vid00000001,ytmusic\n",
                      encoding="utf-8")
    import_csv(csv=str(export))
    assert len(read_enriched_csv()) == 1


def test_sync_after_a_forced_import_does_not_duplicate_the_playlist(world: FakeWorld,
                                                                    home: Path) -> None:
    sync()
    export = home / "export.csv"
    export.write_text("title,artist,album,trackId,platform\n" + "".join(
        f"Song {n},Artist {n},Album,vid{n:08d},ytmusic\n" for n in range(1, 6)), encoding="utf-8")
    import_csv(csv=str(export), force=True)  # what menu 10 > 1 does after its warning
    sync()
    assert sorted(world.playlist) == sorted(f"spotify:track:vid{n:08d}" for n in range(1, 6))
