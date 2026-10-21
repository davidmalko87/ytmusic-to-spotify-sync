"""
Offline tests for the Last.fm backfill steps in enricher.py and `lastfm --retry-missing`.
Author: David Malko
Date: 2026-10-19
Version: 0.7.3

The batch fetchers are replaced by fakes; nothing talks to Last.fm.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from playlist_sync import cli, csv_manager, enricher
from playlist_sync import lastfm_client as lf
from playlist_sync.models import Track


def _track(title: str, artist: str, album: str = "Album", **kw) -> Track:
    return Track(title=title, artist=artist, album=album, platform="ytmusic", **kw)


@pytest.fixture
def track_batch(monkeypatch: pytest.MonkeyPatch):
    """Fake get_tracks_info_batch: answers from a dict, records the requested pairs."""
    calls: list[list[tuple[str, str]]] = []

    def install(answers: dict[tuple[str, str], dict | None]) -> list[list[tuple[str, str]]]:
        def fake(api_key: str, pairs: list[tuple[str, str]]) -> dict:
            calls.append(list(pairs))
            # Pairs missing from `answers` behave like failed requests.
            return {p: answers[p] for p in pairs if p in answers}

        monkeypatch.setattr(enricher, "get_tracks_info_batch", fake)
        return calls

    return install


@pytest.fixture
def artist_batch(monkeypatch: pytest.MonkeyPatch):
    """Fake get_artists_info_batch: answers from a dict, records the requested names."""
    calls: list[list[str]] = []

    def install(answers: dict[str, dict | None]) -> list[list[str]]:
        def fake(api_key: str, artists: list[str]) -> dict:
            calls.append(list(artists))
            return {a: answers[a] for a in artists if a in answers}

        monkeypatch.setattr(enricher, "get_artists_info_batch", fake)
        return calls

    return install


# ── track.getInfo backfill ───────────────────────────────────────────


def test_failed_track_lookup_is_retried_next_run(track_batch) -> None:
    found = _track("Youth", "Daughter")
    missing = _track("Demo 3", "Daughter")
    failed = _track("Smother", "Daughter")
    calls = track_batch({
        ("Daughter", "Youth"): {"playcount": 357802, "listeners": 46410, "tags": ["indie folk"]},
        ("Daughter", "Demo 3"): None,  # Last.fm: not found
        # ("Daughter", "Smother") is absent: the request failed
    })

    enricher.backfill_lastfm_data("KEY", [found, missing, failed])

    assert (found.lastfm_listeners, found.lastfm_track_attempted) == (46410, True)
    assert found.lastfm_tags == "indie folk"
    assert missing.lastfm_track_attempted is True
    assert failed.lastfm_track_attempted is False
    assert enricher.lastfm_track_candidates([found, missing, failed]) == [failed]

    enricher.backfill_lastfm_data("KEY", [found, missing, failed])
    assert calls[1] == [("Daughter", "Smother")]


def test_duplicate_tracks_all_get_the_data(track_batch) -> None:
    """The same song twice in a playlist used to leave all but one copy empty."""
    copies = [_track("Let It Go", "Idina Menzel", track_id=vid) for vid in ("a1", "b2", "c3")]
    calls = track_batch({("Idina Menzel", "Let It Go"): {"playcount": 900, "listeners": 300, "tags": []}})

    enricher.backfill_lastfm_data("KEY", copies)

    assert calls == [[("Idina Menzel", "Let It Go")]]  # one request for all copies
    assert [t.lastfm_listeners for t in copies] == [300, 300, 300]
    assert all(t.lastfm_track_attempted for t in copies)


def test_zero_counts_never_overwrite_known_values(track_batch) -> None:
    track = _track("Youth", "Daughter", lastfm_listeners=46410, lastfm_tags="indie folk")
    track_batch({("Daughter", "Youth"): {"playcount": 12, "listeners": 0, "tags": []}})

    enricher.backfill_lastfm_data("KEY", [track])

    assert track.lastfm_playcount == 12
    assert track.lastfm_listeners == 46410
    assert track.lastfm_tags == "indie folk"


# ── artist.getInfo backfill ──────────────────────────────────────────


def test_failed_artist_lookup_is_retried_next_run(artist_batch) -> None:
    tagged = _track("Riptide", "Thylacine")
    unknown = _track("Song", "Nobody Knows")
    failed = _track("Neptune", "Sultan + Shepard")
    calls = artist_batch({
        "Thylacine": {"name": "Thylacine", "tags": ["Electronic", "French"]},
        "Nobody Knows": None,
    })

    enricher.backfill_lastfm_artist_tags("KEY", [tagged, unknown, failed])

    assert (tagged.artist_tags, tagged.tag_source) == ("electronic, french", "lastfm_artist")
    assert tagged.lastfm_attempted and unknown.lastfm_attempted
    assert failed.lastfm_attempted is False

    enricher.backfill_lastfm_artist_tags("KEY", [tagged, unknown, failed])
    assert calls[1] == ["Sultan + Shepard"]


def test_channel_uploads_are_not_looked_up_as_artists(artist_batch) -> None:
    """Channel names borrow a namesake's tags: "Arrakis" became a metal band."""
    arrakis = _track("3.Lorne Balfe - Beyond", "Arrakis", album="")
    ea = _track('"Still Alive" - The Theme Song from Mirror\'s Edge', "Electronic Arts", album="")
    skipped = _track("Mix", "Some Channel", album="Some Album", skip_reason="no_album")
    calls = artist_batch({})

    enricher.backfill_lastfm_artist_tags("KEY", [arrakis, ea, skipped])

    assert calls == []
    assert not any(t.lastfm_attempted for t in (arrakis, ea, skipped))


def test_channel_upload_by_a_known_artist_is_still_looked_up(artist_batch) -> None:
    on_album = _track("Irenka", "sanah")
    upload = _track("sanah i Vito Bambino „oscar”", "sanah, Vito Bambino", album="")
    named_in_title = _track("Guru Josh - Infinity 2012 (DJ Antoine Remix)", "Guru Josh", album="")
    calls = artist_batch({
        "sanah": {"name": "sanah", "tags": ["polish", "indie pop"]},
        "Guru Josh": {"name": "Guru Josh", "tags": ["house"]},
    })

    enricher.backfill_lastfm_artist_tags("KEY", [on_album, upload, named_in_title])

    assert calls == [["sanah", "Guru Josh"]]
    assert upload.artist_tags == "polish, indie pop"
    assert named_in_title.artist_tags == "house"


def test_artist_name_inside_a_longer_word_is_not_a_title_match() -> None:
    upload = _track("Sevenfold - Live", "Seven", album="")
    assert enricher.lastfm_artist_tag_candidates([upload]) == []


def test_non_ytmusic_tracks_without_album_are_still_looked_up() -> None:
    imported = Track(title="Song", artist="Arrakis", album="", platform="csv")
    assert enricher.lastfm_artist_tag_candidates([imported]) == [imported]


# ── lastfm --retry-missing ───────────────────────────────────────────
#
# CSVs enriched before 0.7.2 flagged failed requests, '+' artists and
# duplicate rows as attempted with no data, so normal runs never retry them.


def _flagged(title: str, artist: str, album: str = "Album", **kw) -> Track:
    """A row an older version marked attempted without getting any data."""
    return _track(title, artist, album, lastfm_track_attempted=True, lastfm_attempted=True, **kw)


def test_retry_includes_flagged_rows_without_data() -> None:
    plus = _flagged("Neptune", "Sultan + Shepard")
    done = _track(
        "Youth", "Daughter", lastfm_playcount=357802, artist_tags="indie folk",
        lastfm_track_attempted=True, lastfm_attempted=True,
    )
    fresh = _track("Riptide", "Thylacine")
    tracks = [plus, done, fresh]

    assert enricher.lastfm_track_candidates(tracks) == [fresh]
    assert enricher.lastfm_artist_tag_candidates(tracks) == [fresh]
    assert enricher.lastfm_track_candidates(tracks, ignore_attempted=True) == [plus, fresh]
    assert enricher.lastfm_artist_tag_candidates(tracks, ignore_attempted=True) == [plus, fresh]


def test_retry_still_skips_channel_uploads_for_artist_tags() -> None:
    arrakis = _flagged("3.Lorne Balfe - Beyond", "Arrakis", album="")

    assert enricher.lastfm_artist_tag_candidates([arrakis], ignore_attempted=True) == []
    # track.getInfo has no channel rule: the song itself can still be on Last.fm.
    assert enricher.lastfm_track_candidates([arrakis], ignore_attempted=True) == [arrakis]


def test_retry_refills_duplicate_rows_with_one_lookup(track_batch) -> None:
    """Older versions filled one copy of a repeated song and flagged the rest."""
    filled = _track(
        "Let It Go", "Idina Menzel", track_id="a1",
        lastfm_playcount=900, lastfm_listeners=300, lastfm_track_attempted=True,
    )
    empty = [_flagged("Let It Go", "Idina Menzel", track_id=vid) for vid in ("b2", "c3")]
    calls = track_batch({
        ("Idina Menzel", "Let It Go"): {"playcount": 950, "listeners": 310, "tags": []},
    })

    enricher.backfill_lastfm_data("KEY", [filled, *empty], ignore_attempted=True)

    assert calls == [[("Idina Menzel", "Let It Go")]]
    assert [t.lastfm_listeners for t in empty] == [310, 310]
    assert filled.lastfm_listeners == 300  # a row with data is not a candidate


def test_failed_retry_is_left_for_the_next_normal_run(track_batch, artist_batch) -> None:
    found = _flagged("Neptune", "Sultan + Shepard")
    not_found = _flagged("Demo 3", "Nobody Knows")
    failed = _flagged("Smother", "Daughter")
    tracks = [found, not_found, failed]
    track_batch({
        ("Sultan + Shepard", "Neptune"): {"playcount": 5000, "listeners": 2000, "tags": []},
        ("Nobody Knows", "Demo 3"): None,
    })
    artist_batch({
        "Sultan + Shepard": {"name": "Sultan + Shepard", "tags": ["House", "Canadian"]},
        "Nobody Knows": None,
    })

    enricher.backfill_lastfm_data("KEY", tracks, ignore_attempted=True)
    enricher.backfill_lastfm_artist_tags("KEY", tracks, ignore_attempted=True)

    assert (found.lastfm_listeners, found.artist_tags) == (2000, "house, canadian")
    assert (found.lastfm_track_attempted, found.lastfm_attempted) == (True, True)
    assert (not_found.lastfm_track_attempted, not_found.lastfm_attempted) == (True, True)
    # The stale flag is gone, so a plain `lastfm` run picks the row up again.
    assert (failed.lastfm_track_attempted, failed.lastfm_attempted) == (False, False)
    assert enricher.lastfm_track_candidates(tracks) == [failed]
    assert enricher.lastfm_artist_tag_candidates(tracks) == [failed]


def test_without_retry_flagged_rows_are_not_requested(track_batch, artist_batch) -> None:
    track = _flagged("Neptune", "Sultan + Shepard")
    track_calls = track_batch({})
    artist_calls = artist_batch({})

    enricher.backfill_lastfm_data("KEY", [track])
    enricher.backfill_lastfm_artist_tags("KEY", [track])

    assert track_calls == [] and artist_calls == []
    assert track.lastfm_track_attempted and track.lastfm_attempted


# ── lastfm command (CLI, temp CSV) ───────────────────────────────────


@pytest.fixture
def lastfm_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture):
    """Point the `lastfm` command at a temp enriched CSV holding pre-0.7.2 rows."""
    csv_path = tmp_path / "playlist_enriched.csv"
    monkeypatch.setattr(cli, "ENRICHED_CSV", csv_path)
    monkeypatch.setattr(csv_manager, "ENRICHED_CSV", csv_path)
    monkeypatch.setattr(cli, "load_config", lambda: {"LASTFM_API_KEY": "KEY"})
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False: None)

    def no_network(*args, **kwargs):
        raise AssertionError("the lastfm command tried to reach the network")

    monkeypatch.setattr(lf.urllib.request, "urlopen", no_network)

    csv_manager.write_enriched_csv([
        _flagged("Neptune", "Sultan + Shepard"),
        _track(
            "Let It Go", "Idina Menzel", track_id="a1", lastfm_playcount=900,
            lastfm_listeners=300, artist_tags="musicals",
            lastfm_track_attempted=True, lastfm_attempted=True,
        ),
        _flagged("Let It Go", "Idina Menzel", track_id="b2", artist_tags="musicals"),
        _flagged("3.Lorne Balfe - Beyond", "Arrakis", album=""),
    ], csv_path)

    def run(*argv: str) -> str:
        cli.dispatch(cli.build_parser().parse_args(["lastfm", *argv]))
        return capsys.readouterr().out

    return csv_path, run


def test_parser_retry_missing_defaults_off() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["lastfm"]).retry_missing is False
    assert parser.parse_args(["lastfm", "--retry-missing"]).retry_missing is True


def test_cli_retry_missing_fills_flagged_rows(lastfm_cli, track_batch, artist_batch) -> None:
    csv_path, run = lastfm_cli
    track_calls = track_batch({
        ("Sultan + Shepard", "Neptune"): {"playcount": 5000, "listeners": 2000, "tags": []},
        ("Idina Menzel", "Let It Go"): {"playcount": 950, "listeners": 310, "tags": []},
        ("Arrakis", "3.Lorne Balfe - Beyond"): None,
    })
    artist_calls = artist_batch({
        "Sultan + Shepard": {"name": "Sultan + Shepard", "tags": ["House", "Canadian"]},
    })

    out = run("--retry-missing")

    assert "incl. previously attempted (--retry-missing): 3 track data, 1 artist tags" in out
    assert track_calls == [[
        ("Sultan + Shepard", "Neptune"),
        ("Idina Menzel", "Let It Go"),
        ("Arrakis", "3.Lorne Balfe - Beyond"),
    ]]
    assert artist_calls == [["Sultan + Shepard"]]  # the channel upload is not looked up

    plus, original, duplicate, upload = csv_manager.read_enriched_csv(csv_path)
    assert (plus.lastfm_listeners, plus.artist_tags) == (2000, "house, canadian")
    assert (plus.lastfm_track_attempted, plus.lastfm_attempted) == (True, True)
    assert (original.lastfm_listeners, duplicate.lastfm_listeners) == (300, 310)
    assert (upload.lastfm_playcount, upload.artist_tags) == (0, "")
    assert upload.lastfm_track_attempted is True


def test_cli_retry_missing_dry_run_sends_nothing(lastfm_cli, track_batch, artist_batch) -> None:
    csv_path, run = lastfm_cli
    before = csv_path.read_bytes()
    track_calls = track_batch({})
    artist_calls = artist_batch({})

    out = run("--retry-missing", "--dry-run")

    assert track_calls == [] and artist_calls == []
    assert csv_path.read_bytes() == before
    assert "Need Last.fm track data: 3" in out
    assert "Need Last.fm artist tags: 1" in out
    assert "[DRY RUN] Would fetch Last.fm track data for 3 tracks" in out
    assert "[DRY RUN] Would fetch Last.fm artist tags for 1 unique artists" in out


def test_cli_without_flag_skips_flagged_rows_and_hints(
    lastfm_cli, track_batch, artist_batch, capsys: pytest.CaptureFixture,
) -> None:
    csv_path, _ = lastfm_cli
    before = csv_path.read_bytes()
    track_calls = track_batch({})
    artist_calls = artist_batch({})

    # The interactive menu builds its own Namespace, without retry_missing.
    cli.dispatch(argparse.Namespace(command="lastfm", verbose=False, dry_run=False))
    out = capsys.readouterr().out

    assert track_calls == [] and artist_calls == []
    assert csv_path.read_bytes() == before
    assert "Skipping 3 track-data and 1 artist-tag row(s)" in out
    assert "lastfm --retry-missing" in out
    assert "All tracks already have Last.fm data and artist tags." in out
