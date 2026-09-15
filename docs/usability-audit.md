# SDK usability audit

Protocol coverage alone does not make a useful high-level API. This audit tracks places where raw fields or operations still need client-side semantics before applications can use them directly.

The comparison source is the current official AnyList web bundle's protobuf convenience methods,
static-data loaders, and client-side rendering/derived helpers. Native source is used where the
web app has no equivalent behavior. Pure UI chrome, telemetry, lifecycle plumbing, and obsolete integrations are out of scope.

## Visual and asset support

The SDK now handles the following behavior that was previously exposed only as raw fields:

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

The implementation is in `aioanylist.visuals`; no AnyList image binaries are distributed with
the package. See [`visual-assets.md`](visual-assets.md).

## Protobuf semantic helpers

The web bundle also defines semantic helpers on top of fields already present in the protobufs. The SDK implements:

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
- category-assignment map and item-ingredient field/matching helpers;
- recipe/event-aware ingredient quantity scaling and full display-string composition;
- create-if-missing item-price convenience matching the official detached-price behavior;
- legacy `deprecatedQuantity` compatibility, validation, display and modern `PBItemQuantity`
  reconstruction;
- shopping-cell quantity/package display formatting;
- `PBItemPrice` amount/details presence semantics and the remaining small quantity/package
  predicates used by the official clients;
- effective theme CSS background, dark-theme detection and font style/weight helpers;
- folder child/list lookup helpers and effective list/folder sort defaults.

These are local derived helpers; they do not add server operations.

## Remaining ergonomic gaps

The remaining domain-level gap is:

| Area | Missing ergonomic behavior | Reason |
|---|---|---|
| Localized date display | `PBItemIngredient.eventDateDisplayString()` | Depends on AnyList's locale/date-format manager. The raw ISO event date is exposed; exact display parity belongs with a future localization layer. |

No other domain-level gaps were found in the current protobuf helper audit. The remaining prototype methods are service mutations already exposed elsewhere, direct field access, or presentation helpers.

## Kept out of the public API

Some official helpers exist solely to render AnyList Web itself. Examples include attachment
indicator image filenames/sizes (`ALItemIconPrice@2x.png`, toolbar/disclosure images), table-cell
CSS classes, popup geometry, promo/welcome artwork, and browser-specific presentation workarounds.
They are presentation details, not SDK domain behavior.

Recipe-collection sort-order subtitle/name helpers are also omitted. They only map enum values to i18next labels such as `By Name` and `By Rating`; the sort-order behavior itself is already exposed. This web build has English/German text translations while date formatting uses the full app locale, so a localization layer would need to handle those separately.

Likewise, the SDK does not package AnyList's icon/texture/category image binaries. It preserves the
official metadata and URL construction so a downstream UI can request the current resources from
AnyList directly.

## Audit rule going forward

When a new field or operation is added, ask both questions:

1. **Wire parity:** can the SDK read/write the same data or invoke the same useful operation?
2. **Usability parity:** can a downstream integration interpret and use it without independently
   reverse engineering an official AnyList client?

Conformance work should cover both layers when client-side code contains domain semantics. Presentation-only details stay out of scope.
