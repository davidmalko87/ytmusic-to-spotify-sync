"""
Offline tests that drive the setup wizard and menu with scripted keyboard input.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0

YT Music, Spotify and Last.fm are replaced by fakes; nothing touches the network.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values

from playlist_sync import cli, menu
from playlist_sync import config as cfg
from playlist_sync.csv_manager import write_enriched_csv
from playlist_sync.models import Track

YT_ID = "PLd1XLAKnZQaXUPjf1khkyTNhg-KqHofWe"
SP_ID = "37i9dQZF1DXcBWIGoYBM5M"
YT_PLAYLISTS = [
    {"id": "LM", "title": "Liked Music", "count": "120"},
    {"id": YT_ID, "title": "Road Trip Mix", "count": "42"},
    {"id": "PLother123", "title": "Chill Evening", "count": "7"},
]


class FakeSpotify:
    """Just enough of spotipy.Spotify for the wizard."""

    def current_user(self) -> dict[str, str]:
        return {"id": "me", "display_name": "Test User"}


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch, home: Path) -> Iterator[dict[str, list]]:
    """Replace every network-facing function the menu calls; record what happened."""
    calls: dict[str, list] = {"created": [], "dispatched": [], "auth": []}

    def fake_setup_browser_auth(path: str, display_path: str | None = None) -> bool:
        calls["auth"].append(path)
        Path(path).write_text('{"cookie": "fake"}', encoding="utf-8")
        return True

    def fake_create(sp: object, name: str, description: str = "") -> dict[str, str]:
        calls["created"].append(name)
        return {"id": SP_ID, "name": name}

    monkeypatch.setattr(menu, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(menu, "setup_browser_auth", fake_setup_browser_auth)
    monkeypatch.setattr(menu, "list_library_playlists", lambda auth: list(YT_PLAYLISTS))
    monkeypatch.setattr(menu, "get_playlist_info",
                        lambda auth, pid: {"id": pid, "title": "Pasted One", "count": "3"})
    monkeypatch.setattr(menu, "get_spotify_client", lambda config: FakeSpotify())
    monkeypatch.setattr(menu, "list_my_playlists", lambda sp: [])
    monkeypatch.setattr(menu, "create_playlist", fake_create)
    monkeypatch.setattr(menu, "get_playlist_access",
                        lambda sp, pid: {"name": "Pasted Spotify", "can_edit": True})
    monkeypatch.setattr(menu, "check_api_key", lambda key: None)
    monkeypatch.setattr(cli, "dispatch", lambda args: calls["dispatched"].append(args))
    yield calls


def saved() -> dict[str, str | None]:
    return dotenv_values(cfg.ENV_FILE) if cfg.ENV_FILE.exists() else {}


def test_wizard_first_run_saves_everything_without_editing_env(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    keyboard([
        "2",            # YT playlist: pick "Road Trip Mix"
        "a" * 32,       # Spotify Client ID
        "b" * 32,       # Spotify client secret
        "",             # log in to Spotify now? -> yes
        "",             # Spotify playlist: Enter = create new
        "",             # new playlist name: keep suggestion (YT title)
        "y",            # Last.fm (optional): set it up now
        "c" * 32,       # Last.fm key
        "n",            # run first sync now? -> no
    ])
    menu.run_setup_wizard()

    values = saved()
    assert values["YTMUSIC_PLAYLIST_ID"] == YT_ID
    assert values["SPOTIPY_CLIENT_ID"] == "a" * 32
    assert values["SPOTIPY_CLIENT_SECRET"] == "b" * 32
    assert values["SPOTIFY_PLAYLIST_ID"] == SP_ID
    assert values["LASTFM_API_KEY"] == "c" * 32
    assert fakes["created"] == ["Road Trip Mix"]
    assert (home / "browser.json").is_file()
    assert not (home / "browser.json.new").exists()
    assert menu.read_state().complete
    assert menu.known_name(YT_ID) == "Road Trip Mix"


def test_wizard_skips_finished_steps(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    (home / "browser.json").write_text("{}", encoding="utf-8")
    cfg.save_setting("YTMUSIC_PLAYLIST_ID", YT_ID)
    # keep login, keep playlist, empty Client ID (= skip), then stop the wizard
    prompts = keyboard(["", "", "", "n"])
    menu.run_setup_wizard()
    assert fakes["auth"] == []
    assert sum("Change it?" in p for p in prompts) == 2
    assert any("Continue with the next one?" in p for p in prompts)
    assert cfg.is_unset(saved().get("SPOTIPY_CLIENT_ID"))


def test_failed_login_check_keeps_previous_login(
    monkeypatch: pytest.MonkeyPatch, keyboard: Callable[[list[str]], list[str]],
    fakes: dict[str, list], home: Path,
) -> None:
    old = home / "browser.json"
    old.write_text('{"cookie": "old"}', encoding="utf-8")

    def rejecting(auth: str) -> list:
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(menu, "list_library_playlists", rejecting)
    keyboard(["n"])  # paste again? -> no
    assert menu.step_yt_login(menu.read_state()) is False
    assert old.read_text(encoding="utf-8") == '{"cookie": "old"}'
    assert not (home / "browser.json.new").exists()


def test_playlist_picker_search_then_number(keyboard: Callable[[list[str]], list[str]]) -> None:
    keyboard(["chill", "1"])
    choice = menu.pick_playlist(YT_PLAYLISTS, "title", {"B": "Back"})
    assert choice == YT_PLAYLISTS[2]


def test_playlist_picker_out_of_range_then_letter(keyboard: Callable[[list[str]], list[str]]) -> None:
    keyboard(["9", "b"])
    assert menu.pick_playlist(YT_PLAYLISTS, "title", {"B": "Back"}) == "B"


def test_pasted_youtube_link_is_reduced_to_id(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    (home / "browser.json").write_text("{}", encoding="utf-8")
    keyboard(["p", f"https://music.youtube.com/playlist?list={YT_ID}&si=abc"])
    assert menu.step_yt_playlist(menu.read_state()) is True
    assert saved()["YTMUSIC_PLAYLIST_ID"] == YT_ID


def test_non_hex_client_id_needs_confirmation(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list],
) -> None:
    keyboard(["not-a-real-id", "n", "a" * 32, "b" * 32, "n"])
    assert menu.step_spotify_app(menu.read_state()) is True
    assert saved()["SPOTIPY_CLIENT_ID"] == "a" * 32


def test_changing_client_id_drops_old_spotify_token(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    cfg.save_setting("SPOTIPY_CLIENT_ID", "a" * 32)
    cfg.save_setting("SPOTIPY_CLIENT_SECRET", "b" * 32)
    cfg.SPOTIFY_TOKEN_CACHE.write_text('{"refresh_token": "r"}', encoding="utf-8")
    cfg.LEGACY_TOKEN_CACHE.write_text('{"refresh_token": "old"}', encoding="utf-8")
    keyboard(["d" * 32, "", "n"])  # new ID, Enter keeps the secret, skip login check
    assert menu.step_spotify_app(menu.read_state()) is True
    assert not cfg.SPOTIFY_TOKEN_CACHE.exists()
    assert not cfg.LEGACY_TOKEN_CACHE.exists()  # would otherwise be adopted again
    assert saved()["SPOTIPY_CLIENT_SECRET"] == "b" * 32


def test_rejected_lastfm_key_is_not_saved_by_default(
    monkeypatch: pytest.MonkeyPatch, keyboard: Callable[[list[str]], list[str]],
    fakes: dict[str, list],
) -> None:
    monkeypatch.setattr(menu, "check_api_key", lambda key: "Invalid API key")
    keyboard(["c" * 32, ""])  # key, then Enter = do not save anyway
    assert menu.step_lastfm(menu.read_state()) is False
    assert cfg.is_unset(saved().get("LASTFM_API_KEY"))


def test_action_offers_only_the_missing_setup(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    (home / "browser.json").write_text("{}", encoding="utf-8")
    keyboard(["", "3"])  # yes, set it up; pick "Chill Evening"
    assert menu.ensure_ready(("yt_login", "yt_playlist")) is True
    assert saved()["YTMUSIC_PLAYLIST_ID"] == "PLother123"
    assert fakes["auth"] == []


def test_refill_with_replace_requires_second_yes(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list],
) -> None:
    keyboard(["", "y", "n"])  # real run, empty first: yes, are you sure: no
    menu.action_refill()
    assert fakes["dispatched"] == []

    keyboard(["", "y", "y"])
    menu.action_refill()
    (args,) = fakes["dispatched"]
    assert args.command == "repush" and args.replace is True and args.dry_run is False


def test_dry_run_choice_reaches_the_command(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list],
) -> None:
    keyboard(["d", "50"])  # dry run; first sync -> limit 50
    menu.action_sync()
    (args,) = fakes["dispatched"]
    assert isinstance(args, argparse.Namespace)
    assert (args.command, args.dry_run, args.limit) == ("sync", True, 50)


def test_menu_welcome_declined_then_quit(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list],
    capsys: pytest.CaptureFixture[str],
) -> None:
    keyboard(["n", "xyz", "4", "", "q"])
    menu.interactive_menu()
    out = capsys.readouterr().out
    assert "Welcome!" in out
    assert "'XYZ' is not an option" in out
    assert [a.command for a in fakes["dispatched"]] == ["status"]
    assert out.rstrip().endswith("Bye!")


def test_menu_survives_end_of_input(keyboard: Callable[[list[str]], list[str]],
                                    fakes: dict[str, list]) -> None:
    keyboard([])
    menu.interactive_menu()  # must return, not raise


def test_menu_survives_a_crashing_action(
    monkeypatch: pytest.MonkeyPatch, keyboard: Callable[[list[str]], list[str]],
    fakes: dict[str, list], capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(args: argparse.Namespace) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "dispatch", boom)
    keyboard(["n", "4", "", "q"])
    menu.interactive_menu()
    assert "Something went wrong: kaboom" in capsys.readouterr().out


def test_menu_output_is_ascii(fakes: dict[str, list], capsys: pytest.CaptureFixture[str]) -> None:
    menu.render_menu(menu.read_state())
    out = capsys.readouterr().out
    assert out.isascii()
    assert "finish setup" in out


def test_empty_library_does_not_silently_replace_a_working_login(
    monkeypatch: pytest.MonkeyPatch, keyboard: Callable[[list[str]], list[str]],
    fakes: dict[str, list], home: Path,
) -> None:
    old = home / "browser.json"
    old.write_text('{"cookie": "old"}', encoding="utf-8")
    monkeypatch.setattr(menu, "list_library_playlists", lambda auth: [])
    keyboard(["", "n"])  # replace anyway? Enter = no; paste again? no
    assert menu.step_yt_login(menu.read_state()) is False
    assert old.read_text(encoding="utf-8") == '{"cookie": "old"}'
    assert not (home / "browser.json.new").exists()


def test_ctrl_c_during_login_check_removes_the_temporary_cookie_file(
    monkeypatch: pytest.MonkeyPatch, fakes: dict[str, list], home: Path,
) -> None:
    def interrupted(auth: str) -> list:
        raise KeyboardInterrupt

    monkeypatch.setattr(menu, "list_library_playlists", interrupted)
    with pytest.raises(KeyboardInterrupt):
        menu.step_yt_login(menu.read_state())
    assert not (home / "browser.json.new").exists()


def _seed_sync_data(home: Path, playlist_id: str = YT_ID) -> None:
    """A 5-track snapshot of which 2 tracks were already searched on Spotify."""
    snapshots = cfg.SNAPSHOTS_DIR
    snapshots.mkdir(parents=True, exist_ok=True)
    tracks = [{"trackId": f"vid{n:08d}", "title": f"Song {n}", "artist": "A"} for n in range(5)]
    (snapshots / "latest.json").write_text(json.dumps(
        {"playlist_id": playlist_id, "track_count": 5, "tracks": tracks}), encoding="utf-8")
    searched = [Track(title=f"Song {n}", artist="A", platform="ytmusic", track_id=f"vid{n:08d}",
                      match_attempted=True) for n in range(2)]
    write_enriched_csv(searched, cfg.ENRICHED_CSV)


def test_switching_source_playlist_archives_old_data_instead_of_mixing(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    (home / "browser.json").write_text("{}", encoding="utf-8")
    cfg.save_setting("YTMUSIC_PLAYLIST_ID", YT_ID)
    _seed_sync_data(home)
    keyboard(["3", ""])  # pick "Chill Evening", then Enter = start fresh (1)
    assert menu.step_yt_playlist(menu.read_state()) is True
    assert saved()["YTMUSIC_PLAYLIST_ID"] == "PLother123"
    assert not cfg.ENRICHED_CSV.exists()
    archived = list((cfg.DATA_DIR / "archive").glob("*/playlist_enriched.csv"))
    assert len(archived) == 1


def test_switching_source_playlist_can_be_cancelled(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    (home / "browser.json").write_text("{}", encoding="utf-8")
    cfg.save_setting("YTMUSIC_PLAYLIST_ID", YT_ID)
    _seed_sync_data(home)
    keyboard(["3", "b"])
    assert menu.step_yt_playlist(menu.read_state()) is False
    assert saved()["YTMUSIC_PLAYLIST_ID"] == YT_ID
    assert cfg.ENRICHED_CSV.exists()


def test_sync_ignores_a_snapshot_of_another_playlist(
    home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_sync_data(home, playlist_id="PLsomethingElse")
    assert cli._snapshot_for(YT_ID) is None
    assert "nothing is removed from Spotify" in capsys.readouterr().out
    _seed_sync_data(home, playlist_id=YT_ID)
    assert len(cli._snapshot_for(YT_ID)) == 5


def test_limit_is_offered_again_while_tracks_are_pending(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    _seed_sync_data(home)  # snapshot says 5 tracks, CSV holds 2 -> 3 pending
    assert menu.pending_tracks() == 3
    prompts = keyboard(["", "2"])
    menu.action_sync()
    assert any("How many tracks" in p for p in prompts)
    (args,) = fakes["dispatched"]
    assert args.limit == 2


def test_csv_import_over_existing_matches_needs_confirmation(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    export = home / "export.csv"
    export.write_text("title,artist\nx,y\n", encoding="utf-8")
    cfg.save_setting("SOURCE_CSV", str(export))
    _seed_sync_data(home)
    keyboard(["1", ""])  # import, then Enter = no
    menu.action_csv()
    assert fakes["dispatched"] == []
    assert cfg.ENRICHED_CSV.exists()

    keyboard(["1", "y"])
    menu.action_csv()
    # import-csv --force reads the CSV first and archives only after that
    assert [(a.command, a.force) for a in fakes["dispatched"]] == [("import-csv", True)]


def test_menu_csv_import_of_an_unreadable_csv_keeps_the_sync_data(
    keyboard: Callable[[list[str]], list[str]], monkeypatch: pytest.MonkeyPatch, home: Path,
) -> None:
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    export = home / "export.csv"
    export.write_text("title,artist,duration\nx,y,soon\n", encoding="utf-8")  # bad cell
    cfg.save_setting("SOURCE_CSV", str(export))
    _seed_sync_data(home)
    before = cfg.ENRICHED_CSV.read_bytes()
    keyboard(["1", "y"])
    menu.action_csv()
    assert cfg.ENRICHED_CSV.read_bytes() == before
    assert not (cfg.DATA_DIR / "archive").exists()


def test_pasted_spotify_playlist_owned_by_someone_else_is_refused(
    monkeypatch: pytest.MonkeyPatch, keyboard: Callable[[list[str]], list[str]],
    fakes: dict[str, list],
) -> None:
    monkeypatch.setattr(menu, "get_playlist_access",
                        lambda sp, pid: {"name": "Today's Top Hits", "can_edit": False})
    keyboard([f"https://open.spotify.com/playlist/{SP_ID}", ""])  # then Enter = back
    assert menu._paste_spotify_playlist(FakeSpotify()) is None


def test_spotify_403_explains_the_premium_rule(
    monkeypatch: pytest.MonkeyPatch, fakes: dict[str, list],
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Forbidden(Exception):
        http_status = 403

    def refuse(config: dict) -> None:
        raise Forbidden("403 Forbidden")

    monkeypatch.setattr(menu, "get_spotify_client", refuse)
    assert menu.spotify_login() is None
    assert "Premium" in capsys.readouterr().out


def test_prompt_with_non_ascii_default_does_not_crash_on_legacy_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cp1252Stdout:
        encoding = "cp1252"

    monkeypatch.setattr(menu.sys, "stdout", Cp1252Stdout())
    seen: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": seen.append(prompt) or "")
    assert menu.ask("Name", default="Рок музыка") == "Рок музыка"
    seen[0].encode("cp1252")  # the prompt itself must be printable there


def test_archive_rolls_back_when_a_file_is_locked(
    monkeypatch: pytest.MonkeyPatch, home: Path,
) -> None:
    _seed_sync_data(home)
    real_move = menu.shutil.move

    def move(src: str, dst: str) -> str:
        if src.endswith("playlist_enriched.csv"):
            raise PermissionError(13, "locked", src)
        return real_move(src, dst)

    monkeypatch.setattr(menu.shutil, "move", move)
    with pytest.raises(OSError, match="open in another program"):
        menu.archive_playlist_data("old")
    assert (cfg.SNAPSHOTS_DIR / "latest.json").is_file()  # moved back
    assert cfg.ENRICHED_CSV.is_file()


def test_lastfm_only_csv_still_counts_as_first_sync(home: Path) -> None:
    _seed_sync_data(home)
    write_enriched_csv([Track(title="x", artist="y", platform="ytmusic", track_id="vid00000001",
                              lastfm_listeners=5)], cfg.ENRICHED_CSV)
    assert menu.pending_tracks() is None
    assert "first sync" in menu.next_hint(menu.SetupState(
        config={}, yt_login=True, yt_playlist=True, spotify_app=True, spotify_playlist=True,
        lastfm=True))


def test_changing_yt_login_resets_the_likes_snapshot(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list], home: Path,
) -> None:
    (home / "browser.json").write_text("{}", encoding="utf-8")
    likes = cfg.LIKES_SNAPSHOTS_DIR
    likes.mkdir(parents=True)
    (likes / "latest.json").write_text("{}", encoding="utf-8")
    assert menu.step_yt_login(menu.read_state()) is True
    assert not (likes / "latest.json").exists()
    assert list((cfg.DATA_DIR / "archive").glob("likes-*/latest.json"))


@pytest.mark.parametrize(("raw", "expected"), [
    (r'"C:\Users\me\My Export.csv"', r"C:\Users\me\My Export.csv"),
    ("'/tmp/export.csv'", "/tmp/export.csv"),
])
def test_clean_path_strips_quotes(raw: str, expected: str) -> None:
    assert menu.clean_path(raw) == expected


def test_clean_path_unescapes_macos_drag_and_drop() -> None:
    raw = r"/Users/me/My\ Export\ \(1\).csv"
    assert menu.clean_path(raw, posix=True) == "/Users/me/My Export (1).csv"
    assert menu.clean_path(raw, posix=False) == raw  # Windows: backslashes are separators


@pytest.mark.parametrize(("uri", "local"), [
    ("http://127.0.0.1:8888/callback", True), ("http://localhost:9090/cb", True),
    ("https://example.com/callback", False), ("http://127.0.0.1/callback", False),
])
def test_local_redirect_detection(uri: str, local: bool) -> None:
    assert menu._local_redirect(uri) is local


def test_likes_retry_is_offered_only_when_likes_failed_before(
    keyboard: Callable[[list[str]], list[str]], fakes: dict[str, list],
) -> None:
    keyboard([""])  # real run; no likes_unmatched.csv -> no retry question
    menu.action_likes()
    keyboard(["", "y"])
    cfg.LIKES_UNMATCHED_CSV.parent.mkdir(parents=True, exist_ok=True)
    cfg.LIKES_UNMATCHED_CSV.write_text("title,artist\nx,y\n", encoding="utf-8")
    menu.action_likes()
    assert [a.retry_unmatched for a in fakes["dispatched"]] == [False, True]


def test_held_back_unsaves_need_a_yes_in_the_menu(
    keyboard: Callable[[list[str]], list[str]], monkeypatch: pytest.MonkeyPatch,
    fakes: dict[str, list],
) -> None:
    runs: list[argparse.Namespace] = []

    def dispatch(args: argparse.Namespace) -> None:
        runs.append(args)
        if not args.force:
            args.held_back, args.held_back_fps = 120, ["fp1", "fp2"]

    monkeypatch.setattr(cli, "dispatch", dispatch)
    keyboard(["", ""])  # real run; Enter = no
    menu.action_likes()
    keyboard(["", "y"])
    menu.action_likes()
    assert [a.force for a in runs] == [False, False, True]
    assert runs[-1].confirmed_fps == ["fp1", "fp2"]  # only what the user was shown
