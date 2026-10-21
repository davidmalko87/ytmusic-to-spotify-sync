"""
Offline checks that README claims match the code and libraries they describe.
Author: David Malko
Date: 2026-10-21
Version: 0.8.0

README.md is also the PyPI long description, so its links must work on pypi.org too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from playlist_sync import matcher, spotify_client, ytmusic_client
from playlist_sync.config import ENRICHED_COLUMNS
from playlist_sync.csv_manager import read_source_csv

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
REPO_BLOB = "https://github.com/davidmalko87/ytmusic-to-spotify-sync/blob/master/"
LINK_RE = re.compile(r"\]\(([^)\s]+)\)|(?:src|href)=\"([^\"]+)\"")

# A playlist item exactly as ytmusicapi 1.12.3 builds it (parsers/playlists.py,
# parse_playlist_item): there is no ISRC field, and get_liked_songs() returns the same shape
YT_ITEM = {
    "videoId": "abcdefghijk", "title": "Song", "artists": [{"name": "Artist", "id": "UC1"}],
    "album": {"name": "Album", "id": "MPRE1"}, "likeStatus": "LIKE", "inLibrary": None,
    "pinnedToListenAgain": None, "thumbnails": [], "isAvailable": True, "isExplicit": False,
    "videoType": "MUSIC_VIDEO_TYPE_ATV", "views": None, "communityVoteStatus": None,
    "duration": "3:20", "duration_seconds": 200, "setVideoId": "S1",
}


def _section(heading: str) -> str:
    """README text from `heading` up to the next heading of any level."""
    start = README.index(heading) + len(heading)
    nxt = re.search(r"^#{1,6} ", README[start:], re.MULTILINE)
    return README[start:start + nxt.start()] if nxt else README[start:]


def _slug(heading: str) -> str:
    """GitHub's heading anchor (PyPI's renderer builds the same id, prefixed)."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


class FakeYTMusic:
    def get_playlist(self, playlist_id: str, limit: int | None = None) -> dict:
        return {"tracks": [dict(YT_ITEM)]}

    def get_liked_songs(self, limit: int = 100) -> dict:
        return {"tracks": [dict(YT_ITEM)]}


class RecordingSpotify:
    """Records every search query and finds nothing, so all three passes run."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, q: str, type: str, limit: int) -> dict:
        self.queries.append(q)
        return {"tracks": {"items": []}}


@pytest.fixture
def no_search_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify_client, "SEARCH_DELAY_SEC", 0)


# -- A-19: column table ------------------------------------------------------

def test_readme_column_table_lists_every_enriched_column() -> None:
    section = _section("## Output: enriched CSV")
    documented: set[str] = set()
    for line in section.splitlines():
        if line.startswith("| "):  # table rows; bold names are `**`name`**`
            documented |= set(re.findall(r"`([A-Za-z_]+)`", line.split("|")[1]))
    assert documented == set(ENRICHED_COLUMNS)
    assert f"**{len(ENRICHED_COLUMNS)} columns**" in section


# -- A-21: links on pypi.org -------------------------------------------------

def test_readme_has_no_relative_links() -> None:
    """PyPI resolves a relative link against pypi.org/project/..., where it is a 404."""
    targets = [a or b for a, b in LINK_RE.findall(README)]
    relative = [t for t in targets if not t.startswith(("https://", "http://", "mailto:", "#"))]
    assert relative == []


def test_readme_repo_links_point_at_existing_files() -> None:
    targets = [a or b for a, b in LINK_RE.findall(README)]
    for target in targets:
        if target.startswith(REPO_BLOB):
            assert (ROOT / target[len(REPO_BLOB):].split("#")[0]).exists(), target


def test_readme_anchor_links_match_a_heading() -> None:
    outside_code = re.sub(r"^```.*?^```", "", README, flags=re.MULTILINE | re.DOTALL)
    slugs = {_slug(h) for h in re.findall(r"^#{1,6} (.+)$", outside_code, re.MULTILINE)}
    anchors = [a[1:] for a, _ in LINK_RE.findall(README) if a.startswith("#")]
    assert anchors and [a for a in anchors if a not in slugs] == []


# -- B-13: where the ISRC pass can run ----------------------------------------

def test_ytmusicapi_returns_no_isrc() -> None:
    """Canary: if ytmusicapi ever exposes ISRCs, README's matching section needs an update."""
    import ytmusicapi

    package = Path(ytmusicapi.__file__).parent
    hits = [p.name for p in package.rglob("*.py")
            if "isrc" in p.read_text(encoding="utf-8", errors="ignore").lower()]
    assert hits == []


def test_live_api_tracks_skip_the_isrc_pass(
    monkeypatch: pytest.MonkeyPatch, no_search_delay: None,
) -> None:
    monkeypatch.setattr(ytmusic_client, "get_ytmusic_client", lambda auth: FakeYTMusic())
    tracks = (ytmusic_client.fetch_playlist_tracks("browser.json", "PL1")
              + ytmusic_client.fetch_liked_tracks("browser.json"))
    assert [t.isrc for t in tracks] == ["", ""]
    sp = RecordingSpotify()
    matcher.match_track(sp, tracks[0])  # type: ignore[arg-type]
    assert sp.queries and not any(q.startswith("isrc:") for q in sp.queries)


def test_csv_export_with_isrc_column_runs_the_isrc_pass_first(
    home: Path, no_search_delay: None,
) -> None:
    source = home / "export.csv"
    source.write_text("title,artist,album,isrc\nSong,Artist,Album,USRC17607839\n",
                      encoding="utf-8")
    track = read_source_csv(source)[0]
    sp = RecordingSpotify()
    matcher.match_track(sp, track)  # type: ignore[arg-type]
    assert sp.queries[0] == "isrc:USRC17607839"


def test_readme_says_the_isrc_pass_needs_a_csv_isrc_column() -> None:
    section = _section("### Track matching strategy")
    step = section[section.index("1. **ISRC"):section.index("\n2. ")]
    assert "`isrc`" in step and "CSV" in step
