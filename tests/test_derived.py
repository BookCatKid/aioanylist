from anylist_sdk.derived import (
    duplicate_recipe_ids,
    recipe_list_item_identifier,
    source_collection_identifier,
    source_display_name,
    source_domain,
    total_cost,
)
from anylist_sdk.proto import PB


def _ingredient(name: str, unit: str = "cup"):
    ing = PB.PBIngredient(identifier="i", name=name)
    item_ing = PB.PBItemIngredient(recipeId="r")
    item_ing.ingredient.CopyFrom(ing)
    item_ing.quantityPb.amount = "2"
    item_ing.quantityPb.unit = unit
    return item_ing


def test_recipe_item_identifier_is_deterministic_and_list_namespaced() -> None:
    ingredient = _ingredient("Chopped Onions")
    a = recipe_list_item_identifier(ingredient, "11111111111141118111111111111111")
    b = recipe_list_item_identifier(ingredient, "11111111111141118111111111111111")
    c = recipe_list_item_identifier(ingredient, "22222222222242228222222222222222")
    assert a == b
    assert a != c


def test_recipe_item_identifier_uses_official_unit_and_package_normalization() -> None:
    from anylist_sdk.identifiers import uuid5_hex

    ingredient = _ingredient("Beans", "Dosen")
    ingredient.packageSizePb.rawPackageSize = "12 ounces jars"
    list_id = "11111111111141118111111111111111"
    # aP normalizes Dosen -> can and raw package "12 ounces jars" -> "12 oz jar".
    expected = uuid5_hex("ALName::bean::ALQuantityUnit::can::ALPackageSize::12 oz jar", list_id)
    assert recipe_list_item_identifier(ingredient, list_id) == expected


def test_total_ingredient_quantity_uses_display_abbreviation_before_pluralization() -> None:
    from anylist_sdk.derived import total_ingredient_quantity

    item = PB.ListItem(identifier="item")
    for rid in ("a", "b"):
        source = item.ingredients.add(recipeId=rid)
        source.ingredient.identifier = rid
        source.quantityPb.amount = "1"
        source.quantityPb.unit = "Dosen"
    total = total_ingredient_quantity(item)
    assert total is not None
    # uP does not translate Dosen to can; unlike normalizedUnit/aP it stays localized here.
    assert total.unit == "Dosen"


def test_ingredient_scaling_helpers_match_web_precedence_and_abbreviation() -> None:
    from anylist_sdk.derived import (
        full_ingredient_string_after_scaling,
        ingredient_quantity_after_scaling,
    )

    ingredient = PB.PBIngredient(quantity="2 tablespoons", name="oil", note="divided")
    recipe = PB.PBRecipe(scaleFactor=2)
    event = PB.PBCalendarEvent(recipeScaleFactor=0.5)

    assert ingredient_quantity_after_scaling(ingredient, recipe) == "4 tablespoons"
    assert ingredient_quantity_after_scaling(ingredient, recipe, abbreviated_units=True) == "4 Tbsp"
    assert ingredient_quantity_after_scaling(ingredient, recipe, event) == "1 tablespoons"
    assert full_ingredient_string_after_scaling(ingredient, recipe, event) == (
        "1 tablespoons oil, divided"
    )


def test_item_quantity_falls_back_to_legacy_quantity_like_web() -> None:
    from anylist_sdk.derived import item_quantity

    item = PB.ListItem(identifier="item", deprecatedQuantity="1.5 lb")
    quantity = item_quantity(item)
    assert quantity.amount == "1.5"
    assert quantity.unit == "lb"
    assert quantity.rawQuantity == "1.5 lb"

    localized = item_quantity(item, decimal_separator=",")
    assert localized.amount == "1,5"
    assert localized.unit == "lb"
    assert localized.rawQuantity == "1,5 lb"

    item.quantityPb.amount = "2"
    item.quantityPb.unit = "cups"
    assert item_quantity(item) is item.quantityPb


def test_display_quantity_package_and_cell_text_match_web() -> None:
    from anylist_sdk.derived import display_quantity_and_package_size, shopping_list_quantity_text

    quantity = PB.PBItemQuantity(rawQuantity="2 tablespoons")
    package = PB.PBItemPackageSize(rawPackageSize="12 ounces jars")
    assert display_quantity_and_package_size(quantity, package) == "2 Tbsp × 12 oz jars"
    assert display_quantity_and_package_size(
        quantity, package, nonbreaking=True, parenthesize=True
    ) == (
        "(2\N{NO-BREAK SPACE}Tbsp\N{NO-BREAK SPACE}×\N{NO-BREAK SPACE}"
        "12\N{NO-BREAK SPACE}oz\N{NO-BREAK SPACE}jars)"
    )

    item = PB.ListItem(identifier="item")
    item.quantityPb.CopyFrom(quantity)
    item.packageSizePb.CopyFrom(package)
    assert shopping_list_quantity_text(item) == "(2 Tbsp × 12 oz jars)"


def test_item_ingredient_and_category_lookup_helpers_match_web() -> None:
    from anylist_sdk.derived import (
        index_of_matching_item_ingredient,
        item_category_assignments_map,
        item_ingredient_ingredient,
        item_ingredient_package_size,
        item_ingredient_quantity,
    )

    item = PB.ListItem(identifier="item")
    first = item.ingredients.add(recipeId="recipe")
    first.ingredient.identifier = "ingredient"
    first.quantityPb.amount = "2"
    first.packageSizePb.rawPackageSize = "12 oz"
    target = PB.PBItemIngredient(recipeId="recipe")
    target.ingredient.identifier = "ingredient"

    assert index_of_matching_item_ingredient(item, target) == 0
    assert item_ingredient_quantity(first).amount == "2"
    assert item_ingredient_package_size(first).rawPackageSize == "12 oz"
    assert item_ingredient_ingredient(first).identifier == "ingredient"
    item.categoryAssignments.add(categoryGroupId="group", categoryId="produce")
    assert item_category_assignments_map(item) == {"group": "produce"}


def test_item_price_for_store_id_or_new_matches_web() -> None:
    from anylist_sdk.derived import item_price_for_store_id_or_new

    item = PB.ListItem(identifier="item")
    fresh = item_price_for_store_id_or_new(item, "store")
    assert fresh.storeId == "store" and len(item.prices) == 0
    stored = item.prices.add(storeId="store", amount=2.5)
    assert item_price_for_store_id_or_new(item, "store") is stored


def test_user_category_system_predicate_matches_web() -> None:
    from anylist_sdk.derived import user_category_is_system

    assert user_category_is_system(PB.PBUserCategory(systemCategory="produce"))
    assert not user_category_is_system(PB.PBUserCategory(name="Custom"))


def test_recipe_source_alias_and_collection_identifier() -> None:
    recipe = PB.PBRecipe(sourceUrl="https://www.allrecipes.com/foo")
    assert source_domain(recipe) == "allrecipes.com"
    assert source_display_name(recipe) == "Allrecipes"
    assert source_collection_identifier("allrecipes") == source_collection_identifier("allrecipes")


def test_duplicate_recipe_ids() -> None:
    collection = PB.PBRecipeCollection(identifier="c")
    collection.recipeIds.extend(["a", "b", "a", "a"])
    assert duplicate_recipe_ids(collection) == ["a", "a"]


def test_account_and_share_display_names_match_web_helpers() -> None:
    from anylist_sdk.derived import account_full_name, email_user_display_name

    assert account_full_name(PB.PBAccountInfoResponse(firstName="Ada", lastName="Lovelace")) == (
        "Ada Lovelace"
    )
    assert account_full_name(PB.PBAccountInfoResponse(firstName="Ada")) == "Ada"
    assert account_full_name(PB.PBAccountInfoResponse()) is None
    assert (
        email_user_display_name(PB.PBEmailUserIDPair(email="ada@example.test", fullName="Ada"))
        == "Ada"
    )
    assert email_user_display_name(PB.PBEmailUserIDPair(email="ada@example.test")) == (
        "ada@example.test"
    )


def test_recipe_photo_and_collection_sort_helpers_match_web_defaults() -> None:
    from anylist_sdk.derived import recipe_collection_sort_order, recipe_photo_id, recipe_photo_url

    recipe = PB.PBRecipe(photoIds=["photo-a", "photo-b"], photoUrls=["https://example/a.jpg"])
    assert recipe_photo_id(recipe) == "photo-a"
    assert recipe_photo_url(recipe) == "https://example/a.jpg"
    assert recipe_photo_id(PB.PBRecipe()) is None

    collection = PB.PBRecipeCollection()
    assert (
        recipe_collection_sort_order(collection)
        == PB.PBRecipeCollectionSettings.SortOrder.ManualSortOrder
    )
    collection.collectionSettings.recipesSortOrder = (
        PB.PBRecipeCollectionSettings.SortOrder.RatingSortOrder
    )
    assert (
        recipe_collection_sort_order(collection)
        == PB.PBRecipeCollectionSettings.SortOrder.RatingSortOrder
    )


def test_source_smart_collections_keep_first_seen_order() -> None:
    from anylist_sdk.derived import source_collection_identifier, source_smart_collections

    recipes = [
        PB.PBRecipe(identifier="b1", sourceName="Beta", sourceUrl="https://beta.example/r"),
        PB.PBRecipe(identifier="a1", sourceName="Alpha", sourceUrl="https://alpha.example/r"),
        PB.PBRecipe(identifier="b2", sourceName="Beta", sourceUrl="https://m.beta.example/r2"),
    ]
    collections = source_smart_collections(recipes)
    assert [c.name for c in collections] == ["Beta", "Alpha"]
    assert list(collections[0].recipeIds) == ["b1", "b2"]
    condition = collections[0].collectionSettings.smartFilter.conditions[0]
    assert condition.fieldID == "normalized-recipe-source-name"
    assert condition.operatorID == "is-equal-to"
    assert condition.value == "beta"
    assert (
        collections[0].collectionSettings.recipesSortOrder
        == PB.PBRecipeCollectionSettings.SortOrder.DateCreatedSortOrder
    )

    saved = PB.PBRecipeCollectionSettings(
        recipesSortOrder=PB.PBRecipeCollectionSettings.SortOrder.RatingSortOrder,
        useReversedSortDirection=True,
    )
    beta_id = source_collection_identifier("beta")
    collections = source_smart_collections(recipes, saved_settings={beta_id: saved})
    assert collections[0].collectionSettings.recipesSortOrder == saved.recipesSortOrder
    assert collections[0].collectionSettings.useReversedSortDirection is True


def test_not_in_collection_smart_collection_matches_official_shape() -> None:
    from anylist_sdk.derived import not_in_collection_smart_collection

    recipes = [PB.PBRecipe(identifier="a"), PB.PBRecipe(identifier="b")]
    user_collection = PB.PBRecipeCollection(identifier="c", recipeIds=["a"])
    synthetic = not_in_collection_smart_collection(recipes, [user_collection])
    assert synthetic.identifier == "74267bf441d04dbc9dda96910dd3ba58"
    assert list(synthetic.recipeIds) == ["b"]
    condition = synthetic.collectionSettings.smartFilter.conditions[0]
    assert condition.fieldID == "recipes-not-in-a-collection"
    assert not condition.HasField("operatorID")
    assert (
        synthetic.collectionSettings.recipesSortOrder
        == PB.PBRecipeCollectionSettings.SortOrder.AlphabeticalSortOrder
    )


def test_total_cost_quantity_override() -> None:
    item = PB.ListItem(identifier="i")
    item.quantityPb.amount = "2"
    price = PB.PBItemPrice(amount=3.5)
    assert total_cost(item, price) == 7.0


def test_list_item_convenience_helpers_match_web_semantics() -> None:
    from anylist_sdk.derived import (
        item_category_id,
        item_event_id,
        item_has_photo,
        item_has_price,
        item_has_store,
        item_is_ingredient_item,
        item_photo_id,
        item_price_for_store_id,
        item_price_store_id_from_store_ids,
        item_store_names_display_string,
    )

    item = PB.ListItem(category="produce", eventId="event", photoIds=["photo"], storeIds=["b", "a"])
    item.prices.add(storeId="a", amount=2.5)
    item.ingredients.add(recipeId="recipe")
    assert item_category_id(item) == "produce"
    item.categoryMatchId = "apple"
    assert item_category_id(item) == "apple"
    assert item_event_id(item) == "event"
    assert item_photo_id(item) == "photo" and item_has_photo(item)
    assert item_has_store(item) and item_has_price(item) and item_is_ingredient_item(item)
    assert item_price_for_store_id(item, "a") is item.prices[0]
    assert item_price_for_store_id(item, "missing") is None
    assert item_price_store_id_from_store_ids(item, ["a"]) == "a"
    assert item_price_store_id_from_store_ids(item, ["a", "b"]) == "a"
    item.prices.add(storeId="b", details="sale")
    assert item_price_store_id_from_store_ids(item, ["a", "b"]) is None
    stores = [PB.PBStore(identifier="a", name="Alpha"), PB.PBStore(identifier="b", name="Beta")]
    assert item_store_names_display_string(item, stores) == "Alpha, Beta"


def test_folder_index_and_effective_sort_helpers_match_web_defaults() -> None:
    from anylist_sdk.derived import (
        folder_index_of_folder_id,
        folder_index_of_item,
        folder_index_of_list_id,
        folder_lists_sort_order,
        folder_sort_position,
    )

    folder = PB.PBListFolder()
    folder.items.add(identifier="list", itemType=PB.PBListFolderItem.ItemType.ListType)
    child = folder.items.add(identifier="folder", itemType=PB.PBListFolderItem.ItemType.FolderType)
    assert folder_index_of_list_id(folder, "list") == 0
    assert folder_index_of_folder_id(folder, "folder") == 1
    assert folder_index_of_item(folder, child) == 1
    assert folder_index_of_list_id(folder, "missing") == -1
    assert folder_lists_sort_order(folder) == PB.PBListFolderSettings.SortOrder.ManualSortOrder
    assert (
        folder_sort_position(folder)
        == PB.PBListFolderSettings.FolderSortPosition.FolderSortPositionAfterLists
    )
    folder.folderSettings.listsSortOrder = PB.PBListFolderSettings.SortOrder.AlphabeticalSortOrder
    folder.folderSettings.folderSortPosition = (
        PB.PBListFolderSettings.FolderSortPosition.FolderSortPositionBeforeLists
    )
    assert (
        folder_lists_sort_order(folder) == PB.PBListFolderSettings.SortOrder.AlphabeticalSortOrder
    )
    assert (
        folder_sort_position(folder)
        == PB.PBListFolderSettings.FolderSortPosition.FolderSortPositionBeforeLists
    )


def _recipe(rid: str, name: str, **fields):
    recipe = PB.PBRecipe(identifier=rid, name=name)
    for key, value in fields.items():
        setattr(recipe, key, value)
    return recipe


def test_recipe_sorting_matches_official_rating_and_tie_break_rules() -> None:
    from anylist_sdk.derived import sort_recipes

    settings = PB.PBRecipeCollectionSettings(
        recipesSortOrder=PB.PBRecipeCollectionSettings.SortOrder.RatingSortOrder
    )
    recipes = [
        _recipe("a", "Zeta", rating=5),
        _recipe("b", "Alpha", rating=5),
        _recipe("c", "Beta", rating=2),
    ]
    assert [x.identifier for x in sort_recipes(recipes, settings)] == ["b", "a", "c"]
    settings.useReversedSortDirection = True
    # Reversal applies to rating, while ties remain alphabetical in the web client.
    assert [x.identifier for x in sort_recipes(recipes, settings)] == ["c", "b", "a"]


def test_recipe_sorting_prep_time_missing_semantics_match_web() -> None:
    from anylist_sdk.derived import sort_recipes

    settings = PB.PBRecipeCollectionSettings(
        recipesSortOrder=PB.PBRecipeCollectionSettings.SortOrder.PrepTimeSortOrder
    )
    recipes = [
        _recipe("a", "Missing"),
        _recipe("b", "Slow", prepTime=30),
        _recipe("c", "Fast", prepTime=10),
    ]
    assert [x.identifier for x in sort_recipes(recipes, settings)] == ["c", "b", "a"]
    settings.useReversedSortDirection = True
    assert [x.identifier for x in sort_recipes(recipes, settings)] == ["a", "b", "c"]


def test_recipe_sorting_uses_past_meal_history_for_date_and_count() -> None:
    from anylist_sdk.derived import sort_recipes

    a = _recipe("a", "A")
    b = _recipe("b", "B")
    events = [
        PB.PBCalendarEvent(identifier="1", recipeId="a", date="2026-09-01", eventType=0),
        PB.PBCalendarEvent(identifier="2", recipeId="a", date="2026-09-02", eventType=0),
        PB.PBCalendarEvent(identifier="3", recipeId="b", date="2026-09-03", eventType=0),
        PB.PBCalendarEvent(identifier="future", recipeId="b", date="2026-10-01", eventType=0),
        PB.PBCalendarEvent(identifier="queue", recipeId="b", date="2026-09-04", eventType=1),
    ]
    settings = PB.PBRecipeCollectionSettings(
        recipesSortOrder=PB.PBRecipeCollectionSettings.SortOrder.DatePreparedSortOrder
    )
    assert [
        x.identifier
        for x in sort_recipes([a, b], settings, meal_plan_events=events, today="2026-09-08")
    ] == ["b", "a"]
    settings.recipesSortOrder = PB.PBRecipeCollectionSettings.SortOrder.TimesPreparedSortOrder
    assert [
        x.identifier
        for x in sort_recipes([a, b], settings, meal_plan_events=events, today="2026-09-08")
    ] == ["a", "b"]


def test_recipe_servings_scaling_preserves_text_prefix() -> None:
    from anylist_sdk.derived import recipe_servings_after_scaling

    recipe = PB.PBRecipe(identifier="r", servings="Serves 4", scaleFactor=1.5)
    assert recipe_servings_after_scaling(recipe) == "Serves 6"
    event = PB.PBCalendarEvent(identifier="e", recipeScaleFactor=0.5)
    assert recipe_servings_after_scaling(recipe, event) == "Serves 2"


def test_recipe_heading_filters_match_official_hash_space_marker() -> None:
    from anylist_sdk.derived import (
        is_recipe_heading,
        recipe_heading_text,
        recipe_ingredients_excluding_headings,
        recipe_prep_steps_excluding_headings,
    )

    recipe = PB.PBRecipe(identifier="r")
    recipe.ingredients.add(identifier="h", name="Sauce", isHeading=True)
    recipe.ingredients.add(identifier="i", name="Tomatoes")
    recipe.preparationSteps.extend(["# Sauce", "Mix", "#Not a heading"])
    assert [x.identifier for x in recipe_ingredients_excluding_headings(recipe)] == ["i"]
    assert recipe_prep_steps_excluding_headings(recipe) == ["Mix", "#Not a heading"]
    assert is_recipe_heading("# Sauce")
    assert not is_recipe_heading("#Sauce")
    assert recipe_heading_text("# Sauce") == "Sauce"


def test_recipe_duplicate_copies_only_official_user_fields_with_fresh_compact_ids() -> None:
    from anylist_sdk.derived import duplicate_recipe

    recipe = PB.PBRecipe(
        identifier="old",
        timestamp=9,
        name="Soup",
        icon="soup",
        sourceName="Example",
        sourceUrl="https://example.test/r",
        creationTimestamp=123,
        photoUrls=["https://cdn/derived"],
        scaleFactor=2,
        rating=5,
        nutritionalInfo="info",
        cookTime=30,
        prepTime=10,
        servings="4",
    )
    recipe.photoIds.append("photo")
    recipe.preparationSteps.append("Cook")
    recipe.ingredients.add(
        identifier="old-ing", rawIngredient="1 cup beans", name="beans", quantity="1 cup"
    )

    duplicate = duplicate_recipe(recipe)

    assert len(duplicate.identifier) == 32 and "-" not in duplicate.identifier
    assert duplicate.identifier != recipe.identifier
    assert duplicate.name == "Soup" and list(duplicate.photoIds) == ["photo"]
    assert list(duplicate.photoUrls) == [] and duplicate.creationTimestamp == 0
    assert duplicate.timestamp == 0
    assert duplicate.ingredients[0].identifier != "old-ing"
    assert len(duplicate.ingredients[0].identifier) == 32
    assert duplicate.ingredients[0].rawIngredient == "1 cup beans"


def test_cooking_state_icon_descriptor_and_template_group_helpers() -> None:
    from anylist_sdk.derived import (
        cooking_states_equal,
        descriptor_for_calendar_event,
        descriptor_for_queue_event,
        descriptor_is_calendar_event,
        descriptor_is_queue_event,
        descriptors_equal,
        icon_resource_path,
        icons_equal,
        template_group_item_for_group,
        template_group_item_for_template,
        template_group_items_equal,
    )

    a = PB.PBRecipeCookingState(
        recipeId="r",
        eventId="e",
        lastOpenedTimestamp=1,
        selectedTabId=2,
        checkedIngredientIds=["a", "b"],
        selectedStepNumber=3,
    )
    b = PB.PBRecipeCookingState()
    b.CopyFrom(a)
    assert cooking_states_equal(a, b)
    b.checkedIngredientIds.reverse()
    assert not cooking_states_equal(a, b)

    icon = PB.PBIcon(iconName="food/apple", tintHexColor="FF0000")
    assert icons_equal(icon, PB.PBIcon(iconName="food/apple", tintHexColor="FF0000"))
    assert icon_resource_path(icon) == "icon_sets/food/apple.png"

    calendar = descriptor_for_calendar_event("e")
    queue = descriptor_for_queue_event("q")
    assert descriptor_is_calendar_event(calendar) and not descriptor_is_queue_event(calendar)
    assert descriptor_is_queue_event(queue)
    assert descriptors_equal(calendar, descriptor_for_calendar_event("e"))

    template = template_group_item_for_template("t")
    group = template_group_item_for_group("g")
    assert template.itemType == PB.PBMealPlanTemplateGroupItem.Type.Template
    assert group.itemType == PB.PBMealPlanTemplateGroupItem.Type.Group
    assert template_group_items_equal(template, template_group_item_for_template("t"))


def test_event_list_item_equality_supports_official_normalized_mode() -> None:
    from anylist_sdk.derived import event_list_item_arrays_equal, event_list_items_equal

    a = PB.PBCalendarEventListItem(identifier="a", name="Crème Sugar", details="Fine cut")
    b = PB.PBCalendarEventListItem(identifier="a", name="creme sugar", details="fine cut")
    a.quantityPb.amount = b.quantityPb.amount = "2"
    a.quantityPb.rawQuantity = b.quantityPb.rawQuantity = "2"
    assert not event_list_items_equal(a, b)
    assert event_list_items_equal(a, b, normalized=True)

    b.name = a.name
    b.details = a.details
    b.identifier = "different"
    assert not event_list_item_arrays_equal([a], [b])
    assert event_list_item_arrays_equal([a], [b], ignore_identifier=True)
