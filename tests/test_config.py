# SPDX-License-Identifier: GPL-3.0-or-later
"""Config loading: load_dotenv, load_config precedence, user_config_path."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from juanita import cli


def test_load_dotenv_parses_comments_quotes_and_blanks(tmp_path, monkeypatch):
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        'FOO="quoted value"\n'
        "BAR = bare \n"
        "BAZ='single'\n"
        "not a kv line\n"
    )

    assert cli.load_dotenv(env) is True
    assert os.environ["FOO"] == "quoted value"
    assert os.environ["BAR"] == "bare"
    assert os.environ["BAZ"] == "single"


def test_load_dotenv_missing_file_returns_false(tmp_path):
    assert cli.load_dotenv(tmp_path / "nope.env") is False


def test_load_dotenv_does_not_override_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOO", "real")
    (tmp_path / ".env").write_text("FOO=fromfile\n")

    cli.load_dotenv(tmp_path / ".env")
    assert os.environ["FOO"] == "real"  # setdefault: real env wins


def test_user_config_path_respects_xdg(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")
    assert cli.user_config_path() == Path("/tmp/xdg/juanita/config.env")


def test_user_config_path_defaults_to_home(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/tester")))
    assert cli.user_config_path() == Path("/home/tester/.config/juanita/config.env")


def test_load_config_explicit_missing_raises():
    with pytest.raises(FileNotFoundError):
        cli.load_config("/does/not/exist.env")


def test_load_config_precedence_cwd_over_user(tmp_path, monkeypatch):
    for key in ("SHARED", "ONLY_CWD", "ONLY_USER"):
        monkeypatch.delenv(key, raising=False)

    # ./.env in the (chdir'd) working directory
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SHARED=from_cwd\nONLY_CWD=yes\n")

    # per-user config under a fake XDG home
    xdg = tmp_path / "xdg"
    cfg = xdg / "juanita" / "config.env"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("SHARED=from_user\nONLY_USER=yes\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    cli.load_config(None)

    assert os.environ["SHARED"] == "from_cwd"   # ./.env wins
    assert os.environ["ONLY_CWD"] == "yes"
    assert os.environ["ONLY_USER"] == "yes"     # but user config still contributes
