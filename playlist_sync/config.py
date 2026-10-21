"""
Configuration and environment variable loading.
Author: David
Date: 2026-03-22
Version: 0.8.0
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from dotenv import dotenv_values, set_key

# Directory that contains the package; in a git checkout this is the repo root,
# in a pip install it is site-packages (which must never receive user files).
SOURCE_ROOT = Path(__file__).resolve().parent.parent
HOME_ENV_VAR = "PLAYLIST_SYNC_HOME"
APP_DIR_NAME = "ytmusic-to-spotify-sync"


def is_source_checkout(source_root: Path | None = None) -> bool:
    """True when running from a cloned repo rather than an installed package."""
    root = source_root or SOURCE_ROOT
    return (root / "playlist_sync.py").is_file() and (root / "pyproject.toml").is_file()


def default_user_home(
    environ: Mapping[str, str],
    platform: str = sys.platform,
    user_home: Path | None = None,
) -> Path:
    """Per-user settings folder for installed copies (%APPDATA%, ~/Library, ~/.config)."""
    base_home = user_home or Path.home()
    if platform.startswith("win"):
        appdata = environ.get("APPDATA", "").strip()
        return (Path(appdata) if appdata else base_home / "AppData" / "Roaming") / APP_DIR_NAME
    if platform == "darwin":
        return base_home / "Library" / "Application Support" / APP_DIR_NAME
    xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(xdg) if xdg else base_home / ".config") / APP_DIR_NAME


def resolve_home(
    source_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str = sys.platform,
    user_home: Path | None = None,
) -> Path:
    """Pick the folder that holds .env, browser.json, data/ and the log file.

    A git checkout keeps everything next to the code, as it always has. An
    installed copy uses one fixed per-user folder: the current directory would
    scatter secrets (a terminal opened in ~ would put .env and the YT Music
    cookies there) and lose the settings whenever the tool starts elsewhere.
    PLAYLIST_SYNC_HOME overrides both, which suits cron/Jenkins jobs.
    """
    env = os.environ if environ is None else environ
    override = env.get(HOME_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    root = source_root or SOURCE_ROOT
    if is_source_checkout(root):
        return root
    return default_user_home(env, platform, user_home)


PROJECT_ROOT = resolve_home()
ENV_FILE = PROJECT_ROOT / ".env"
ENV_TEMPLATE = SOURCE_ROOT / ".env.example"
# Not ".cache": that name is often an existing cache directory (e.g. ~/.cache)
SPOTIFY_TOKEN_CACHE = PROJECT_ROOT / ".spotify_token_cache"
LEGACY_TOKEN_CACHE = PROJECT_ROOT / ".cache"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"

# Variables set by the real environment before .env was read. They win over
# .env, so the setup wizard warns when one shadows a value it saves.
_PROCESS_ENV_KEYS = frozenset(os.environ)

DATA_DIR = PROJECT_ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
LIKES_SNAPSHOTS_DIR = DATA_DIR / "snapshots" / "likes"
DEBUG_DIR = DATA_DIR / "debug"
ENRICHED_CSV = DATA_DIR / "playlist_enriched.csv"
UNMATCHED_CSV = DATA_DIR / "unmatched.csv"
SKIPPED_CSV = DATA_DIR / "skipped.csv"
LIKES_ENRICHED_CSV = DATA_DIR / "likes_enriched.csv"
LIKES_UNMATCHED_CSV = DATA_DIR / "likes_unmatched.csv"
LOG_FILE = PROJECT_ROOT / "playlist_sync.log"

# Enriched CSV columns (original + enrichment)
ENRICHED_COLUMNS = [
    "title", "artist", "album", "isrc", "platform", "trackId",
    "duration", "addedDate", "addedBy", "url",
    "spotify_uri", "spotify_url", "spotify_duration_ms",
    "isrc_enriched", "explicit", "album_release_date",
    "danceability", "energy", "valence", "tempo",
    "key", "mode", "loudness", "speechiness",
    "acousticness", "instrumentalness", "liveness", "time_signature",
    "audio_features_fetched",
    "popularity", "artist_genres", "album_type", "track_number",
    "lastfm_playcount", "lastfm_listeners", "lastfm_tags",
    "artist_tags", "tag_source", "lastfm_attempted", "lastfm_track_attempted",
    "primary_genre", "mood",
    "spotify_metadata_attempted", "spotify_genres_attempted",
    "skip_reason", "match_attempted",
    "match_method", "match_confidence", "first_synced", "last_synced",
]

# Duration tolerance for track matching (seconds)
DURATION_TOLERANCE_SEC = 5

# Spotify API batch limits
SPOTIFY_PLAYLIST_BATCH = 100
SPOTIFY_SEARCH_LIMIT = 10

# Rate limiting (seconds between Spotify searches)
# Spotify Dev Mode has strict per-minute and daily quotas.
# For large playlists (1000+ tracks) 0.5s is too aggressive and triggers
# 24-hour bans. 1.5s (~40 req/min) is a safe conservative rate.
SEARCH_DELAY_SEC = 1.5

# Match cache file for resuming after rate limits
MATCH_CACHE = DATA_DIR / "match_cache.json"


# Template values shipped in .env.example, e.g. "your_playlist_id_here".
_PLACEHOLDER_RE = re.compile(r"^your_[a-z0-9_]*_here$", re.IGNORECASE)

# YT Music playlist IDs: PL..., OLAK5uy_..., RDCLAK..., LM, VL-prefixed browse IDs.
_YTM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,}$")
_YTM_LIST_PARAM_RE = re.compile(r"(?:^|[?&])list=([A-Za-z0-9_-]+)")
_YTM_BROWSE_RE = re.compile(r"/browse/VL([A-Za-z0-9_-]+)")

# Spotify IDs are 22 base62 characters; links and URIs wrap them.
_SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
_SPOTIFY_LINK_RE = re.compile(r"playlist[/:]([A-Za-z0-9]{22})")

# Values that need no quoting in .env; anything else is written single-quoted.
_SAFE_UNQUOTED_RE = re.compile(r"^[A-Za-z0-9_./:@+=-]*$")


def is_unset(value: str | None) -> bool:
    """True for empty values and untouched 'your_..._here' template placeholders."""
    text = (value or "").strip()
    return not text or bool(_PLACEHOLDER_RE.match(text))


def _strip_paste(raw: str) -> str:
    """Trim whitespace and the quotes people often copy along with a value."""
    return raw.strip().strip("'\"").strip()


def parse_ytmusic_playlist_id(raw: str) -> str:
    """Return the playlist ID from a pasted ID or YT / YT Music link, or '' if invalid."""
    text = _strip_paste(raw)
    match = _YTM_LIST_PARAM_RE.search(text) or _YTM_BROWSE_RE.search(text)
    if match:
        text = match.group(1)
    if is_unset(text) or not _YTM_ID_RE.match(text):
        return ""
    return text


def parse_spotify_playlist_id(raw: str) -> str:
    """Return the playlist ID from a pasted ID, open.spotify.com link or URI, or ''."""
    text = _strip_paste(raw)
    match = _SPOTIFY_LINK_RE.search(text)
    if match:
        return match.group(1)
    return text if _SPOTIFY_ID_RE.match(text) else ""


def resolve_user_path(value: str) -> Path:
    """Resolve a relative path from .env against the settings folder, not the CWD."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config() -> dict[str, str]:
    """Load .env and return config dict.

    .env is re-read on every call, so edits made while the menu is open show up;
    real environment variables still win over it. Playlist links pasted into
    .env are reduced to their IDs, so both forms work.
    """
    if ENV_FILE.exists():
        for key, value in dotenv_values(ENV_FILE).items():
            if value is not None and key not in _PROCESS_ENV_KEYS:
                os.environ[key] = value

    yt_playlist = os.getenv("YTMUSIC_PLAYLIST_ID", "").strip()
    sp_playlist = os.getenv("SPOTIFY_PLAYLIST_ID", "").strip()
    config = {
        "SPOTIPY_CLIENT_ID": os.getenv("SPOTIPY_CLIENT_ID", "").strip(),
        "SPOTIPY_CLIENT_SECRET": os.getenv("SPOTIPY_CLIENT_SECRET", "").strip(),
        "SPOTIPY_REDIRECT_URI": os.getenv("SPOTIPY_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI,
        "SPOTIFY_PLAYLIST_ID": parse_spotify_playlist_id(sp_playlist) or sp_playlist,
        "YTMUSIC_PLAYLIST_ID": parse_ytmusic_playlist_id(yt_playlist) or yt_playlist,
        "YTMUSIC_AUTH_FILE": str(resolve_user_path(
            os.getenv("YTMUSIC_AUTH_FILE", "").strip() or "browser.json"
        )),
        "SOURCE_CSV": os.getenv("SOURCE_CSV", "").strip(),
        "LASTFM_API_KEY": os.getenv("LASTFM_API_KEY", "").strip(),
    }
    return config


def missing_settings(config: Mapping[str, str], keys: Iterable[str]) -> list[str]:
    """Return the keys that are empty or still hold a template placeholder."""
    return [k for k in keys if is_unset(config.get(k))]


def shadowed_by_environment(key: str) -> bool:
    """True when a real environment variable overrides whatever .env says."""
    return key in _PROCESS_ENV_KEYS


def cli_command() -> str:
    """How the user starts this tool: the repo script for a clone, else the console script."""
    return "python playlist_sync.py" if is_source_checkout() else "ytmusic-sync"


def setup_command() -> str:
    """The command that starts the setup wizard for this kind of install."""
    return f"{cli_command()} setup"


def print_setup_hint(what: str) -> None:
    """Tell the user which command fixes a missing setting, for this install and folder."""
    print(f"Run the setup wizard to {what}: {setup_command()}")
    if os.environ.get(HOME_ENV_VAR, "").strip():
        print(f"(keep {HOME_ENV_VAR}={PROJECT_ROOT} set so it fills this settings folder)")


def make_private(path: Path) -> None:
    """Restrict a file holding secrets to its owner (POSIX only; Windows uses ACLs)."""
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # e.g. a filesystem without POSIX permissions; not worth failing over


def ensure_home() -> Path:
    """Create the settings folder (owner-only on POSIX) and return it."""
    if not PROJECT_ROOT.is_dir():
        PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            try:
                os.chmod(PROJECT_ROOT, 0o700)
            except OSError:
                pass
    return PROJECT_ROOT


# Files that 0.4-0.7.1 pip installs kept inside site-packages (their old settings folder)
_LEGACY_MARKERS = (".env", "browser.json", "data/playlist_enriched.csv", "data/snapshots/latest.json")


def find_legacy_home() -> Path | None:
    """Return site-packages when an older pip install left its settings there."""
    if is_source_checkout() or PROJECT_ROOT == SOURCE_ROOT:
        return None
    if any((SOURCE_ROOT / marker).exists() for marker in _LEGACY_MARKERS):
        return SOURCE_ROOT
    return None


def migrate_legacy_home(source: Path) -> list[str]:
    """Copy an old install's .env, browser.json and data/ into the settings folder.

    Only items the new folder does not have yet are copied, and data/ only when
    it really is this tool's (site-packages may hold other packages' data dirs).
    The originals are left in place. Returns the names that were copied.
    """
    ensure_home()
    copied: list[str] = []
    for name in (".env", "browser.json", "data"):
        src, dst = source / name, PROJECT_ROOT / name
        if not src.exists():
            continue
        if name == "data":
            if not ((src / "playlist_enriched.csv").exists() or (src / "snapshots").is_dir()):
                continue
            # Merge file by file: any command may already have created an empty
            # data/ here, and existing files must never be overwritten
            before = sum(1 for f in dst.rglob("*") if f.is_file()) if dst.exists() else 0
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            copy_function=lambda s, d: None if os.path.exists(d) else shutil.copy2(s, d))
            if sum(1 for f in dst.rglob("*") if f.is_file()) > before:
                copied.append(name)
            continue
        if dst.exists():
            continue
        else:
            shutil.copy2(src, dst)
            make_private(dst)
        copied.append(name)
    return copied


# Header for a new .env when .env.example is not available (pip installs don't ship it)
_ENV_HEADER = """\
# Settings for ytmusic-to-spotify-sync, written by the setup wizard.
# Change them with the wizard (menu S / C, or the 'setup' command) - no need
# to edit this file by hand. Keep it private: it holds your API keys.
"""


def _create_env_file(path: Path) -> None:
    """Create .env from the commented template when available, else with a short header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if ENV_TEMPLATE.is_file():
        shutil.copyfile(ENV_TEMPLATE, path)
    else:
        path.write_text(_ENV_HEADER, encoding="utf-8")


def save_setting(key: str, value: str, env_file: Path | None = None) -> Path:
    """Write KEY=value into .env (creating it if needed) and apply it to this process.

    Comments and every other line in .env are preserved, and the file is kept
    owner-only on POSIX on every write (set_key keeps whatever mode it had).
    os.environ is updated too, so the rest of this session sees the new value.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(f"{key} must be a single line")
    path = env_file or ENV_FILE
    if not path.exists():
        if path == ENV_FILE:
            ensure_home()
        _create_env_file(path)
    quote_mode = "never" if _SAFE_UNQUOTED_RE.match(value) else "always"
    set_key(path, key, value, quote_mode=quote_mode)
    make_private(path)
    os.environ[key] = value
    return path


def get_source_csv(config: dict[str, str]) -> Path | None:
    """Get the source CSV path from config, or None if not set."""
    csv_path = config.get("SOURCE_CSV", "")
    if not is_unset(csv_path):
        return resolve_user_path(csv_path)
    return None


def require_spotify_config(config: dict[str, str], need_playlist: bool = True) -> None:
    """Validate that Spotify credentials (and by default the target playlist) are filled in."""
    keys = ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"]
    if need_playlist:
        keys.append("SPOTIFY_PLAYLIST_ID")
    missing = missing_settings(config, keys)
    if missing:
        print(f"Error: these settings are not filled in yet: {', '.join(missing)}")
        print_setup_hint("fill them in")
        sys.exit(1)


def require_ytmusic_config(config: dict[str, str]) -> None:
    """Validate that YT Music auth file exists."""
    auth_file = Path(config["YTMUSIC_AUTH_FILE"])
    if not auth_file.exists():
        print(f"Error: not logged in to YT Music yet (no auth file at {auth_file})")
        print_setup_hint("log in")
        sys.exit(1)


def require_ytmusic_playlist(config: dict[str, str]) -> None:
    """Validate that a real YT Music source playlist ID is configured."""
    value = config.get("YTMUSIC_PLAYLIST_ID", "")
    if is_unset(value):
        print("Error: no YT Music playlist chosen yet (YTMUSIC_PLAYLIST_ID is empty "
              "or still the template placeholder).")
        print_setup_hint("pick one from your library")
        sys.exit(1)
    if not parse_ytmusic_playlist_id(value):
        print(f"Error: YTMUSIC_PLAYLIST_ID is not a playlist ID or link: {value!r}")
        print("Expected something like https://music.youtube.com/playlist?list=PL...")
        print_setup_hint("pick it from your library")
        sys.exit(1)


def ensure_dirs() -> None:
    """Create data directories if they don't exist."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    LIKES_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
