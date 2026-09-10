from __future__ import annotations

from uuid import UUID

import pytest

from anylist_sdk.identifiers import uuid5_hex
from anylist_sdk.proto import PB
from anylist_sdk.services.settings import ListSettingsService, MobileSettingsService
from anylist_sdk.state import AnyListState


@pytest.mark.asyncio
async def test_migrated_category_group_operation_uses_list_category_group_id(fake_transport) -> None:
    state = AnyListState(user_id="user")
    service = ListSettingsService(fake_transport, state, user_id="user")

    settings = await service.set_migrated_list_category_group_id("list", "group")

    assert settings.listCategoryGroupId == "group"
    assert not settings.HasField("migrationListCategoryGroupIdForNewList")
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "set-migrated-list-category-group-id"
    assert operation.updatedSettings.listCategoryGroupId == "group"
    assert not operation.updatedSettings.HasField("migrationListCategoryGroupIdForNewList")


@pytest.mark.asyncio
async def test_list_settings_does_not_invent_handler_for_schema_only_field(fake_transport) -> None:
    state = AnyListState(user_id="user")
    service = ListSettingsService(fake_transport, state, user_id="user")
    with pytest.raises(ValueError):
        await service.set("list", "listColorType", 1)


@pytest.mark.asyncio
async def test_mobile_settings_does_not_invent_handler_for_read_only_web_field(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.mobile_app_settings = PB.PBMobileAppSettings(identifier="user", timestamp=1)
    service = MobileSettingsService(fake_transport, state, user_id="user")
    with pytest.raises(ValueError):
        await service.set("webCurrencyCode", "EUR")


@pytest.mark.asyncio
async def test_web_selected_meal_plan_event_handler_is_blocked_by_official_schema_gap(
    fake_transport,
) -> None:
    # app.js calls setWebSelectedMealPlanEventId() and queues this handler, but the embedded
    # PBMobileAppSettings schema has no webSelectedMealPlanEventId field. Keep the handler
    # accounted for without inventing an unencodable protobuf field.
    assert "webSelectedMealPlanEventId" not in PB.PBMobileAppSettings.DESCRIPTOR.fields_by_name
    state = AnyListState(user_id="user")
    state.mobile_app_settings = PB.PBMobileAppSettings(identifier="user", timestamp=1)
    service = MobileSettingsService(fake_transport, state, user_id="user")

    with pytest.raises(TypeError, match="webSelectedMealPlanEventId"):
        await service.set(
            "webSelectedMealPlanEventId",
            "event",
            handler_id="set-web-selected-meal-plan-event-id",
        )


@pytest.mark.asyncio
async def test_list_settings_partial_carries_object_timestamp(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", timestamp=42.5
    )
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.set("list", "shouldHidePrices", True)

    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.updatedSettings.timestamp == 42.5


@pytest.mark.asyncio
async def test_mobile_settings_partial_carries_current_timestamp(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.mobile_app_settings = PB.PBMobileAppSettings(
        identifier="mobile", timestamp=88.25
    )
    service = MobileSettingsService(fake_transport, state, user_id="user")

    await service.set("webSelectedTabId", "lists")

    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.updatedSettings.identifier == "mobile"
    assert operation.updatedSettings.timestamp == 88.25
    assert operation.updatedSettings.webSelectedTabId == "lists"


@pytest.mark.asyncio
async def test_mobile_recipe_cooking_state_operations_carry_timestamp(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.mobile_app_settings = PB.PBMobileAppSettings(
        identifier="mobile", timestamp=7.0
    )
    service = MobileSettingsService(fake_transport, state, user_id="user")
    cooking = PB.PBRecipeCookingState(recipeId="recipe")

    await service.save_recipe_cooking_states([cooking])
    save_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert save_op.metadata.handlerId == "save-recipe-cooking-states"
    assert save_op.updatedSettings.timestamp == 7.0
    assert list(save_op.updatedSettings.recipeCookingStates) == [cooking]

    await service.remove_recipe_cooking_states([cooking])
    remove_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert remove_op.metadata.handlerId == "remove-recipe-cooking-states"
    assert remove_op.updatedSettings.timestamp == 7.0
    assert list(remove_op.updatedSettings.recipeCookingStates) == [cooking]

@pytest.mark.asyncio
async def test_clear_store_filter_id_uses_official_handler_with_absent_field(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", timestamp=3.5, storeFilterId="filter"
    )
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.clear_store_filter_id("list")

    assert not state.list_settings["list"].HasField("storeFilterId")
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "set-store-filter-id"
    assert op.updatedSettings.timestamp == 3.5
    assert not op.updatedSettings.HasField("storeFilterId")

@pytest.mark.asyncio
async def test_list_settings_suppresses_official_unchanged_mutation(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", shouldHidePrices=True
    )
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.set("list", "shouldHidePrices", True)

    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_list_settings_remove_sends_only_identity_timestamp_partial(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings",
        userId="user",
        listId="list",
        timestamp=4.5,
        shouldHidePrices=True,
        badgeMode="all",
    )
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.remove("list")

    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "remove-list-settings"
    sent = operation.updatedSettings
    assert sent.identifier == "settings"
    assert sent.userId == "user"
    assert sent.listId == "list"
    assert sent.timestamp == 4.5
    assert not sent.HasField("shouldHidePrices")
    assert not sent.HasField("badgeMode")


@pytest.mark.asyncio
async def test_list_settings_absent_icon_to_null_still_queues_official_handler(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", timestamp=2.0
    )
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.set("list", "icon", None)

    assert len(fake_transport.calls) == 1
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "set-icon"
    assert operation.updatedSettings.identifier == "settings"
    assert operation.updatedSettings.timestamp == 2.0
    assert not operation.updatedSettings.HasField("icon")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "handler"),
    [
        ("shouldHideCategories", True, "set-should-hide-categories"),
        ("shouldHideCompletedItems", True, "set-should-hide-completed-items"),
        ("shouldHideStoreNames", True, "set-should-hide-store-names"),
        ("shouldHideRunningTotals", True, "set-should-hide-running-total-bar"),
        ("badgeMode", "all", "set-badge-mode"),
        ("favoritesAutocompleteEnabled", True, "set-favorites-autocomplete-enabled"),
        (
            "genericGroceryAutocompleteEnabled",
            True,
            "set-generic-grocery-autocomplete-enabled",
        ),
        ("leftRunningTotalType", 1, "set-left-running-total-type"),
        ("recentItemsAutocompleteEnabled", True, "set-recent-items-autocomplete-enabled"),
        ("rightRunningTotalType", 2, "set-right-running-total-type"),
        ("shouldRememberItemCategories", True, "set-should-remember-item-categories"),
        ("listItemSortOrder", "alphabetical", "set-list-item-sort-order"),
        ("listCategoryGroupId", "group", "set-list-category-group-id"),
        ("locationNotificationsEnabled", True, "set-location-notifications-enabled"),
        (
            "shouldShowSharedListCategoryOrderHintBanner",
            True,
            "set-should-show-shared-list-category-order-hint-banner",
        ),
    ],
)
async def test_list_settings_scalar_handler_contracts(
    fake_transport, field: str, value: object, handler: str
) -> None:
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", timestamp=12.5
    )
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.set("list", field, value)

    assert getattr(state.list_settings["list"], field) == value
    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/list-settings/update"
    assert response_type == "PBEditOperationResponse"
    operation = fields["operations"].operations[0]
    assert operation.DESCRIPTOR.name == "PBListSettingsOperation"
    assert operation.metadata.handlerId == handler
    assert operation.metadata.userId == "user"
    sent = operation.updatedSettings
    assert sent.identifier == "settings"
    assert sent.userId == "user"
    assert sent.listId == "list"
    assert sent.timestamp == 12.5
    assert getattr(sent, field) == value
    assert {descriptor.name for descriptor, _ in sent.ListFields()} == {
        "identifier",
        "userId",
        "listId",
        "timestamp",
        field,
    }


@pytest.mark.asyncio
async def test_starter_list_settings_mutations_use_separate_official_queue(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.starter_list_settings["starter"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="starter", timestamp=3.0
    )
    service = ListSettingsService(fake_transport, state, user_id="user", starter=True)

    await service.set("starter", "shouldHideCategories", True)

    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/starter-list-settings/update"
    assert response_type == "PBEditOperationResponse"
    operation = fields["operations"].operations[0]
    assert operation.metadata.handlerId == "set-should-hide-categories"
    assert operation.updatedSettings.listId == "starter"


@pytest.mark.asyncio
async def test_list_settings_custom_theme_always_queues_exact_partial(fake_transport) -> None:
    state = AnyListState(user_id="user")
    theme = PB.PBListTheme()
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", timestamp=6.0
    )
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.set("list", "customTheme", theme)

    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "save-custom-theme"
    sent = operation.updatedSettings
    assert sent.HasField("customTheme")
    assert {descriptor.name for descriptor, _ in sent.ListFields()} == {
        "identifier",
        "userId",
        "listId",
        "timestamp",
        "customTheme",
    }


@pytest.mark.asyncio
async def test_list_settings_can_clear_optional_scalar_with_absent_wire_field(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", listThemeId="theme"
    )
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.set("list", "listThemeId", None)

    assert not state.list_settings["list"].HasField("listThemeId")
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "set-list-theme-id"
    assert not op.updatedSettings.HasField("listThemeId")


@pytest.mark.asyncio
async def test_new_list_settings_initialization_matches_official_grocery_batch(fake_transport) -> None:
    state = AnyListState(user_id="user")
    service = ListSettingsService(fake_transport, state, user_id="user")

    settings = await service.initialize_new_list("list", "group")

    assert settings.listThemeId == "7b1dd303fb6a44fbbed44667f199aa63"
    assert settings.shouldHideCategories is False
    assert settings.genericGroceryAutocompleteEnabled is True
    assert settings.listItemSortOrder == "ALListItemSortOrderAlphabetical"
    assert settings.listCategoryGroupId == "group"
    assert settings.shouldRememberItemCategories is True
    assert settings.favoritesAutocompleteEnabled is True
    assert settings.recentItemsAutocompleteEnabled is True
    assert not settings.HasField("categoryGroupingId")

    assert len(fake_transport.calls) == 1
    operations = fake_transport.calls[0][1]["operations"].operations
    assert [operation.metadata.handlerId for operation in operations] == [
        "set-list-theme-id",
        "set-should-hide-categories",
        "set-generic-grocery-autocomplete-enabled",
        "set-list-item-sort-order",
        "set-list-category-group-id",
        "set-should-remember-item-categories",
        "set-favorites-autocomplete-enabled",
        "set-recent-items-autocomplete-enabled",
    ]


@pytest.mark.asyncio
async def test_new_list_settings_copies_selected_custom_new_list_theme(fake_transport) -> None:
    state = AnyListState(user_id="user")
    service = ListSettingsService(fake_transport, state, user_id="user")
    template_custom_id = uuid5_hex(
        "userALCustomSettingsListID", UUID(hex="471ba5c9888f4f30a159308708ba7949")
    )
    template = PB.PBListSettings(
        identifier="template-settings",
        userId="user",
        listId="ALCustomSettingsListID",
        listThemeId=template_custom_id,
    )
    template.customTheme.CopyFrom(
        PB.PBListTheme(
            identifier=template_custom_id,
            userId="user",
            name="My Custom Theme",
            bannerHexColor="ABCDEF",
        )
    )
    state.list_settings["ALCustomSettingsListID"] = template

    settings = await service.initialize_new_list("list", "group")

    expected_id = uuid5_hex(
        "userlist", UUID(hex="471ba5c9888f4f30a159308708ba7949")
    )
    assert settings.listThemeId == expected_id
    assert settings.customTheme.identifier == expected_id
    assert settings.customTheme.userId == "user"
    assert settings.customTheme.name == "My Custom Theme"
    assert settings.customTheme.bannerHexColor == "ABCDEF"
    operations = fake_transport.calls[0][1]["operations"].operations
    assert operations[0].metadata.handlerId == "save-custom-theme"
    assert operations[1].metadata.handlerId == "set-list-theme-id"


@pytest.mark.asyncio
async def test_mobile_selected_defaults_suppress_equivalent_mutations(fake_transport) -> None:
    state = AnyListState(user_id="user")
    settings = PB.PBMobileAppSettings(identifier="mobile", timestamp=1.0, defaultListId="default")
    state.mobile_app_settings = settings
    service = MobileSettingsService(fake_transport, state, user_id="user")

    await service.set("webSelectedListId", "default")
    await service.set("webSelectedMealPlanTab", 0)
    await service.set("webMealPlanWeekEventListType", 1)
    await service.set("webMealPlanNotesSortOrder", 5)

    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_mobile_recipe_collection_layout_setter_compares_raw_optional_field(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.mobile_app_settings = PB.PBMobileAppSettings(identifier="mobile", timestamp=1.0)
    service = MobileSettingsService(fake_transport, state, user_id="user")

    # KT() defaults an absent field to 1 for reads, but QT() compares against the raw
    # protobuf property. Therefore absent -> 1 is still a real mutation in the web client.
    await service.set("webRecipeCollectionLayoutStyle", 1)

    assert len(fake_transport.calls) == 1
    op = fake_transport.calls[0][1]["operations"].operations[0]
    assert op.metadata.handlerId == "set-web-recipe-collection-layout-style"
    assert op.updatedSettings.webRecipeCollectionLayoutStyle == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "handler"),
    [
        ("listIdForRecipeIngredients", "list", "set-list-id-for-recipe-ingredients"),
        ("webSelectedListId", "list", "set-web-selected-list-id"),
        ("webSelectedRecipeId", "recipe", "set-web-selected-recipe-id"),
        (
            "webSelectedRecipeCollectionId",
            "collection",
            "set-web-selected-recipe-collection-id",
        ),
        (
            "webSelectedRecipeCollectionType",
            1,
            "set-web-selected-recipe-collection-type",
        ),
        ("webSelectedListFolderPath", "root/folder", "set-web-selected-list-folder-path"),
        ("webSelectedTabId", "recipes", "set-web-selected-tab-id"),
        ("webSelectedMealPlanTab", 1, "set-web-selected-meal-plan-tab-v2"),
        ("webMealPlanCalendarLayout", 1, "set-web-meal-plan-calendar-layout"),
        ("webMealPlanMonthEventListType", 1, "set-web-meal-plan-month-event-list-type"),
        ("webMealPlanWeekEventListType", 0, "set-web-meal-plan-week-event-list-type"),
        ("webMealPlanNotesSortOrder", 4, "set-web-meal-plan-notes-sort-order"),
        (
            "webHasHiddenStoresAndFiltersHelp",
            True,
            "set-web-has-hidden-stores-and-filters-help",
        ),
        ("webHasHiddenItemPricesHelp", True, "set-web-has-hidden-item-prices-help"),
        ("didSuppressAccountNamePrompt", True, "set-did-suppress-account-name-prompt"),
        (
            "hasMigratedUserCategoriesToListCategories",
            True,
            "set-has-migrated-user-categories-to-list-categories",
        ),
        (
            "shouldExcludeNewListsFromAlexaByDefault",
            True,
            "set-should-exclude-new-lists-from-alexa-by-default",
        ),
        (
            "webMealPlanAddEntriesScreenPinnedEntriesCollapsed",
            True,
            "set-web-meal-plan-add-entries-screen-pinned-entries-collapsed",
        ),
        (
            "webMealPlanAddEntriesScreenQueueEntriesCollapsed",
            True,
            "set-web-meal-plan-add-entries-screen-queue-entries-collapsed",
        ),
    ],
)
async def test_mobile_settings_scalar_handler_contracts(
    fake_transport, field: str, value: object, handler: str
) -> None:
    state = AnyListState(user_id="user")
    state.mobile_app_settings = PB.PBMobileAppSettings(identifier="mobile", timestamp=9.25)
    service = MobileSettingsService(fake_transport, state, user_id="user")

    await service.set(field, value)

    assert getattr(state.mobile_app_settings, field) == value
    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/mobile-app-settings/update"
    assert response_type == "PBEditOperationResponse"
    operation = fields["operations"].operations[0]
    assert operation.DESCRIPTOR.name == "PBMobileAppSettingsOperation"
    assert operation.metadata.handlerId == handler
    assert operation.metadata.userId == "user"
    sent = operation.updatedSettings
    assert sent.identifier == "mobile"
    assert sent.timestamp == 9.25
    assert getattr(sent, field) == value
    assert {descriptor.name for descriptor, _ in sent.ListFields()} == {
        "identifier",
        "timestamp",
        field,
    }


@pytest.mark.asyncio
async def test_list_settings_refresh_returns_before_http_while_edit_queue_pending(fake_transport) -> None:
    state = AnyListState(user_id="user")
    service = ListSettingsService(fake_transport, state, user_id="user")
    await service.queue.enqueue(service.queue.new_operation("set-should-hide-prices"), flush=False)

    result = await service.refresh()

    assert result is None
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_mobile_settings_refresh_returns_before_http_while_edit_queue_pending(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.mobile_app_settings = PB.PBMobileAppSettings(identifier="mobile", timestamp=1.0)
    service = MobileSettingsService(fake_transport, state, user_id="user")
    await service.queue.enqueue(service.queue.new_operation("set-web-selected-tab-id"), flush=False)

    result = await service.refresh()

    assert result is None
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_mobile_optional_selection_can_be_cleared(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.mobile_app_settings = PB.PBMobileAppSettings(
        identifier="mobile", timestamp=1.0, webSelectedRecipeId="recipe"
    )
    service = MobileSettingsService(fake_transport, state, user_id="user")

    await service.set("webSelectedRecipeId", None)

    assert not state.mobile_app_settings.HasField("webSelectedRecipeId")
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert not op.updatedSettings.HasField("webSelectedRecipeId")


@pytest.mark.asyncio
async def test_recipe_cooking_states_are_keyed_by_recipe_and_event(fake_transport) -> None:
    state = AnyListState(user_id="user")
    settings = PB.PBMobileAppSettings(identifier="mobile", timestamp=1.0)
    settings.recipeCookingStates.add(recipeId="recipe", eventId="event-a", selectedStepNumber=1)
    settings.recipeCookingStates.add(recipeId="recipe", eventId="event-b", selectedStepNumber=2)
    state.mobile_app_settings = settings
    service = MobileSettingsService(fake_transport, state, user_id="user")

    await service.save_recipe_cooking_states([
        PB.PBRecipeCookingState(recipeId="recipe", eventId="event-a", selectedStepNumber=3)
    ])

    values = {(x.recipeId, x.eventId): x.selectedStepNumber for x in settings.recipeCookingStates}
    assert values == {("recipe", "event-a"): 3, ("recipe", "event-b"): 2}

    await service.remove_recipe_cooking_states([
        PB.PBRecipeCookingState(recipeId="recipe", eventId="event-a")
    ])
    assert [(x.recipeId, x.eventId) for x in settings.recipeCookingStates] == [("recipe", "event-b")]


@pytest.mark.asyncio
async def test_list_settings_direct_refresh_uses_stable_official_timestamp_id(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.list_settings_timestamp = 8.5
    state.list_settings_timestamp_id = "loaded"
    response = PB.PBListSettingsList()
    response.timestamp.identifier = "delta"
    response.timestamp.timestamp = 9.0
    fake_transport.responses.append(response)
    service = ListSettingsService(fake_transport, state, user_id="user")

    await service.refresh()

    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/list-settings/all"
    assert response_type == "PBListSettingsList"
    assert fields["timestamp"].identifier == "list-settings-timestamp"
    assert fields["timestamp"].timestamp == 8.5
    assert state.list_settings_timestamp == 9.0
    assert state.list_settings_timestamp_id == "list-settings-timestamp"


@pytest.mark.asyncio
async def test_starter_settings_direct_refresh_uses_same_official_timestamp_id(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.starter_list_settings_timestamp = 3.0
    state.starter_list_settings_timestamp_id = "loaded"
    fake_transport.responses.append(PB.PBListSettingsList())
    service = ListSettingsService(fake_transport, state, user_id="user", starter=True)

    await service.refresh()

    endpoint, fields, _ = fake_transport.calls[-1]
    assert endpoint == "/data/starter-list-settings/all"
    assert fields["timestamp"].identifier == "list-settings-timestamp"


@pytest.mark.asyncio
async def test_settings_refresh_does_not_overwrite_optimistic_state_while_queue_pending(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", shouldHidePrices=True
    )
    state.list_settings_timestamp_id = "loaded"
    state.list_settings_timestamp = 1
    service = ListSettingsService(fake_transport, state, user_id="user")
    await service.set("list", "shouldHidePrices", False, flush=False)
    response = PB.PBListSettingsList()
    response.timestamp.identifier = "all"
    response.timestamp.timestamp = 2
    response.settings.add(
        identifier="settings", userId="user", listId="list", shouldHidePrices=True
    )
    fake_transport.responses.append(response)

    await service.refresh()

    assert state.list_settings["list"].shouldHidePrices is False
    assert state.list_settings_timestamp == 1
