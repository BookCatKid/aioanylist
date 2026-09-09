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
    expected = uuid5_hex(
        "ALName::bean::ALQuantityUnit::can::ALPackageSize::12 oz jar", list_id
    )
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


def test_recipe_source_alias_and_collection_identifier() -> None:
    recipe = PB.PBRecipe(sourceUrl="https://www.allrecipes.com/foo")
    assert source_domain(recipe) == "allrecipes.com"
    assert source_display_name(recipe) == "Allrecipes"
    assert source_collection_identifier("allrecipes") == source_collection_identifier("allrecipes")


def test_duplicate_recipe_ids() -> None:
    collection = PB.PBRecipeCollection(identifier="c")
    collection.recipeIds.extend(["a", "b", "a", "a"])
    assert duplicate_recipe_ids(collection) == ["a", "a"]


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
    recipes = [_recipe("a", "Zeta", rating=5), _recipe("b", "Alpha", rating=5), _recipe("c", "Beta", rating=2)]
    assert [x.identifier for x in sort_recipes(recipes, settings)] == ["b", "a", "c"]
    settings.useReversedSortDirection = True
    # Reversal applies to rating, while ties remain alphabetical in the web client.
    assert [x.identifier for x in sort_recipes(recipes, settings)] == ["c", "b", "a"]


def test_recipe_sorting_prep_time_missing_semantics_match_web() -> None:
    from anylist_sdk.derived import sort_recipes

    settings = PB.PBRecipeCollectionSettings(
        recipesSortOrder=PB.PBRecipeCollectionSettings.SortOrder.PrepTimeSortOrder
    )
    recipes = [_recipe("a", "Missing"), _recipe("b", "Slow", prepTime=30), _recipe("c", "Fast", prepTime=10)]
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
    assert [x.identifier for x in sort_recipes([a, b], settings, meal_plan_events=events, today="2026-09-08")] == ["b", "a"]
    settings.recipesSortOrder = PB.PBRecipeCollectionSettings.SortOrder.TimesPreparedSortOrder
    assert [x.identifier for x in sort_recipes([a, b], settings, meal_plan_events=events, today="2026-09-08")] == ["a", "b"]


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
    recipe.ingredients.add(identifier="old-ing", rawIngredient="1 cup beans", name="beans", quantity="1 cup")

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
        recipeId="r", eventId="e", lastOpenedTimestamp=1, selectedTabId=2,
        checkedIngredientIds=["a", "b"], selectedStepNumber=3,
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
