"""
Offline tests for the Last.fm client: '+' encoding, not-found vs failure.
Author: David Malko
Date: 2026-10-18
Version: 0.7.2

urlopen is replaced by a fake, so no request leaves the machine.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from collections.abc import Callable

import pytest

from playlist_sync import lastfm_client as lf


class FakeResponse(io.BytesIO):
    """Minimal stand-in for the object urlopen returns."""

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _json(payload: dict) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(lf.LASTFM_BASE_URL, code, "error", {}, io.BytesIO(body))


@pytest.fixture
def fake_urlopen(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable], list[str]]:
    """Install a fake urlopen driven by `handler(url)`; returns the list of requested URLs."""
    monkeypatch.setattr(lf, "LASTFM_DELAY_SEC", 0)

    def install(handler: Callable[[str], FakeResponse]) -> list[str]:
        urls: list[str] = []

        def fake(req, timeout: float = 0) -> FakeResponse:
            urls.append(req.full_url)
            return handler(req.full_url)

        monkeypatch.setattr(lf.urllib.request, "urlopen", fake)
        return urls

    return install


def _param(url: str, name: str) -> str:
    """The raw (still percent-encoded) value of one query parameter."""
    query = urllib.parse.urlsplit(url).query
    return dict(pair.split("=", 1) for pair in query.split("&"))[name]


def _lastfm_decode(raw: str) -> str:
    """Decode a query value the way Last.fm does: twice."""
    return urllib.parse.unquote_plus(urllib.parse.unquote_plus(raw))


ARTIST_REPLY = {"artist": {"name": "Sultan + Shepard", "tags": {"tag": [{"name": "House"}]}}}


# ── '+' in names ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["Sultan + Shepard", "Florence + The Machine", "1+1"])
def test_plus_survives_lastfm_double_decoding(fake_urlopen, name: str) -> None:
    urls = fake_urlopen(lambda url: _json(ARTIST_REPLY))
    lf.get_artist_info("KEY", name)

    raw = _param(urls[0], "artist")
    assert "%252B" in raw
    assert _lastfm_decode(raw) == name


def test_plain_urlencode_loses_the_plus() -> None:
    """The bug being fixed: one level of encoding turns '+' into a space on Last.fm."""
    raw = urllib.parse.urlencode({"artist": "Sultan + Shepard"}).split("=", 1)[1]
    assert _lastfm_decode(raw) == "Sultan   Shepard"


@pytest.mark.parametrize("name", ["Radiohead", "Of Monsters and Men", "AC/DC", "Simon & Garfunkel", "Кино"])
def test_names_without_plus_round_trip(fake_urlopen, name: str) -> None:
    urls = fake_urlopen(lambda url: _json(ARTIST_REPLY))
    lf.get_artist_info("KEY", name)
    assert _lastfm_decode(_param(urls[0], "artist")) == name


def test_track_lookup_encodes_both_artist_and_title(fake_urlopen) -> None:
    urls = fake_urlopen(lambda url: _json({"track": {"playcount": "5", "listeners": "2"}}))
    lf.get_track_info("KEY", "Sultan + Shepard", "Assassin + Friends")
    assert _lastfm_decode(_param(urls[0], "artist")) == "Sultan + Shepard"
    assert _lastfm_decode(_param(urls[0], "track")) == "Assassin + Friends"


# ── not found vs failed ──────────────────────────────────────────────


def test_found_track_parses_counts_and_tags(fake_urlopen) -> None:
    fake_urlopen(lambda url: _json({"track": {
        "playcount": "357802", "listeners": "46410",
        "toptags": {"tag": [{"name": "indie folk"}, {"name": "female vocalists"}]},
    }}))
    info = lf.get_track_info("KEY", "Daughter", "Youth")
    assert info == {"playcount": 357802, "listeners": 46410, "tags": ["indie folk", "female vocalists"]}


def test_error_6_means_not_found(fake_urlopen) -> None:
    fake_urlopen(lambda url: _json({"error": 6, "message": "Track not found"}))
    assert lf.get_track_info("KEY", "Nobody", "Nothing") is None
    assert lf.get_artist_info("KEY", "Nobody") is None


def test_error_6_with_http_4xx_status_means_not_found(fake_urlopen) -> None:
    def handler(url: str) -> FakeResponse:
        raise _http_error(400, b'{"error": 6, "message": "The artist you supplied could not be found"}')

    fake_urlopen(handler)
    assert lf.get_artist_info("KEY", "Nobody") is None


def test_empty_reply_means_not_found(fake_urlopen) -> None:
    fake_urlopen(lambda url: _json({}))
    assert lf.get_artist_info("KEY", "Nobody") is None


@pytest.mark.parametrize("handler", [
    pytest.param(lambda url: _json({"error": 29, "message": "Rate limit exceeded"}), id="rate-limit"),
    pytest.param(lambda url: _json({"error": 8, "message": "Operation failed"}), id="backend-error"),
    pytest.param(lambda url: FakeResponse(b"<html>502 Bad Gateway</html>"), id="non-json-200"),
])
def test_api_and_reply_errors_raise(fake_urlopen, handler) -> None:
    fake_urlopen(handler)
    with pytest.raises(lf.LastfmRequestError):
        lf.get_artist_info("KEY", "Radiohead")


@pytest.mark.parametrize("error", [
    pytest.param(_http_error(503, b"Service Unavailable"), id="http-503"),
    pytest.param(_http_error(403, b'{"error": 10, "message": "Invalid API key"}'), id="bad-key"),
    pytest.param(urllib.error.URLError("timed out"), id="network"),
    pytest.param(TimeoutError("read timed out"), id="timeout"),
])
def test_transport_errors_raise(fake_urlopen, error: Exception) -> None:
    def handler(url: str) -> FakeResponse:
        raise error

    fake_urlopen(handler)
    with pytest.raises(lf.LastfmRequestError):
        lf.get_track_info("KEY", "Radiohead", "Creep")


# ── batches ──────────────────────────────────────────────────────────


def test_artist_batch_keeps_not_found_and_drops_failures(fake_urlopen) -> None:
    def handler(url: str) -> FakeResponse:
        artist = _lastfm_decode(_param(url, "artist"))
        if artist == "Ghost":
            return _json({"error": 6, "message": "not found"})
        if artist == "Flaky":
            raise urllib.error.URLError("connection reset")
        return _json({"artist": {"name": artist, "tags": {"tag": [{"name": "rock"}]}}})

    fake_urlopen(handler)
    results = lf.get_artists_info_batch("KEY", ["Radiohead", "Ghost", "Flaky", "Radiohead"])

    assert results["Radiohead"] == {"name": "Radiohead", "tags": ["rock"]}
    assert results["Ghost"] is None          # answered: not on Last.fm
    assert "Flaky" not in results             # failed: caller retries next run


def test_track_batch_keeps_not_found_and_drops_failures(fake_urlopen) -> None:
    def handler(url: str) -> FakeResponse:
        title = _lastfm_decode(_param(url, "track"))
        if title == "Unknown":
            return _json({"error": 6, "message": "Track not found"})
        if title == "Timeout":
            raise TimeoutError("read timed out")
        return _json({"track": {"playcount": "10", "listeners": "3"}})

    fake_urlopen(handler)
    pairs = [("Daughter, Guest", "Youth"), ("Daughter", "Unknown"), ("Daughter", "Timeout")]
    results = lf.get_tracks_info_batch("KEY", pairs)

    assert results[("Daughter, Guest", "Youth")]["listeners"] == 3
    assert results[("Daughter", "Unknown")] is None
    assert ("Daughter", "Timeout") not in results


def test_batch_stops_after_consecutive_failures_only(fake_urlopen, monkeypatch) -> None:
    monkeypatch.setattr(lf, "MAX_CONSECUTIVE_FAILURES", 3)

    def handler(url: str) -> FakeResponse:
        artist = _lastfm_decode(_param(url, "artist"))
        if artist.startswith("missing"):
            return _json({"error": 6, "message": "not found"})
        raise urllib.error.URLError("down")

    urls = fake_urlopen(handler)
    # Not-found answers never count towards the stop; three failures in a row do.
    names = [f"missing{i}" for i in range(5)] + [f"down{i}" for i in range(5)]
    results = lf.get_artists_info_batch("KEY", names)

    assert len(urls) == 8
    assert set(results) == {f"missing{i}" for i in range(5)}
