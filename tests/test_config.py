"""
Offline tests for settings resolution, placeholder detection and .env writes.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from playlist_sync import config as cfg

YT_ID = "PLd1XLAKnZQaXUPjf1khkyTNhg-KqHofWe"
SP_ID = "37i9dQZF1DXcBWIGoYBM5M"


@pytest.mark.parametrize("value", ["", "   ", None, "your_playlist_id_here", "YOUR_CLIENT_ID_HERE",
                                   "your_api_key_here"])
def test_is_unset_true_for_empty_and_placeholders(value: str | None) -> None:
    assert cfg.is_unset(value)


@pytest.mark.parametrize("value", [YT_ID, "LM", "abc123", "your_real_key"])
def test_is_unset_false_for_real_values(value: str) -> None:
    assert not cfg.is_unset(value)


@pytest.mark.parametrize("raw", [
    YT_ID,
    f"  {YT_ID}  ",
    f'"{YT_ID}"',
    f"https://music.youtube.com/playlist?list={YT_ID}",
    f"https://music.youtube.com/playlist?list={YT_ID}&si=AbCdEf",
    f"https://www.youtube.com/watch?v=dQw4w9WgXcQ&list={YT_ID}&index=3",
    f"music.youtube.com/playlist?list={YT_ID}",
    f"list={YT_ID}",
    f"https://music.youtube.com/browse/VL{YT_ID}",
])
def test_parse_ytmusic_playlist_id_accepts_ids_and_links(raw: str) -> None:
    assert cfg.parse_ytmusic_playlist_id(raw) == YT_ID


@pytest.mark.parametrize("raw", ["", "your_playlist_id_here", "https://music.youtube.com/",
                                 "not a playlist", "x"])
def test_parse_ytmusic_playlist_id_rejects_garbage(raw: str) -> None:
    assert cfg.parse_ytmusic_playlist_id(raw) == ""


@pytest.mark.parametrize("raw", [
    SP_ID,
    f"https://open.spotify.com/playlist/{SP_ID}",
    f"https://open.spotify.com/playlist/{SP_ID}?si=0123456789abcdef",
    f"https://open.spotify.com/intl-de/playlist/{SP_ID}",
    f"spotify:playlist:{SP_ID}",
])
def test_parse_spotify_playlist_id_accepts_ids_and_links(raw: str) -> None:
    assert cfg.parse_spotify_playlist_id(raw) == SP_ID


@pytest.mark.parametrize("raw", ["", "your_playlist_id_here", "https://open.spotify.com/",
                                 "tooShort"])
def test_parse_spotify_playlist_id_rejects_garbage(raw: str) -> None:
    assert cfg.parse_spotify_playlist_id(raw) == ""


def test_resolve_home_prefers_override(tmp_path: Path) -> None:
    target = tmp_path / "custom"
    home = cfg.resolve_home(tmp_path, environ={cfg.HOME_ENV_VAR: str(target)})
    assert home == target.resolve()


def test_resolve_home_uses_checkout_root(tmp_path: Path) -> None:
    (tmp_path / "playlist_sync.py").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert cfg.resolve_home(tmp_path, environ={}) == tmp_path


@pytest.mark.parametrize(("platform", "environ", "expected"), [
    ("win32", {"APPDATA": "C:/Users/u/AppData/Roaming"},
     Path("C:/Users/u/AppData/Roaming") / cfg.APP_DIR_NAME),
    ("win32", {}, Path("/home/u") / "AppData" / "Roaming" / cfg.APP_DIR_NAME),
    ("darwin", {}, Path("/home/u") / "Library" / "Application Support" / cfg.APP_DIR_NAME),
    ("linux", {}, Path("/home/u") / ".config" / cfg.APP_DIR_NAME),
    ("linux", {"XDG_CONFIG_HOME": "/xdg"}, Path("/xdg") / cfg.APP_DIR_NAME),
])
def test_installed_copy_uses_fixed_per_user_folder(
    tmp_path: Path, platform: str, environ: dict[str, str], expected: Path,
) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    home = cfg.resolve_home(site_packages, environ=environ, platform=platform,
                            user_home=Path("/home/u"))
    assert home == expected
    assert site_packages not in home.parents


@pytest.mark.parametrize(("checkout", "command"), [
    (True, "python playlist_sync.py setup"), (False, "ytmusic-sync setup"),
])
def test_setup_command_matches_install_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, checkout: bool, command: str,
) -> None:
    if checkout:
        (tmp_path / "playlist_sync.py").write_text("", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(cfg, "SOURCE_ROOT", tmp_path)
    assert cfg.setup_command() == command


def test_setup_hint_mentions_override(monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(cfg.HOME_ENV_VAR, "/srv/sync")
    cfg.print_setup_hint("log in")
    assert f"keep {cfg.HOME_ENV_VAR}=" in capsys.readouterr().out


def test_load_config_sees_hand_edits_made_after_first_read(template: Path) -> None:
    cfg.save_setting("YTMUSIC_PLAYLIST_ID", YT_ID)
    assert cfg.load_config()["YTMUSIC_PLAYLIST_ID"] == YT_ID
    text = cfg.ENV_FILE.read_text(encoding="utf-8").replace(YT_ID, "PLeditedByHand")
    cfg.ENV_FILE.write_text(text, encoding="utf-8")
    assert cfg.load_config()["YTMUSIC_PLAYLIST_ID"] == "PLeditedByHand"


def test_real_environment_still_wins_over_env_file(monkeypatch: pytest.MonkeyPatch,
                                                   template: Path) -> None:
    cfg.save_setting("LASTFM_API_KEY", "a" * 32)
    monkeypatch.setenv("LASTFM_API_KEY", "b" * 32)
    monkeypatch.setattr(cfg, "_PROCESS_ENV_KEYS", frozenset({"LASTFM_API_KEY"}))
    assert cfg.load_config()["LASTFM_API_KEY"] == "b" * 32


def test_ensure_home_creates_missing_folder(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    target = home / "new" / "settings"
    monkeypatch.setattr(cfg, "PROJECT_ROOT", target)
    assert cfg.ensure_home() == target
    assert target.is_dir()


def test_legacy_site_packages_state_is_found_and_copied(
    monkeypatch: pytest.MonkeyPatch, home: Path,
) -> None:
    site = home / "site-packages"
    (site / "data" / "snapshots").mkdir(parents=True)
    (site / "data" / "playlist_enriched.csv").write_text("title\n", encoding="utf-8")
    (site / "browser.json").write_text("{}", encoding="utf-8")
    (site / ".env").write_text("YTMUSIC_PLAYLIST_ID=PLold\n", encoding="utf-8")
    new_home = home / "settings"
    monkeypatch.setattr(cfg, "SOURCE_ROOT", site)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", new_home)

    assert cfg.find_legacy_home() == site
    assert sorted(cfg.migrate_legacy_home(site)) == [".env", "browser.json", "data"]
    assert (new_home / "data" / "playlist_enriched.csv").is_file()
    assert (site / "browser.json").is_file()  # originals are left in place
    assert cfg.migrate_legacy_home(site) == []  # nothing is overwritten on a second run


def test_foreign_data_dir_in_site_packages_is_not_copied(
    monkeypatch: pytest.MonkeyPatch, home: Path,
) -> None:
    site = home / "site-packages"
    (site / "data").mkdir(parents=True)
    (site / "data" / "some_other_package.dat").write_text("x", encoding="utf-8")
    (site / "browser.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cfg, "SOURCE_ROOT", site)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", home / "settings")
    assert cfg.migrate_legacy_home(site) == ["browser.json"]


def test_checkout_never_reports_legacy_state() -> None:
    # The real repo is a checkout: its own .env/browser.json are current, not legacy
    assert cfg.find_legacy_home() is None


def test_load_config_turns_pasted_links_into_ids(home: Path) -> None:
    cfg.ENV_FILE.write_text(
        f"YTMUSIC_PLAYLIST_ID=https://music.youtube.com/playlist?list={YT_ID}&si=x\n"
        f"SPOTIFY_PLAYLIST_ID=https://open.spotify.com/playlist/{SP_ID}?si=y\n",
        encoding="utf-8",
    )
    config = cfg.load_config()
    assert config["YTMUSIC_PLAYLIST_ID"] == YT_ID
    assert config["SPOTIFY_PLAYLIST_ID"] == SP_ID
    assert config["SPOTIPY_REDIRECT_URI"] == cfg.DEFAULT_REDIRECT_URI
    assert Path(config["YTMUSIC_AUTH_FILE"]) == home / "browser.json"


def test_save_setting_creates_env_from_template_and_keeps_comments(template: Path) -> None:
    cfg.save_setting("YTMUSIC_PLAYLIST_ID", YT_ID)
    text = cfg.ENV_FILE.read_text(encoding="utf-8")
    assert f"YTMUSIC_PLAYLIST_ID={YT_ID}\n" in text
    assert "# Create an app at https://developer.spotify.com/dashboard" in text
    assert text.count("YTMUSIC_PLAYLIST_ID=") == 1
    assert os.environ["YTMUSIC_PLAYLIST_ID"] == YT_ID


def test_save_setting_without_template_creates_empty_env(monkeypatch: pytest.MonkeyPatch,
                                                         home: Path) -> None:
    monkeypatch.setattr(cfg, "ENV_TEMPLATE", home / "missing.example")
    cfg.save_setting("LASTFM_API_KEY", "0" * 32)
    assert dotenv_values(cfg.ENV_FILE) == {"LASTFM_API_KEY": "0" * 32}


def test_save_setting_round_trips_values_that_need_quotes(template: Path) -> None:
    tricky = "a b#c'd\"e$f"
    cfg.save_setting("SOURCE_CSV", tricky)
    assert dotenv_values(cfg.ENV_FILE)["SOURCE_CSV"] == tricky


def test_save_setting_is_seen_by_next_load_config(template: Path) -> None:
    # load_dotenv puts the placeholder into os.environ and never overrides it,
    # so save_setting must update os.environ for the new value to show up.
    assert cfg.is_unset(cfg.load_config()["YTMUSIC_PLAYLIST_ID"])
    cfg.save_setting("YTMUSIC_PLAYLIST_ID", YT_ID)
    assert cfg.load_config()["YTMUSIC_PLAYLIST_ID"] == YT_ID


def test_save_setting_rejects_multiline_values(template: Path) -> None:
    with pytest.raises(ValueError):
        cfg.save_setting("SOURCE_CSV", "a\nB=injected")


def test_missing_settings_reports_placeholders(template: Path) -> None:
    config = cfg.load_config()
    assert cfg.missing_settings(config, ("SPOTIPY_CLIENT_ID", "YTMUSIC_PLAYLIST_ID")) == [
        "SPOTIPY_CLIENT_ID", "YTMUSIC_PLAYLIST_ID",
    ]


def test_require_ytmusic_playlist_exits_on_placeholder(template: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    cfg.ENV_FILE.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cfg.require_ytmusic_playlist(cfg.load_config())
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "no YT Music playlist chosen yet" in out
    assert "setup" in out


def test_require_ytmusic_playlist_accepts_real_id() -> None:
    cfg.require_ytmusic_playlist({"YTMUSIC_PLAYLIST_ID": YT_ID})


def test_require_spotify_config_can_skip_playlist(capsys: pytest.CaptureFixture[str]) -> None:
    config = {"SPOTIPY_CLIENT_ID": "a" * 32, "SPOTIPY_CLIENT_SECRET": "b" * 32,
              "SPOTIFY_PLAYLIST_ID": "your_playlist_id_here"}
    cfg.require_spotify_config(config, need_playlist=False)
    with pytest.raises(SystemExit):
        cfg.require_spotify_config(config)
    assert "SPOTIFY_PLAYLIST_ID" in capsys.readouterr().out


def test_legacy_data_merges_into_an_existing_empty_data_folder(
    monkeypatch: pytest.MonkeyPatch, home: Path,
) -> None:
    site = home / "site-packages"
    (site / "data" / "snapshots").mkdir(parents=True)
    (site / "data" / "snapshots" / "latest.json").write_text("{}", encoding="utf-8")
    (site / "data" / "playlist_enriched.csv").write_text("old", encoding="utf-8")
    new_home = home / "settings"
    (new_home / "data" / "debug").mkdir(parents=True)  # created by an earlier command
    (new_home / "data" / "playlist_enriched.csv").write_text("new", encoding="utf-8")
    monkeypatch.setattr(cfg, "SOURCE_ROOT", site)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", new_home)
    assert cfg.migrate_legacy_home(site) == ["data"]
    assert (new_home / "data" / "snapshots" / "latest.json").is_file()
    assert (new_home / "data" / "playlist_enriched.csv").read_text(encoding="utf-8") == "new"
