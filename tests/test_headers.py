"""
Offline tests for YT Music header pastes (cURL, Chrome two-line, raw) and their diagnostics.
Author: David Malko
Date: 2026-10-18
Version: 0.8.0

All header values below are synthetic; no real session cookies.
"""

from __future__ import annotations

import json
from pathlib import Path

from playlist_sync.ytmusic_client import (
    _parse_curl,
    _read_pasted_block,
    explain_header_problem,
    headers_from_paste,
    save_browser_auth,
)

COOKIE = "SID=fake1; HSID=fake2; SAPISID=fakeSap/xyz; __Secure-3PAPISID=fake3; PREF=f6=4&tz=Europe.Kiev"

CURL_BASH = f"""curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\
  -H 'accept: */*' \\
  -H 'authorization: SAPISIDHASH 123_abc SAPISID1PHASH 123_abc' \\
  -H 'content-type: application/json' \\
  -b '{COOKIE}' \\
  -H 'origin: https://music.youtube.com' \\
  -H 'x-goog-authuser: 0' \\
  -H 'x-origin: https://music.youtube.com' \\
  --data-raw '{{"context":{{"client":{{"clientName":"WEB_REMIX"}}}}}}'"""

CURL_CMD = (
    'curl ^"https://music.youtube.com/youtubei/v1/browse?prettyPrint=false^" ^\n'
    '  -H ^"accept: */*^" ^\n'
    '  -b ^"SID=fake1; PREF=f6=4^&tz=Europe.Kiev^" ^\n'
    '  -H ^"x-goog-authuser: 0^" ^\n'
    '  --data-raw ^"^{^\\^"context^\\^":1^}^"'
)

CHROME_TWO_LINE = [
    "accept", "*/*",
    "authorization", "SAPISIDHASH 123_abc",
    "cookie", COOKIE,
    "x-goog-authuser", "0",
    "x-origin", "https://music.youtube.com",
]


def _as_dict(headers_raw: str) -> dict[str, str]:
    pairs = (line.split(": ", 1) for line in headers_raw.splitlines() if ": " in line)
    return {k: v for k, v in pairs}


def test_curl_bash_headers_and_cookie_flag() -> None:
    headers = _parse_curl(CURL_BASH)
    assert headers is not None
    assert headers["cookie"] == COOKIE
    assert headers["x-goog-authuser"] == "0"
    assert headers["authorization"].startswith("SAPISIDHASH")
    assert "--data-raw" not in headers


def test_curl_cmd_flavour_unescapes_carets() -> None:
    headers = _parse_curl(CURL_CMD)
    assert headers is not None
    assert headers["cookie"] == "SID=fake1; PREF=f6=4&tz=Europe.Kiev"
    assert headers["x-goog-authuser"] == "0"


def test_non_curl_text_is_not_parsed_as_curl() -> None:
    assert _parse_curl("cookie: a=b") is None


def test_headers_from_paste_handles_all_formats() -> None:
    for lines in (CURL_BASH.splitlines(), CHROME_TWO_LINE,
                  [f"cookie: {COOKIE}", "x-goog-authuser: 0", "authorization: SAPISIDHASH 1_a"]):
        parsed = _as_dict(headers_from_paste(lines))
        assert parsed["cookie"] == COOKIE
        assert parsed["x-goog-authuser"] == "0"
        assert explain_header_problem(lines, headers_from_paste(lines)) is None


def test_single_line_paste_is_explained() -> None:
    lines = ["authorization SAPISIDHASH 1_a content-type application/json x-goog-authuser 0 " * 5]
    problem = explain_header_problem(lines, headers_from_paste(lines))
    assert problem is not None and "single line" in problem


def test_paste_starting_mid_cookie_is_explained() -> None:
    lines = ["PRIVACY_METADATA=CgJV; PREF=f6=4; SID=fake", "priority", "u=1, i",
             "x-goog-authuser", "0"]
    problem = explain_header_problem(lines, headers_from_paste(lines))
    assert problem is not None and "middle of the cookie" in problem


def test_missing_cookie_is_explained() -> None:
    lines = ["authorization", "SAPISIDHASH 1_a", "x-goog-authuser", "0"]
    problem = explain_header_problem(lines, headers_from_paste(lines))
    assert problem is not None and "No 'cookie' header" in problem


def test_save_browser_auth_writes_json(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "browser.json"
    assert save_browser_auth(CURL_BASH.splitlines(), str(target)) is None
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["cookie"] == COOKIE
    assert saved["x-goog-authuser"] == "0"


def test_save_browser_auth_returns_reason_instead_of_raising(tmp_path: Path) -> None:
    target = tmp_path / "browser.json"
    problem = save_browser_auth(["accept: */*", "x-goog-authuser: 0"], str(target))
    assert problem is not None
    assert not target.exists()


def test_missing_authorization_is_explained() -> None:
    lines = [f"cookie: {COOKIE}", "x-goog-authuser: 0"]
    problem = explain_header_problem(lines, headers_from_paste(lines))
    assert problem is not None and "'authorization' header" in problem


def test_enter_before_pasting_means_go_back(monkeypatch) -> None:
    answers = iter(["", "never read"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert _read_pasted_block() == []
