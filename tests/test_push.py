# SPDX-License-Identifier: GPL-3.0-or-later
"""push_to_mealie: document assembly, source URL, tags, image, linking."""
from __future__ import annotations

from juanita import cli
from juanita.cli import Ingredient, Recipe


def make_recipe(**over) -> Recipe:
    base = dict(
        name="Pan de nuez",
        description="Rico pan.",
        recipe_yield="1 pan",
        ingredients=[Ingredient(quantity=365, unit="g", food="harina", note="")],
        instructions=["Mezclar.", "Hornear."],
        tags=["pan", "dulce"],
    )
    base.update(over)
    return Recipe(**base)


def video_source(**over) -> dict:
    src = {
        "title": "Pan",
        "description": "",
        "source_url": "https://youtu.be/abc",
        "thumbnail": "https://img/abc.jpg",
        "body": "...",
    }
    src.update(over)
    return src


def test_push_returns_slug_and_creates_with_name(fake_mealie):
    slug = cli.push_to_mealie(fake_mealie, make_recipe(), video_source())
    assert slug == "the-slug"
    assert fake_mealie.created_name == "Pan de nuez"


def test_push_assembles_document(fake_mealie):
    cli.push_to_mealie(fake_mealie, make_recipe(), video_source())
    doc = fake_mealie.updated

    assert doc["recipeYield"] == "1 pan"
    assert doc["orgURL"] == "https://youtu.be/abc"
    assert "Source: https://youtu.be/abc" in doc["description"]
    assert doc["recipeInstructions"] == [{"text": "Mezclar."}, {"text": "Hornear."}]
    # name is intentionally left as Mealie set it on create()
    assert "name" not in doc or doc["name"] == "Pan de nuez"


def test_push_links_ingredients_by_default(fake_mealie):
    cli.push_to_mealie(fake_mealie, make_recipe(), video_source())
    ing = fake_mealie.updated["recipeIngredient"][0]

    assert ing["quantity"] == 365
    assert ing["food"] == {"id": "food-harina", "name": "harina"}
    assert ing["unit"] == {"id": "unit-g", "name": "g"}
    assert fake_mealie.food_calls == ["harina"]


def test_push_not_linked_ingredients_are_plain_text(fake_mealie):
    cli.push_to_mealie(fake_mealie, make_recipe(), video_source(), link_ingredients=False)
    assert fake_mealie.updated["recipeIngredient"] == [{"note": "365 g harina"}]
    assert fake_mealie.food_calls == []  # database untouched


def test_push_attaches_tags(fake_mealie):
    cli.push_to_mealie(fake_mealie, make_recipe(), video_source())
    assert fake_mealie.tag_calls == ["pan", "dulce"]
    assert [t["name"] for t in fake_mealie.updated["tags"]] == ["pan", "dulce"]


def test_push_no_tags(fake_mealie):
    cli.push_to_mealie(fake_mealie, make_recipe(), video_source(), include_tags=False)
    assert fake_mealie.tag_calls == []
    assert "tags" not in fake_mealie.updated


def test_push_sets_image_from_thumbnail(fake_mealie):
    cli.push_to_mealie(fake_mealie, make_recipe(), video_source())
    assert fake_mealie.images == [("the-slug", "https://img/abc.jpg")]


def test_push_image_failure_is_non_fatal(fake_mealie):
    fake_mealie.image_should_fail = True
    slug = cli.push_to_mealie(fake_mealie, make_recipe(), video_source())
    assert slug == "the-slug"  # no exception despite image failure


def test_push_local_file_source_has_no_url_or_image(fake_mealie):
    src = video_source(source_url=None, thumbnail=None)
    cli.push_to_mealie(fake_mealie, make_recipe(), src)
    doc = fake_mealie.updated

    assert "orgURL" not in doc
    assert "Source:" not in doc["description"]
    assert fake_mealie.images == []
