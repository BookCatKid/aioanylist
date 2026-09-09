from anylist_sdk.proto import PB
from anylist_sdk.state import AnyListState


def test_list_settings_are_indexed_by_list_id_and_full_refresh_clears() -> None:
    state = AnyListState()
    state.list_settings["stale"] = PB.PBListSettings(identifier="old", listId="stale")
    response = PB.PBListSettingsList()
    response.timestamp.identifier = "all"
    response.timestamp.timestamp = 7
    response.settings.add(identifier="opaque", listId="list-1", shouldHidePrices=True)
    state.apply_list_settings(response)
    assert set(state.list_settings) == {"list-1"}
    assert state.list_settings["list-1"].identifier == "opaque"
    assert state.list_settings_timestamp == 7


def test_categories_and_categorized_items_full_refresh() -> None:
    state = AnyListState()
    state.user_categories["old"] = PB.PBUserCategory(identifier="old")
    cats = PB.PBUserCategoryData(identifier="all", timestamp=5)
    cats.categories.add(identifier="new", name="Produce")
    state.apply_user_categories(cats)
    assert set(state.user_categories) == {"new"}

    state.categorized_items["old"] = PB.ListItem(identifier="old")
    items = PB.PBCategorizedItemsList()
    items.timestamp.identifier = "all"
    items.timestamp.timestamp = 8
    items.categorizedItems.add(identifier="new", name="milk")
    state.apply_categorized_items(items)
    assert set(state.categorized_items) == {"new"}


def test_user_data_timestamps_include_incremental_domains() -> None:
    state = AnyListState(user_id="u")
    state.shopping_lists["l"] = PB.ShoppingList(identifier="l", timestamp=2, logicalClockTime=3)
    state.recipe_data_id = "r"
    state.recipe_timestamp = 4
    state.meal_plan_calendar_id = "c"
    state.meal_plan_logical_timestamp = 5
    state.ordered_starter_list_ids_timestamp_id = "u"
    state.ordered_starter_list_ids_timestamp = 6
    ts = state.user_data_timestamps()
    assert ts.shoppingListTimestamps.timestamps[0].identifier == "l"
    assert ts.shoppingListLogicalTimestamps.timestamps[0].logicalTimestamp == 3
    assert ts.userRecipeDataTimestamp.timestamp == 4
    assert ts.mealPlanningCalendarTimestamp.logicalTimestamp == 5
    assert ts.orderedStarterListIdsTimestamp.timestamp == 6


def test_recipe_incremental_prunes_deleted_recipes_and_collections_and_merges_system_settings() -> None:
    state = AnyListState(user_id="user", recipe_data_id="rd", recipe_timestamp=1)
    state.recipes["keep"] = PB.PBRecipe(identifier="keep")
    state.recipes["gone"] = PB.PBRecipe(identifier="gone")
    state.recipe_collections["keep-c"] = PB.PBRecipeCollection(
        identifier="keep-c", recipeIds=["keep", "gone"]
    )
    state.recipe_collections["gone-c"] = PB.PBRecipeCollection(identifier="gone-c")
    state.system_recipe_collection_settings["existing"] = PB.PBRecipeCollectionSettings(
        recipesSortOrder=1
    )

    response = PB.PBRecipeDataResponse(
        recipeDataId="rd",
        timestamp=2,
        includesRecipeCollectionIds=True,
        recipeCollectionIds=["keep-c"],
    )
    response.allRecipesCollection.identifier = "all"
    response.allRecipesCollection.recipeIds.append("keep")
    response.settingsMapForSystemCollections["new"].recipesSortOrder = 3

    state.apply_recipes(response)

    assert set(state.recipes) == {"keep"}
    assert set(state.recipe_collections) == {"keep-c"}
    assert list(state.recipe_collections["keep-c"].recipeIds) == ["keep"]
    assert set(state.system_recipe_collection_settings) == {"existing", "new"}
    assert state.recipe_timestamp == 2


def test_recipe_incremental_without_recipe_data_id_is_ignored() -> None:
    state = AnyListState(user_id="user", recipe_data_id="rd", recipe_timestamp=4)
    state.recipes["keep"] = PB.PBRecipe(identifier="keep")
    response = PB.PBRecipeDataResponse(timestamp=99)
    response.recipes.add(identifier="unexpected")

    state.apply_recipes(response)

    assert state.recipe_timestamp == 4
    assert set(state.recipes) == {"keep"}


def test_recipe_full_apply_replaces_recipe_and_collection_indexes() -> None:
    state = AnyListState(user_id="user")
    state.recipes["stale"] = PB.PBRecipe(identifier="stale")
    state.recipe_collections["stale-c"] = PB.PBRecipeCollection(identifier="stale-c")
    response = PB.PBRecipeDataResponse(recipeDataId="new-data", timestamp=5)
    response.recipes.add(identifier="fresh")
    response.recipeCollections.add(identifier="fresh-c")
    response.recipeCollectionIds.append("fresh-c")
    response.allRecipesCollection.identifier = "all"
    response.allRecipesCollection.recipeIds.append("fresh")

    state.apply_recipes_full(response)

    assert set(state.recipes) == {"fresh"}
    assert set(state.recipe_collections) == {"fresh-c"}
    assert state.recipe_collection_ids == ["fresh-c"]
    assert state.recipe_data_id == "new-data"


def test_shopping_list_response_applies_full_list_local_state_and_clock() -> None:
    state = AnyListState()
    state.shopping_lists["list"] = PB.ShoppingList(identifier="list", logicalClockTime=1)
    state.list_stores["list"] = {"stale-store": PB.PBStore(identifier="stale-store", listId="list")}
    state.list_store_filters["list"] = {"stale-filter": PB.PBStoreFilter(identifier="stale-filter", listId="list")}
    state.list_category_groups["list"] = {"stale-group": PB.PBListCategoryGroup(identifier="stale-group", listId="list")}
    state.list_categories["list"] = {"stale-category": PB.PBListCategory(identifier="stale-category", listId="list")}
    state.list_categorization_rules["list"] = {"stale-rule": PB.PBListCategorizationRule(identifier="stale-rule", listId="list")}

    detail = PB.PBListResponse(listId="list", isFullSync=True, logicalTimestamp=9)
    detail.stores.add(identifier="store", listId="list", name="Market")
    detail.storeFilters.add(identifier="filter", listId="list", name="Nearby")
    group_response = detail.categoryGroupResponses.add()
    group_response.categoryGroup.identifier = "group"
    group_response.categoryGroup.listId = "list"
    group_response.categoryGroup.name = "Groceries"
    group_response.categoryGroup.categories.add(
        identifier="category", categoryGroupId="group", listId="list", name="Produce"
    )
    detail.categorizationRules.add(
        identifier="rule",
        listId="list",
        categoryGroupId="group",
        itemName="apple",
        categoryId="category",
    )

    state.apply_list_response(detail)

    assert state.shopping_lists["list"].logicalClockTime == 9
    assert set(state.list_stores["list"]) == {"store"}
    assert set(state.list_store_filters["list"]) == {"filter"}
    assert set(state.list_category_groups["list"]) == {"group"}
    assert list(state.list_category_groups["list"]["group"].categories) == []
    assert set(state.list_categories["list"]) == {"category"}
    assert set(state.list_categorization_rules["list"]) == {"rule"}


def test_shopping_list_response_delta_deletes_group_categories_and_other_domains() -> None:
    state = AnyListState()
    state.list_stores["list"] = {
        "remove": PB.PBStore(identifier="remove", listId="list"),
        "keep": PB.PBStore(identifier="keep", listId="list"),
    }
    state.list_store_filters["list"] = {
        "remove": PB.PBStoreFilter(identifier="remove", listId="list"),
        "keep": PB.PBStoreFilter(identifier="keep", listId="list"),
    }
    state.list_category_groups["list"] = {
        "remove-group": PB.PBListCategoryGroup(identifier="remove-group", listId="list"),
        "keep-group": PB.PBListCategoryGroup(identifier="keep-group", listId="list"),
    }
    state.list_categories["list"] = {
        "from-removed-group": PB.PBListCategory(
            identifier="from-removed-group", categoryGroupId="remove-group", listId="list"
        ),
        "delete-directly": PB.PBListCategory(
            identifier="delete-directly", categoryGroupId="keep-group", listId="list"
        ),
        "keep": PB.PBListCategory(identifier="keep", categoryGroupId="keep-group", listId="list"),
    }
    state.list_categorization_rules["list"] = {
        "remove": PB.PBListCategorizationRule(identifier="remove", listId="list"),
        "keep": PB.PBListCategorizationRule(identifier="keep", listId="list"),
    }

    detail = PB.PBListResponse(listId="list")
    detail.deletedStoreIds.append("remove")
    detail.deletedStoreFilterIds.append("remove")
    detail.deletedCategoryGroupIds.append("remove-group")
    group_response = detail.categoryGroupResponses.add()
    group_response.categoryGroup.identifier = "keep-group"
    group_response.categoryGroup.listId = "list"
    group_response.deletedCategoryIds.append("delete-directly")
    detail.deletedCategorizationRuleIds.append("remove")

    state.apply_list_response(detail)

    assert set(state.list_stores["list"]) == {"keep"}
    assert set(state.list_store_filters["list"]) == {"keep"}
    assert set(state.list_category_groups["list"]) == {"keep-group"}
    assert set(state.list_categories["list"]) == {"keep"}
    assert set(state.list_categorization_rules["list"]) == {"keep"}


def test_shopping_response_empty_order_and_unknown_list_clear_local_subdomains() -> None:
    state = AnyListState(ordered_shopping_list_ids=["old"])
    state.shopping_lists["gone"] = PB.ShoppingList(identifier="gone")
    state.list_stores["gone"] = {"s": PB.PBStore(identifier="s", listId="gone")}
    state.list_store_filters["gone"] = {"f": PB.PBStoreFilter(identifier="f", listId="gone")}
    state.list_category_groups["gone"] = {"g": PB.PBListCategoryGroup(identifier="g", listId="gone")}
    state.list_categories["gone"] = {"c": PB.PBListCategory(identifier="c", listId="gone")}
    state.list_categorization_rules["gone"] = {"r": PB.PBListCategorizationRule(identifier="r", listId="gone")}

    response = PB.ShoppingListsResponse()
    response.unknownIds.append("gone")
    state.apply_shopping_lists(response)

    assert state.ordered_shopping_list_ids == []
    assert "gone" not in state.shopping_lists
    assert "gone" not in state.list_stores
    assert "gone" not in state.list_store_filters
    assert "gone" not in state.list_category_groups
    assert "gone" not in state.list_categories
    assert "gone" not in state.list_categorization_rules


def test_meal_plan_rejects_old_response_version_and_cross_calendar_delta() -> None:
    state = AnyListState(meal_plan_calendar_id="calendar-a", meal_plan_logical_timestamp=4)
    state.meal_plan_events["existing"] = PB.PBCalendarEvent(identifier="existing", calendarId="calendar-a")

    old = PB.PBCalendarResponse(
        calendarId="calendar-a", logicalTimestamp=99, responseVersion=0, isFullSync=True
    )
    old.events.add(identifier="bad", calendarId="calendar-a")
    state.apply_meal_plan(old)
    assert state.meal_plan_logical_timestamp == 4
    assert set(state.meal_plan_events) == {"existing"}

    other_delta = PB.PBCalendarResponse(
        calendarId="calendar-b", logicalTimestamp=8, responseVersion=1, isFullSync=False
    )
    other_delta.events.add(identifier="other", calendarId="calendar-b")
    state.apply_meal_plan(other_delta)
    assert state.meal_plan_calendar_id == "calendar-a"
    assert set(state.meal_plan_events) == {"existing"}


def test_meal_plan_full_sync_can_switch_calendar_and_caps_processed_version() -> None:
    state = AnyListState(meal_plan_calendar_id="calendar-a", meal_plan_response_version=1)
    state.meal_plan_events["old"] = PB.PBCalendarEvent(identifier="old", calendarId="calendar-a")
    response = PB.PBCalendarResponse(
        calendarId="calendar-b", logicalTimestamp=10, responseVersion=7, isFullSync=True
    )
    response.events.add(identifier="new", calendarId="calendar-b")

    state.apply_meal_plan(response)

    assert state.meal_plan_calendar_id == "calendar-b"
    assert state.meal_plan_logical_timestamp == 10
    assert state.meal_plan_response_version == 1
    assert set(state.meal_plan_events) == {"new"}
