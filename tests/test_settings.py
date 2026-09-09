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
