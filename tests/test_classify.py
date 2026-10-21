"""
Offline tests for genre and mood classification from Last.fm tag pools.
Author: David Malko
Date: 2026-10-18
Version: 0.7.2

Tag pools are copied from real Last.fm artist.getInfo answers (top 5 tags,
strongest first) for artists in a real 3,600-track library.
"""

from __future__ import annotations

import pytest

from playlist_sync.enricher import classify_all_tracks, classify_track
from playlist_sync.models import Track

# (artist, artist_tags, track-level lastfm_tags, expected primary_genre)
CASES: list[tuple[str, str, str, str]] = [
    # Post-punk is rock, not punk
    ("SadSvit", "post-punk, ukrainian, ukraine, coldwave, new wave", "", "rock"),
    ("Кино", "russian rock, post-punk, rock, russian, 80s", "", "rock"),
    ("ДК Енергетик", "post-punk, ukrainian, new wave, ukraine, gothic rock", "", "rock"),
    ("AP$ENT", "belarusian, post-punk, belarussian, belarus", "", "rock"),
    ("Vundabar", "indie rock, garage rock, surf rock, rock, boston",
     "indie rock, Post-punk revival, indie, rock, garage rock revival", "rock"),
    # A stray punk / hardcore tag below an electronic or pop one
    ("Thylacine", "electronic, french, punk, hardcore, australian", "", "electronic"),
    ("Culture Shock", "drum and bass, liquid funk, punk, dnb, anarcho-punk", "", "electronic"),
    ("KATO", "house, electronic, danish, hardcore, dance", "", "electronic"),
    ("SMS", "pop, dance, happy hardcore, female vocals, bosnian", "", "electronic"),
    ("girl in red", "dream pop, indie pop, indie, bedroom pop, lo-fi",
     "dream pop, indie pop, jangle pop, bedroom pop, punk", "pop"),
    ("Tachibana", "arranger, hardcore, touhou, lyricist, nicovideo", "", ""),
    # Real punk stays punk
    ("Green Day", "punk rock, rock, punk, pop punk, alternative", "", "punk"),
    ("Король и Шут", "punk rock, russian, russian rock, punk, rock", "", "punk"),
    ("Finnish band", "punk, ukrainian, hardcore punk, finnish punk, finnish", "", "punk"),
    ("Post-hardcore band", "post-hardcore, trancecore, nintendocore, hardcore, screamo", "", "punk"),
    ("Rock first", "rock, punk, alternative", "", "punk"),
    # Unchanged behaviour
    ("Daughter", "indie, indie folk, female vocalists, acoustic, british", "", "folk"),
    ("sanah", "polish, indie pop, pop, indie, female vocalists", "", "pop"),
    ("Low Roar", "ambient, indie, folk, icelandic, alternative", "", "folk"),
    ("Armin van Buuren", "trance, progressive trance, electronic, vocal trance, dance", "", "electronic"),
    ("Linkin Park", "nu metal, rock, alternative rock, alternative, metal", "", "metal"),
    ("Explosions in the Sky", "post-rock, instrumental, ambient, rock, atmospheric", "", "ambient"),
]


def _track(artist: str, artist_tags: str, lastfm_tags: str = "", album: str = "Album",
           title: str = "Song") -> Track:
    return Track(title=title, artist=artist, album=album, platform="ytmusic",
                 artist_tags=artist_tags, lastfm_tags=lastfm_tags)


@pytest.mark.parametrize("artist,artist_tags,track_tags,genre", CASES, ids=[c[0] for c in CASES])
def test_primary_genre(artist: str, artist_tags: str, track_tags: str, genre: str) -> None:
    assert classify_track(_track(artist, artist_tags, track_tags)).primary_genre == genre


def test_post_rock_still_is_not_soundtrack() -> None:
    assert classify_track(_track("Mono", "post-rock")).primary_genre == "rock"


# ── mood ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("artist,artist_tags,mood", [
    ("Daughter", "indie, indie folk, female vocalists, acoustic, british", "acoustic"),
    ("Pomme", "french, chanson, folk, pop, french pop", "acoustic"),
    ("Agnes Obel", "folk, singer-songwriter, female vocalists, danish, indie", "acoustic"),
    ("Of Monsters and Men", "indie, folk, indie pop, icelandic, iceland", "acoustic"),
    ("Low Roar", "ambient, indie, folk, icelandic, alternative", "ambient, acoustic"),
    ("Beach House", "dream pop, indie, shoegaze, indie pop, female vocalists", "dreamy"),
    ("Sleep band", "peaceful, lullaby", "chill"),
    ("Red House Painters", "sadcore, slowcore, indie", "sad"),
    ("MrSuicideSheep", "chillout, uplifting, house, ambient, amazing", "chill, energetic, ambient"),
])
def test_mood(artist: str, artist_tags: str, mood: str) -> None:
    assert classify_track(_track(artist, artist_tags)).mood == mood


@pytest.mark.parametrize("artist_tags,mood", [
    ("folk metal, viking metal, pagan metal", ""),          # "folk" counts only as a whole tag
    ("folk punk, punk, acoustic punk", "acoustic"),         # "acoustic" counts as a word too
    ("electroacoustic, experimental", ""),                  # word boundary, not substring
    ("polish, indie pop, pop, indie, female vocalists", ""),  # no mood signal at all
])
def test_mood_keywords_respect_word_boundaries(artist_tags: str, mood: str) -> None:
    assert classify_track(_track("X", artist_tags)).mood == mood


# ── channel uploads ──────────────────────────────────────────────────


def test_channel_upload_tags_of_a_namesake_are_ignored() -> None:
    arrakis = _track("Arrakis", "trance, acid trance, power metal, psychedelic rock, acid",
                     album="", title="3.Lorne Balfe - Beyond")
    ea = _track("Electronic Arts", "ambient, chillout, techno, electronic, jazz",
                album="", title='"Still Alive" - The Theme Song from Mirror\'s Edge')

    classify_all_tracks([arrakis, ea])

    assert (arrakis.primary_genre, arrakis.mood) == ("", "")
    assert (ea.primary_genre, ea.mood) == ("", "")


def test_channel_upload_keeps_track_level_tags() -> None:
    upload = _track("Some Channel", "power metal", lastfm_tags="trance",
                    album="", title="Someone - Something")
    classify_all_tracks([upload])
    assert upload.primary_genre == "electronic"


def test_channel_upload_by_an_artist_with_albums_is_classified() -> None:
    tags = "polish, indie pop, pop, indie, female vocalists"
    album_track = _track("sanah", tags)
    upload = _track("sanah, ENKLAWA", tags, album="", title="ITEPE ITEDE")

    classify_all_tracks([album_track, upload])

    assert upload.primary_genre == "pop"


def test_channel_upload_named_in_its_title_is_classified() -> None:
    upload = _track("Guru Josh", "house, electronic, techno, dance, 90s",
                    album="", title="Guru Josh - Infinity 2012 (DJ Antoine Remix)")
    classify_all_tracks([upload])
    assert upload.primary_genre == "electronic"
