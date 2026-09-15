from aioanylist.item_semantics import (
    EXCLUDE_DETAILS,
    EXCLUDE_EVENT_ID,
    EXCLUDE_ITEM_QUANTITY,
    EXCLUDE_NAME,
    EXCLUDE_PACKAGE_SIZE,
    EXCLUDE_RECIPE_ID,
    apply_properties_from_item,
    deprecated_display_quantity,
    deprecated_quantity_unit_display_string,
    deprecated_quantity_value,
    deprecated_quantity_without_unit,
    is_valid_legacy_quantity,
    item_hash,
    item_is_bare,
    items_equal,
    package_size_not_empty,
    price_has_amount,
    price_has_details,
    prices_match,
    quantity_not_empty,
)
from aioanylist.proto import PB


def item(name="Milk"):
    return PB.ListItem(identifier="i", listId="l", name=name)


def test_item_hash_matches_javascript_32bit_hash_and_name_exclusion() -> None:
    assert item_hash(item("abc")) == 96354
    assert item_hash(item("Milk")) == item_hash(item("milk"))
    # JS hashString hashes UTF-16 code units, so astral characters are two contributions.
    assert item_hash(item("😀")) == 1772899
    assert item_hash(item("A😀B")) == 57849694
    assert item_hash(item(), EXCLUDE_NAME) > 1e300


def test_item_equality_is_base_sensitive_and_respects_exclusion_mask() -> None:
    a = item("Café 2")
    b = item("cafe 02")
    assert items_equal(a, b)
    a.details = "one"
    b.details = "two"
    assert not items_equal(a, b)
    assert items_equal(a, b, EXCLUDE_DETAILS)


def test_item_equality_quantity_and_package_masks() -> None:
    a = item()
    b = item()
    a.quantityPb.amount = "2"
    b.quantityPb.amount = "3"
    assert not items_equal(a, b)
    assert items_equal(a, b, EXCLUDE_ITEM_QUANTITY)
    a.quantityPb.CopyFrom(b.quantityPb)
    a.packageSizePb.rawPackageSize = "12 oz"
    b.packageSizePb.rawPackageSize = "16 oz"
    assert not items_equal(a, b)
    assert items_equal(a, b, EXCLUDE_PACKAGE_SIZE)


def test_recipe_and_event_ids_use_official_casefolded_diacritic_compare() -> None:
    a = item()
    b = item()
    a.recipeId = "RÉCIPE"
    b.recipeId = "recipe"
    a.eventId = "ÉVENT"
    b.eventId = "event"
    assert items_equal(a, b)

    b.recipeId = "different"
    assert not items_equal(a, b)
    assert items_equal(a, b, EXCLUDE_RECIPE_ID)
    b.recipeId = a.recipeId
    b.eventId = "different"
    assert not items_equal(a, b)
    assert items_equal(a, b, EXCLUDE_EVENT_ID)


def test_prices_ignore_empty_entries_and_are_order_insensitive() -> None:
    a = [PB.PBItemPrice(amount=2, storeId="a"), PB.PBItemPrice(amount=3, storeId="b")]
    b = [
        PB.PBItemPrice(amount=3, storeId="b"),
        PB.PBItemPrice(),
        PB.PBItemPrice(amount=2, storeId="a"),
    ]
    assert prices_match(a, b)


def test_nested_optional_field_presence_matches_protobufjs_direct_comparisons() -> None:
    from aioanylist.item_semantics import ingredient_equal, price_equal

    a = PB.PBIngredient(name="onion")
    b = PB.PBIngredient(name="onion")
    assert ingredient_equal(a, b)
    b.rawIngredient = ""
    assert not ingredient_equal(a, b)
    b.ClearField("rawIngredient")
    b.isHeading = False
    assert not ingredient_equal(a, b)

    pa = PB.PBItemPrice(amount=2, details="sale")
    pb = PB.PBItemPrice(amount=2, details="sale")
    assert price_equal(pa, pb)
    pb.storeId = ""
    assert not price_equal(pa, pb)


def test_item_ingredient_equality_is_order_independent() -> None:
    a = item()
    b = item()
    for target, order in ((a, ("x", "y")), (b, ("y", "x"))):
        for ident in order:
            src = target.ingredients.add(recipeId="r", recipeName="R")
            src.ingredient.identifier = ident
            src.ingredient.name = ident
    assert items_equal(a, b)


def test_apply_properties_uses_official_copy_mask() -> None:
    source = item()
    source.details = "note"
    source.photoIds.extend(["p1", "p2"])
    source.storeIds.extend(["a", "a", "b"])
    source.quantityPb.amount = "4"
    source.packageSizePb.rawPackageSize = "12 oz"
    source.priceQuantityPb.amount = "2"
    source.priceQuantityShouldOverrideItemQuantity = True
    source.pricePackageSizePb.rawPackageSize = "6 oz"
    source.pricePackageSizeShouldOverrideItemPackageSize = True
    source.productUpc = "123"
    source.prices.add(amount=5, storeId="a")
    target = item()
    apply_properties_from_item(target, source, EXCLUDE_ITEM_QUANTITY | EXCLUDE_PACKAGE_SIZE)
    assert (
        target.details == "note"
        and list(target.photoIds) == ["p1"]
        and list(target.storeIds) == ["a", "b"]
    )
    assert not target.HasField("quantityPb") and not target.HasField("packageSizePb")
    assert target.priceQuantityPb.amount == "2" and target.priceQuantityShouldOverrideItemQuantity
    assert target.pricePackageSizePb.rawPackageSize == "6 oz" and target.productUpc == "123"


def test_quantity_replace_amount_falls_back_to_amount_and_unit_when_raw_is_empty() -> None:
    from aioanylist.parsing.quantity import replace_quantity_amount

    original = PB.PBItemQuantity(amount="2", unit="cups")
    updated = replace_quantity_amount(original, "1")
    assert updated.amount == "1"
    assert updated.unit == "cup"
    assert updated.rawQuantity == "1 cup"


def test_quantity_deprecated_string_matches_legacy_lb_kg_rules() -> None:
    from aioanylist.item_semantics import quantity_to_deprecated_string

    assert (
        quantity_to_deprecated_string(PB.PBItemQuantity(amount="1 1/2", unit="pounds")) == "1½ lb"
    )
    assert quantity_to_deprecated_string(PB.PBItemQuantity(amount="2", unit="kg")) == "2 kg"
    assert quantity_to_deprecated_string(PB.PBItemQuantity(amount="2", unit="cups")) == ""


def test_legacy_quantity_helpers_match_web_semantics() -> None:
    legacy = item()
    legacy.deprecatedQuantity = "1.5 lb"
    assert is_valid_legacy_quantity("1.5 lb")
    assert is_valid_legacy_quantity("½ kg")
    assert is_valid_legacy_quantity("lb")
    assert not is_valid_legacy_quantity("2 cups")
    assert deprecated_quantity_without_unit(legacy) == "1.5"
    assert deprecated_quantity_value(legacy) == 1.5
    assert deprecated_display_quantity(legacy) == "1.5\N{NO-BREAK SPACE}lb"
    assert deprecated_display_quantity(legacy, decimal_separator=",") == "1,5\N{NO-BREAK SPACE}lb"
    assert deprecated_quantity_unit_display_string(legacy) == "lb"

    legacy.deprecatedQuantity = "kg"
    assert deprecated_display_quantity(legacy) == ""
    assert deprecated_quantity_unit_display_string(legacy) == ""


def test_presence_and_bare_item_helpers_match_web() -> None:
    price = PB.PBItemPrice()
    assert not price_has_amount(price) and not price_has_details(price)
    price.amount = 0
    assert price_has_amount(price)
    price.details = "sale"
    assert price_has_details(price)

    quantity = PB.PBItemQuantity(amount="1")
    package = PB.PBItemPackageSize(rawPackageSize="12 oz")
    assert quantity_not_empty(quantity)
    assert package_size_not_empty(package)

    bare = item()
    bare.quantityPb.amount = "1"
    assert item_is_bare(bare)
    bare.photoIds.append("photo")
    assert not item_is_bare(bare)
