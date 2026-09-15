# Visual assets and themes

AnyList stores visual selections in protobuf fields such as `PBIcon`, `PBListTheme`,
`PBListSettings.customTheme`, and `PBListSettings.customDarkTheme`, but those wire values are
not sufficient by themselves to build an AnyList-like UI. The official web client also loads
runtime icon catalogs, constructs image URLs, supplies built-in theme definitions and palettes,
and applies fallback/derived rendering rules.

The SDK exposes this through `client.visuals`. Image artwork is not bundled or redistributed; catalog metadata comes from AnyList's public web resources and image helpers return AnyList URLs.

## Official icon catalogs

The current web client loads:

```text
/static/webapp/data/icon_sets_json/emoji_icon_set.json
/static/webapp/data/icon_sets_json/food_and_cooking_icon_set.json
/static/webapp/data/icon_sets_json/classic_recipes_icon_set.json
```

Localized filenames follow the exact web-client rules: emoji uses
`emoji_icon_set_<language>.json`, while food/cooking and classic recipes use
`<name>.<language>.json`.

Each catalog preserves the official group identifier/display name, searchable keywords, and
icon variations. `IconCatalog.unique_entries()` includes variations while deduplicating by icon
name plus tint.

On 2026-09-13 a live read of the English resources returned 26 groups and **3,485 unique icon
names** across the three catalogs. The live web catalog is used instead of a frozen drawable inventory from one Android APK.

```python
from aioanylist import IconContext

catalog = await client.visuals.icon_catalog(IconContext.RECIPE)
for group in catalog.groups:
    for entry in group.icons:
        print(entry.icon_name, entry.keywords, client.visuals.icon_url(entry))
```

Context-specific catalogs match these picker sets:

- shopping lists: nine theme-colored `default_list_icon` entries plus emoji;
- folders: nine colored `default_folder_icon` entries plus emoji;
- recipe collections: food/cooking plus emoji;
- recipes: food/cooking plus classic recipe icons plus emoji;
- meal-plan notes: the default note icon, food/cooking except the Recipes group, plus emoji;
- meal-plan templates: food/cooking plus emoji.

## Asset URL resolution

The web app constructs icon URLs as:

```text
/static/webapp/img/icon_sets/<iconName>.png
```

Textures use:

```text
/static/webapp/img/shopping-list-textures/<texture>@2x.png
```

and shopping-category artwork uses:

```text
/static/webapp/img/shopping-list-category-icons/<category>@2x.png
```

`VisualsService.icon_url()`, `texture_url()`, and `category_icon_url()` return absolute URLs
against the client's configured AnyList host. Both `www.anylist.com` and
`production.anylist.com` serve the static resources.

The package contains identifiers, metadata, and URL resolution code only. Applications fetch the artwork from AnyList.

## Built-in themes and palettes

`client.visuals.built_in_themes()` exposes the exact web definitions for the nine basic color
themes (Aqua, Purple, Blue, Green, Yellow, Orange, Red, Pink, Gray) and the five richer themes
(Classic, Executive, Sticky Note, Nightshade, Wood).

The module also exposes the official custom-theme editor data:

- `BACKGROUND_HEX_COLORS` — 20 background swatches;
- `ACCENT_HEX_COLORS` — 32 accent swatches;
- `FOLDER_HEX_COLORS` — 9 folder colors;
- `TEXTURE_NAMES` / `DARK_TEXTURE_NAMES`;
- `TEXTURE_BACKGROUND_SIZES` — the web client's CSS tile sizes.

`theme_style()` resolves the web defaults for omitted fields, including banner/control,
background/table, item name/details, separator, selection color, font-family mapping, texture
URL, and texture tile size. Raw optional theme fields such as `backgroundImage`, `cellHexColor`,
and `cellTexture` remain available without inventing defaults that the official client does not
define.

The service also exposes these web-client rendering helpers: effective
table-background CSS (texture URL or color), theme darkness from navigation-bar HSV brightness,
font-style/name mapping, and the item-name font-weight rule. `backgroundImage` is left raw: this web build copies the protobuf field through the custom-theme editor, but its actual
`PBListTheme` rendering helpers do not resolve it into an asset URL or use it as the list/table
background. No asset-URL convention is applied to that field.

## Effective list theme and icon

`resolve_list_theme(settings, dark=False)` follows the official selection fallback:

1. explicit `listThemeId` when present;
2. legacy `listColorType` mapped to its built-in theme;
3. Aqua when neither is usable.

When the selected ID is the list's custom theme, `customTheme` is used. In dark mode an explicit
`customDarkTheme` wins. If no explicit dark custom theme exists, the SDK reproduces Android's
native dark conversion, including the known texture substitutions such as
`classic_paper -> noisy_net`, `executive_paper -> dark_dotted`, and `wood -> dark_wood`.

Built-in dark variants likewise follow the Android client: accent colors are mapped to their
night equivalents, text/background/selection fallbacks are adjusted, and Sticky Note is left unchanged to match the native client.

`resolve_list_icon()` returns an explicit stored icon when one exists. Otherwise it synthesizes
`default_list_icon` tinted with the effective theme control color, matching the native fallback.
Folder color/icon and recipe-collection icon fallbacks are exposed as well.

## Caching

When `AnyListClient(cache_dir=...)` is used, downloaded icon JSON is cached under the client's
`visuals/` cache directory with the same 24-hour/stale-on-network-error behavior used by the
official tag-data loader. Image binaries themselves are never cached or packaged by this SDK.
