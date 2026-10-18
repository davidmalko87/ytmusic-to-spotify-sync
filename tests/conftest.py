"""
Shared fixtures: every test runs against a throwaway settings folder.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from playlist_sync import config as cfg

SETTING_KEYS = (
    "SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI",
    "SPOTIFY_PLAYLIST_ID", "YTMUSIC_PLAYLIST_ID", "YTMUSIC_AUTH_FILE",
    "SOURCE_CSV", "LASTFM_API_KEY", cfg.HOME_ENV_VAR,
)


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every path constant at tmp_path and clear settings from the environment.

    Without this a test could read the developer's real .env (load_dotenv puts
    its values into os.environ) or write into the real data/ folder.
    """
    for key in SETTING_KEYS:
        monkeypatch.delenv(key, raising=False)
    data = tmp_path / "data"
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(cfg, "SPOTIFY_TOKEN_CACHE", tmp_path / ".spotify_token_cache")
    monkeypatch.setattr(cfg, "LEGACY_TOKEN_CACHE", tmp_path / ".cache")
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "SNAPSHOTS_DIR", data / "snapshots")
    monkeypatch.setattr(cfg, "ENRICHED_CSV", data / "playlist_enriched.csv")
    monkeypatch.setattr(cfg, "UNMATCHED_CSV", data / "unmatched.csv")
    monkeypatch.setattr(cfg, "SKIPPED_CSV", data / "skipped.csv")
    monkeypatch.setattr(cfg, "MATCH_CACHE", data / "match_cache.json")
    monkeypatch.setattr(cfg, "LIKES_SNAPSHOTS_DIR", data / "snapshots" / "likes")
    monkeypatch.setattr(cfg, "DEBUG_DIR", data / "debug")
    monkeypatch.setattr(cfg, "_PROCESS_ENV_KEYS", frozenset())
    # Modules that imported data paths by name keep their own copies; redirect
    # those too, so no test can ever write into the real data/ folder
    by_name = {
        "playlist_sync.ytmusic_client": {
            "SNAPSHOTS_DIR": data / "snapshots",
            "LIKES_SNAPSHOTS_DIR": data / "snapshots" / "likes",
        },
        "playlist_sync.csv_manager": {
            "ENRICHED_CSV": data / "playlist_enriched.csv",
            "UNMATCHED_CSV": data / "unmatched.csv",
            "SKIPPED_CSV": data / "skipped.csv",
        },
        "playlist_sync.cli": {
            "PROJECT_ROOT": tmp_path,
            "DEBUG_DIR": data / "debug",
            "ENRICHED_CSV": data / "playlist_enriched.csv",
            "LIKES_ENRICHED_CSV": data / "likes_enriched.csv",
            "LIKES_UNMATCHED_CSV": data / "likes_unmatched.csv",
            "MATCH_CACHE": data / "match_cache.json",
            "SKIPPED_CSV": data / "skipped.csv",
            "SNAPSHOTS_DIR": data / "snapshots",
            "UNMATCHED_CSV": data / "unmatched.csv",
        },
        "playlist_sync.utils": {"LOG_FILE": tmp_path / "playlist_sync.log"},
    }
    for module, names in by_name.items():
        for name, value in names.items():
            monkeypatch.setattr(f"{module}.{name}", value)
    yield tmp_path


@pytest.fixture
def template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the shipped .env.example used as the template for new .env files."""
    source = Path(__file__).resolve().parent.parent / ".env.example"
    copy = tmp_path / "template.env"
    copy.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(cfg, "ENV_TEMPLATE", copy)
    return copy


@pytest.fixture
def keyboard(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], list[str]]:
    """Script the answers typed at input(); running out behaves like Ctrl+Z / Ctrl+D."""

    def script(answers: list[str]) -> list[str]:
        remaining = iter(answers)
        prompts: list[str] = []

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            try:
                return next(remaining)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr("builtins.input", fake_input)
        return prompts

    return script
