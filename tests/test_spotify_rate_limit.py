"""
Spotify rate limits and failed requests, against a local HTTP server (no Spotify contact).
Author: David Malko
Date: 2026-10-21
Version: 0.8.0

spotipy's default session let urllib3 sleep out Retry-After on its own (up to
6 h per retry) and then report the 429 without headers, so long bans hung the
sync. These tests run real spotipy + urllib3 against 127.0.0.1.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from collections.abc import Iterator

import pytest
import requests
import spotipy

from playlist_sync import spotify_client as sc


class FakeSpotifyServer:
    """Answers each request with the next scripted (status, headers, body)."""

    def __init__(self) -> None:
        self.script: list[tuple[int, dict[str, str], dict]] = []
        self.hits = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self) -> None:
                # Drain the request body: closing a socket with unread data makes
                # Windows reset the connection (WinError 10053) before the client
                # reads the answer
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.hits += 1
                status, headers, body = outer.script[min(outer.hits, len(outer.script)) - 1]
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _answer

            def log_message(self, *args: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def client(self) -> spotipy.Spotify:
        sp = spotipy.Spotify(auth="fake-token", requests_session=sc._spotify_session(),
                             requests_timeout=5)
        sp.prefix = self.url
        return sp


RATE_LIMITED = {"error": {"status": 429, "message": "API rate limit exceeded"}}
FOUND = {"tracks": {"items": [{"uri": "spotify:track:1"}]}}


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSpotifyServer]:
    monkeypatch.setattr(sc, "SEARCH_DELAY_SEC", 0)
    fake = FakeSpotifyServer()
    yield fake
    fake.server.shutdown()


def test_long_ban_stops_at_once_with_the_real_wait(server: FakeSpotifyServer) -> None:
    server.script = [(429, {"Retry-After": "7200"}, RATE_LIMITED)]
    start = time.monotonic()
    with pytest.raises(sc.RateLimitError) as exc:
        sc.search_track(server.client(), "song")
    assert exc.value.retry_after == 7200
    assert server.hits == 1  # urllib3 no longer sleeps it out on its own
    assert time.monotonic() - start < 3


def test_short_wait_is_slept_then_the_search_continues(
    server: FakeSpotifyServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(sc.time, "sleep", slept.append)
    server.script = [(429, {"Retry-After": "2"}, RATE_LIMITED), (200, {}, FOUND)]
    assert sc.search_track(server.client(), "song") == [{"uri": "spotify:track:1"}]
    assert [s for s in slept if s] == [3]  # Retry-After + 1 (the 0 is the pre-search delay)


def test_server_errors_mean_unavailable_not_no_match(server: FakeSpotifyServer) -> None:
    server.script = [(503, {}, {"error": {"status": 503, "message": "down"}})]
    with pytest.raises(sc.SearchUnavailable):
        sc.search_track(server.client(), "song")


def test_unreachable_spotify_means_unavailable(
    server: FakeSpotifyServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sp = server.client()

    def refuse(*args: object, **kwargs: object) -> None:
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(sp._session, "request", refuse)  # a real refused port is slow on Windows
    with pytest.raises(sc.SearchUnavailable):
        sc.search_track(sp, "song")


def test_a_bad_query_is_still_just_no_results(server: FakeSpotifyServer) -> None:
    server.script = [(400, {}, {"error": {"status": 400, "message": "bad query"}})]
    assert sc.search_track(server.client(), "song") == []


def test_playlist_write_waits_out_a_short_429_and_retries(
    server: FakeSpotifyServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)
    server.script = [(429, {"Retry-After": "1"}, RATE_LIMITED), (201, {}, {"snapshot_id": "x"})]
    assert sc.add_tracks_to_playlist(server.client(), "pl", ["spotify:track:1"]) == 1


def test_playlist_write_raises_on_a_long_ban(server: FakeSpotifyServer) -> None:
    server.script = [(429, {"Retry-After": "86400"}, RATE_LIMITED)]
    with pytest.raises(sc.RateLimitError):
        sc.add_tracks_to_playlist(server.client(), "pl", ["spotify:track:1"])
