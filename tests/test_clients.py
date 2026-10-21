"""
Offline tests for logging setup and the Spotify client helpers.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0
"""

from __future__ import annotations

import json
import logging
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
