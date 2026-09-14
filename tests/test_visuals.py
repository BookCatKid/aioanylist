from __future__ import annotations

import json

import pytest
from aiohttp import web
from test_transport import server

from anylist_sdk.proto import PB
from anylist_sdk.transport import AnyListTransport
from anylist_sdk.visuals import (
    IconContext,
    IconSetKind,
    VisualsService,
    built_in_themes,
)


def catalog_payload(group: str, icon: str, *, variation: str | None = None) -> dict:
    value = {"icon_name": icon, "keywords": ["hello", "wave"]}
    if variation:
        value["icon_variations"] = [{"icon_name": icon}, {"icon_name": variation}]
    return {
        "icon_groups": [
            {
                "identifier": group,
                "display_name": group.replace("-", " ").title(),
                "icons": [value],
            }
        ]
    }


def test_official_catalog_paths_and_asset_urls() -> None:
    transport = AnyListTransport(base_url="https://production.anylist.com")
    visuals = VisualsService(transport)
    assert visuals.catalog_path(IconSetKind.EMOJI) == (
        "/static/webapp/data/icon_sets_json/emoji_icon_set.json"
    )
    assert visuals.catalog_path(IconSetKind.EMOJI, "de").endswith("emoji_icon_set_de.json")
    assert visuals.catalog_path(IconSetKind.FOOD_AND_COOKING, "de").endswith(
        "food_and_cooking_icon_set.de.json"
    )
    assert visuals.icon_url("emoji/1f3a8") == (
        "https://production.anylist.com/static/webapp/img/icon_sets/emoji/1f3a8.png"
    )
    assert visuals.texture_url("wood").endswith(
        "/static/webapp/img/shopping-list-textures/wood@2x.png"
    )
    assert visuals.category_icon_url("produce").endswith(
        "/static/webapp/img/shopping-list-category-icons/produce@2x.png"
    )


@pytest.mark.asyncio
async def test_icon_catalog_loads_variations_searches_and_uses_cache(tmp_path) -> None:
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return web.json_response(
            catalog_payload("emoji-people", "emoji/1f44b", variation="emoji/1f44b-1f3fb")
        )

    app = web.Application()
    app.router.add_get("/static/webapp/data/icon_sets_json/emoji_icon_set.json", handler)
    async with server(app) as base, AnyListTransport(base_url=base) as transport:
        visuals = VisualsService(transport, cache_dir=tmp_path)
        catalog = await visuals.get_icon_set(IconSetKind.EMOJI, "en")
        assert [entry.icon_name for entry in catalog.unique_entries()] == [
            "emoji/1f44b",
            "emoji/1f44b-1f3fb",
        ]
        assert catalog.search("wave")[0].icon_name == "emoji/1f44b"
        assert await visuals.get_icon_set(IconSetKind.EMOJI, "en") is catalog
    assert calls == 1
    assert json.loads((tmp_path / "emoji_en.json").read_text())["icon_groups"]


@pytest.mark.asyncio
async def test_context_catalogs_match_official_web_picker_composition() -> None:
    payloads = {
        "emoji_icon_set.json": catalog_payload("emoji-people", "emoji/1f44b"),
        "food_and_cooking_icon_set.json": {
            "icon_groups": [
                {
                    "identifier": "food-and-cooking-recipes",
                    "display_name": "Recipes",
                    "icons": [{"icon_name": "food_and_cooking/recipe_card", "keywords": []}],
                },
                {
                    "identifier": "food-and-cooking-fruit",
                    "display_name": "Fruit",
                    "icons": [{"icon_name": "food_and_cooking/apple", "keywords": []}],
                },
            ]
        },
        "classic_recipes_icon_set.json": catalog_payload(
            "anylist-classic-recipe", "classic_recipe_icons/turkey"
        ),
    }

    async def handler(request):
        return web.json_response(payloads[request.match_info["name"]])

    app = web.Application()
    app.router.add_get("/static/webapp/data/icon_sets_json/{name}", handler)
    async with server(app) as base, AnyListTransport(base_url=base) as transport:
        visuals = VisualsService(transport)
        lists = await visuals.icon_catalog(IconContext.LIST, language="en")
        recipes = await visuals.icon_catalog(IconContext.RECIPE, language="en")
        notes = await visuals.icon_catalog(IconContext.MEAL_PLAN_NOTE, language="en")

    assert len(lists.groups[0].icons) == 9
    assert all(icon.icon_name == "default_list_icon" for icon in lists.groups[0].icons)
    assert any(group.identifier == "anylist-classic-recipe" for group in recipes.groups)
    assert notes.groups[0].icons[0].icon_name == "default_meal_plan_note_icon"
    assert not any(group.identifier == "food-and-cooking-recipes" for group in notes.groups)


def test_builtin_theme_catalog_matches_official_ids_and_values() -> None:
    themes = built_in_themes()
    assert len(themes) == 14
    by_name = {theme.name: theme for theme in themes}
    assert by_name["Blue"].identifier == "b080cdc28dd94207819cab9005c91be3"
    assert by_name["Blue"].controlHexColor == "1277DC"
    assert by_name["Classic"].tableTexture == "classic_paper"
    assert by_name["Wood"].fontName == "Iowan Old Style"
    assert by_name["Nightshade"].navigationBarHexColor == "5D5D60"


def test_effective_theme_icon_and_style_resolution() -> None:
    transport = AnyListTransport()
    visuals = VisualsService(transport)
    settings = PB.PBListSettings(listColorType=2)
    light = visuals.resolve_list_theme(settings)
    dark = visuals.resolve_list_theme(settings, dark=True)
    icon = visuals.resolve_list_icon(settings)
    assert light.name == "Blue" and light.controlHexColor == "1277DC"
    assert dark.controlHexColor == "639FDB"
    assert dark.tableHexColor == "202124"
    assert icon.iconName == "default_list_icon" and icon.tintHexColor == "1277DC"

    wood = visuals.built_in_theme("4ec9ecba6d0042859b5f86e7e2121bc2")
    assert wood is not None
    style = visuals.theme_style(wood)
    assert style.table_texture == "wood"
    assert style.table_texture_url.endswith("/wood@2x.png")
    assert style.table_background_size == "512px, 512px"
    assert style.font_family.startswith('"Palatino"')


def test_custom_dark_theme_wins_and_missing_dark_variant_is_derived() -> None:
    visuals = VisualsService(AnyListTransport())
    settings = PB.PBListSettings(listThemeId="custom")
    settings.customTheme.CopyFrom(
        PB.PBListTheme(identifier="custom", tableTexture="wood", itemNameHexColor="222222")
    )
    derived = visuals.resolve_list_theme(settings, dark=True)
    assert derived.tableTexture == "dark_wood"
    assert derived.itemNameHexColor == "FFFFFF"

    settings.customDarkTheme.CopyFrom(
        PB.PBListTheme(identifier="custom", tableHexColor="010203", itemNameHexColor="AABBCC")
    )
    explicit = visuals.resolve_list_theme(settings, dark=True)
    assert explicit.tableHexColor == "010203"
    assert explicit.itemNameHexColor == "AABBCC"


def test_folder_visual_fallbacks_match_web_client() -> None:
    visuals = VisualsService(AnyListTransport())
    folder = PB.PBListFolder()
    assert visuals.folder_hex_color(folder) == "16A1E0"
    icon = visuals.resolve_folder_icon(folder)
    assert icon.iconName == "default_folder_icon" and icon.tintHexColor == "16A1E0"

    folder.folderSettings.folderHexColor = "FF3B30"
    folder.folderSettings.icon.CopyFrom(PB.PBIcon(iconName="emoji/1f4c1"))
    assert visuals.folder_hex_color(folder) == "FF3B30"
    assert visuals.resolve_folder_icon(folder).iconName == "emoji/1f4c1"


@pytest.mark.asyncio
async def test_recipe_collection_icon_validates_against_official_catalog() -> None:
    payloads = {
        "emoji_icon_set.json": catalog_payload("emoji-people", "emoji/1f44b"),
        "food_and_cooking_icon_set.json": catalog_payload(
            "food-and-cooking-recipes", "food_and_cooking/cookbooks"
        ),
    }

    async def handler(request):
        return web.json_response(payloads[request.match_info["name"]])

    app = web.Application()
    app.router.add_get("/static/webapp/data/icon_sets_json/{name}", handler)
    async with server(app) as base, AnyListTransport(base_url=base) as transport:
        visuals = VisualsService(transport)
        collection = PB.PBRecipeCollection()
        collection.collectionSettings.icon.CopyFrom(
            PB.PBIcon(iconName="food_and_cooking/cookbooks")
        )
        assert (await visuals.resolve_recipe_collection_icon(collection)).iconName == (
            "food_and_cooking/cookbooks"
        )
        collection.collectionSettings.icon.iconName = "not/a/real/icon"
        assert (await visuals.resolve_recipe_collection_icon(collection)).iconName == (
            "food_and_cooking/stack_of_recipe_cards"
        )
