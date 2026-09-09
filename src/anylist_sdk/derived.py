from __future__ import annotations

import json
from datetime import date
from functools import cmp_to_key, lru_cache
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from google.protobuf.message import Message

from .identifiers import uuid4_hex, uuid5_hex
from .normalization import collapse_whitespace, localized_sort_key, normalized_for_search, remove_diacritics
from .parsing.quantity import (
    abbreviate_units_in_text,
    amount_as_float,
    decimal_to_friendly_fraction,
    normalize_unit,
    normalize_units_in_text,
    pluralize_unit,
    singularize_unit,
    singularize_units_in_text,
    parse_quantity_and_package_size,
    scale_quantity_text,
)
from .proto import PB
from .stemming import stem_words

_SOURCE_COLLECTION_NAMESPACE = UUID(hex="6d86f27f66474ca6a540fcf62af29e59")
_UNKNOWN_SOURCE_COLLECTION_ID = "435af7e2930e455298a53ae96d143b55"
_NOT_IN_COLLECTION_ID = "74267bf441d04dbc9dda96910dd3ba58"


@lru_cache(maxsize=1)
def recipe_source_aliases() -> dict[str, str]:
    path = Path(__file__).with_name("data") / "recipe_source_aliases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def source_domain(recipe: Message) -> str | None:
    value = getattr(recipe, "sourceUrl", "") or ""
    if not value:
        return None
    if not value.startswith("http"):
        value = "http://" + value
    try:
        host = urlparse(value).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def source_display_name(recipe: Message) -> str | None:
    value = (getattr(recipe, "sourceName", "") or "") or source_domain(recipe)
    if not value:
        return None
    if value.startswith("m.") and len(value) > 2:
        value = value[2:]
    return recipe_source_aliases().get(value.lower(), value)


def normalized_source_name(recipe: Message) -> str:
    value = source_display_name(recipe)
    if not value:
        return ""
    value = remove_diacritics(value.lower()).replace("’", "'").replace("&", " and ")
    return collapse_whitespace(value).strip()


def source_collection_identifier(normalized_name: str) -> str:
    if not normalized_name:
        return _UNKNOWN_SOURCE_COLLECTION_ID
    return uuid5_hex(normalized_name, _SOURCE_COLLECTION_NAMESPACE)


def source_smart_collection(
    recipes: list[Message],
    normalized_name: str,
    *,
    unknown_name: str = "Unknown Source",
    saved_settings: Message | None = None,
) -> Message:
    collection_id = source_collection_identifier(normalized_name)
    matching = [r for r in recipes if normalized_source_name(r) == normalized_name]
    display = source_display_name(matching[0]) if matching else None
    collection = PB.PBRecipeCollection(identifier=collection_id, name=display or unknown_name)
    collection.recipeIds.extend(r.identifier for r in matching)
    settings = PB.PBRecipeCollectionSettings()
    smart = PB.PBSmartFilter(identifier=collection_id, name=collection.name)
    condition = smart.conditions.add()
    # The generated protobuf uses the schema's literal ID capitalization even though the
    # JavaScript accessor layer exposes these as fieldId/operatorId.
    condition.fieldID = "normalized-recipe-source-name"
    condition.operatorID = "is-equal-to"
    condition.value = normalized_name
    settings.smartFilter.CopyFrom(smart)
    if saved_settings is not None:
        settings.recipesSortOrder = int(saved_settings.recipesSortOrder)
        settings.useReversedSortDirection = bool(saved_settings.useReversedSortDirection)
    else:
        # Official web defaults source smart collections to Date Created sort order.
        settings.recipesSortOrder = PB.PBRecipeCollectionSettings.SortOrder.DateCreatedSortOrder
    collection.collectionSettings.CopyFrom(settings)
    return collection


def source_smart_collections(
    recipes: list[Message], *, saved_settings: dict[str, Message] | None = None
) -> list[Message]:
    # eh.hh builds a JS object while walking recipes in manager order, then returns its
    # values. UUID-like keys retain insertion order, so source buckets are first-seen order.
    names = list(dict.fromkeys(normalized_source_name(r) for r in recipes))
    out: list[Message] = []
    for name in names:
        identifier = source_collection_identifier(name)
        out.append(
            source_smart_collection(
                recipes,
                name,
                saved_settings=(saved_settings or {}).get(identifier),
            )
        )
    return out


def recipes_not_in_collection(recipes: list[Message], collections: list[Message]) -> list[Message]:
    assigned: set[str] = set()
    for collection in collections:
        # Ignore client-derived smart collections when computing the synthetic bucket.
        if collection.HasField("collectionSettings") and collection.collectionSettings.HasField("smartFilter"):
            continue
        assigned.update(collection.recipeIds)
    return [recipe for recipe in recipes if recipe.identifier not in assigned]


def not_in_collection_smart_collection(
    recipes: list[Message],
    collections: list[Message],
    *,
    name: str = "Not in a Collection",
    saved_settings: Message | None = None,
) -> Message:
    """Build the synthetic collection produced by AnyList Web's ``eh.fh`` helper."""
    collection = PB.PBRecipeCollection(identifier=_NOT_IN_COLLECTION_ID, name=name)
    collection.recipeIds.extend(
        recipe.identifier for recipe in recipes_not_in_collection(recipes, collections)
    )
    smart = PB.PBSmartFilter(identifier=_NOT_IN_COLLECTION_ID, name=name)
    condition = smart.conditions.add()
    condition.fieldID = "recipes-not-in-a-collection"
    settings = PB.PBRecipeCollectionSettings()
    settings.smartFilter.CopyFrom(smart)
    if saved_settings is not None:
        settings.recipesSortOrder = int(saved_settings.recipesSortOrder)
        settings.useReversedSortDirection = bool(saved_settings.useReversedSortDirection)
    else:
        settings.recipesSortOrder = PB.PBRecipeCollectionSettings.SortOrder.AlphabeticalSortOrder
    collection.collectionSettings.CopyFrom(settings)
    return collection


def duplicate_recipe_ids(collection: Message) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for recipe_id in collection.recipeIds:
        # PBRecipeCollection.findDuplicateRecipeIDs appends on every occurrence after the
        # first; it does not uniquify the duplicate report.
        if recipe_id in seen:
            dupes.append(recipe_id)
        seen.add(recipe_id)
    return dupes





def _recipe_name_compare(a: Message, b: Message) -> int:
    ka = localized_sort_key((getattr(a, "name", "") or "").lower())
    kb = localized_sort_key((getattr(b, "name", "") or "").lower())
    return -1 if ka < kb else 1 if ka > kb else 0


def _meal_history_for_recipe(
    recipe_id: str, events: list[Message] | tuple[Message, ...], *, today: str
) -> tuple[str, int]:
    """Return AnyList Web's last-prepared date and prepared count for a recipe."""
    calendar_type = PB.PBCalendarEventType.MealPlanCalendarEvent
    dates: list[str] = []
    for event in events:
        if (getattr(event, "recipeId", "") or "") != recipe_id:
            continue
        # The official index includes legacy events where eventType was absent alongside
        # explicit MealPlanCalendarEvent values.
        has_type = event.HasField("eventType") if "eventType" in event.DESCRIPTOR.fields_by_name else False
        if has_type and int(event.eventType) != int(calendar_type):
            continue
        event_date = getattr(event, "date", "") or ""
        if event_date and event_date <= today:
            dates.append(event_date)
    return (max(dates) if dates else "", len(dates))


def sort_recipes(
    recipes: list[Message] | tuple[Message, ...],
    settings: Message | None,
    *,
    meal_plan_events: list[Message] | tuple[Message, ...] = (),
    today: str | None = None,
) -> list[Message]:
    """Sort recipes with the exact ordering rules used by AnyList Web.

    Manual order is preserved. For Date Prepared and Times Prepared the web client derives
    values from meal-plan calendar history, so callers can provide the synchronized events.
    """
    values = list(recipes)
    if settings is None:
        return values
    order = int(getattr(settings, "recipesSortOrder", 0) or 0)
    reversed_direction = bool(getattr(settings, "useReversedSortDirection", False))
    enum = PB.PBRecipeCollectionSettings.SortOrder
    if order == int(enum.ManualSortOrder):
        return values

    today_value = today or date.today().isoformat()
    history_cache: dict[str, tuple[str, int]] = {}

    def history(recipe: Message) -> tuple[str, int]:
        rid = str(getattr(recipe, "identifier", "") or "")
        if rid not in history_cache:
            history_cache[rid] = _meal_history_for_recipe(
                rid, meal_plan_events, today=today_value
            )
        return history_cache[rid]

    def cmp(a: Message, b: Message) -> int:
        name_cmp = _recipe_name_compare(a, b)
        if order == int(enum.AlphabeticalSortOrder):
            return -name_cmp if reversed_direction else name_cmp
        if order == int(enum.RatingSortOrder):
            av = float(getattr(a, "rating", 0.0) or 0.0)
            bv = float(getattr(b, "rating", 0.0) or 0.0)
            if av != bv:
                result = -1 if av > bv else 1
                return -result if reversed_direction else result
            return name_cmp
        if order == int(enum.DateCreatedSortOrder):
            av = float(getattr(a, "creationTimestamp", 0.0) or 0.0)
            bv = float(getattr(b, "creationTimestamp", 0.0) or 0.0)
            if av > 0 or bv > 0:
                result = -1 if av > bv else 1 if av < bv else 0
                return -result if reversed_direction else result
            return name_cmp
        if order in (int(enum.PrepTimeSortOrder), int(enum.CookTimeSortOrder)):
            field = "prepTime" if order == int(enum.PrepTimeSortOrder) else "cookTime"
            av = float(getattr(a, field, 0.0) or 0.0)
            bv = float(getattr(b, field, 0.0) or 0.0)
            ah, bh = av > 0, bv > 0
            if ah and bh and av != bv:
                result = -1 if av < bv else 1
                return -result if reversed_direction else result
            if ah != bh:
                result = -1 if ah else 1
                return -result if reversed_direction else result
            return name_cmp
        if order == int(enum.DatePreparedSortOrder):
            prepared_a = history(a)[0]
            prepared_b = history(b)[0]
            ah, bh = bool(prepared_a), bool(prepared_b)
            if ah and bh and prepared_a != prepared_b:
                result = -1 if prepared_a > prepared_b else 1
                return -result if reversed_direction else result
            if ah != bh:
                result = -1 if ah else 1
                return -result if reversed_direction else result
            return name_cmp
        if order == int(enum.TimesPreparedSortOrder):
            count_a = history(a)[1]
            count_b = history(b)[1]
            if count_a != count_b:
                result = -1 if count_a > count_b else 1
                return -result if reversed_direction else result
            return name_cmp
        return 0

    values.sort(key=cmp_to_key(cmp))
    return values


def effective_recipe_scale_factor(recipe: Message | None) -> float:
    if recipe is None:
        return 1.0
    value = float(getattr(recipe, "scaleFactor", 0.0) or 0.0)
    return value or 1.0


def effective_event_scale_factor(event: Message | None) -> float:
    if event is None:
        return 1.0
    value = float(getattr(event, "recipeScaleFactor", 0.0) or 0.0)
    return value or 1.0


def full_ingredient_string(ingredient: Message, *, quantity: str | None = None) -> str:
    amount = (getattr(ingredient, "quantity", "") or "") if quantity is None else quantity
    name = getattr(ingredient, "name", "") or ""
    note = getattr(ingredient, "note", "") or ""
    value = amount
    if amount and name:
        value += " "
    value += name
    if value and note:
        value += ", "
    value += note
    return value


def ingredient_to_item_ingredient(
    ingredient: Message, recipe: Message, event: Message | None = None
) -> Message:
    """Port ``PBIngredient.toItemIngredientWithRecipeAndEvent`` from AnyList Web.

    This is intentionally protobuf-in/protobuf-out: the provenance object is part of the
    official wire/state model and is subsequently used to derive the deterministic list
    item identifier.
    """
    out = PB.PBItemIngredient(
        recipeId=str(recipe.identifier),
        recipeName=str(getattr(recipe, "name", "") or ""),
    )
    if event is not None:
        out.eventId = str(getattr(event, "identifier", "") or "")
        out.eventDate = str(getattr(event, "date", "") or "")

    factor = effective_event_scale_factor(event) if event is not None else effective_recipe_scale_factor(recipe)
    original_quantity = getattr(ingredient, "quantity", "") or ""

    if factor == 1:
        out.ingredient.CopyFrom(ingredient)
        parsed = parse_quantity_and_package_size(original_quantity)
    else:
        source = ingredient.__class__()
        source.CopyFrom(ingredient)
        parsed_before = parse_quantity_and_package_size(original_quantity)
        # AnyList has a package-only scaling special case: if parsing yields a package
        # size but no numeric quantity, synthesize a count of one before scaling.
        if (
            parsed_before is not None
            and (not parsed_before.HasField("quantityPb") or not parsed_before.quantityPb.amount)
            and parsed_before.HasField("packageSizePb")
            and parsed_before.packageSizePb.rawPackageSize
        ):
            source.quantity = f"1 {source.quantity}"
            source.rawIngredient = f"1 × {source.rawIngredient}"
        scaled = scale_quantity_text(source.quantity, factor)
        scaled_ingredient = source.__class__()
        scaled_ingredient.CopyFrom(source)
        scaled_ingredient.quantity = scaled
        scaled_ingredient.rawIngredient = full_ingredient_string(source, quantity=scaled)
        out.ingredient.CopyFrom(scaled_ingredient)
        parsed = parse_quantity_and_package_size(scaled)

    if parsed is not None:
        if parsed.HasField("quantityPb"):
            out.quantityPb.CopyFrom(parsed.quantityPb)
        if parsed.HasField("packageSizePb"):
            out.packageSizePb.CopyFrom(parsed.packageSizePb)

    # The official client singularizes package fields before storing provenance.
    if out.HasField("packageSizePb") and any(
        getattr(out.packageSizePb, field, "")
        for field in ("size", "unit", "packageType", "rawPackageSize")
    ):
        package = PB.PBItemPackageSize()
        package.CopyFrom(out.packageSizePb)
        package.unit = singularize_units_in_text(package.unit)
        package.packageType = singularize_units_in_text(package.packageType)
        package.rawPackageSize = singularize_units_in_text(package.rawPackageSize)
        out.packageSizePb.CopyFrom(package)
    return out

def normalized_raw_package_size(package: Message | None) -> str:
    if package is None:
        return ""
    raw = getattr(package, "rawPackageSize", "") or ""
    if not raw:
        return ""
    # PBItemPackageSize.normalizedRawPackageSize reparses rawPackageSize with sP, applies
    # aP to only the parsed prefix, then appends the untouched remainder. It does not use the
    # structured size/unit/packageType fields for this derived string.
    from .parsing.ingredient import split_quantity_prefix

    prefix, remainder = split_quantity_prefix(raw)
    if not prefix:
        return raw
    normalized = normalize_units_in_text(prefix)
    return f"{normalized} {remainder}".strip() if remainder else normalized


def recipe_list_item_identifier(item_ingredient: Message, list_id: str) -> str:
    package = item_ingredient.packageSizePb if item_ingredient.HasField("packageSizePb") else PB.PBItemPackageSize()
    quantity = item_ingredient.quantityPb if item_ingredient.HasField("quantityPb") else PB.PBItemQuantity()
    ingredient = item_ingredient.ingredient if item_ingredient.HasField("ingredient") else PB.PBIngredient()
    package_key = normalized_raw_package_size(package).lower()
    unit_key = normalize_unit(quantity.unit or "").lower()
    name = (ingredient.name or "").lower()
    stemmed = " ".join(stem_words(name.split(" ")))
    seed = f"ALName::{stemmed}::ALQuantityUnit::{unit_key}::ALPackageSize::{package_key}"
    return uuid5_hex(seed, list_id)


def same_recipe_ingredient(a: Message, b: Message) -> bool:
    a_ing = a.ingredient.identifier if a.HasField("ingredient") else ""
    b_ing = b.ingredient.identifier if b.HasField("ingredient") else ""
    return (
        a_ing == b_ing
        and (getattr(a, "recipeId", "") or "") == (getattr(b, "recipeId", "") or "")
        and (getattr(a, "eventId", "") or "") == (getattr(b, "eventId", "") or "")
    )


def add_item_ingredient(item: Message, ingredient: Message) -> None:
    for index, existing in enumerate(item.ingredients):
        if same_recipe_ingredient(existing, ingredient):
            item.ingredients[index].CopyFrom(ingredient)
            return
    item.ingredients.add().CopyFrom(ingredient)


def remove_item_ingredient(item: Message, ingredient: Message) -> bool:
    for index, existing in enumerate(item.ingredients):
        if same_recipe_ingredient(existing, ingredient):
            del item.ingredients[index]
            return True
    return False


def item_quantity(item: Message) -> Message:
    return item.quantityPb if item.HasField("quantityPb") else PB.PBItemQuantity()


def ingredient_package_size(item: Message) -> Message:
    if item.ingredients and item.ingredients[0].HasField("packageSizePb"):
        return item.ingredients[0].packageSizePb
    return PB.PBItemPackageSize()


def total_ingredient_quantity(item: Message) -> Message | None:
    if not item.ingredients:
        return None
    out = PB.PBItemQuantity()
    total = 0.0
    for source in item.ingredients:
        q = source.quantityPb if source.HasField("quantityPb") else PB.PBItemQuantity()
        amount = amount_as_float(q.amount or "")
        if amount:
            total += amount
        elif source.HasField("packageSizePb") and any(
            getattr(source.packageSizePb, field, "") for field in ("size", "unit", "packageType", "rawPackageSize")
        ) and len(item.ingredients) > 1:
            total += 1
    if total > 0:
        out.amount = decimal_to_friendly_fraction(total, unicode=True)
    first_q = item.ingredients[0].quantityPb if item.ingredients[0].HasField("quantityPb") else PB.PBItemQuantity()
    if first_q.unit:
        abbreviated = abbreviate_units_in_text(first_q.unit)
        out.unit = singularize_unit(abbreviated) if total == 1 else pluralize_unit(abbreviated)
    package_empty = not any(
        getattr(ingredient_package_size(item), f, "") for f in ("size", "unit", "packageType", "rawPackageSize")
    )
    if len(item.ingredients) == 1 and package_empty:
        out.rawQuantity = item.ingredients[0].ingredient.quantity if item.ingredients[0].HasField("ingredient") else ""
    elif not out.amount and package_empty:
        first = (item.ingredients[0].ingredient.quantity if item.ingredients[0].HasField("ingredient") else "") or ""
        if all(((x.ingredient.quantity if x.HasField("ingredient") else "") or "").lower() == first.lower() for x in item.ingredients):
            out.rawQuantity = first
    else:
        raw = out.amount or ""
        if raw and out.unit:
            raw += " " + out.unit
        if raw:
            out.rawQuantity = raw
    return out


def list_quantity(item: Message) -> Message:
    if item.ingredients and not bool(getattr(item, "itemQuantityShouldOverrideIngredientQuantity", False)):
        return total_ingredient_quantity(item) or PB.PBItemQuantity()
    return item_quantity(item)


def active_quantity_for_total_cost(item: Message) -> Message:
    if bool(getattr(item, "priceQuantityShouldOverrideItemQuantity", False)):
        return item.priceQuantityPb if item.HasField("priceQuantityPb") else PB.PBItemQuantity()
    return list_quantity(item)


def total_cost(item: Message, price: Message) -> float:
    amount = float(getattr(price, "amount", 0.0) or 0.0)
    quantity = active_quantity_for_total_cost(item)
    multiplier = amount_as_float(quantity.amount or "") if quantity.amount else 1.0
    return amount * multiplier


def active_package_size(item: Message) -> Message:
    if bool(getattr(item, "pricePackageSizeShouldOverrideItemPackageSize", False)):
        return item.pricePackageSizePb if item.HasField("pricePackageSizePb") else PB.PBItemPackageSize()
    return item.packageSizePb if item.HasField("packageSizePb") else PB.PBItemPackageSize()


def unit_price(item: Message, price: Message) -> float:
    amount = float(getattr(price, "amount", 0.0) or 0.0)
    package = active_package_size(item)
    divisor = amount_as_float(package.size or "") if package.size else 0.0
    if divisor == 0:
        divisor = 1.0
    return amount / divisor


def display_quantity_and_package_size(quantity: Message | None, package: Message | None) -> str:
    """Plain-text form used by AnyList when a meal-plan list item becomes an ingredient."""
    q = (getattr(quantity, "rawQuantity", "") or "") if quantity is not None else ""
    p = (getattr(package, "rawPackageSize", "") or "") if package is not None else ""
    if q and p:
        return f"{q} × {p}"
    return q or p


def event_list_item_to_item_ingredient(item: Message, event: Message) -> Message:
    """Port PBCalendarEventListItem.toItemIngredientWithEvent from AnyList Web."""
    quantity = item.quantityPb if item.HasField("quantityPb") else PB.PBItemQuantity()
    package = item.packageSizePb if item.HasField("packageSizePb") else PB.PBItemPackageSize()
    display = display_quantity_and_package_size(quantity, package)
    name = getattr(item, "name", "") or ""
    details = getattr(item, "details", "") or ""
    raw = display
    if raw and name:
        raw += " "
    raw += name
    if raw and details:
        raw += ", "
    raw += details
    ingredient = PB.PBIngredient(
        identifier=str(item.identifier),
        rawIngredient=raw.strip(),
        quantity=display,
        name=name,
        note=details,
    )
    out = PB.PBItemIngredient(
        recipeName=str(getattr(event, "title", "") or ""),
        eventId=str(event.identifier),
        eventDate=str(getattr(event, "date", "") or ""),
    )
    out.ingredient.CopyFrom(ingredient)
    if item.HasField("quantityPb"):
        out.quantityPb.CopyFrom(item.quantityPb)
    if item.HasField("packageSizePb"):
        out.packageSizePb.CopyFrom(item.packageSizePb)
    return out


def recipe_servings_after_scaling(recipe: Message, event: Message | None = None) -> str:
    """Port PBRecipe.servingsAfterScalingForEvent."""
    servings = str(getattr(recipe, "servings", "") or "")
    if not servings:
        return ""
    factor = (
        effective_event_scale_factor(event)
        if event is not None
        else effective_recipe_scale_factor(recipe)
    )
    if factor == 1:
        return servings
    # The web client preserves a textual prefix before the first digit (for example
    # "Serves 4") and scales only the suffix beginning with that first digit.
    first_digit = next((i for i, ch in enumerate(servings) if ch.isdigit()), -1)
    suffix = servings[first_digit:] if first_digit > 0 else servings
    scaled = scale_quantity_text(suffix, factor)
    return servings[:first_digit] + scaled if first_digit > 0 else scaled


def recipe_ingredients_excluding_headings(recipe: Message, enabled: bool = True) -> list[Message] | None:
    """Port PBRecipe.ingredientsExcludingHeadings's intentionally gated behavior."""
    if not enabled:
        return None
    return [ingredient for ingredient in recipe.ingredients if not ingredient.isHeading]


def is_recipe_heading(value: str) -> bool:
    return value.startswith("# ")


def recipe_heading_text(value: str) -> str:
    return value[2:] if is_recipe_heading(value) else value


def recipe_prep_steps_excluding_headings(recipe: Message, enabled: bool = True) -> list[str] | None:
    if not enabled:
        return None
    return [step for step in recipe.preparationSteps if not is_recipe_heading(step)]


def duplicate_ingredient(ingredient: Message) -> Message:
    """Duplicate an ingredient exactly like PBIngredient.duplicateIngredient."""
    out = PB.PBIngredient(identifier=uuid4_hex())
    for field in ("rawIngredient", "name", "quantity", "note"):
        value = getattr(ingredient, field, "") or ""
        if value:
            setattr(out, field, value)
    if bool(getattr(ingredient, "isHeading", False)):
        out.isHeading = True
    return out


def duplicate_recipe(recipe: Message) -> Message:
    """Port PBRecipe.duplicateRecipe; server-owned/derived fields are deliberately omitted."""
    out = PB.PBRecipe(identifier=uuid4_hex())
    for field in ("name", "icon", "note", "sourceName", "sourceUrl"):
        value = getattr(recipe, field, "") or ""
        if value:
            setattr(out, field, value)
    for ingredient in recipe.ingredients:
        out.ingredients.add().CopyFrom(duplicate_ingredient(ingredient))
    if recipe.preparationSteps:
        out.preparationSteps.extend(recipe.preparationSteps)
    if recipe.photoIds:
        out.photoIds.extend(recipe.photoIds)
    for field in ("scaleFactor", "rating", "nutritionalInfo", "cookTime", "prepTime", "servings"):
        value = getattr(recipe, field)
        if value:
            setattr(out, field, value)
    return out


def cooking_states_equal(a: Message, b: Message | None) -> bool:
    if b is None:
        return False
    return (
        (getattr(a, "recipeId", "") or "") == (getattr(b, "recipeId", "") or "")
        and (getattr(a, "eventId", "") or "") == (getattr(b, "eventId", "") or "")
        and float(getattr(a, "lastOpenedTimestamp", 0) or 0)
        == float(getattr(b, "lastOpenedTimestamp", 0) or 0)
        and int(getattr(a, "selectedTabId", 0) or 0) == int(getattr(b, "selectedTabId", 0) or 0)
        and list(a.checkedIngredientIds) == list(b.checkedIngredientIds)
        and int(getattr(a, "selectedStepNumber", 0) or 0)
        == int(getattr(b, "selectedStepNumber", 0) or 0)
    )


def icons_equal(a: Message, b: Message | None) -> bool:
    if b is None:
        return False
    return (getattr(a, "iconName", "") or "") == (getattr(b, "iconName", "") or "") and (
        getattr(a, "tintHexColor", "") or ""
    ) == (getattr(b, "tintHexColor", "") or "")


def icon_resource_path(icon: Message) -> str:
    return f"icon_sets/{getattr(icon, 'iconName', '')}.png"


def calendar_event_descriptor(event_id: str, event_type: int) -> Message:
    return PB.PBCalendarEventDescriptor(eventId=event_id, eventType=event_type)


def descriptor_for_calendar_event(event_id: str) -> Message:
    return calendar_event_descriptor(event_id, PB.PBCalendarEventType.MealPlanCalendarEvent)


def descriptor_for_queue_event(event_id: str) -> Message:
    return calendar_event_descriptor(event_id, PB.PBCalendarEventType.MealPlanQueueEvent)


def descriptor_for_favorite_event(event_id: str) -> Message:
    return calendar_event_descriptor(event_id, PB.PBCalendarEventType.MealPlanFavoriteEvent)


def descriptor_for_template_event(event_id: str) -> Message:
    return calendar_event_descriptor(event_id, PB.PBCalendarEventType.MealPlanTemplateEvent)


def event_descriptor(event: Message) -> Message:
    return calendar_event_descriptor(str(event.identifier), int(event.eventType))


def descriptors_equal(a: Message, b: Message | None) -> bool:
    return b is not None and a.eventId == b.eventId and int(a.eventType) == int(b.eventType)


def descriptor_is_calendar_event(descriptor: Message) -> bool:
    return int(descriptor.eventType) == int(PB.PBCalendarEventType.MealPlanCalendarEvent)


def descriptor_is_queue_event(descriptor: Message) -> bool:
    return int(descriptor.eventType) == int(PB.PBCalendarEventType.MealPlanQueueEvent)


def descriptor_is_favorite_event(descriptor: Message) -> bool:
    return int(descriptor.eventType) == int(PB.PBCalendarEventType.MealPlanFavoriteEvent)


def descriptor_is_template_event(descriptor: Message) -> bool:
    return int(descriptor.eventType) == int(PB.PBCalendarEventType.MealPlanTemplateEvent)


def template_group_item_for_template(template_id: str) -> Message:
    return PB.PBMealPlanTemplateGroupItem(
        identifier=template_id, itemType=PB.PBMealPlanTemplateGroupItem.Type.Template
    )


def template_group_item_for_group(group_id: str) -> Message:
    return PB.PBMealPlanTemplateGroupItem(
        identifier=group_id, itemType=PB.PBMealPlanTemplateGroupItem.Type.Group
    )


def template_group_items_equal(a: Message, b: Message) -> bool:
    return int(a.itemType) == int(b.itemType) and a.identifier == b.identifier


def event_list_items_equal(a: Message, b: Message, *, ignore_identifier: bool = False, normalized: bool = False) -> bool:
    if not ignore_identifier and a.identifier != b.identifier:
        return False
    a_name = a.name or ""
    b_name = b.name or ""
    a_details = a.details or ""
    b_details = b.details or ""
    if normalized:
        a_name = normalized_for_search(a_name).strip()
        b_name = normalized_for_search(b_name).strip()
        a_details = normalized_for_search(a_details).strip()
        b_details = normalized_for_search(b_details).strip()
    if a_name != b_name or a_details != b_details:
        return False
    aq = a.quantityPb if a.HasField("quantityPb") else PB.PBItemQuantity()
    bq = b.quantityPb if b.HasField("quantityPb") else PB.PBItemQuantity()
    ap = a.packageSizePb if a.HasField("packageSizePb") else PB.PBItemPackageSize()
    bp = b.packageSizePb if b.HasField("packageSizePb") else PB.PBItemPackageSize()
    from .item_semantics import package_size_equal, quantity_equal
    return quantity_equal(aq, bq) and package_size_equal(ap, bp)


def event_list_item_arrays_equal(a, b, *, ignore_identifier: bool = False) -> bool:
    left = list(a or ())
    right = list(b or ())
    if len(left) != len(right):
        return False
    # The static web helper passes its third argument as isEqualToItem's *second* argument,
    # i.e. the ignore-identifier flag. It does not expose normalized comparison here.
    return all(
        event_list_items_equal(x, y, ignore_identifier=ignore_identifier)
        for x, y in zip(left, right)
    )
