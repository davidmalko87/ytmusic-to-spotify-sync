"""
Offline tests for the Last.fm backfill steps in enricher.py.
Author: David Malko
Date: 2026-10-18
Version: 0.7.2

The batch fetchers are replaced by fakes; nothing talks to Last.fm.
"""

from __future__ import annotations

import pytest

from playlist_sync import enricher
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
