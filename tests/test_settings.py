from __future__ import annotations

import pytest

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
    assert save_op.updatedSettings.timestamp == 7.0

    await service.remove_recipe_cooking_states([cooking])
    remove_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert remove_op.updatedSettings.timestamp == 7.0

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
async def test_mobile_selected_defaults_suppress_equivalent_mutations(fake_transport) -> None:
    state = AnyListState(user_id="user")
    settings = PB.PBMobileAppSettings(identifier="mobile", timestamp=1.0, defaultListId="default")
    state.mobile_app_settings = settings
    service = MobileSettingsService(fake_transport, state, user_id="user")

    await service.set("webSelectedListId", "default")
    await service.set("webRecipeCollectionLayoutStyle", 1)
    await service.set("webSelectedMealPlanTab", 0)
    await service.set("webMealPlanWeekEventListType", 1)
    await service.set("webMealPlanNotesSortOrder", 5)

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
