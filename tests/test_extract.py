# SPDX-License-Identifier: GPL-3.0-or-later
"""extract_recipe: prompt assembly and structured-output handling (no network)."""
from __future__ import annotations

import pytest

from juanita import cli
from juanita.cli import Ingredient, Recipe


class _Usage:
    input_tokens = 10
    output_tokens = 20


class _Resp:
    def __init__(self, parsed):
        self.parsed_output = parsed
        self.stop_reason = "end_turn"
        self.usage = _Usage()


class _Messages:
    def __init__(self, parsed):
        self._parsed = parsed
        self.kwargs: dict | None = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return _Resp(self._parsed)


class FakeClient:
    def __init__(self, parsed):
        self.messages = _Messages(parsed)


def _source():
    return {"title": "Pan", "description": "ctx", "body": "mezclar harina"}


def test_extract_recipe_returns_parsed_output():
    parsed = Recipe(
        name="Pan", description="d", recipe_yield="1",
        ingredients=[Ingredient(quantity=1, unit="g", food="harina", note="")],
        instructions=["x"], tags=["pan"],
    )
    client = FakeClient(parsed)

    out = cli.extract_recipe(client, _source())

    assert out is parsed
    # The schema is the contract, and the source text/title reach the model.
    assert client.messages.kwargs["output_format"] is Recipe
    prompt = client.messages.kwargs["messages"][0]["content"]
    assert "mezclar harina" in prompt
    assert "Pan" in prompt


def test_extract_recipe_none_output_raises():
    client = FakeClient(None)
    with pytest.raises(RuntimeError, match="parseable"):
        cli.extract_recipe(client, _source())
