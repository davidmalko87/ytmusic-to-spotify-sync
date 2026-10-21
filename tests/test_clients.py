"""
Offline tests for logging setup and the Spotify client helpers.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0
"""

from __future__ import annotations

import json
import logging
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from playlist_sync import config as cfg
from playlist_sync import spotify_client, utils


@pytest.fixture
def bare_logger() -> Iterator[logging.Logger]:
    """The package logger without handlers, restored afterwards."""
    logger = logging.getLogger("playlist_sync")
    saved = logger.handlers[:]
    logger.handlers.clear()
    yield logger
    for handler in logger.handlers:
        handler.close()
    logger.handlers[:] = saved


def test_setup_logging_creates_a_missing_settings_folder(
    monkeypatch: pytest.MonkeyPatch, home: Path, bare_logger: logging.Logger,
) -> None:
    folder = home / "brand-new-home"
    monkeypatch.setattr(cfg, "PROJECT_ROOT", folder)
    monkeypatch.setattr(utils, "LOG_FILE", folder / "playlist_sync.log")
    utils.setup_logging()
    assert (folder / "playlist_sync.log").is_file()


def _point_token_paths(monkeypatch: pytest.MonkeyPatch, home: Path) -> tuple[Path, Path]:
    new, legacy = home / ".spotify_token_cache", home / ".cache"
    monkeypatch.setattr(spotify_client, "SPOTIFY_TOKEN_CACHE", new)
    monkeypatch.setattr(spotify_client, "LEGACY_TOKEN_CACHE", legacy)
    return new, legacy


def test_old_token_file_is_adopted(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    new, legacy = _point_token_paths(monkeypatch, home)
    legacy.write_text(json.dumps({"access_token": "a", "refresh_token": "r"}), encoding="utf-8")
    spotify_client._adopt_legacy_token_cache()
    assert json.loads(new.read_text(encoding="utf-8"))["refresh_token"] == "r"


def test_cache_directory_or_foreign_file_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, home: Path,
) -> None:
    new, legacy = _point_token_paths(monkeypatch, home)
    legacy.mkdir()  # e.g. ~/.cache
    spotify_client._adopt_legacy_token_cache()
    assert not new.exists()

    legacy.rmdir()
    legacy.write_text("not json", encoding="utf-8")
    spotify_client._adopt_legacy_token_cache()
    assert not new.exists()


class FakePlaylistApi:
    """Two pages of playlist entries in the 2026 ('item') and older ('track') shapes."""

    def playlist_items(self, playlist_id: str, additional_types: tuple = ()) -> dict:
        return {"items": [{"item": {"type": "track", "uri": "spotify:track:1"}},
                          {"item": {"type": "episode", "uri": "spotify:episode:9"}}],
                "next": "page2"}

    def next(self, page: dict) -> dict:
        return {"items": [{"track": {"type": "track", "uri": "spotify:track:2"}}], "next": None}


def test_playlist_tracks_reads_new_and_old_entry_shapes() -> None:
    tracks = spotify_client.get_playlist_tracks(FakePlaylistApi(), "pl")
    assert [t["uri"] for t in tracks] == ["spotify:track:1", "spotify:track:2"]


class _FakeSpotify:
    def __init__(self, auth_manager: object, **kwargs: object) -> None:
        self.auth_manager = auth_manager

    def current_user(self) -> dict:
        return {"id": "me", "display_name": "me"}


_CONFIG = {"SPOTIPY_CLIENT_ID": "a" * 32, "SPOTIPY_CLIENT_SECRET": "b" * 32,
           "SPOTIPY_REDIRECT_URI": cfg.DEFAULT_REDIRECT_URI}


def test_client_uses_the_private_token_cache_without_deprecation(
    monkeypatch: pytest.MonkeyPatch, home: Path, recwarn: pytest.WarningsRecorder,
) -> None:
    new, _ = _point_token_paths(monkeypatch, home)
    monkeypatch.setattr(spotify_client.spotipy, "Spotify", _FakeSpotify)
    sp = spotify_client.get_spotify_client(_CONFIG)
    handler = sp.auth_manager.cache_handler
    assert isinstance(handler, spotify_client.PrivateCacheFileHandler)
    assert Path(handler.cache_path) == new
    assert not [w for w in recwarn if "cache_path" in str(w.message)]


def test_token_cache_is_created_owner_only(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    path = home / ".spotify_token_cache"
    modes: list[int] = []
    real_open = spotify_client.os.open

    def spy_open(file: str, flags: int, mode: int = 0o777) -> int:
        modes.append(mode)
        return real_open(file, flags, mode)

    monkeypatch.setattr(spotify_client.os, "open", spy_open)
    handler = spotify_client.PrivateCacheFileHandler(cache_path=str(path))
    handler.save_token_to_cache({"access_token": "a", "refresh_token": "r"})
    handler.save_token_to_cache({"access_token": "b", "refresh_token": "r"})  # overwrite
    assert modes == [0o600, 0o600]
    assert handler.get_cached_token() == {"access_token": "b", "refresh_token": "r"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_token_cache_mode_is_0600_even_with_a_loose_umask(
    monkeypatch: pytest.MonkeyPatch, home: Path,
) -> None:
    path = home / ".spotify_token_cache"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)  # left behind by spotipy < 2.25.1
    old_umask = os.umask(0o022)
    try:
        spotify_client.PrivateCacheFileHandler(cache_path=str(path)).save_token_to_cache({"x": 1})
        fresh = home / "fresh_cache"
        spotify_client.PrivateCacheFileHandler(cache_path=str(fresh)).save_token_to_cache({"x": 1})
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_existing_loose_token_cache_is_tightened_on_login(
    monkeypatch: pytest.MonkeyPatch, home: Path,
) -> None:
    new, _ = _point_token_paths(monkeypatch, home)
    new.write_text('{"refresh_token": "r"}', encoding="utf-8")
    new.chmod(0o644)
    monkeypatch.setattr(spotify_client.spotipy, "Spotify", _FakeSpotify)
    spotify_client.get_spotify_client(_CONFIG)
    assert stat.S_IMODE(new.stat().st_mode) == 0o600


def test_unwritable_token_cache_only_warns(
    monkeypatch: pytest.MonkeyPatch, home: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    target = home / "no-such-dir" / ".spotify_token_cache"
    spotify_client.PrivateCacheFileHandler(cache_path=str(target)).save_token_to_cache({"x": 1})
    assert "Could not write the Spotify token cache" in caplog.text
