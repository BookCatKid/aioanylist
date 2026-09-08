from __future__ import annotations

from google.protobuf.message import Message

from .normalization import localized_sort_key
from .proto import PB
from .parsing.quantity import amount_as_float, decimal_to_friendly_fraction

# Bit mask values used by AnyList Web's ListItem.isEqualToItemExcludingFields and
# applyPropertiesFromListItem. Keeping the native values makes it possible to port call
# sites without translating their masks.
EXCLUDE_NAME = 1
EXCLUDE_ITEM_QUANTITY = 2
EXCLUDE_DETAILS = 4
EXCLUDE_PHOTOS = 8
EXCLUDE_STORES = 16
EXCLUDE_RECIPE_ID = 32
EXCLUDE_EVENT_ID = 64
EXCLUDE_PRICES = 128
EXCLUDE_PACKAGE_SIZE = 256
EXCLUDE_INGREDIENTS = 512
EXCLUDE_PRICE_QUANTITY = 1024
EXCLUDE_PRICE_PACKAGE_SIZE = 2048
EXCLUDE_PRODUCT_UPC = 4096


def _localized_equal(a: str | None, b: str | None) -> bool:
    return localized_sort_key(a or "") == localized_sort_key(b or "")


def _array_matches(a, b, comparator=None) -> bool:
    """AnyList's arrayMatchesArray: same length and every left value occurs on right."""
    left = list(a or ())
    right = list(b or ())
    if len(left) != len(right):
        return False
    if comparator is None:
        return all(value in right for value in left)
    return all(any(comparator(value, candidate) for candidate in right) for value in left)


def quantity_equal(a: Message | None, b: Message | None) -> bool:
    if a is None or b is None:
        return False
    return all((getattr(a, f, "") or "") == (getattr(b, f, "") or "") for f in ("amount", "unit", "rawQuantity"))


def package_size_equal(a: Message | None, b: Message | None) -> bool:
    if a is None or b is None:
        return False
    return all(
        (getattr(a, f, "") or "") == (getattr(b, f, "") or "")
        for f in ("size", "unit", "packageType", "rawPackageSize")
    )


def _quantity(item: Message, field: str) -> Message:
    return getattr(item, field) if item.HasField(field) else PB.PBItemQuantity()


def _package(item: Message, field: str) -> Message:
    return getattr(item, field) if item.HasField(field) else PB.PBItemPackageSize()


def price_empty(price: Message) -> bool:
    has_amount = price.HasField("amount") if "amount" in price.DESCRIPTOR.fields_by_name else bool(getattr(price, "amount", 0))
    return not has_amount and not (getattr(price, "details", "") or "")


def price_equal(a: Message, b: Message) -> bool:
    a_has = a.HasField("amount") if "amount" in a.DESCRIPTOR.fields_by_name else True
    b_has = b.HasField("amount") if "amount" in b.DESCRIPTOR.fields_by_name else True
    if a_has != b_has:
        return False
    if a_has and float(a.amount) != float(b.amount):
        return False
    return (a.details or "") == (b.details or "") and (a.storeId or "") == (b.storeId or "")


def prices_match(a, b) -> bool:
    left = [p for p in (a or ()) if not price_empty(p)]
    right = [p for p in (b or ()) if not price_empty(p)]
    return _array_matches(left, right, price_equal)


def ingredient_equal(a: Message, b: Message, *, ignore_identifier: bool = False) -> bool:
    for field in ("rawIngredient", "quantity", "name", "note"):
        if (getattr(a, field, "") or "") != (getattr(b, field, "") or ""):
            return False
    if bool(getattr(a, "isHeading", False)) != bool(getattr(b, "isHeading", False)):
        return False
    return ignore_identifier or (getattr(a, "identifier", "") or "") == (getattr(b, "identifier", "") or "")


def same_recipe_ingredient(a: Message, b: Message) -> bool:
    a_id = a.ingredient.identifier if a.HasField("ingredient") else ""
    b_id = b.ingredient.identifier if b.HasField("ingredient") else ""
    return (
        a_id == b_id
        and (getattr(a, "recipeId", "") or "") == (getattr(b, "recipeId", "") or "")
        and (getattr(a, "eventId", "") or "") == (getattr(b, "eventId", "") or "")
    )


def item_ingredient_identical(a: Message, b: Message) -> bool:
    if not same_recipe_ingredient(a, b):
        return False
    a_ing = a.ingredient if a.HasField("ingredient") else PB.PBIngredient()
    b_ing = b.ingredient if b.HasField("ingredient") else PB.PBIngredient()
    if not ingredient_equal(a_ing, b_ing, ignore_identifier=True):
        return False
    if not quantity_equal(_quantity(a, "quantityPb"), _quantity(b, "quantityPb")):
        return False
    if not package_size_equal(_package(a, "packageSizePb"), _package(b, "packageSizePb")):
        return False
    return (
        (getattr(a, "recipeName", "") or "") == (getattr(b, "recipeName", "") or "")
        and (getattr(a, "eventDate", "") or "") == (getattr(b, "eventDate", "") or "")
    )


def item_ingredients_identical(a, b) -> bool:
    left = list(a or ())
    remaining = list(b or ())
    if len(left) != len(remaining):
        return False
    for source in left:
        for index, candidate in enumerate(remaining):
            if item_ingredient_identical(source, candidate):
                del remaining[index]
                break
        else:
            return False
    return True


def item_hash(item: Message, excluding_fields: int = 0) -> int | float:
    if excluding_fields & EXCLUDE_NAME:
        return float("1.7976931348623157e+308")  # Number.MAX_VALUE
    value = (getattr(item, "name", "") or "").lower()
    result = 0
    for ch in value:
        result = ((result << 5) - result + ord(ch)) & 0xFFFFFFFF
        if result & 0x80000000:
            result -= 0x100000000
    return result


def items_equal(a: Message, b: Message, excluding_fields: int = 0) -> bool:
    if not (excluding_fields & EXCLUDE_NAME) and not _localized_equal(a.name or "", b.name or ""):
        return False
    if not (excluding_fields & EXCLUDE_ITEM_QUANTITY):
        if not quantity_equal(_quantity(a, "quantityPb"), _quantity(b, "quantityPb")):
            return False
        if not quantity_equal(_quantity(a, "priceQuantityPb"), _quantity(b, "priceQuantityPb")):
            return False
        if bool(getattr(a, "priceQuantityShouldOverrideItemQuantity", False)) != bool(getattr(b, "priceQuantityShouldOverrideItemQuantity", False)):
            return False
        if bool(getattr(a, "itemQuantityShouldOverrideIngredientQuantity", False)) != bool(getattr(b, "itemQuantityShouldOverrideIngredientQuantity", False)):
            return False
    if not (excluding_fields & EXCLUDE_PACKAGE_SIZE):
        if not package_size_equal(_package(a, "packageSizePb"), _package(b, "packageSizePb")):
            return False
        if not package_size_equal(_package(a, "pricePackageSizePb"), _package(b, "pricePackageSizePb")):
            return False
        if bool(getattr(a, "pricePackageSizeShouldOverrideItemPackageSize", False)) != bool(getattr(b, "pricePackageSizeShouldOverrideItemPackageSize", False)):
            return False
        if bool(getattr(a, "itemPackageSizeShouldOverrideIngredientPackageSize", False)) != bool(getattr(b, "itemPackageSizeShouldOverrideIngredientPackageSize", False)):
            return False
    if not (excluding_fields & EXCLUDE_DETAILS) and not _localized_equal(a.details or "", b.details or ""):
        return False
    if not (excluding_fields & EXCLUDE_PHOTOS) and not _array_matches(a.photoIds, b.photoIds):
        return False
    if not (excluding_fields & EXCLUDE_RECIPE_ID) and (getattr(a, "recipeId", "") or "") != (getattr(b, "recipeId", "") or ""):
        return False
    if not (excluding_fields & EXCLUDE_EVENT_ID) and (getattr(a, "eventId", "") or "") != (getattr(b, "eventId", "") or ""):
        return False
    if not (excluding_fields & EXCLUDE_STORES) and not _array_matches(a.storeIds, b.storeIds):
        return False
    if not (excluding_fields & EXCLUDE_PRICES) and not prices_match(a.prices, b.prices):
        return False
    if not (excluding_fields & EXCLUDE_INGREDIENTS) and not item_ingredients_identical(a.ingredients, b.ingredients):
        return False
    if not (excluding_fields & EXCLUDE_PRODUCT_UPC) and not _localized_equal(a.productUpc or "", b.productUpc or ""):
        return False
    return True


def _copy_optional_message(target: Message, source: Message, field: str) -> None:
    if source.HasField(field):
        getattr(target, field).CopyFrom(getattr(source, field))
    else:
        target.ClearField(field)


def apply_properties_from_item(target: Message, source: Message, excluding_fields: int = 0) -> None:
    """Port ListItem.applyPropertiesFromListItem from the official web client."""
    if not (excluding_fields & EXCLUDE_DETAILS):
        target.details = source.details
    if not (excluding_fields & EXCLUDE_PHOTOS):
        del target.photoIds[:]
        if source.photoIds:
            target.photoIds.append(source.photoIds[0])
    if not (excluding_fields & EXCLUDE_STORES):
        del target.storeIds[:]
        for store_id in source.storeIds:
            if store_id not in target.storeIds:
                target.storeIds.append(store_id)
    if not (excluding_fields & EXCLUDE_PRICES):
        del target.prices[:]
        by_store: dict[str, Message] = {}
        for price in source.prices:
            if price_empty(price):
                continue
            by_store[str(price.storeId or "")] = price
        for price in by_store.values():
            target.prices.add().CopyFrom(price)
    if not (excluding_fields & EXCLUDE_ITEM_QUANTITY):
        _copy_optional_message(target, source, "quantityPb")
        if getattr(source, "deprecatedQuantity", ""):
            target.deprecatedQuantity = source.deprecatedQuantity
        else:
            target.ClearField("deprecatedQuantity")
    if not (excluding_fields & EXCLUDE_PRICE_QUANTITY):
        _copy_optional_message(target, source, "priceQuantityPb")
        target.priceQuantityShouldOverrideItemQuantity = bool(source.priceQuantityShouldOverrideItemQuantity)
    if not (excluding_fields & EXCLUDE_PACKAGE_SIZE):
        _copy_optional_message(target, source, "packageSizePb")
    if not (excluding_fields & EXCLUDE_PRICE_PACKAGE_SIZE):
        _copy_optional_message(target, source, "pricePackageSizePb")
        target.pricePackageSizeShouldOverrideItemPackageSize = bool(source.pricePackageSizeShouldOverrideItemPackageSize)
    if not (excluding_fields & EXCLUDE_PRODUCT_UPC):
        target.productUpc = source.productUpc


def quantity_empty(quantity: Message) -> bool:
    return not (quantity.amount or quantity.unit or quantity.rawQuantity)


def package_size_empty(package: Message) -> bool:
    return not (package.size or package.unit or package.packageType or package.rawPackageSize)


def quantity_to_deprecated_string(quantity: Message) -> str:
    """Port PBItemQuantity.toDeprecatedQuantityString used by legacy list data."""
    import math
    import re

    amount = quantity.amount or ""
    number = ""
    if amount:
        value = amount_as_float(amount)
        whole = math.floor(value)
        remainder = value - whole
        if remainder < 0.05:
            if whole == 0 and math.isclose(remainder, 1 / 32):
                number = "1/32"
            elif whole == 0 and math.isclose(remainder, 1 / 64):
                number = "1/64"
            elif whole == 0:
                number = f"{value:.3f}".rstrip("0").rstrip(".")
            else:
                number = str(whole)
        elif remainder > 0.95:
            number = str(whole + 1)
        else:
            fraction = decimal_to_friendly_fraction(remainder, unicode=True)
            # CP falls back to a decimal when no single-character vulgar fraction exists.
            if len(fraction) > 1:
                number = f"{value:.3f}".rstrip("0").rstrip(".")
            elif whole:
                number = f"{whole}{fraction}"
            else:
                number = fraction
    unit = quantity.unit or ""
    if not unit:
        return number
    if re.search(r"pounds|lbs?\.?", unit, re.I):
        return f"{number} lb"
    if re.search(r"kilograms|kgs?\.?", unit, re.I):
        return f"{number} kg"
    return ""
