# SPDX-License-Identifier: GPL-3.0-or-later
"""Ingredient formatting and recipeIngredient construction."""
from __future__ import annotations

import pytest

from juanita import cli
from juanita.cli import Ingredient


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, ""),
        (365.0, "365"),
        (2.0, "2"),
        (0.5, "0.5"),
        (1.25, "1.25"),
    ],
)
def test_format_quantity(value, expected):
    assert cli._format_quantity(value) == expected


def test_original_text_full_line():
    ing = Ingredient(quantity=365, unit="g", food="harina leudante", note="")
    assert cli._ingredient_original_text(ing) == "365 g harina leudante"


def test_original_text_note_only():
    ing = Ingredient(quantity=None, unit="", food="nueces", note="a gusto")
    assert cli._ingredient_original_text(ing) == "nueces (a gusto)"


def test_original_text_note_without_other_parts():
    ing = Ingredient(quantity=None, unit="", food="", note="salt to taste")
    assert cli._ingredient_original_text(ing) == "salt to taste"


def test_build_recipe_ingredient_unlinked_is_note_only(fake_mealie):
    ing = Ingredient(quantity=1, unit="taza", food="leche", note="")
    out = cli._build_recipe_ingredient(fake_mealie, ing, link=False)

    assert out == {"note": "1 taza leche"}
    assert fake_mealie.food_calls == []  # nothing touched in the database
    assert fake_mealie.unit_calls == []


def test_build_recipe_ingredient_linked(fake_mealie):
    ing = Ingredient(quantity=365, unit="g", food="harina", note="sifted")
    out = cli._build_recipe_ingredient(fake_mealie, ing, link=True)

    assert out["quantity"] == 365
    assert out["food"] == {"id": "food-harina", "name": "harina"}
    assert out["unit"] == {"id": "unit-g", "name": "g"}
    assert out["note"] == "sifted"
    assert out["originalText"] == "365 g harina (sifted)"


def test_build_recipe_ingredient_linking_failure_falls_back(fake_mealie, monkeypatch):
    def boom(_name):
        raise RuntimeError("mealie down")

    monkeypatch.setattr(fake_mealie, "resolve_food", boom)
    ing = Ingredient(quantity=2, unit="", food="huevos", note="or 3")

    out = cli._build_recipe_ingredient(fake_mealie, ing, link=True)
    # Best-effort: no exception, food/unit unset, but still amounted.
    assert out["food"] is None
    assert out["unit"] is None
    assert out["quantity"] == 2
    assert out["originalText"] == "2 huevos (or 3)"
