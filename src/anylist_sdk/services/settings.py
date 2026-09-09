from __future__ import annotations

import hashlib
from typing import Any

from google.protobuf.message import Message

from ..operations import QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message


def _present_value(message: Message, field: str) -> Any:
    """Return proto2 optional fields as None when absent, matching protobuf.js v5."""
    descriptor = message.DESCRIPTOR.fields_by_name[field]
    if descriptor.is_repeated:
        return list(getattr(message, field))
    try:
        if not message.HasField(field):
            return None
    except ValueError:
        pass
    return getattr(message, field)


def _set_proto_field(message: Message, field: str, value: Any) -> None:
    descriptor = message.DESCRIPTOR.fields_by_name[field]
    target = getattr(message, field)
    if descriptor.is_repeated:
        del target[:]
        if descriptor.message_type:
            for item in value:
                target.add().CopyFrom(item)
        else:
            target.extend(value)
    elif value is None:
        message.ClearField(field)
    elif descriptor.message_type:
        target.CopyFrom(value)
    else:
        setattr(message, field, value)


def _mobile_effective_value(settings: Message, field: str) -> Any:
    raw = _present_value(settings, field)
    if field == "webSelectedListId":
        return raw or (_present_value(settings, "defaultListId") or None)
    if field in {
        "webSelectedRecipeId", "webSelectedRecipeCollectionId",
        "webSelectedListFolderPath", "webSelectedTabId",
    }:
        return raw or None
    if field == "webRecipeCollectionLayoutStyle":
        return 1 if raw is None else raw
    if field == "webSelectedMealPlanTab":
        return 0 if raw is None else raw
    if field == "webMealPlanCalendarLayout":
        if raw is not None and 0 <= int(raw) < 2:
            return raw
        deprecated = _present_value(settings, "webSelectedMealPlanTabDeprecated")
        if deprecated == 0:
            return 1
        if deprecated == 1:
            return 0
        return 0
    if field == "webMealPlanMonthEventListType":
        return raw if raw is not None and 0 <= int(raw) < 2 else 0
    if field == "webMealPlanWeekEventListType":
        return raw if raw is not None and 0 <= int(raw) < 2 else 1
    if field == "webMealPlanNotesSortOrder":
        return raw if raw is not None and 0 <= int(raw) < 6 else 5
    if field in {
        "webMealPlanAddEntriesScreenPinnedEntriesCollapsed",
        "webMealPlanAddEntriesScreenQueueEntriesCollapsed",
    }:
        return False if raw is None else raw
    return raw


class ListSettingsService(OperationService):
    def __init__(self, transport: AnyListTransport, state: AnyListState, *, user_id: str,
                 starter: bool = False, journal=None):
        self.starter = starter
        endpoint = "/data/starter-list-settings/update" if starter else "/data/list-settings/update"
        self.read_endpoint = "/data/starter-list-settings/all" if starter else "/data/list-settings/all"
        qid = "starter-list-settings" if starter else "list-settings"
        super().__init__(transport, state, user_id=user_id,
            spec=QueueSpec(f"{user_id}:{qid}", endpoint,
                           "PBListSettingsOperation", "PBListSettingsOperationList"), journal=journal)
        self.user_id = user_id
        self.queue.on_response = self._on_response

    async def _on_response(self,response:Message)->None:
        if not response.originalTimestamps or not response.newTimestamps:return
        original=float(response.originalTimestamps[0].timestamp)
        current=(self.state.starter_list_settings_timestamp if self.starter
                 else self.state.list_settings_timestamp)
        if original==float(current):
            value=float(response.newTimestamps[0].timestamp)
            if self.starter:self.state.starter_list_settings_timestamp=value
            else:self.state.list_settings_timestamp=value
        else:
            await self.refresh()

    @property
    def _store(self):
        return self.state.starter_list_settings if self.starter else self.state.list_settings

    def get(self, list_id: str = "") -> Message | None:
        # State is keyed by list ID during apply; default settings may be stored under "".
        return self._store.get(list_id)

    def ensure(self, list_id: str = "") -> Message:
        value = self.get(list_id)
        if value is not None: return value
        # Official JS identifies per-list settings with md5(userId + "-" + listId).
        identifier = hashlib.md5(f"{self.user_id}-{list_id}".encode()).hexdigest()
        value = PB.PBListSettings(identifier=identifier, userId=self.user_id)
        if list_id: value.listId = list_id
        self._store[list_id] = value
        return value

    async def set(self, list_id: str, field: str, value: Any, *, handler_id: str | None = None,
                  flush: bool = True) -> Message:
        settings = self.ensure(list_id)
        desc = settings.DESCRIPTOR.fields_by_name.get(field)
        if desc is None: raise TypeError(f"PBListSettings has no field {field!r}")
        official_handlers = {
            "shouldHideCategories": "set-should-hide-categories",
            "shouldHideCompletedItems": "set-should-hide-completed-items",
            "shouldHideStoreNames": "set-should-hide-store-names",
            "shouldHidePrices": "set-should-hide-prices",
            "shouldHideRunningTotals": "set-should-hide-running-total-bar",
            "listThemeId": "set-list-theme-id",
            "icon": "set-icon",
            "listCategoryGroupId": "set-list-category-group-id",
            "genericGroceryAutocompleteEnabled": "set-generic-grocery-autocomplete-enabled",
            "favoritesAutocompleteEnabled": "set-favorites-autocomplete-enabled",
            "recentItemsAutocompleteEnabled": "set-recent-items-autocomplete-enabled",
            "shouldRememberItemCategories": "set-should-remember-item-categories",
            "listItemSortOrder": "set-list-item-sort-order",
            "storeFilterId": "set-store-filter-id",
            "leftRunningTotalType": "set-left-running-total-type",
            "rightRunningTotalType": "set-right-running-total-type",
            "badgeMode": "set-badge-mode",
            "locationNotificationsEnabled": "set-location-notifications-enabled",
            "customTheme": "save-custom-theme",
            "shouldShowSharedListCategoryOrderHintBanner": "set-should-show-shared-list-category-order-hint-banner",
        }
        if handler_id is None:
            handler_id = official_handlers.get(field)
            if handler_id is None:
                raise ValueError(
                    f"The official web client has no generic list-settings mutation for {field!r}; "
                    "pass an explicitly proven handler_id only when reproducing an official operation"
                )
        no_op_if_unchanged = field not in {
            "customTheme", "shouldShowSharedListCategoryOrderHintBanner"
        }
        if no_op_if_unchanged and _present_value(settings, field) == value:
            return settings
        _set_proto_field(settings, field, value)
        partial = PB.PBListSettings(identifier=settings.identifier)
        if settings.userId: partial.userId = settings.userId
        if settings.listId: partial.listId = settings.listId
        # The official qF() helper copies the per-settings timestamp into every partial
        # PBListSettings mutation.  Keep it even though the queue response also carries
        # the manager-level timestamp; the server uses the embedded value for conflict
        # semantics on this specific settings object.
        if settings.HasField("timestamp"):
            partial.timestamp = settings.timestamp
        _set_proto_field(partial, field, value)
        await self.operation(handler_id, updatedSettings=partial, flush=flush)
        return settings

    async def clear_store_filter_id(self, list_id: str, *, flush: bool = True) -> Message:
        """Clear the selected store filter using the official set-store-filter-id handler."""
        settings = self.ensure(list_id)
        if not settings.HasField("storeFilterId") or not settings.storeFilterId:
            return settings
        settings.ClearField("storeFilterId")
        partial = PB.PBListSettings(identifier=settings.identifier)
        if settings.userId:
            partial.userId = settings.userId
        if settings.listId:
            partial.listId = settings.listId
        if settings.HasField("timestamp"):
            partial.timestamp = settings.timestamp
        # AnyList Web calls setStoreFilterId(null), which leaves the optional protobuf field
        # absent while the handler ID communicates the clear operation.
        await self.operation("set-store-filter-id", updatedSettings=partial, flush=flush)
        return settings

    async def set_migrated_list_category_group_id(
        self, list_id: str, category_group_id: str, *, flush: bool = True
    ) -> Message:
        """Mirror the web client's migration-only category-group mutation.

        Despite the handler name, the official client writes ``listCategoryGroupId`` in
        ``updatedSettings``; it does not write ``migrationListCategoryGroupIdForNewList``.
        """
        return await self.set(
            list_id,
            "listCategoryGroupId",
            category_group_id,
            handler_id="set-migrated-list-category-group-id",
            flush=flush,
        )

    async def remove(self, list_id: str, *, flush: bool = True) -> None:
        settings = self._store.pop(list_id, None)
        if settings is None: return
        await self.operation("remove-list-settings", updatedSettings=settings, flush=flush)


class MobileSettingsService(OperationService):
    def __init__(self, transport: AnyListTransport, state: AnyListState, *, user_id: str, journal=None):
        super().__init__(transport, state, user_id=user_id,
            spec=QueueSpec(f"{user_id}:mobile-settings", "/data/mobile-app-settings/update",
                           "PBMobileAppSettingsOperation", "PBMobileAppSettingsOperationList"), journal=journal)
        self.queue.on_response = self._on_response

    async def _on_response(self,response:Message)->None:
        if not response.originalTimestamps or not response.newTimestamps:return
        settings=self.state.mobile_app_settings
        if settings is None:return
        original=float(response.originalTimestamps[0].timestamp)
        if original==float(settings.timestamp):settings.timestamp=response.newTimestamps[0].timestamp
        else:await self.refresh()

    async def refresh(self) -> Message:
        fields: dict[str, Message] = {}
        settings = self.state.mobile_app_settings
        if settings is not None:
            fields["timestamp"] = PB.PBTimestamp(
                identifier="mobile-app-settings-timestamp", timestamp=settings.timestamp
            )
        response = await self.transport.post_proto(
            "/data/mobile-app-settings/by-id", fields=fields, response_type="PBMobileAppSettings"
        )
        assert isinstance(response, Message)
        self.state.apply_mobile_settings(response)
        return response

    def get(self) -> Message | None: return self.state.mobile_app_settings

    async def set(self, field: str, value: Any, *, handler_id: str | None = None,
                  flush: bool = True) -> Message:
        settings = self.state.mobile_app_settings
        if settings is None: raise RuntimeError("Mobile app settings have not been synchronized")
        desc = settings.DESCRIPTOR.fields_by_name.get(field)
        if desc is None: raise TypeError(f"PBMobileAppSettings has no field {field!r}")
        # AnyList Web's UT() helper seeds every mobile-settings operation with both
        # identifier and the current timestamp before setting the changed field.
        names = {
            "listIdForRecipeIngredients":"set-list-id-for-recipe-ingredients",
            "webSelectedListId":"set-web-selected-list-id",
            "webSelectedRecipeId":"set-web-selected-recipe-id",
            "webSelectedRecipeCollectionId":"set-web-selected-recipe-collection-id",
            "webSelectedRecipeCollectionType":"set-web-selected-recipe-collection-type",
            "webRecipeCollectionLayoutStyle":"set-web-recipe-collection-layout-style",
            "webSelectedListFolderPath":"set-web-selected-list-folder-path",
            "webSelectedTabId":"set-web-selected-tab-id",
            "webSelectedMealPlanTab":"set-web-selected-meal-plan-tab-v2",
            "webSelectedMealPlanEventId":"set-web-selected-meal-plan-event-id",
            "webMealPlanCalendarLayout":"set-web-meal-plan-calendar-layout",
            "webMealPlanMonthEventListType":"set-web-meal-plan-month-event-list-type",
            "webMealPlanWeekEventListType":"set-web-meal-plan-week-event-list-type",
            "webMealPlanNotesSortOrder":"set-web-meal-plan-notes-sort-order",
            "webHasHiddenStoresAndFiltersHelp":"set-web-has-hidden-stores-and-filters-help",
            "webHasHiddenItemPricesHelp":"set-web-has-hidden-item-prices-help",
            "didSuppressAccountNamePrompt":"set-did-suppress-account-name-prompt",
            "recipeCookingStates":"save-recipe-cooking-states",
            "hasMigratedUserCategoriesToListCategories":"set-has-migrated-user-categories-to-list-categories",
            "shouldExcludeNewListsFromAlexaByDefault":"set-should-exclude-new-lists-from-alexa-by-default",
            "webMealPlanAddEntriesScreenPinnedEntriesCollapsed":"set-web-meal-plan-add-entries-screen-pinned-entries-collapsed",
            "webMealPlanAddEntriesScreenQueueEntriesCollapsed":"set-web-meal-plan-add-entries-screen-queue-entries-collapsed",
        }
        if handler_id is None:
            handler_id = names.get(field)
            if handler_id is None:
                raise ValueError(
                    f"The official web client has no generic mobile-settings mutation for {field!r}; "
                    "pass an explicitly proven handler_id only when reproducing an official operation"
                )
        no_op_fields = {
            "webSelectedListId", "webSelectedRecipeId", "webSelectedRecipeCollectionId",
            "webSelectedRecipeCollectionType", "webRecipeCollectionLayoutStyle",
            "webSelectedListFolderPath", "webSelectedTabId", "webSelectedMealPlanTab",
            "webSelectedMealPlanEventId", "webMealPlanCalendarLayout",
            "webMealPlanMonthEventListType", "webMealPlanWeekEventListType",
            "webMealPlanNotesSortOrder", "webHasHiddenStoresAndFiltersHelp",
            "webHasHiddenItemPricesHelp",
            "webMealPlanAddEntriesScreenPinnedEntriesCollapsed",
            "webMealPlanAddEntriesScreenQueueEntriesCollapsed",
        }
        if field in no_op_fields and _mobile_effective_value(settings, field) == value:
            return settings
        _set_proto_field(settings, field, value)
        partial = PB.PBMobileAppSettings(identifier=settings.identifier, timestamp=settings.timestamp)
        _set_proto_field(partial, field, value)
        await self.operation(handler_id, updatedSettings=partial, flush=flush)
        return settings
    async def save_recipe_cooking_states(
        self, states: list[Message], *, flush: bool = True
    ) -> str:
        settings = self.state.mobile_app_settings
        if settings is None:
            raise RuntimeError("Mobile app settings have not been synchronized")
        by_id = {
            (str(x.recipeId or ""), str(x.eventId or "")): clone_message(x)
            for x in settings.recipeCookingStates
        }
        for value in states:
            key = (str(value.recipeId or ""), str(value.eventId or ""))
            by_id[key] = clone_message(value)
        del settings.recipeCookingStates[:]
        for value in by_id.values():
            settings.recipeCookingStates.add().CopyFrom(value)
        partial = PB.PBMobileAppSettings(identifier=settings.identifier, timestamp=settings.timestamp)
        for value in states:
            partial.recipeCookingStates.add().CopyFrom(value)
        return await self.operation(
            "save-recipe-cooking-states", updatedSettings=partial, flush=flush
        )

    async def remove_recipe_cooking_states(
        self, states: list[Message], *, flush: bool = True
    ) -> str:
        settings = self.state.mobile_app_settings
        if settings is None:
            raise RuntimeError("Mobile app settings have not been synchronized")
        remove_ids = {
            (str(x.recipeId or ""), str(x.eventId or "")) for x in states
        }
        kept = [
            clone_message(x)
            for x in settings.recipeCookingStates
            if (str(x.recipeId or ""), str(x.eventId or "")) not in remove_ids
        ]
        del settings.recipeCookingStates[:]
        for value in kept:
            settings.recipeCookingStates.add().CopyFrom(value)
        partial = PB.PBMobileAppSettings(identifier=settings.identifier, timestamp=settings.timestamp)
        for value in states:
            partial.recipeCookingStates.add().CopyFrom(value)
        return await self.operation(
            "remove-recipe-cooking-states", updatedSettings=partial, flush=flush
        )

