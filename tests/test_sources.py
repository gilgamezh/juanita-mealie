# SPDX-License-Identifier: GPL-3.0-or-later
"""Source loading: local text files and caption parsing/retry."""
from __future__ import annotations

import json

import pytest

from juanita import cli


class FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class FakeYDL:
    """Fake YoutubeDL exposing just the urlopen used by _download_caption."""

    def __init__(self, payload: bytes = b"", exc: Exception | None = None):
        self._payload = payload
        self._exc = exc
        self.calls = 0

    def urlopen(self, url: str) -> FakeResp:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return FakeResp(self._payload)


def test_load_text_file_uses_first_line_as_title(tmp_path):
    f = tmp_path / "recipe.txt"
    f.write_text("\n  Grandma's Bread  \n\n365 g flour\nwalnuts\n")

    rec = cli.load_text_file(str(f))
    assert rec["title"] == "Grandma's Bread"
    assert rec["source_url"] is None
    assert rec["thumbnail"] is None
    assert "365 g flour" in rec["body"]
    assert rec["body"].startswith("Grandma's Bread")  # stripped of leading blanks


def test_load_text_file_empty_raises(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("   \n\n")
    with pytest.raises(RuntimeError, match="empty"):
        cli.load_text_file(str(f))


def test_download_caption_json3():
    payload = json.dumps({
        "events": [
            {"segs": [{"utf8": "Hello "}, {"utf8": "world"}]},
            {"segs": [{"utf8": "!"}]},
            {"segs": None},
        ]
    }).encode()
    ydl = FakeYDL(payload=payload)

    assert cli._download_caption(ydl, "http://x", "json3") == "Hello world!"


def test_download_caption_vtt_strips_timestamps_and_headers():
    vtt = (
        "WEBVTT\n\n"
        "1\n00:00:00.000 --> 00:00:01.000\nHello\n\n"
        "2\n00:00:01.000 --> 00:00:02.000\nworld\n"
    )
    ydl = FakeYDL(payload=vtt.encode())

    assert cli._download_caption(ydl, "http://x", "vtt") == "Hello\nworld"


def test_download_caption_non_429_error_propagates():
    ydl = FakeYDL(exc=ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        cli._download_caption(ydl, "http://x", "json3")


def test_download_caption_429_exhausts_with_friendly_error(monkeypatch):
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)  # don't actually wait
    ydl = FakeYDL(exc=Exception("HTTP Error 429: Too Many Requests"))

    with pytest.raises(RuntimeError, match="429"):
        cli._download_caption(ydl, "http://x", "json3", attempts=2)
    assert ydl.calls == 2  # retried the configured number of times


def test_extract_transcript_picks_json3_from_automatic_captions():
    payload = json.dumps({"events": [{"segs": [{"utf8": "hi"}]}]}).encode()
    ydl = FakeYDL(payload=payload)
    info = {"automatic_captions": {"en": [{"ext": "json3", "url": "http://x"}]}}

    assert cli._extract_transcript(ydl, info) == "hi"


def test_extract_transcript_no_captions_returns_empty():
    assert cli._extract_transcript(FakeYDL(), {}) == ""
