from __future__ import annotations

import asyncio
import colorsys
import json
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

from .exceptions import VisualDataError
from .proto import PB, PBIcon, PBListFolder, PBListSettings, PBListTheme, PBRecipeCollection
from .transport import AnyListTransport

_ICON_DATA_ROOT = "/static/webapp/data/icon_sets_json"
_ICON_IMAGE_ROOT = "/static/webapp/img/icon_sets"
_TEXTURE_IMAGE_ROOT = "/static/webapp/img/shopping-list-textures"
_CATEGORY_IMAGE_ROOT = "/static/webapp/img/shopping-list-category-icons"

_DEFAULT_THEME_COLORS = (
    ("Aqua", "7b1dd303fb6a44fbbed44667f199aa63", "16A1E0"),
    ("Purple", "c314b1dbf4d94b0493b02dca94d6453d", "5B5BB1"),
    ("Blue", "b080cdc28dd94207819cab9005c91be3", "1277DC"),
    ("Green", "eba40aa7b96241d3912af580e41d8d88", "00B745"),
    ("Yellow", "254bc21da06e44aba7ea1e3395276b53", "F2B000"),
    ("Orange", "42f2ebf231c34ef695cffee6904c51c0", "F3891F"),
    ("Red", "c8280208796e41e7a9d6a597c4b4ec8a", "E1321F"),
    ("Pink", "229bac21b3684e8794a88e9051d3255c", "F30084"),
    ("Gray", "2469b102a84c4033977ca5a08572893e", "657D8C"),
)

BACKGROUND_HEX_COLORS = (
    "FFFFFF",
    "F3F3F3",
    "FFFCEB",
    "F0E1CE",
    "D9D2BC",
    "F3BBCD",
    "CFE0F3",
    "CDDD73",
    "FAF2AD",
    "ECBA7E",
    "EE5E9F",
    "36A9CE",
    "B5E84B",
    "F7DD52",
    "F68D3A",
    "536878",
    "4C5860",
    "3B3B41",
    "1B2427",
    "000000",
)

ACCENT_HEX_COLORS = (
    "19A9EB",
    "2C90BE",
    "00A6C2",
    "3872A6",
    "51A7F9",
    "2077D8",
    "007AFF",
    "0D5591",
    "70BF41",
    "1BB74E",
    "27D313",
    "00882B",
    "E5AD48",
    "F2BB2F",
    "F28B32",
    "D05518",
    "EC5D57",
    "CE282E",
    "F2382D",
    "A50235",
    "E45278",
    "FF8BD7",
    "FF48FC",
    "F21285",
    "B36AE2",
    "8368FF",
    "852AF2",
    "5C5CAD",
    "79797C",
    "536878",
    "4C5860",
    "3B3B41",
)

FOLDER_HEX_COLORS = (
    "16A1E0",
    "0078FF",
    "00D846",
    "5C5CCC",
    "FF9335",
    "FF3B30",
    "FF148D",
    "FFC532",
    "8E8E93",
)

TEXTURE_NAMES = (
    "classic_paper",
    "executive_paper",
    "beige_paper",
    "wood",
    "tweed",
    "dark_wood",
    "dark_dotted",
    "noisy_net",
)

DARK_TEXTURE_NAMES = ("tweed", "dark_wood", "dark_dotted", "noisy_net")

TEXTURE_BACKGROUND_SIZES: dict[str, str] = {
    "beige_paper": "138px, 138px",
    "classic_paper": "48px, 48px",
    "executive_paper": "250px, 296px",
    "tweed": "200px, 200px",
    "wood": "512px, 512px",
    "dark_wood": "200px, 200px",
    "dark_dotted": "5px, 5px",
    "noisy_net": "200px, 200px",
}

_DARK_ACCENT_MAP = {
    "00B745": "37B868",
    "0D5591": "74A1C4",
    "1277DC": "639FDB",
    "16A1E0": "5AB6E0",
    "4C5860": "93B5CC",
    "5B5BB1": "7B7BB0",
    "657D8C": "B0BEC5",
    "CE282E": "E05B4C",
    "E1321F": "E05B4C",
    "F2B000": "F2D279",
    "F30084": "F291C7",
    "F3891F": "F2AD68",
}

_DARK_TEXTURE_MAP = {
    "beige_paper": "tweed",
    "classic_paper": "noisy_net",
    "executive_paper": "dark_dotted",
    "wood": "dark_wood",
}

_STICKY_NOTE_THEME_ID = "247ff210e248473a9941c37eeb10bcfd"


class IconSetKind(StrEnum):
    EMOJI = "emoji"
    FOOD_AND_COOKING = "food_and_cooking"
    CLASSIC_RECIPES = "classic_recipes"


class IconContext(StrEnum):
    ALL = "all"
    LIST = "list"
    FOLDER = "folder"
    RECIPE_COLLECTION = "recipe_collection"
    RECIPE = "recipe"
    MEAL_PLAN_NOTE = "meal_plan_note"
    MEAL_PLAN_TEMPLATE = "meal_plan_template"


@dataclass(slots=True, frozen=True)
class IconEntry:
    icon_name: str
    keywords: tuple[str, ...] = ()
    tint_hex_color: str | None = None
    variations: tuple[IconEntry, ...] = ()

    def as_pb_icon(self) -> PBIcon:
        icon = PB.PBIcon(iconName=self.icon_name)
        if self.tint_hex_color:
            icon.tintHexColor = self.tint_hex_color
        return icon


@dataclass(slots=True, frozen=True)
class IconGroup:
    identifier: str
    display_name: str
    icons: tuple[IconEntry, ...]


@dataclass(slots=True, frozen=True)
class IconCatalog:
    groups: tuple[IconGroup, ...]

    def entries(self, *, include_variations: bool = True) -> tuple[IconEntry, ...]:
        out: list[IconEntry] = []
        for group in self.groups:
            for icon in group.icons:
                out.append(icon)
                if include_variations:
                    out.extend(icon.variations)
        return tuple(out)

    def unique_entries(self, *, include_variations: bool = True) -> tuple[IconEntry, ...]:
        seen: set[tuple[str, str | None]] = set()
        out: list[IconEntry] = []
        for icon in self.entries(include_variations=include_variations):
            key = (icon.icon_name, icon.tint_hex_color)
            if key not in seen:
                seen.add(key)
                out.append(icon)
        return tuple(out)

    def search(self, query: str, *, include_variations: bool = True) -> tuple[IconEntry, ...]:
        terms = tuple(part.casefold() for part in query.split() if part)
        if not terms:
            return self.unique_entries(include_variations=include_variations)
        matches: list[IconEntry] = []
        for icon in self.unique_entries(include_variations=include_variations):
            haystack = " ".join((icon.icon_name, *icon.keywords)).casefold()
            if all(term in haystack for term in terms):
                matches.append(icon)
        return tuple(matches)


@dataclass(slots=True, frozen=True)
class ThemeStyle:
    identifier: str
    name: str
    banner_hex_color: str
    control_hex_color: str
    background_hex_color: str
    table_hex_color: str
    item_name_hex_color: str
    item_details_hex_color: str
    separator_hex_color: str
    selection_hex_color: str
    navigation_bar_hex_color: str | None
    cell_hex_color: str | None
    font_name: str | None
    font_family: str
    table_texture: str | None
    table_texture_url: str | None
    table_background_size: str | None
    background_texture: str | None
    background_texture_url: str | None
    background_image: str | None
    cell_texture: str | None
    table_background_css: str
    is_dark: bool


def _parse_icon(value: dict[str, Any]) -> IconEntry:
    name = value.get("icon_name")
    if not isinstance(name, str) or not name:
        raise VisualDataError("AnyList icon entry is missing icon_name")
    raw_keywords = value.get("keywords", [])
    keywords = tuple(str(item) for item in raw_keywords) if isinstance(raw_keywords, list) else ()
    raw_variations = value.get("icon_variations", [])
    variations = (
        tuple(_parse_icon(item) for item in raw_variations if isinstance(item, dict))
        if isinstance(raw_variations, list)
        else ()
    )
    tint = value.get("tint_hex_color")
    return IconEntry(
        icon_name=name,
        keywords=keywords,
        tint_hex_color=str(tint) if tint else None,
        variations=variations,
    )


def _parse_catalog(value: dict[str, Any]) -> IconCatalog:
    raw_groups = value.get("icon_groups")
    if not isinstance(raw_groups, list):
        raise VisualDataError("AnyList icon catalog is missing icon_groups")
    groups: list[IconGroup] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        identifier = raw_group.get("identifier")
        display_name = raw_group.get("display_name")
        raw_icons = raw_group.get("icons")
        if not isinstance(identifier, str) or not isinstance(display_name, str):
            raise VisualDataError("AnyList icon group is missing its identifier or display_name")
        if not isinstance(raw_icons, list):
            raise VisualDataError(f"AnyList icon group {identifier!r} is missing icons")
        groups.append(
            IconGroup(
                identifier=identifier,
                display_name=display_name,
                icons=tuple(_parse_icon(item) for item in raw_icons if isinstance(item, dict)),
            )
        )
    return IconCatalog(tuple(groups))


def _theme(**values: str) -> PBListTheme:
    theme = PB.PBListTheme()
    for field, value in values.items():
        setattr(theme, field, value)
    return theme


def built_in_themes() -> tuple[PBListTheme, ...]:
    themes = [
        _theme(
            identifier=identifier,
            name=name,
            bannerHexColor=color,
            controlHexColor=color,
            tableHexColor="FFFFFF",
        )
        for name, identifier, color in _DEFAULT_THEME_COLORS
    ]
    themes.extend(
        [
            _theme(
                identifier="80c0524de86c43349a82e841560e517d",
                name="Classic",
                bannerHexColor="CE282E",
                controlHexColor="CE282E",
                tableTexture="classic_paper",
            ),
            _theme(
                identifier="d448c5750a0845a686891bbffadc63c1",
                name="Executive",
                bannerHexColor="4C5860",
                controlHexColor="4C5860",
                tableTexture="executive_paper",
                fontName="Avenir",
            ),
            _theme(
                identifier=_STICKY_NOTE_THEME_ID,
                name="Sticky Note",
                bannerHexColor="D05518",
                tableHexColor="F7DD52",
                selectionHexColor="BBAE68",
                separatorHexColor="AC9A39",
                itemDetailsHexColor="424242",
                fontName="Chalkboard SE",
            ),
            _theme(
                identifier="4ee59556cf4a4dddb1214ce21f37b16c",
                name="Nightshade",
                bannerHexColor="008E13",
                tableHexColor="3B3B41",
                selectionHexColor="696974",
                separatorHexColor="767679",
                itemNameHexColor="FFFFFF",
                itemDetailsHexColor="B5B5B5",
                navigationBarHexColor="5D5D60",
                fontName="Avenir",
            ),
            _theme(
                identifier="4ec9ecba6d0042859b5f86e7e2121bc2",
                name="Wood",
                bannerHexColor="0D5591",
                controlHexColor="0D5591",
                tableTexture="wood",
                separatorHexColor="A2937D",
                selectionHexColor="B9A890",
                fontName="Iowan Old Style",
                itemDetailsHexColor="515151",
            ),
        ]
    )
    return tuple(themes)


def _clone_theme(theme: PBListTheme) -> PBListTheme:
    clone = PB.PBListTheme()
    clone.CopyFrom(theme)
    return clone


def _has_field(message: Any, field: str) -> bool:
    try:
        return bool(message.HasField(field))
    except (ValueError, AttributeError):
        return bool(getattr(message, field, None))


def _dark_accent(value: str) -> str:
    return _DARK_ACCENT_MAP.get(value.upper(), value)


def _brightness(hex_color: str) -> float:
    value = hex_color.removeprefix("#")
    if len(value) != 6:
        return 1.0
    try:
        r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 1.0
    return colorsys.rgb_to_hsv(r, g, b)[2]


def _darken_built_in(theme: PBListTheme) -> PBListTheme:
    # Android deliberately leaves Sticky Note unchanged in dark mode.
    if theme.identifier == _STICKY_NOTE_THEME_ID:
        return _clone_theme(theme)
    out = _clone_theme(theme)
    texture = str(out.tableTexture or "")
    if texture in {"classic_paper", "executive_paper", "wood"}:
        out.tableTexture = _DARK_TEXTURE_MAP[texture]
    else:
        out.tableHexColor = "202124"
    if out.controlHexColor:
        out.controlHexColor = _dark_accent(str(out.controlHexColor))
    if out.bannerHexColor:
        out.bannerHexColor = _dark_accent(str(out.bannerHexColor))
    out.itemNameHexColor = "FFFFFF"
    out.itemDetailsHexColor = "8D8D92"
    out.ClearField("separatorHexColor")
    out.selectionHexColor = "FFFFFF"
    out.ClearField("navigationBarHexColor")
    return out


def _darken_custom(theme: PBListTheme) -> PBListTheme:
    out = _clone_theme(theme)
    item_name = str(theme.itemNameHexColor or "2B3640")
    item_details = str(theme.itemDetailsHexColor or "626C73")
    out.itemNameHexColor = item_name
    out.itemDetailsHexColor = item_details
    texture = str(theme.tableTexture or "")
    if texture in _DARK_TEXTURE_MAP:
        out.tableTexture = _DARK_TEXTURE_MAP[texture]
        if _brightness(item_name) <= 0.5:
            out.itemNameHexColor = "FFFFFF"
        if _brightness(item_details) <= 0.5:
            out.itemDetailsHexColor = "8D8D92"
    elif not texture:
        out.tableHexColor = "202124"
        out.ClearField("separatorHexColor")
        out.selectionHexColor = "FFFFFF"
        out.ClearField("cellHexColor")
        out.ClearField("cellTexture")
        if _brightness(item_name) <= 0.5:
            out.itemNameHexColor = "FFFFFF"
        if _brightness(item_details) <= 0.5:
            out.itemDetailsHexColor = "8D8D92"
    return out


class VisualsService:
    """AnyList-owned visual metadata and URL resolution without bundling its artwork."""

    def __init__(
        self,
        transport: AnyListTransport,
        *,
        locale: str = "en-US",
        cache_dir: str | Path | None = None,
        cache_ttl: float = 24 * 60 * 60,
        allow_stale: bool = True,
    ) -> None:
        self.transport = transport
        self.locale = locale
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.cache_ttl = cache_ttl
        self.allow_stale = allow_stale
        self._loaded: dict[tuple[IconSetKind, str], IconCatalog] = {}
        self._locks: dict[tuple[IconSetKind, str], asyncio.Lock] = {}

    @property
    def language(self) -> str:
        return self.locale.replace("_", "-").split("-", 1)[0].lower() or "en"

    @staticmethod
    def catalog_path(kind: IconSetKind, language: str = "en") -> str:
        if kind is IconSetKind.EMOJI:
            filename = (
                "emoji_icon_set.json" if language == "en" else f"emoji_icon_set_{language}.json"
            )
        elif kind is IconSetKind.FOOD_AND_COOKING:
            filename = (
                "food_and_cooking_icon_set.json"
                if language == "en"
                else f"food_and_cooking_icon_set.{language}.json"
            )
        else:
            filename = (
                "classic_recipes_icon_set.json"
                if language == "en"
                else f"classic_recipes_icon_set.{language}.json"
            )
        return f"{_ICON_DATA_ROOT}/{filename}"

    def absolute_url(self, path: str) -> str:
        return urljoin(self.transport.base_url.rstrip("/") + "/", path.lstrip("/"))

    def icon_url(self, icon: str | PBIcon | IconEntry) -> str:
        if isinstance(icon, str):
            name = icon
        elif isinstance(icon, IconEntry):
            name = icon.icon_name
        else:
            name = str(icon.iconName)
        return self.absolute_url(f"{_ICON_IMAGE_ROOT}/{name}.png")

    def texture_url(self, texture_name: str) -> str:
        return self.absolute_url(f"{_TEXTURE_IMAGE_ROOT}/{texture_name}@2x.png")

    def category_icon_url(self, image_name: str) -> str:
        return self.absolute_url(f"{_CATEGORY_IMAGE_ROOT}/{image_name}@2x.png")

    def _cache_path(self, kind: IconSetKind, language: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{kind.value}_{language}.json"

    async def _load_cache(
        self, kind: IconSetKind, language: str
    ) -> tuple[IconCatalog | None, float]:
        path = self._cache_path(kind, language)
        if path is None or not path.exists():
            return None, 0.0
        try:
            stat = await asyncio.to_thread(path.stat)
            raw = await asyncio.to_thread(path.read_text, "utf-8")
            return _parse_catalog(json.loads(raw)), stat.st_mtime
        except (OSError, ValueError, TypeError, VisualDataError):
            return None, 0.0

    async def _save_cache(self, kind: IconSetKind, language: str, raw: str) -> None:
        path = self._cache_path(kind, language)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, raw, "utf-8")

    async def get_icon_set(
        self,
        kind: IconSetKind,
        language: str | None = None,
        *,
        refresh: bool = False,
    ) -> IconCatalog:
        language = (language or self.language).lower()
        key = (kind, language)
        if not refresh and key in self._loaded:
            return self._loaded[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not refresh and key in self._loaded:
                return self._loaded[key]
            cached, mtime = await self._load_cache(kind, language)
            if cached is not None and not refresh and time.time() - mtime < self.cache_ttl:
                self._loaded[key] = cached
                return cached
            url = self.absolute_url(self.catalog_path(kind, language))
            try:
                async with self.transport.session.get(url) as response:
                    if response.status >= 400:
                        raise VisualDataError(
                            f"AnyList icon catalog {kind.value!r}/{language!r} returned HTTP "
                            f"{response.status}"
                        )
                    raw = await response.text()
                parsed = _parse_catalog(json.loads(raw))
                self._loaded[key] = parsed
                await self._save_cache(kind, language, raw)
                return parsed
            except (aiohttp.ClientError, ValueError, VisualDataError) as exc:
                if cached is not None and self.allow_stale:
                    self._loaded[key] = cached
                    return cached
                if isinstance(exc, VisualDataError):
                    raise
                raise VisualDataError(
                    f"Unable to load AnyList icon catalog {kind.value!r}/{language!r}"
                ) from exc

    async def icon_catalog(
        self,
        context: IconContext = IconContext.ALL,
        *,
        language: str | None = None,
        refresh: bool = False,
    ) -> IconCatalog:
        emoji = await self.get_icon_set(IconSetKind.EMOJI, language, refresh=refresh)
        if context is IconContext.LIST:
            defaults = tuple(
                IconEntry("default_list_icon", tint_hex_color=color)
                for _, _, color in _DEFAULT_THEME_COLORS
            )
            return IconCatalog(
                (IconGroup("anylist-list-icons", "AnyList", defaults),) + emoji.groups
            )
        if context is IconContext.FOLDER:
            defaults = tuple(
                IconEntry("default_folder_icon", tint_hex_color=color)
                for color in FOLDER_HEX_COLORS
            )
            return IconCatalog(
                (IconGroup("default-folder-icons", "AnyList", defaults),) + emoji.groups
            )
        food = await self.get_icon_set(IconSetKind.FOOD_AND_COOKING, language, refresh=refresh)
        if context in {IconContext.RECIPE_COLLECTION, IconContext.MEAL_PLAN_TEMPLATE}:
            return IconCatalog(food.groups + emoji.groups)
        if context is IconContext.RECIPE:
            classic = await self.get_icon_set(
                IconSetKind.CLASSIC_RECIPES, language, refresh=refresh
            )
            return IconCatalog(food.groups + classic.groups + emoji.groups)
        if context is IconContext.ALL:
            classic = await self.get_icon_set(
                IconSetKind.CLASSIC_RECIPES, language, refresh=refresh
            )
            return IconCatalog(food.groups + classic.groups + emoji.groups)
        food_groups = tuple(
            group for group in food.groups if group.identifier != "food-and-cooking-recipes"
        )
        default_note = IconGroup(
            "default-event-icons",
            "Meal Plan Note",
            (IconEntry("default_meal_plan_note_icon", keywords=("note",)),),
        )
        return IconCatalog((default_note,) + food_groups + emoji.groups)

    @staticmethod
    def built_in_themes() -> tuple[PBListTheme, ...]:
        return built_in_themes()

    @staticmethod
    def built_in_theme(identifier: str, *, dark: bool = False) -> PBListTheme | None:
        for theme in built_in_themes():
            if theme.identifier == identifier:
                return _darken_built_in(theme) if dark else theme
        return None

    @staticmethod
    def selected_theme_id(settings: PBListSettings | None) -> str:
        if settings is not None and _has_field(settings, "listThemeId") and settings.listThemeId:
            return str(settings.listThemeId)
        color_type = 0
        if settings is not None and _has_field(settings, "listColorType"):
            color_type = int(settings.listColorType)
        if not 0 <= color_type < len(_DEFAULT_THEME_COLORS):
            color_type = 0
        return _DEFAULT_THEME_COLORS[color_type][1]

    def resolve_list_theme(
        self, settings: PBListSettings | None, *, dark: bool = False
    ) -> PBListTheme:
        selected = self.selected_theme_id(settings)
        if settings is not None and _has_field(settings, "customTheme"):
            custom = settings.customTheme
            if custom.identifier and custom.identifier == selected:
                if dark and _has_field(settings, "customDarkTheme"):
                    return _clone_theme(settings.customDarkTheme)
                return _darken_custom(custom) if dark else _clone_theme(custom)
        built_in = self.built_in_theme(selected, dark=dark)
        if built_in is not None:
            return built_in
        default = self.built_in_theme(_DEFAULT_THEME_COLORS[0][1], dark=dark)
        assert default is not None
        return default

    def resolve_list_icon(self, settings: PBListSettings | None, *, dark: bool = False) -> PBIcon:
        if settings is not None and _has_field(settings, "icon") and settings.icon.iconName:
            icon = PB.PBIcon()
            icon.CopyFrom(settings.icon)
            return icon
        theme = self.resolve_list_theme(settings, dark=dark)
        tint = str(theme.controlHexColor or theme.bannerHexColor or "16A1E0")
        return PB.PBIcon(iconName="default_list_icon", tintHexColor=tint)

    @staticmethod
    def folder_hex_color(folder: PBListFolder | None) -> str:
        if folder is not None and _has_field(folder, "folderSettings"):
            settings = folder.folderSettings
            if _has_field(settings, "folderHexColor") and settings.folderHexColor:
                return str(settings.folderHexColor)
        return "16A1E0"

    def resolve_folder_icon(self, folder: PBListFolder | None) -> PBIcon:
        if folder is not None and _has_field(folder, "folderSettings"):
            settings = folder.folderSettings
            if _has_field(settings, "icon") and settings.icon.iconName:
                icon = PB.PBIcon()
                icon.CopyFrom(settings.icon)
                return icon
        return PB.PBIcon(
            iconName="default_folder_icon",
            tintHexColor=self.folder_hex_color(folder),
        )

    async def resolve_recipe_collection_icon(
        self,
        collection: PBRecipeCollection | None,
        *,
        language: str | None = None,
    ) -> PBIcon:
        icon: PBIcon | None = None
        if collection is not None and _has_field(collection, "collectionSettings"):
            settings = collection.collectionSettings
            if _has_field(settings, "icon") and settings.icon.iconName:
                icon = PB.PBIcon()
                icon.CopyFrom(settings.icon)
        if icon is not None:
            catalog = await self.icon_catalog(IconContext.RECIPE_COLLECTION, language=language)
            if any(entry.icon_name == icon.iconName for entry in catalog.unique_entries()):
                return icon
        return PB.PBIcon(iconName="food_and_cooking/stack_of_recipe_cards")

    def theme_style(self, theme: PBListTheme) -> ThemeStyle:
        banner = str(theme.bannerHexColor or "16A1E0").removeprefix("#")
        control = str(theme.controlHexColor or banner).removeprefix("#")
        background = str(theme.backgroundHexColor or "FFFFFF").removeprefix("#")
        table = str(theme.tableHexColor or background).removeprefix("#")
        item_name = str(theme.itemNameHexColor or "2B3640").removeprefix("#")
        item_details = str(theme.itemDetailsHexColor or "626C73").removeprefix("#")
        separator = str(theme.separatorHexColor or "D8D8D8").removeprefix("#")
        selection = str(theme.selectionHexColor or "F2F2F2").removeprefix("#")
        table_texture = str(theme.tableTexture) if theme.tableTexture else None
        background_texture = str(theme.backgroundTexture) if theme.backgroundTexture else None
        font_name = str(theme.fontName) if theme.fontName else None
        return ThemeStyle(
            identifier=str(theme.identifier),
            name=str(theme.name),
            banner_hex_color=banner,
            control_hex_color=control,
            background_hex_color=background,
            table_hex_color=table,
            item_name_hex_color=item_name,
            item_details_hex_color=item_details,
            separator_hex_color=separator,
            selection_hex_color=selection,
            navigation_bar_hex_color=(
                str(theme.navigationBarHexColor).removeprefix("#")
                if theme.navigationBarHexColor
                else None
            ),
            cell_hex_color=(
                str(theme.cellHexColor).removeprefix("#") if theme.cellHexColor else None
            ),
            font_name=font_name,
            font_family=self.font_family(font_name),
            table_texture=table_texture,
            table_texture_url=self.texture_url(table_texture) if table_texture else None,
            table_background_size=(
                TEXTURE_BACKGROUND_SIZES.get(table_texture) if table_texture else None
            ),
            background_texture=background_texture,
            background_texture_url=(
                self.texture_url(background_texture) if background_texture else None
            ),
            background_image=str(theme.backgroundImage) if theme.backgroundImage else None,
            cell_texture=str(theme.cellTexture) if theme.cellTexture else None,
            table_background_css=self.table_background_css_property(theme),
            is_dark=self.is_dark_theme(theme),
        )

    def table_background_css_property(self, theme: PBListTheme) -> str:
        """Port ``PBListTheme.tableBackgroundCSSProperty`` with an absolute asset URL."""
        texture = str(theme.tableTexture) if theme.tableTexture else None
        if texture:
            return f'url("{self.texture_url(texture)}")'
        color = str(theme.tableHexColor or theme.backgroundHexColor or "FFFFFF").removeprefix("#")
        return f"#{color.lower()}"

    @staticmethod
    def font_style_for_font_name(font_name: str | None) -> str:
        if font_name in {"Chalkboard SE", "Noteworthy"}:
            return "Casual"
        if font_name == "Courier":
            return "Monospace"
        if font_name == "Iowan Old Style":
            return "Serif"
        return "Default"

    @staticmethod
    def font_name_for_font_style(font_style: str) -> str:
        if font_style == "Casual":
            return "Chalkboard SE"
        if font_style == "Monospace":
            return "Courier"
        if font_style == "Serif":
            return "Iowan Old Style"
        return "ALSystemFont"

    @staticmethod
    def item_name_font_weight(font_name: str | None, *, mac_browser: bool = False) -> int:
        family = VisualsService.font_family(font_name)
        return 500 if mac_browser and family.startswith("system-ui") else 600

    @staticmethod
    def is_dark_theme(theme: PBListTheme) -> bool:
        value = str(theme.navigationBarHexColor or "")
        return bool(value) and _brightness(value) <= 0.7

    @staticmethod
    def font_family(font_name: str | None) -> str:
        if font_name in {"Chalkboard SE", "Noteworthy"}:
            return '"Chalkboard SE", "Comic Sans MS", Chilanka, TSCu_Comic'
        if font_name == "Courier":
            return (
                '"Courier New", "Lucida Sans Typewriter", "Lucida Typewriter", '
                '"Nimbus Mono PS", Courier, monospace'
            )
        if font_name == "Iowan Old Style":
            return (
                '"Palatino", "Palatino Linotype", "Palatino LT STD", "URW Palladio L", P052, serif'
            )
        return 'system-ui, "Avenir", "HelveticaNeue", "Helvetica Neue", sans-serif'
