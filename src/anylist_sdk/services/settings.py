from __future__ import annotations

import hashlib
from typing import Any

from google.protobuf.message import Message

from ..operations import QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message


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
        target = getattr(settings, field)
        if desc.is_repeated:
            del target[:]
            if desc.message_type:
                for v in value: target.add().CopyFrom(v)
            else: target.extend(value)
        elif desc.message_type:
            target.CopyFrom(value)
        else:
            setattr(settings, field, value)
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
        partial = PB.PBListSettings(identifier=settings.identifier)
        if settings.userId: partial.userId = settings.userId
        if settings.listId: partial.listId = settings.listId
        # The official qF() helper copies the per-settings timestamp into every partial
        # PBListSettings mutation.  Keep it even though the queue response also carries
        # the manager-level timestamp; the server uses the embedded value for conflict
        # semantics on this specific settings object.
        if settings.HasField("timestamp"):
            partial.timestamp = settings.timestamp
        ptarget = getattr(partial, field)
        if desc.is_repeated:
            if desc.message_type:
                for v in value: ptarget.add().CopyFrom(v)
            else: ptarget.extend(value)
        elif desc.message_type: ptarget.CopyFrom(value)
        else: setattr(partial, field, value)
        await self.operation(handler_id, updatedSettings=partial, flush=flush)
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
        target = getattr(settings, field)
        if desc.is_repeated:
            del target[:]
            if desc.message_type:
                for x in value: target.add().CopyFrom(x)
            else: target.extend(value)
        elif desc.message_type: target.CopyFrom(value)
        else: setattr(settings, field, value)
        # AnyList Web's UT() helper seeds every mobile-settings operation with both
        # identifier and the current timestamp before setting the changed field.
        partial = PB.PBMobileAppSettings(identifier=settings.identifier, timestamp=settings.timestamp)
        ptarget = getattr(partial, field)
        if desc.is_repeated:
            if desc.message_type:
                for x in value: ptarget.add().CopyFrom(x)
            else: ptarget.extend(value)
        elif desc.message_type: ptarget.CopyFrom(value)
        else: setattr(partial, field, value)
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
        await self.operation(handler_id, updatedSettings=partial, flush=flush)
        return settings
    async def save_recipe_cooking_states(
        self, states: list[Message], *, flush: bool = True
    ) -> str:
        settings = self.state.mobile_app_settings
        if settings is None:
            raise RuntimeError("Mobile app settings have not been synchronized")
        by_id = {str(x.recipeId): x for x in settings.recipeCookingStates if x.recipeId}
        for value in states:
            if value.recipeId:
                by_id[str(value.recipeId)] = clone_message(value)
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
        remove_ids = {str(x.recipeId) for x in states if x.recipeId}
        kept = [clone_message(x) for x in settings.recipeCookingStates if str(x.recipeId) not in remove_ids]
        del settings.recipeCookingStates[:]
        for value in kept:
            settings.recipeCookingStates.add().CopyFrom(value)
        partial = PB.PBMobileAppSettings(identifier=settings.identifier, timestamp=settings.timestamp)
        for value in states:
            partial.recipeCookingStates.add().CopyFrom(value)
        return await self.operation(
            "remove-recipe-cooking-states", updatedSettings=partial, flush=flush
        )

