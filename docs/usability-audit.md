# SDK usability audit

Protocol completeness is not the same as a complete SDK. A protobuf field or low-level operation
can technically expose data while still forcing every downstream application to reverse engineer
the official clients to interpret it. This audit tracks those gaps separately from endpoint
coverage.

The comparison source is the current official AnyList web bundle's protobuf convenience methods,
static-data loaders, and client-side rendering/derived helpers. Native source is used where the
web app has no equivalent behavior. Pure UI chrome, telemetry, lifecycle plumbing, and obsolete
integrations are intentionally excluded.

## Closed in the visual-assets audit

The following previously existed only as raw protobuf/string fields and are now first-class SDK
behavior:

- current official icon-catalog fetching, grouping, keywords, emoji variations, caching, and
  searching;
- context-correct icon pickers for lists, folders, recipe collections, recipes, meal-plan notes,
  and templates;
- canonical AnyList URLs for icon PNGs, list textures, and shopping-category icons;
- all built-in list themes, theme IDs, official color palettes, folder colors, texture names and
  CSS tile sizes;
- effective theme defaults and font-family mapping;
- selected theme fallback from `listThemeId` / legacy `listColorType`;
- explicit custom light/dark themes plus native dark-theme derivation;
- effective list icon fallback and tint;
- folder effective color/icon fallback;
- recipe-collection default/valid-icon behavior.

The implementation is in `anylist_sdk.visuals`; no AnyList image binaries are distributed with
the package. See [`visual-assets.md`](visual-assets.md).

## Closed in the same audit: protobuf convenience behavior

The official web bundle also exposed small but useful semantic helpers that were easy to miss
because all of their raw fields were already present. The SDK now ports:

- account `fullName` composition and shared-user display-name fallback;
- recipe first-photo ID / first-photo URL;
- effective recipe-collection sort-order default;
- list-item category/event/first-photo helpers;
- list-item `hasPhoto`, `hasStore`, `hasPrice`, and ingredient-item predicates;
- price lookup for a store and the official single-price-store inference rule;
- AnyList Web's locale/settings-driven item-price and unit-price display strings;
- localized store-name display joining;
- state-aware recipe attachment detection (`hasRecipe`);
- ingredient grocery-tag classification convenience;
- category-group category resolution with synchronized list/category context;
- folder child/list lookup helpers and effective list/folder sort defaults.

These are local derived helpers; they do not add server operations.

## Remaining high-value ergonomic gaps

These are real official behaviors worth considering next, but they need more than a trivial field
wrapper:

| Area | Missing ergonomic behavior | Why it is not blindly ported yet |
|---|---|---|
| Localized date display | `PBItemIngredient.eventDateDisplayString()` | Depends on AnyList's locale/date-format manager. The raw ISO event date is exposed; exact display parity belongs with a future localization layer. |
| Sort-order subtitles | Collection settings subtitle/display strings | These are localized UI labels, not data semantics. Useful for a UI helper layer, but lower priority than actual behavior. |

## Intentionally not promoted

Some official helpers exist solely to render AnyList Web itself. Examples include attachment
indicator image filenames/sizes (`ALItemIconPrice@2x.png`, toolbar/disclosure images), table-cell
CSS classes, popup geometry, promo/welcome artwork, and browser-specific presentation workarounds.
Those are not missing SDK functionality merely because they are present in `app.js`.

Likewise, the SDK does not package AnyList's icon/texture/category image binaries. It preserves the
official metadata and URL construction so a downstream UI can request the current resources from
AnyList directly.

## Audit rule going forward

When a new field or operation is added, ask both questions:

1. **Wire parity:** can the SDK read/write the same data or invoke the same useful operation?
2. **Usability parity:** can a downstream integration interpret and use it without independently
   reverse engineering an official AnyList client?

Conformance work should close both when the second layer contains real domain semantics. It should
not copy presentation-only implementation details merely to increase an API surface count.
