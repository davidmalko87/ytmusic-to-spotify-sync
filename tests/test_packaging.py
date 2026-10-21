"""
Offline checks for packaging metadata, the single version source and the CLI version flag.
Author: David Malko
Date: 2026-10-21
Version: 0.8.0

The wheel itself is built and installed by the CI 'package' job; these tests
catch the same mistakes earlier, on every matrix leg, without building anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement

import playlist_sync
from playlist_sync import cli
from playlist_sync import config as cfg

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def pyproject() -> dict[str, Any]:
    """pyproject.toml parsed with tomllib (stdlib from 3.11; the 3.10 leg skips these)."""
    tomllib = pytest.importorskip("tomllib")
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# -- A-14: one version source -------------------------------------------------

def test_version_comes_only_from_package_init(pyproject: dict[str, Any]) -> None:
    project = pyproject["project"]
    assert "version" not in project, "static version in pyproject.toml shadows __init__.py"
    assert "version" in project["dynamic"]
    attr = pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    # "playlist_sync.__version__" would make setuptools execute the repo-root
    # playlist_sync.py script (it is found before playlist_sync/__init__.py)
    assert attr == "playlist_sync.__init__.__version__"


def test_setuptools_resolves_the_configured_version(pyproject: dict[str, Any]) -> None:
    expand = pytest.importorskip("setuptools.config.expand")
    attr = pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    assert expand.read_attr(attr, {}, str(ROOT)) == playlist_sync.__version__


# -- ME-M-7: SPDX license string needs setuptools >= 77 -----------------------

def test_build_backend_floor_supports_spdx_license(pyproject: dict[str, Any]) -> None:
    project = pyproject["project"]
    assert project["license"] == "MIT"
    assert "LICENSE" in project["license-files"]
    # setuptools >= 77 rejects a License :: classifier next to an SPDX expression
    assert not any(c.startswith("License ::") for c in project["classifiers"])
    reqs = [Requirement(r) for r in pyproject["build-system"]["requires"]]
    setuptools_req = next(r for r in reqs if r.name == "setuptools")
    assert not setuptools_req.specifier.contains("76.1.0")
    assert setuptools_req.specifier.contains("77.0.3")


# -- A-25: keywords cover the GitHub topics and main features ------------------

def test_keywords_cover_topics_and_features(pyproject: dict[str, Any]) -> None:
    keywords = pyproject["project"]["keywords"]
    assert len(keywords) == len(set(keywords))
    assert all(k == k.lower() for k in keywords)
    expected = {"cli-tool", "playlist-migration", "music-sync", "lastfm", "liked-songs"}
    assert expected <= set(keywords)


# -- A-23: real command name in usage, --version flag --------------------------

def test_version_flag_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"ytmusic-to-spotify-sync {playlist_sync.__version__}\n"


@pytest.mark.parametrize(("checkout", "command"), [
    (False, "ytmusic-sync"),
    (True, "python playlist_sync.py"),
])
def test_usage_names_the_command_the_user_runs(
    monkeypatch: pytest.MonkeyPatch, checkout: bool, command: str,
) -> None:
    monkeypatch.setattr(cfg, "is_source_checkout", lambda source_root=None: checkout)
    assert cli.build_parser().format_usage().startswith(f"usage: {command} [-h]")


def test_version_flag_from_repo_script_touches_nothing(tmp_path: Path) -> None:
    home = tmp_path / "settings"
    env = {**os.environ, cfg.HOME_ENV_VAR: str(home)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "playlist_sync.py"), "--version"],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"ytmusic-to-spotify-sync {playlist_sync.__version__}"
    assert not home.exists()
    assert list(tmp_path.iterdir()) == []


def test_bug_template_asks_for_the_cli_version() -> None:
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(encoding="utf-8")
    assert "ytmusic-sync --version" in text
    assert "import playlist_sync" not in text


# -- #37: the sdist leaves the test suite out ----------------------------------

def test_sdist_ships_the_package_without_the_test_suite(tmp_path: Path) -> None:
    setuptools = pytest.importorskip("setuptools")
    if int(setuptools.__version__.split(".")[0]) < 77:
        pytest.skip("the SPDX license field needs setuptools >= 77")
    import tarfile

    # Build from a copy: setuptools writes an egg-info folder next to pyproject.toml
    source = tmp_path / "src"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
        ".git", "data", "dist", "build", "*.egg-info", "__pycache__", ".*_cache", ".env",
        "browser.json"))
    out = tmp_path / "dist"
    result = subprocess.run(
        [sys.executable, "-c",
         f"from setuptools import build_meta; build_meta.build_sdist({str(out)!r})"],
        capture_output=True, text=True, cwd=source, timeout=300, check=False,
    )
    assert result.returncode == 0, result.stderr
    (sdist,) = out.glob("*.tar.gz")
    with tarfile.open(sdist) as tar:
        names = [n.split("/", 1)[1] for n in tar.getnames() if "/" in n]
    assert "playlist_sync/cli.py" in names and "MANIFEST.in" in names
    assert not [n for n in names if n.startswith("tests")]
