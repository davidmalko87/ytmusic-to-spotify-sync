"""
Interactive menu and guided setup wizard.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0

The menu shows what is set up and what to do next, and asks for every value
the tool needs (YT Music login, both playlists, Spotify app keys, Last.fm key)
so nobody has to edit .env by hand. Console output stays ASCII because
Windows consoles often cannot print anything else.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import logging
import os
import re
import shlex
import shutil
import sys
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from spotipy.exceptions import SpotifyBaseException
from ytmusicapi.exceptions import YTMusicError

from playlist_sync import __version__
from playlist_sync import config as cfg
from playlist_sync.lastfm_client import check_api_key
from playlist_sync.spotify_client import (
    create_playlist,
    get_playlist_access,
    get_spotify_client,
    list_my_playlists,
)
from playlist_sync.utils import setup_logging
from playlist_sync.ytmusic_client import (
    get_playlist_info,
    list_library_playlists,
    setup_browser_auth,
)

logger = logging.getLogger("playlist_sync")

# What the YT Music / Spotify libraries raise for bad logins, unknown playlists
# and HTTP or network failures (requests errors are OSError; ytmusicapi raises
# KeyError when a page has an unexpected shape). Anything else is a bug: it
# reaches _run_safely, which reports it and logs the traceback.
API_ERRORS = (YTMusicError, SpotifyBaseException, OSError, LookupError, ValueError,
              TypeError, RuntimeError)

WIDTH = 66
SPOTIFY_DASHBOARD_URL = "https://developer.spotify.com/dashboard"
LASTFM_CREATE_URL = "https://www.last.fm/api/account/create"
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


# -- Small console helpers ------------------------------------------------

class Cancelled(Exception):
    """The user backed out of a prompt (Ctrl+C or end of input)."""


def console_safe(text: str) -> str:
    """Make text printable on the current console.

    A redirected Windows console uses a legacy code page, where a Cyrillic or
    emoji playlist title would raise UnicodeEncodeError.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        return text.encode(encoding, "replace").decode(encoding, "replace")


def say(text: str = "") -> None:
    """Print text that may contain user data (playlist names) without crashing."""
    print(console_safe(text))


def ask(prompt: str, default: str = "") -> str:
    """Read one line; an empty answer returns the default."""
    shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
    try:
        answer = input(console_safe(shown)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Cancelled from None
    return answer or default


def ask_secret(prompt: str) -> str:
    """Read a secret without echoing it when attached to a real terminal."""
    if not sys.stdin.isatty():
        return ask(prompt)
    try:
        return getpass.getpass(f"{prompt} (hidden - paste it and press Enter): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Cancelled from None


def confirm(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question; Enter picks the default."""
    hint = "Y/n" if default else "y/N"
    while True:
        answer = ask(f"{prompt} ({hint})").lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please type y or n.")


def pause() -> None:
    """Wait for Enter so the user can read the output before the menu redraws."""
    try:
        input("\nPress Enter to return to the menu...")
    except (EOFError, KeyboardInterrupt):
        print()


def ok(text: str) -> None:
    say(f"  [OK] {text}")


def warn(text: str) -> None:
    say(f"  [!]  {text}")


def mask(secret: str) -> str:
    """Show just enough of a secret to recognise it."""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{'*' * 6}{secret[-4:]}"


def shorten(text: str, width: int = 34) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def ask_hex_key(prompt: str, secret: bool = False, keep: str = "") -> str:
    """Ask for a 32-character hex key (Spotify, Last.fm); '' means keep / skip."""
    while True:
        if secret:
            value = ask_secret(prompt + (" (Enter keeps the current one)" if keep else ""))
        else:
            value = ask(prompt, default=keep)
        value = value.strip().strip("'\"")
        if not value or value == keep:
            return value
        if _HEX32_RE.match(value):
            return value
        warn(f"That is {len(value)} characters; these keys are normally 32 letters/digits.")
        if confirm("Use it anyway?", default=False):
            return value


def clean_path(raw: str, posix: bool | None = None) -> str:
    """Undo what dragging a file into a terminal adds: quotes, or macOS backslash escapes.

    Backslash unescaping only happens on POSIX, where '\\' is not a path separator.
    """
    text = raw.strip()
    if posix is None:
        posix = os.name != "nt"
    if posix and "\\" in text and not Path(text).expanduser().exists():
        with contextlib.suppress(ValueError):
            parts = shlex.split(text)
            if len(parts) == 1:
                return parts[0]
    return text.strip("'\"")


def warn_if_shadowed(key: str) -> None:
    """Tell the user when a system environment variable will override .env."""
    if cfg.shadowed_by_environment(key):
        warn(f"{key} is also set as a system environment variable, and that one wins")
        warn("over .env. Remove it from your environment or this change will not stick.")


# -- Remembered playlist names (cosmetic, so the menu can show titles) ----

def _names_file() -> Path:
    return cfg.DATA_DIR / "playlist_names.json"


def known_name(playlist_id: str) -> str:
    """Return the title saved for a playlist ID, or ''."""
    try:
        names = json.loads(_names_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(names.get(playlist_id, "")) if isinstance(names, dict) else ""


def remember_name(playlist_id: str, name: str) -> None:
    """Store a playlist title for display; failures only cost the label."""
    path = _names_file()
    try:
        names = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(names, dict):
            names = {}
        names[playlist_id] = name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError) as e:
        logger.debug("Could not store playlist name for %s: %s", playlist_id, e)


def describe_playlist(playlist_id: str) -> str:
    """The playlist's name when known (IDs only get cut off in the status column)."""
    return known_name(playlist_id) or playlist_id


# -- Setup state ------------------------------------------------------------

@dataclass
class SetupState:
    """What is configured right now, read fresh from .env and the auth file."""

    config: dict[str, str]
    yt_login: bool
    yt_playlist: bool
    spotify_app: bool
    spotify_playlist: bool
    lastfm: bool

    @property
    def complete(self) -> bool:
        return self.yt_login and self.yt_playlist and self.spotify_app and self.spotify_playlist


def read_state() -> SetupState:
    config = cfg.load_config()
    return SetupState(
        config=config,
        yt_login=Path(config["YTMUSIC_AUTH_FILE"]).is_file(),
        yt_playlist=bool(cfg.parse_ytmusic_playlist_id(config["YTMUSIC_PLAYLIST_ID"])),
        spotify_app=not cfg.missing_settings(config, ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET")),
        spotify_playlist=not cfg.is_unset(config["SPOTIFY_PLAYLIST_ID"]),
        lastfm=not cfg.is_unset(config["LASTFM_API_KEY"]),
    )


# -- Playlist picker ----------------------------------------------------------

def pick_playlist(
    playlists: list[dict[str, str]],
    name_key: str,
    options: dict[str, str],
    default: str = "",
) -> dict[str, str] | str:
    """Show playlists numbered and return the chosen one or an option letter.

    Typing text that is not a number or option filters the list by name, which
    keeps big libraries usable.
    """
    shown = playlists
    while True:
        print()
        for i, pl in enumerate(shown, 1):
            count = f"  ({pl['count']} tracks)" if pl.get("count") else ""
            say(f"  {i:>3}  {shorten(pl[name_key], 46)}{count}")
        if playlists and not shown:
            print("       (no playlist matches that search)")
        for key, text in options.items():
            say(f"  {key:>3}  {text}")
        hint = "Type a number or letter"
        if len(playlists) > 1:
            hint += ", or part of a name to search"
        answer = ask(hint, default=default)
        if not answer:
            continue
        if answer.isdigit():
            index = int(answer)
            if 1 <= index <= len(shown):
                return shown[index - 1]
            print(f"  There is no number {index} in the list.")
            continue
        if answer.upper() in options:
            return answer.upper()
        shown = [pl for pl in playlists if answer.lower() in pl[name_key].lower()]


# -- Setup steps ------------------------------------------------------------

def _verify_yt_login(auth_file: str, replacing: bool) -> bool:
    """Make one real YT Music call with a freshly captured login.

    A signed-in library normally lists at least "Liked Music", so an empty
    library is suspicious: it never silently replaces a login that exists.
    """
    print("\nChecking the login with YT Music...")
    try:
        playlists = list_library_playlists(auth_file)
    except API_ERRORS as e:
        warn(f"YT Music did not accept this login: {e}")
        return False
    if playlists:
        ok(f"Logged in - found {len(playlists)} playlist(s) in your library.")
        return True
    warn("YT Music answered, but your library shows no playlists at all. That usually")
    warn("means the headers came from a logged-out page or another account.")
    if replacing:
        return confirm("Replace your saved YT Music login with this one anyway?", default=False)
    return confirm("Keep this login anyway?", default=True)


def step_yt_login(state: SetupState) -> bool:
    """Capture browser headers into a temporary file and keep it only if it works."""
    auth_file = state.config["YTMUSIC_AUTH_FILE"]
    candidate = auth_file + ".new"
    replacing = Path(auth_file).is_file()
    try:
        while True:
            if not setup_browser_auth(candidate, display_path=auth_file):
                return False
            if _verify_yt_login(candidate, replacing):
                os.replace(candidate, auth_file)
                ok(f"YT Music login saved to {auth_file}")
                if replacing:
                    _reset_likes_snapshot()
                return True
            if replacing:
                print("Your previous YT Music login was kept.")
            if not confirm("Paste the headers again?", default=True):
                return False
    finally:
        # Never leave live session cookies behind in the temporary file
        Path(candidate).unlink(missing_ok=True)


def _reset_likes_snapshot() -> None:
    """After a login change, compare likes as a first run (adds only, never unsaves).

    The new login may be another account; diffing its likes against the old
    account's snapshot would unsave the old account's likes on Spotify.
    """
    latest = cfg.LIKES_SNAPSHOTS_DIR / "latest.json"
    if not latest.is_file():
        return
    target = cfg.DATA_DIR / "archive" / f"likes-{datetime.now():%Y%m%d-%H%M%S}"
    target.mkdir(parents=True, exist_ok=True)
    shutil.move(str(latest), str(target / latest.name))
    print("  The next 'Sync Liked Songs' starts fresh: it only adds, nothing is unsaved.")


def _paste_yt_playlist(auth_file: str) -> dict[str, str] | None:
    print("\nOpen the playlist on music.youtube.com and copy the link from the address bar.")
    print("It looks like https://music.youtube.com/playlist?list=PL...")
    while True:
        raw = ask("Paste the playlist link or ID (Enter to go back)")
        if not raw:
            return None
        playlist_id = cfg.parse_ytmusic_playlist_id(raw)
        if not playlist_id:
            warn("That does not contain a playlist ID (the part after 'list=').")
            continue
        try:
            return get_playlist_info(auth_file, playlist_id)
        except API_ERRORS as e:  # unknown or private playlists fail deep inside ytmusicapi
            logger.debug("get_playlist failed for %s: %s", playlist_id, e)
            warn("YT Music could not open that playlist. Check the link, and that the")
            warn("playlist belongs to (or is shared with) the account you logged in with.")
            if confirm("Use this ID anyway?", default=False):
                return {"id": playlist_id, "title": playlist_id, "count": ""}


def step_yt_playlist(state: SetupState) -> bool:
    """Pick the source playlist from the YT Music library (or paste a link)."""
    if not state.yt_login:
        warn("Log in to YT Music first (the step before this one).")
        return False
    auth_file = state.config["YTMUSIC_AUTH_FILE"]
    print("\nLoading your YT Music playlists...")
    try:
        playlists = list_library_playlists(auth_file)
    except API_ERRORS as e:
        warn(f"Could not load your playlists ({e}). You can paste a link instead.")
        playlists = []

    choice: dict[str, str] | str = "P"
    if playlists:
        print("Which YT Music playlist should be copied to Spotify?")
        choice = pick_playlist(
            playlists, "title",
            {"P": "Paste a playlist link instead", "B": "Back"},
        )
    if choice == "B":
        return False
    info = _paste_yt_playlist(auth_file) if choice == "P" else choice
    if not isinstance(info, dict):
        return False

    old_id = cfg.parse_ytmusic_playlist_id(state.config["YTMUSIC_PLAYLIST_ID"])
    switching = bool(old_id) and old_id != info["id"]
    if switching and _has_playlist_data() and not _confirm_source_switch(old_id, info):
        return False

    cfg.save_setting("YTMUSIC_PLAYLIST_ID", info["id"])
    remember_name(info["id"], info["title"])
    ok(f"Source playlist: {info['title']}")
    warn_if_shadowed("YTMUSIC_PLAYLIST_ID")
    if switching and read_state().spotify_app and confirm(
        "Choose which Spotify playlist receives it now?", default=True,
    ):
        step_spotify_playlist(read_state())
    return True


# Per-playlist sync state; likes data, debug stats and playlist names are kept
def _playlist_data_files() -> list[Path]:
    files = [cfg.UNMATCHED_CSV, cfg.SKIPPED_CSV, cfg.MATCH_CACHE]
    if cfg.SNAPSHOTS_DIR.is_dir():
        files += sorted(cfg.SNAPSHOTS_DIR.glob("*.json"))
    # The matches go last, so a locked file earlier never strands them
    files += [cfg.ENRICHED_CSV.with_suffix(".json"), cfg.ENRICHED_CSV]
    return [f for f in files if f.is_file()]


def _has_playlist_data() -> bool:
    return bool(_playlist_data_files())


def archive_playlist_data(label: str) -> Path | None:
    """Move the current playlist's sync data into data/archive/ (nothing is deleted)."""
    files = _playlist_data_files()
    if not files:
        return None
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label)[:40] or "playlist"
    target = cfg.DATA_DIR / "archive" / f"{safe_label}-{datetime.now():%Y%m%d-%H%M%S}"
    target.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    try:
        for path in files:
            shutil.move(str(path), str(target / path.name))
            moved.append(path)
    except OSError as e:
        # A file open in Excel cannot move on Windows: put everything back
        for path in moved:
            shutil.move(str(target / path.name), str(path))
        with contextlib.suppress(OSError):
            target.rmdir()
        raise OSError(f"{e.filename or 'a data file'} is open in another program (Excel?). "
                      "Close it and try again.") from e
    return target


def _confirm_source_switch(old_id: str, new: dict[str, str]) -> bool:
    """Explain what switching the source does to existing sync data; True to go ahead."""
    old_name = known_name(old_id) or old_id
    print()
    warn(f"The sync data in this folder belongs to '{shorten(old_name, 40)}'.")
    warn("Syncing a different playlist against it would treat all of its tracks as")
    warn("removed and delete them from the Spotify playlist.")
    print()
    say(f"  1  Start fresh for '{shorten(new['title'], 40)}' (recommended). The old sync data")
    print("     is moved to data/archive/ - nothing is deleted, and nothing is removed")
    print("     from Spotify.")
    print("  B  Back - keep the current playlist")
    while True:
        answer = ask("Choice", default="1").upper()
        if answer == "B":
            return False
        if answer == "1":
            archived = archive_playlist_data(old_name)
            if archived:
                ok(f"Old sync data moved to {archived}")
            return True
        print("  Type 1 or B.")


def step_spotify_app(state: SetupState) -> bool:
    """Collect the Spotify app's Client ID and secret."""
    redirect = state.config["SPOTIPY_REDIRECT_URI"]
    print()
    print("Spotify only lets tools use your account through a developer 'app', and only")
    print("while the account that creates it has Spotify Premium.")
    print("Creating one takes about two minutes and is needed only once:")
    print(f"  1. Open {SPOTIFY_DASHBOARD_URL} and log in with the account you sync to.")
    print("  2. Click 'Create app'. Any name and description will do.")
    print(f"  3. Redirect URI: paste exactly  {redirect}  and click 'Add'.")
    print("  4. Tick 'Web API', accept the terms and click 'Save'.")
    print("  5. Open the app's 'Settings'. Copy the Client ID, then click")
    print("     'View client secret' and copy that too.")
    print()

    old_id = "" if cfg.is_unset(state.config["SPOTIPY_CLIENT_ID"]) else state.config["SPOTIPY_CLIENT_ID"]
    old_secret = (
        "" if cfg.is_unset(state.config["SPOTIPY_CLIENT_SECRET"])
        else state.config["SPOTIPY_CLIENT_SECRET"]
    )
    client_id = ask_hex_key("Client ID", keep=old_id)
    if not client_id:
        print("No Client ID entered - Spotify app keys were not changed.")
        return False
    secret = ask_hex_key("Client secret", secret=True, keep=old_secret) or old_secret
    if not secret:
        print("No client secret entered - Spotify app keys were not changed.")
        return False

    if client_id != old_id:
        # A token issued to another app cannot be refreshed with the new keys;
        # the 0.7.x '.cache' file would otherwise be adopted again at login
        for token_file in (cfg.SPOTIFY_TOKEN_CACHE, cfg.LEGACY_TOKEN_CACHE):
            if token_file.is_file() and _is_spotipy_token(token_file):
                try:
                    token_file.unlink()
                except OSError as e:
                    logger.debug("Could not remove old Spotify token %s: %s", token_file, e)
    cfg.save_setting("SPOTIPY_CLIENT_ID", client_id)
    cfg.save_setting("SPOTIPY_CLIENT_SECRET", secret)
    ok(f"Spotify app keys saved (secret {mask(secret)}).")
    for key in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"):
        warn_if_shadowed(key)

    if confirm("Log in to Spotify now to check them? A browser window will open.", default=True):
        return spotify_login() is not None
    return True


def _unblock_oauth_server(redirect_uri: str) -> None:
    """Send the waiting spotipy callback server a request so its thread can finish.

    spotipy waits for the browser redirect in HTTPServer.handle_request(), which
    Ctrl+C cannot interrupt on Windows; an 'error' callback makes it return.
    """
    parsed = urllib.parse.urlparse(redirect_uri)
    if not parsed.port:
        return
    url = f"http://{parsed.hostname or '127.0.0.1'}:{parsed.port}{parsed.path or '/'}?error=cancelled"
    with contextlib.suppress(Exception):
        urllib.request.urlopen(url, timeout=2).close()


def _is_spotipy_token(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "refresh_token" in data


def _local_redirect(redirect_uri: str) -> bool:
    """True when spotipy will catch the redirect with its own local HTTP server."""
    parsed = urllib.parse.urlparse(redirect_uri)
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost") \
        and bool(parsed.port)


def spotify_login():  # -> spotipy.Spotify | None (spotipy types stay out of the signature)
    """Log in to Spotify (browser consent on first use) and report who we are."""
    config = cfg.load_config()
    if cfg.SPOTIFY_TOKEN_CACHE.is_file():
        print("\nConnecting to Spotify...")
    else:
        print("\nA browser window opens. Log in to Spotify and click 'Agree', then come back.")
        print("If the page says 'INVALID_CLIENT: Invalid redirect URI', add")
        print(f"  {config['SPOTIPY_REDIRECT_URI']}")
        print("to the app's Redirect URIs, click Save, press Ctrl+C here and try again.")

    result: dict[str, object] = {}

    def login() -> None:
        try:
            sp = get_spotify_client(config)
            user = sp.current_user()
            result["name"] = user.get("display_name") or user.get("id")
            result["sp"] = sp
        except Exception as e:  # noqa: BLE001 - the worker must hand every failure to the main thread
            result["error"] = e

    if not _local_redirect(config["SPOTIPY_REDIRECT_URI"]):
        # spotipy asks for the redirected URL with input(); keep that on the
        # main thread, where Ctrl+C interrupts it
        try:
            login()
        except KeyboardInterrupt:
            print()
            raise Cancelled from None
    else:
        # A worker thread keeps the main thread responsive to Ctrl+C while
        # spotipy's local server waits for the browser (not interruptible on Windows)
        worker = threading.Thread(target=login, daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                worker.join(0.25)
        except KeyboardInterrupt:
            _unblock_oauth_server(config["SPOTIPY_REDIRECT_URI"])
            print()
            raise Cancelled from None

    error = result.get("error")
    if error is not None:
        warn(f"Spotify login failed: {error}")
        if getattr(error, "http_status", None) == 403:
            warn("Spotify refused access. Developer apps only work while the account that")
            warn("created the app has Premium, and you must log in with that account (or one")
            warn("added under 'User Management' in the app's settings).")
        else:
            warn("Check the Client ID / secret (C in the menu changes them).")
        return None
    ok(f"Logged in to Spotify as {result['name']}.")
    return result["sp"]


def _paste_spotify_playlist(sp) -> dict[str, str] | None:
    print("\nIn Spotify: open the playlist > ... > Share > Copy link to playlist.")
    while True:
        raw = ask("Paste the playlist link or ID (Enter to go back)")
        if not raw:
            return None
        playlist_id = cfg.parse_spotify_playlist_id(raw)
        if not playlist_id:
            warn("That does not look like a Spotify playlist link.")
            continue
        try:
            access = get_playlist_access(sp, playlist_id)
        except API_ERRORS as e:
            warn(f"Spotify could not open that playlist ({e}).")
            continue
        name = str(access["name"])
        if not access["can_edit"]:
            warn(f"'{shorten(name, 40)}' belongs to someone else, so tracks cannot be added")
            warn("to it. Pick one of your own playlists, or create a new one (N).")
            continue
        return {"id": playlist_id, "name": name, "count": ""}


def step_spotify_playlist(state: SetupState) -> bool:
    """Pick, paste or create the Spotify playlist that receives the tracks."""
    if not state.spotify_app:
        warn("Add the Spotify app keys first (the step before this one).")
        return False
    sp = spotify_login()
    if sp is None:
        return False

    print("\nLoading your Spotify playlists...")
    try:
        playlists = list_my_playlists(sp)
    except API_ERRORS as e:
        warn(f"Could not load your playlists ({e}).")
        playlists = []

    yt_id = state.config["YTMUSIC_PLAYLIST_ID"]
    suggested = known_name(yt_id) or "From YT Music"
    print("Which Spotify playlist should receive the tracks?")
    choice = pick_playlist(
        playlists, "name",
        {
            "N": f"Create a new playlist (\"{shorten(suggested, 30)}\")",
            "P": "Paste a playlist link instead",
            "B": "Back",
        },
        default="N" if not playlists else "",
    )
    if choice == "B":
        return False
    if choice == "N":
        name = ask("Name for the new Spotify playlist", default=suggested)
        try:
            info: dict[str, str] | None = create_playlist(
                sp, name, "Synced from YT Music by ytmusic-to-spotify-sync",
            )
        except API_ERRORS as e:
            warn(f"Spotify did not create the playlist: {e}")
            return False
        ok(f"Created private playlist '{name}'.")
    elif choice == "P":
        info = _paste_spotify_playlist(sp)
    else:
        info = choice
    if not isinstance(info, dict):
        return False

    previous = state.config["SPOTIFY_PLAYLIST_ID"]
    cfg.save_setting("SPOTIFY_PLAYLIST_ID", info["id"])
    remember_name(info["id"], info["name"])
    ok(f"Target playlist: {info['name']}")
    warn_if_shadowed("SPOTIFY_PLAYLIST_ID")
    if not cfg.is_unset(previous) and previous != info["id"] and cfg.ENRICHED_CSV.exists():
        print("  Tip: this is a different playlist than before. Use 'Re-fill Spotify")
        print("  playlist' in the menu to copy your existing matches into it.")
    return True


def step_lastfm(state: SetupState) -> bool:
    """Optional Last.fm key for play counts, listeners and genre tags."""
    print()
    print("Last.fm adds play counts, listener numbers and genre tags. Free and optional.")
    print(f"  1. Open {LASTFM_CREATE_URL} (log in or sign up).")
    print("  2. Enter any application name; leave the other fields empty. Submit.")
    print("  3. Copy the 'API key' (not the 'Shared secret').")
    old = "" if cfg.is_unset(state.config["LASTFM_API_KEY"]) else state.config["LASTFM_API_KEY"]
    prompt = "Last.fm API key (Enter keeps the current one)" if old else "Last.fm API key (Enter to skip)"
    key = ask_hex_key(prompt)
    if not key or key == old:
        print("Last.fm key not changed.")
        return bool(old)

    print("Checking the key with Last.fm...")
    problem = check_api_key(key)
    if problem:
        warn(f"Last.fm did not accept it: {problem}")
        if not confirm("Save it anyway?", default=False):
            return False
    else:
        ok("Last.fm key works.")
    cfg.save_setting("LASTFM_API_KEY", key)
    ok(f"Last.fm key saved ({mask(key)}).")
    warn_if_shadowed("LASTFM_API_KEY")
    return True


def step_source_csv(state: SetupState) -> bool:
    """Point the tool at a playlist CSV export (Exportify, TuneMyMusic...)."""
    current = cfg.get_source_csv(state.config)
    print("\nTip: you can drag the CSV file into this window instead of typing the path.")
    raw = clean_path(ask("Path to the CSV export", default=str(current) if current else ""))
    if not raw:
        return False
    path = cfg.resolve_user_path(raw)
    if not path.is_file():
        warn(f"No file at {path}")
        return False
    cfg.save_setting("SOURCE_CSV", str(path))
    ok(f"CSV export: {path}")
    return True


def step_redirect_uri(state: SetupState) -> bool:
    """Advanced: change the OAuth redirect URI (must match the Spotify app)."""
    print("\nThis must match one of the Redirect URIs in your Spotify app settings exactly.")
    value = ask("Redirect URI", default=state.config["SPOTIPY_REDIRECT_URI"])
    if not value.startswith(("http://", "https://")):
        warn("A redirect URI starts with http:// or https://")
        return False
    cfg.save_setting("SPOTIPY_REDIRECT_URI", value)
    ok(f"Redirect URI: {value}")
    return True


@dataclass(frozen=True)
class Step:
    """One setup item: how to tell it is done, describe it and (re)do it."""

    key: str
    label: str
    optional: bool
    is_done: Callable[[SetupState], bool]
    describe: Callable[[SetupState], str]
    run: Callable[[SetupState], bool]


STEPS: tuple[Step, ...] = (
    Step(
        "yt_login", "YT Music login", False,
        lambda s: s.yt_login,
        lambda s: "saved" if s.yt_login else "not logged in yet",
        step_yt_login,
    ),
    Step(
        "yt_playlist", "YT Music playlist", False,
        lambda s: s.yt_playlist,
        lambda s: describe_playlist(s.config["YTMUSIC_PLAYLIST_ID"]) if s.yt_playlist else "not chosen yet",
        step_yt_playlist,
    ),
    Step(
        "spotify_app", "Spotify app keys", False,
        lambda s: s.spotify_app,
        lambda s: f"Client ID {mask(s.config['SPOTIPY_CLIENT_ID'])}" if s.spotify_app else "not added yet",
        step_spotify_app,
    ),
    Step(
        "spotify_playlist", "Spotify playlist", False,
        lambda s: s.spotify_playlist,
        lambda s: (
            describe_playlist(s.config["SPOTIFY_PLAYLIST_ID"]) if s.spotify_playlist
            else "not chosen yet"
        ),
        step_spotify_playlist,
    ),
    Step(
        "lastfm", "Last.fm key", True,
        lambda s: s.lastfm,
        lambda s: mask(s.config["LASTFM_API_KEY"]) if s.lastfm else "optional - not added",
        step_lastfm,
    ),
)
STEP_BY_KEY = {step.key: step for step in STEPS}

# Settings that only "Change a setting" offers (not part of the wizard)
EXTRA_SETTINGS: tuple[Step, ...] = (
    Step(
        "source_csv", "CSV export file", True,
        lambda s: cfg.get_source_csv(s.config) is not None,
        lambda s: str(cfg.get_source_csv(s.config) or "not set"),
        step_source_csv,
    ),
    Step(
        "redirect_uri", "Spotify redirect URI", True,
        lambda s: True,
        lambda s: s.config["SPOTIPY_REDIRECT_URI"],
        step_redirect_uri,
    ),
)


def offer_legacy_migration() -> None:
    """Offer to copy settings an older pip install left inside site-packages."""
    legacy = cfg.find_legacy_home()
    if legacy is None:
        return
    print()
    warn("Settings from an older version of this tool were found in:")
    say(f"       {legacy}")
    say(f"       New versions keep them in {cfg.PROJECT_ROOT}")
    if not confirm("Copy them there now (login, .env and sync data)?", default=True):
        return
    copied = cfg.migrate_legacy_home(legacy)
    if copied:
        ok(f"Copied: {', '.join(copied)}. The old files were left where they were;")
        ok("you can delete them once everything works.")
    else:
        print("  Nothing needed copying - the new folder already has those files.")


def run_setup_wizard() -> None:
    """Walk through every setup step, skipping the ones already done unless asked."""
    print()
    print("=" * WIDTH)
    print("  Setup wizard - everything is saved for you, no file editing needed")
    print("=" * WIDTH)
    say(f"  Settings are stored in {cfg.ENV_FILE}")
    print("  Press Ctrl+C at any prompt to stop; finished steps stay saved.")
    offer_legacy_migration()

    for number, step in enumerate(STEPS, 1):
        state = read_state()
        print()
        print(f"--- Step {number} of {len(STEPS)}: {step.label} " + "-" * 20)
        if step.is_done(state):
            say(f"  Already set: {step.describe(state)}")
            if not confirm("Change it?", default=False):
                continue
        elif step.optional and not confirm("Set it up now? (optional)", default=False):
            continue
        if (not step.run(read_state()) and not step.optional
                and not confirm("This step is not finished. Continue with the next one?",
                                default=True)):
            break

    state = read_state()
    print()
    if state.complete:
        ok("Setup complete!")
        say(f"  Your settings are saved in {cfg.PROJECT_ROOT}")
        if pending_tracks() is None and confirm("Run your first sync now?", default=True):
            action_sync()
    else:
        missing = [s.label for s in STEPS if not s.optional and not s.is_done(state)]
        warn(f"Still missing: {', '.join(missing)}.")
        warn(f"Run the setup again any time: S in the menu, or {cfg.setup_command()}")


def ensure_ready(needs: tuple[str, ...]) -> bool:
    """Offer to run just the setup steps an action needs; True when all are done."""
    state = read_state()
    missing = [STEP_BY_KEY[k] for k in needs if not STEP_BY_KEY[k].is_done(state)]
    if not missing:
        return True
    print(f"\nThis needs a bit of setup first: {', '.join(s.label for s in missing)}.")
    if not confirm("Set it up now?", default=True):
        return False
    for step in missing:
        state = read_state()
        if step.is_done(state):
            continue
        print(f"\n--- {step.label} " + "-" * 30)
        if not step.run(state):
            return False
    return True


def change_setting() -> None:
    """Let the user redo any single setup item."""
    items = STEPS + EXTRA_SETTINGS
    state = read_state()
    print("\nWhich setting do you want to change?")
    for number, step in enumerate(items, 1):
        say(f"  {number:>2}  {step.label:<22} {shorten(step.describe(state), 38)}")
    print("   B  Back")
    answer = ask("Type a number").upper()
    if not answer.isdigit() or not 1 <= int(answer) <= len(items):
        return
    items[int(answer) - 1].run(read_state())


# -- Actions ------------------------------------------------------------------

def run_command(command: str, **options: object) -> argparse.Namespace:
    """Run a CLI command handler with menu-chosen options; returns its args (results ride on them)."""
    from playlist_sync.cli import dispatch

    args = argparse.Namespace(
        command=command, verbose=False, dry_run=False, csv=None, from_csv=None,
        force=False, output=None, retry_unmatched=False, replace=False, limit=None,
        playlist=None, all=False, source=None, to=None, new_name=None, privacy="private",
    )
    for key, value in options.items():
        setattr(args, key, value)
    try:
        dispatch(args)
    except SystemExit:
        pass  # handlers print their own error before exiting
    return args


def ask_run_mode(what: str) -> bool | None:
    """Return False for a real run, True for a dry run, None to go back."""
    print()
    print(f"  Enter  {what}")
    print("  D      Dry run - do the same work but change nothing on Spotify")
    print("  B      Back")
    while True:
        answer = ask("Choice").upper()
        if answer == "":
            return False
        if answer == "D":
            return True
        if answer == "B":
            return None
        print("  Press Enter, D or B.")


def action_sync() -> None:
    dry_run = ask_run_mode("Sync now: find new YT Music tracks on Spotify and add them")
    if dry_run is None:
        return
    limit = None
    pending = pending_tracks()
    if pending is None or pending > 0:
        if pending is None:
            print("\nFirst sync: every track is searched on Spotify (about 1.5 s each), so a big")
            print("playlist takes a while.")
        else:
            print(f"\nAbout {pending} track(s) from earlier runs still need searching.")
        print("If Spotify pauses the app, what matched so far is added to the playlist and")
        print("the next sync continues where it stopped.")
        raw = ask("How many tracks to match in this run? (Enter = all)")
        if raw.isdigit() and int(raw) > 0:
            limit = int(raw)
    run_command("sync", dry_run=dry_run, limit=limit)


def pending_tracks() -> int | None:
    """Tracks in the last snapshot that never made it into the enriched CSV.

    None means there has been no sync yet. A run with a track limit leaves the
    rest pending; this lets the menu offer the limit again on the next run.
    """
    from playlist_sync.csv_manager import read_enriched_csv

    if not cfg.ENRICHED_CSV.exists():
        return None
    try:
        rows = read_enriched_csv(cfg.ENRICHED_CSV)
        snapshot = json.loads((cfg.SNAPSHOTS_DIR / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    searched = {t.fingerprint for t in rows
                if t.has_spotify_match or t.match_attempted or t.skip_reason}
    if not searched:
        return None  # e.g. only a Last.fm run so far: no Spotify sync yet
    snapshot_fps = {f"ytmusic:{t['trackId']}" if t.get("trackId")
                    else f"ytmusic:{t.get('title', '')}:{t.get('artist', '')}".lower()
                    for t in snapshot.get("tracks", [])}
    return len(snapshot_fps - searched)


def action_preview() -> None:
    print("\nComparing the YT Music playlist with the last snapshot (Spotify is not touched)...")
    run_command("diff")


def action_likes() -> None:
    dry_run = ask_run_mode("Copy new YT Music likes to Spotify 'Liked Songs'")
    if dry_run is None:
        return
    retry = cfg.LIKES_UNMATCHED_CSV.is_file() and confirm(
        "Also search Spotify again for likes that did not match before?", default=False,
    )
    args = run_command("sync-likes", dry_run=dry_run, retry_unmatched=retry)
    held_back = getattr(args, "held_back", 0)
    if held_back and confirm(
        f"Unsave those {held_back} track(s) from Spotify Liked Songs anyway?", default=False,
    ):
        run_command("sync-likes", dry_run=dry_run, force=True)


def action_status() -> None:
    run_command("status")


def action_retry() -> None:
    dry_run = ask_run_mode("Search Spotify again for tracks that did not match")
    if dry_run is not None:
        run_command("retry-unmatched", dry_run=dry_run)


def action_refill() -> None:
    dry_run = ask_run_mode("Add every matched track that is missing from the Spotify playlist")
    if dry_run is None:
        return
    replace = confirm(
        "Empty the Spotify playlist first? Only needed to clean up duplicates.", default=False,
    )
    if replace and not dry_run and not confirm(
        "This removes every track from the Spotify playlist before refilling it. Sure?",
        default=False,
    ):
        return
    run_command("repush", dry_run=dry_run, replace=replace)


def action_lastfm() -> None:
    run_command("lastfm")


def action_classify() -> None:
    force = confirm("Also re-label tracks that already have a genre and mood?", default=False)
    run_command("classify", force=force)


def action_export() -> None:
    default = cfg.ENRICHED_CSV.with_suffix(".json")
    print(f"\nEvery sync already keeps a JSON copy at {default}.")
    print("Tip: you can drag a folder into this window instead of typing its path.")
    raw = clean_path(ask("Save a copy to (file or folder; Enter = the usual place)"))
    output = None
    if raw:
        target = Path(raw).expanduser()
        output = str(target / default.name if target.is_dir() else target)
    run_command("export", output=output)


def action_snapshot() -> None:
    run_command("snapshot")


def action_csv() -> None:
    state = read_state()
    if cfg.get_source_csv(state.config) is None or not cfg.get_source_csv(state.config).is_file():
        print("\nFirst, which CSV export should be used?")
        if not step_source_csv(state):
            return
    print()
    print("  1  Import it as the starting point (replaces the local sync data;")
    print("     Spotify is not touched)")
    print("  2  Sync it to the Spotify playlist")
    print("  B  Back")
    answer = ask("Choice").upper()
    if answer == "1":
        if cfg.ENRICHED_CSV.exists():
            warn("Importing replaces the local sync data, including every Spotify match")
            warn("found so far - the next sync would search and add all tracks again.")
            if not confirm("Import anyway? (the current data is archived, not deleted)",
                           default=False):
                return
        # --force archives the old data only after the CSV was read without error
        run_command("import-csv", force=True)
    elif answer == "2":
        if not ensure_ready(("spotify_app", "spotify_playlist")):
            return
        dry_run = ask_run_mode("Sync the CSV tracks to Spotify")
        if dry_run is not None:
            run_command("sync", from_csv="default", dry_run=dry_run)


def _ask_yt_playlist_link() -> str:
    raw = ask("Paste the YT Music playlist link or ID (Enter to go back)")
    playlist_id = cfg.parse_ytmusic_playlist_id(raw) if raw else ""
    if raw and not playlist_id:
        warn("That does not contain a playlist ID (the part after 'list=').")
    return playlist_id


def action_export_yt() -> None:
    auth_file = read_state().config["YTMUSIC_AUTH_FILE"]
    print("\nSaves a JSON file (for re-importing or other tools) and a CSV next to it.")
    print("Loading your YT Music playlists...")
    try:
        playlists = list_library_playlists(auth_file)
    except API_ERRORS as e:
        warn(f"Could not load your playlists ({e}).")
        playlists = []
    playlists = [pl for pl in playlists if pl["id"] != "LM"]  # offered as L below
    print("What should be exported?")
    choice = pick_playlist(playlists, "title", {
        "L": "Liked Songs",
        "A": "Everything: Liked Songs and all playlists above",
        "P": "Paste a playlist link",
        "B": "Back",
    })
    if choice == "B":
        return
    if choice == "A":
        run_command("export-yt", all=True)
    elif choice == "L":
        run_command("export-yt", playlist="LM")
    elif choice == "P":
        playlist_id = _ask_yt_playlist_link()
        if playlist_id:
            run_command("export-yt", playlist=playlist_id)
    else:
        run_command("export-yt", playlist=choice["id"])


def _export_files() -> list[dict[str, str]]:
    """Exports in data/exports, newest first, labelled with title and size."""
    from playlist_sync.yt_transfer import exports_dir

    folder = exports_dir()
    files = sorted(folder.rglob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True) \
        if folder.is_dir() else []
    labelled = []
    for path in files[:30]:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            title = str((data.get("source") or {}).get("title") or path.stem)
            count = str(data.get("track_count", ""))
        except (OSError, ValueError, AttributeError):
            continue
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        labelled.append({"path": str(path), "title": f"{title}  [{when}]", "count": count,
                         "name": title})
    return labelled


def action_import_yt() -> None:
    state = read_state()
    auth_file = state.config["YTMUSIC_AUTH_FILE"]
    print("\nAdds the tracks of an export file or another YT Music playlist to one of your")
    print("YT Music playlists. Tracks already there are skipped, so it is safe to repeat.")
    print("Tip: to copy into another YT Music account, export first, switch the login")
    print("(C > YT Music login), import here, then switch back to your main login.")

    files = _export_files()
    print("\nWhere do the tracks come from?")
    source_choice = pick_playlist(files, "title", {
        "Y": "Another YT Music playlist (copy it directly)",
        "L": "Your Liked Songs",
        "F": "A different file (type its path)",
        "B": "Back",
    }, default="" if files else "F")
    if source_choice == "B":
        return
    if source_choice == "Y":
        source = _ask_yt_playlist_link()
        name = known_name(source) or "Copied playlist"
    elif source_choice == "L":
        source, name = "LM", "Liked Songs"
    elif source_choice == "F":
        print("Tip: you can drag the file into this window instead of typing the path.")
        source = clean_path(ask("Path to the .json or .csv file"))
        name = Path(source).stem if source else ""
    else:
        source, name = source_choice["path"], source_choice["name"]
    if not source:
        return

    print("\nLoading your YT Music playlists...")
    try:
        own = [pl for pl in list_library_playlists(auth_file) if pl.get("owned") and pl["id"] != "LM"]
    except API_ERRORS as e:
        warn(f"Could not load your playlists ({e}).")
        own = []
    print("Which YT Music playlist should receive the tracks?")
    target = pick_playlist(own, "title", {
        "N": f"Create a new playlist (\"{shorten(name, 30)}\")",
        "B": "Back",
    }, default="N")
    if target == "B":
        return
    options: dict[str, object] = {"source": source}
    if target == "N":
        options["new_name"] = ask("Name for the new playlist", default=name)
        visibility = ask("Who can see it? Enter = only you, U = anyone with the link, P = public")
        options["privacy"] = {"U": "unlisted", "P": "public"}.get(visibility.upper(), "private")
    else:
        options["to"] = target["id"]

    dry_run = ask_run_mode("Add the tracks to YT Music")
    if dry_run is not None:
        run_command("import-yt", dry_run=dry_run, **options)


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    hint: str
    needs: tuple[str, ...]
    run: Callable[[], None]


SECTIONS: tuple[tuple[str, tuple[Action, ...]], ...] = (
    ("SYNC", (
        Action("1", "Sync playlist now", "YT Music -> Spotify",
               ("yt_login", "yt_playlist", "spotify_app", "spotify_playlist"), action_sync),
        Action("2", "Preview changes", "what changed on YT Music",
               ("yt_login", "yt_playlist"), action_preview),
        Action("3", "Sync Liked Songs", "YT likes -> Spotify Liked Songs",
               ("yt_login", "spotify_app"), action_likes),
        Action("4", "Status", "match rate, unmatched, snapshots", (), action_status),
    )),
    ("FIX & MAINTAIN", (
        Action("5", "Retry unmatched tracks", "search Spotify again",
               ("spotify_app", "spotify_playlist"), action_retry),
        Action("6", "Re-fill Spotify playlist", "after recreating or emptying it",
               ("spotify_app", "spotify_playlist"), action_refill),
    )),
    ("EXTRAS", (
        Action("7", "Last.fm tags & play counts", "needs a free Last.fm key",
               ("lastfm",), action_lastfm),
        Action("8", "Genre & mood labels", "from tags, no API calls", (), action_classify),
        Action("9", "Save a JSON copy", "of the synced data, where you choose", (), action_export),
        Action("10", "Use a CSV export", "import or sync from a CSV file", (), action_csv),
        Action("11", "Save a snapshot", "record the YT playlist as it is now",
               ("yt_login", "yt_playlist"), action_snapshot),
    )),
    ("YT MUSIC BACKUP (no Spotify needed)", (
        Action("12", "Export from YT Music", "playlist / Liked Songs -> JSON + CSV",
               ("yt_login",), action_export_yt),
        Action("13", "Import into YT Music", "file or playlist -> YT playlist",
               ("yt_login",), action_import_yt),
    )),
)
ACTIONS = {action.key: action for _, actions in SECTIONS for action in actions}


def next_hint(state: SetupState) -> str:
    if not state.complete:
        return "finish setup - press S (about 5 minutes, no file editing)"
    if pending_tracks() is None:
        return "run your first sync - press 1"
    return ""


def render_menu(state: SetupState) -> None:
    print()
    print("=" * WIDTH)
    print(f"  YT Music -> Spotify Sync   v{__version__}")
    print("=" * WIDTH)
    say(f"  Settings folder: {cfg.PROJECT_ROOT}")
    print()
    print("  SETUP")
    for step in STEPS:
        done = step.is_done(state)
        mark = "[ OK ]" if done else ("[ -- ]" if step.optional else "[ !! ]")
        say(f"    {mark} {step.label:<18} {shorten(step.describe(state), 38)}")
    hint = next_hint(state)
    if hint:
        print()
        print(f"  >> Next: {hint}")
    for title, actions in SECTIONS:
        print()
        print(f"  {title}")
        for action in actions:
            print(f"   {action.key:>2}  {action.label:<28} {action.hint}")
    print()
    print("  SETTINGS")
    print("    S  Setup wizard                 walk through every step")
    print("    C  Change one setting           playlists, keys, login")
    print("    Q  Quit")
    print()


def _run_action(action: Action) -> None:
    """Offer any missing setup the action needs, then run it."""
    if ensure_ready(action.needs):
        action.run()


def _run_safely(func: Callable[[], None]) -> None:
    """Run a menu entry so no error or Ctrl+C can kill the whole menu."""
    try:
        func()
    except Cancelled:
        print("  Cancelled - nothing else was changed.")
    except KeyboardInterrupt:
        print("\n  Stopped.")
    except Exception as e:
        logger.debug("Menu action failed", exc_info=True)
        print(f"\n  Something went wrong: {e}")
        print(f"  Full details are in the log file: {cfg.LOG_FILE}")


def interactive_menu() -> None:
    """Show the menu in a loop until the user quits."""
    setup_logging()
    if not read_state().complete:
        print()
        print("=" * WIDTH)
        print(f"  YT Music -> Spotify Sync   v{__version__}")
        print("=" * WIDTH)
        print("  Welcome! A few things need to be set up before the first sync.")
        print("  You need a YT Music account and a Spotify Premium account.")
        print("  The setup wizard asks for everything and saves it for you.")
        say(f"  Settings folder: {cfg.PROJECT_ROOT}")
        try:
            start = confirm("Start the setup wizard now?", default=True)
        except Cancelled:
            return
        if start:
            _run_safely(run_setup_wizard)

    while True:
        state = read_state()
        render_menu(state)
        try:
            choice = input("Choose an option: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice in ("Q", "0", "QUIT", "EXIT"):
            break
        if choice == "S":
            _run_safely(run_setup_wizard)
        elif choice == "C":
            _run_safely(change_setting)
        elif choice in ACTIONS:
            action = ACTIONS[choice]
            print(f"\n--- {action.label} " + "-" * 30)
            _run_safely(lambda a=action: _run_action(a))
        elif choice:
            print(f"  '{choice}' is not an option - type one of the numbers or letters shown.")
            continue
        else:
            continue
        pause()
    print("Bye!")
