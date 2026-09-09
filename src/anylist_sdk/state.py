from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from google.protobuf.message import Message

from .proto import PB, message_class


def clone(message: Message) -> Message:
    out = message.__class__()
    out.CopyFrom(message)
    return out


def by_identifier(values: Iterable[Message]) -> dict[str, Message]:
    return {str(v.identifier): clone(v) for v in values if getattr(v, "identifier", "")}


@dataclass(slots=True)
class AnyListState:
    """In-memory mirror of the official web client's synchronized domains."""

    user_id: str | None = None
    shopping_lists: dict[str, Message] = field(default_factory=dict)
    ordered_shopping_list_ids: list[str] = field(default_factory=list)
    list_responses: dict[str, Message] = field(default_factory=dict)
    # PBListResponse carries synchronized list-local domains that are independent of the
    # ShoppingList protobuf itself.  AnyList Web keeps each of these in an indexed manager.
    list_stores: dict[str, dict[str, Message]] = field(default_factory=dict)
    list_store_filters: dict[str, dict[str, Message]] = field(default_factory=dict)
    list_category_groups: dict[str, dict[str, Message]] = field(default_factory=dict)
    list_categories: dict[str, dict[str, Message]] = field(default_factory=dict)
    list_categorization_rules: dict[str, dict[str, Message]] = field(default_factory=dict)
    list_folders: dict[str, Message] = field(default_factory=dict)
    root_folder_id: str | None = None
    list_data_id: str | None = None
    has_migrated_list_ordering: bool = False

    recipes: dict[str, Message] = field(default_factory=dict)
    recipe_collections: dict[str, Message] = field(default_factory=dict)
    recipe_collection_ids: list[str] = field(default_factory=list)
    all_recipes_collection: Message | None = None
    recipe_data_id: str | None = None
    recipe_timestamp: float = 0.0
    recipe_max_count: int | None = None
    pending_recipe_link_requests: list[Message] = field(default_factory=list)
    recipe_link_requests_to_confirm: list[Message] = field(default_factory=list)
    linked_recipe_users: list[Message] = field(default_factory=list)
    system_recipe_collection_settings: dict[str, Message] = field(default_factory=dict)

    meal_plan_calendar_id: str | None = None
    meal_plan_logical_timestamp: int = 0
    meal_plan_response_version: int = 0
    meal_plan_events: dict[str, Message] = field(default_factory=dict)
    meal_plan_labels: dict[str, Message] = field(default_factory=dict)
    meal_plan_templates: dict[str, Message] = field(default_factory=dict)
    meal_plan_template_events: dict[str, Message] = field(default_factory=dict)
    meal_plan_template_groups: dict[str, Message] = field(default_factory=dict)

    categorized_items: dict[str, Message] = field(default_factory=dict)
    categorized_items_timestamp: float = 0.0
    categorized_items_timestamp_id: str = ""

    user_categories: dict[str, Message] = field(default_factory=dict)
    category_groupings: dict[str, Message] = field(default_factory=dict)
    user_categories_timestamp: float = 0.0
    user_category_data_id: str = ""
    user_categories_requires_refresh_timestamp: float = 0.0
    has_migrated_category_orderings: bool = False

    list_settings: dict[str, Message] = field(default_factory=dict)
    list_settings_timestamp: float = 0.0
    list_settings_timestamp_id: str = ""
    starter_list_settings: dict[str, Message] = field(default_factory=dict)
    starter_list_settings_timestamp: float = 0.0
    starter_list_settings_timestamp_id: str = ""

    starter_lists: dict[str, Message] = field(default_factory=dict)
    recent_item_lists: dict[str, Message] = field(default_factory=dict)
    favorite_item_lists: dict[str, Message] = field(default_factory=dict)
    ordered_starter_list_ids: list[str] = field(default_factory=list)
    ordered_starter_list_ids_timestamp: float = 0.0
    ordered_starter_list_ids_timestamp_id: str = ""
    has_migrated_user_favorites: bool = False

    mobile_app_settings: Message | None = None
    account_info: Message | None = None
    loaded_once: bool = False

    def get_list(self, list_id: str) -> Message | None:
        return self.shopping_lists.get(list_id)

    def get_item(self, list_id: str, item_id: str) -> Message | None:
        shopping_list = self.shopping_lists.get(list_id)
        if shopping_list is None:
            return None
        for item in shopping_list.items:
            if item.identifier == item_id:
                return item
        return None

    @staticmethod
    def _merge_index(
        index: dict[str, Message], values: Iterable[Message], deleted_ids: Iterable[str] = ()
    ) -> None:
        for identifier in deleted_ids:
            index.pop(str(identifier), None)
        for value in values:
            identifier = getattr(value, "identifier", None)
            if identifier:
                index[str(identifier)] = clone(value)

    def _drop_list_local_state(self, list_id: str) -> None:
        self.list_responses.pop(list_id, None)
        self.list_stores.pop(list_id, None)
        self.list_store_filters.pop(list_id, None)
        self.list_category_groups.pop(list_id, None)
        self.list_categories.pop(list_id, None)
        self.list_categorization_rules.pop(list_id, None)

    @staticmethod
    def _list_index(container: dict[str, dict[str, Message]], list_id: str) -> dict[str, Message]:
        return container.setdefault(list_id, {})

    def apply_list_response(self, detail: Message) -> None:
        """Apply ShoppingListManager.hQ semantics for one PBListResponse."""
        list_id = str(detail.listId or "")
        if not list_id:
            return

        # hQ updates the ShoppingList wrapper's logical clock before applying the list-local
        # domains.  If the list isn't loaded, the official client still keeps the response
        # domains via their global managers, so don't require the ShoppingList to exist here.
        shopping_list = self.shopping_lists.get(list_id)
        if shopping_list is not None:
            shopping_list.logicalClockTime = detail.logicalTimestamp

        stores = self._list_index(self.list_stores, list_id)
        filters = self._list_index(self.list_store_filters, list_id)
        groups = self._list_index(self.list_category_groups, list_id)
        categories = self._list_index(self.list_categories, list_id)
        rules = self._list_index(self.list_categorization_rules, list_id)

        if detail.isFullSync:
            stores.clear()
            filters.clear()
            groups.clear()
            categories.clear()
            rules.clear()

        self._merge_index(stores, detail.stores, detail.deletedStoreIds)
        self._merge_index(filters, detail.storeFilters, detail.deletedStoreFilterIds)

        # Deleting a category group also deletes all categories that belong to that group.
        deleted_group_ids = {str(x) for x in detail.deletedCategoryGroupIds}
        for group_id in deleted_group_ids:
            groups.pop(group_id, None)
        if deleted_group_ids:
            for category_id, category in tuple(categories.items()):
                if str(category.categoryGroupId) in deleted_group_ids:
                    categories.pop(category_id, None)

        for group_response in detail.categoryGroupResponses:
            for category_id in group_response.deletedCategoryIds:
                categories.pop(str(category_id), None)
            if not group_response.HasField("categoryGroup"):
                continue
            group = group_response.categoryGroup
            self._merge_index(categories, group.categories)
            stored_group = clone(group)
            del stored_group.categories[:]
            if stored_group.identifier:
                groups[str(stored_group.identifier)] = stored_group

        self._merge_index(
            rules, detail.categorizationRules, detail.deletedCategorizationRuleIds
        )
        self.list_responses[list_id] = clone(detail)

    def apply_shopping_lists(self, response: Message) -> None:
        for identifier in response.unknownIds:
            list_id = str(identifier)
            self.shopping_lists.pop(list_id, None)
            self._drop_list_local_state(list_id)
        self._merge_index(self.shopping_lists, response.newLists)
        self._merge_index(self.shopping_lists, response.modifiedLists)
        # cQ assigns orderedIds even when it is empty; an empty server order must clear an
        # old local ordering rather than leave stale IDs behind.
        self.ordered_shopping_list_ids = list(response.orderedIds)
        for detail in response.listResponses:
            self.apply_list_response(detail)

    def apply_list_folders(self, response: Message) -> None:
        if response.listDataId:
            self.list_data_id = str(response.listDataId)
        if response.rootFolderId:
            self.root_folder_id = str(response.rootFolderId)
        if response.HasField("hasMigratedListOrdering"):
            self.has_migrated_list_ordering = bool(response.hasMigratedListOrdering)
        # The official folder manager clears its index before applying a response that
        # explicitly says it contains every folder.  Without this, a folder removed on
        # another client can survive forever in our local mirror even after a full fetch.
        if bool(response.includesAllFolders):
            self.list_folders.clear()
        self._merge_index(self.list_folders, response.listFolders, response.deletedFolderIds)

    def apply_recipes(self, response: Message) -> None:
        """Apply the official incremental PBRecipeDataResponse semantics."""
        # RecipeManager.FX ignores malformed/empty responses that lack the recipe-data ID.
        if not response.recipeDataId:
            return
        self.recipe_data_id = str(response.recipeDataId)
        self.recipe_timestamp = float(response.timestamp)
        if response.HasField("maxRecipeCount"):
            self.recipe_max_count = int(response.maxRecipeCount)

        self._merge_index(self.recipes, response.recipes)
        self._merge_index(self.recipe_collections, response.recipeCollections)

        if response.includesRecipeCollectionIds:
            self.recipe_collection_ids = list(response.recipeCollectionIds)
            allowed_collections = set(self.recipe_collection_ids)
            for identifier in tuple(self.recipe_collections):
                if identifier not in allowed_collections:
                    self.recipe_collections.pop(identifier, None)

        if response.HasField("allRecipesCollection"):
            self.all_recipes_collection = clone(response.allRecipesCollection)
            allowed_recipes = set(response.allRecipesCollection.recipeIds)
            for identifier in tuple(self.recipes):
                if identifier not in allowed_recipes:
                    self.recipes.pop(identifier, None)
            # The web client also strips deleted recipe IDs from every user collection.
            for collection in self.recipe_collections.values():
                kept = [rid for rid in collection.recipeIds if rid in allowed_recipes]
                if len(kept) != len(collection.recipeIds):
                    del collection.recipeIds[:]
                    collection.recipeIds.extend(kept)

        # These arrays are complete snapshots whenever recipe data is returned.
        self.pending_recipe_link_requests = [clone(x) for x in response.pendingRecipeLinkRequests]
        self.recipe_link_requests_to_confirm = [clone(x) for x in response.recipeLinkRequestsToConfirm]
        self.linked_recipe_users = [clone(x) for x in response.linkedUsers]

        # Incremental responses may omit unchanged system-collection settings. FX merges
        # entries into the existing map instead of replacing the map with an empty one.
        for key, value in response.settingsMapForSystemCollections.items():
            self.system_recipe_collection_settings[str(key)] = clone(value)

    def apply_recipes_full(self, response: Message) -> None:
        """Apply RecipeManager.MX semantics used by the unlink-recipes response."""
        if response.HasField("timestamp"):
            self.recipe_timestamp = float(response.timestamp)
        self.recipe_data_id = str(response.recipeDataId or "") or None
        self.all_recipes_collection = (
            clone(response.allRecipesCollection)
            if response.HasField("allRecipesCollection")
            else None
        )
        self.recipe_collection_ids = list(response.recipeCollectionIds)
        self.recipes = by_identifier(response.recipes)
        self.recipe_collections = by_identifier(response.recipeCollections)
        self.pending_recipe_link_requests = [clone(x) for x in response.pendingRecipeLinkRequests]
        self.recipe_link_requests_to_confirm = [clone(x) for x in response.recipeLinkRequestsToConfirm]
        self.linked_recipe_users = [clone(x) for x in response.linkedUsers]

    @staticmethod
    def _apply_delta(
        index: dict[str, Message], values: Iterable[Message], deleted_ids: Iterable[str], *, full: bool
    ) -> None:
        if full:
            index.clear()
        for identifier in deleted_ids:
            index.pop(str(identifier), None)
        for value in values:
            identifier = getattr(value, "identifier", None)
            if identifier:
                index[str(identifier)] = clone(value)

    def apply_meal_plan(self, response: Message) -> None:
        # MealPlanManager.Zq rejects malformed calendars and response versions older than
        # the client's supported version. A changed calendar ID is accepted only on a full
        # sync; accepting a delta for another calendar would corrupt the current mirror.
        calendar_id = str(response.calendarId or "")
        if not calendar_id:
            return
        supported_version = 1
        response_version = int(response.responseVersion)
        if response_version < supported_version:
            return
        full = bool(response.isFullSync)
        if (
            self.meal_plan_calendar_id
            and self.meal_plan_calendar_id != calendar_id
            and not full
        ):
            return

        self.meal_plan_calendar_id = calendar_id
        self.meal_plan_logical_timestamp = int(response.logicalTimestamp)
        # The web client records min(server response version, supported version).
        self.meal_plan_response_version = min(response_version, supported_version)
        self._apply_delta(self.meal_plan_events, response.events, response.deletedEventIds, full=full)
        self._apply_delta(self.meal_plan_labels, response.labels, response.deletedLabelIds, full=full)
        self._apply_delta(
            self.meal_plan_templates, response.templates, response.deletedTemplateIds, full=full
        )
        self._apply_delta(
            self.meal_plan_template_events,
            response.templateEvents,
            response.deletedTemplateEventIds,
            full=full,
        )
        self._apply_delta(
            self.meal_plan_template_groups,
            response.templateGroups,
            response.deletedTemplateGroupIds,
            full=full,
        )

    def apply_categorized_items(self, response: Message) -> None:
        if response.HasField("timestamp"):
            self.categorized_items_timestamp = float(response.timestamp.timestamp)
            self.categorized_items_timestamp_id = str(response.timestamp.identifier)
            if response.timestamp.identifier == "all":
                self.categorized_items.clear()
        self._merge_index(self.categorized_items, response.categorizedItems)

    def apply_user_categories(self, response: Message) -> None:
        self.user_category_data_id = str(response.identifier)
        self.user_categories_timestamp = float(response.timestamp)
        if response.HasField("requiresRefreshTimestamp"):
            self.user_categories_requires_refresh_timestamp = float(response.requiresRefreshTimestamp)
        if response.HasField("hasMigratedCategoryOrderings"):
            self.has_migrated_category_orderings = bool(response.hasMigratedCategoryOrderings)
        if response.identifier == "all":
            self.user_categories.clear()
            self.category_groupings.clear()
        self._merge_index(self.user_categories, response.categories)
        self._merge_index(self.category_groupings, response.groupings)

    def apply_list_settings(self, response: Message, *, starter: bool = False) -> None:
        target = self.starter_list_settings if starter else self.list_settings
        if response.HasField("timestamp"):
            if starter:
                self.starter_list_settings_timestamp = float(response.timestamp.timestamp)
                self.starter_list_settings_timestamp_id = str(response.timestamp.identifier)
            else:
                self.list_settings_timestamp = float(response.timestamp.timestamp)
                self.list_settings_timestamp_id = str(response.timestamp.identifier)
            if response.timestamp.identifier == "all":
                target.clear()
        # The official manager indexes PBListSettings by listId, with the account-wide
        # default settings stored under the empty-string key.
        for value in response.settings:
            target[str(value.listId or "")] = clone(value)

    @staticmethod
    def _apply_starter_batch(target: dict[str, Message], batch: Message) -> None:
        if batch.includesAllLists:
            target.clear()
        for identifier in batch.unknownListIds:
            target.pop(str(identifier), None)
        for item in batch.listResponses:
            if item.HasField("starterList"):
                target[str(item.starterList.identifier)] = clone(item.starterList)

    def apply_starter_lists(self, response: Message) -> None:
        if response.HasField("hasMigratedUserFavorites"):
            self.has_migrated_user_favorites = bool(response.hasMigratedUserFavorites)
        if response.HasField("userListsResponse"):
            self._apply_starter_batch(self.starter_lists, response.userListsResponse)
        if response.HasField("recentItemListsResponse"):
            self._apply_starter_batch(self.recent_item_lists, response.recentItemListsResponse)
        if response.HasField("favoriteItemListsResponse"):
            self._apply_starter_batch(self.favorite_item_lists, response.favoriteItemListsResponse)

    def apply_ordered_starter_ids(self, response: Message) -> None:
        self.ordered_starter_list_ids = list(response.identifiers)
        if response.HasField("timestamp"):
            self.ordered_starter_list_ids_timestamp = float(response.timestamp)
            # The official response carries a scalar timestamp without an identifier; the
            # manager uses a stable logical identifier for the request timestamp.
            if self.user_id:
                self.ordered_starter_list_ids_timestamp_id = self.user_id

    def apply_mobile_settings(self, response: Message) -> None:
        self.mobile_app_settings = clone(response)

    def apply_user_data(self, response: Message) -> None:
        if response.HasField("mobileAppSettingsResponse"):
            self.apply_mobile_settings(response.mobileAppSettingsResponse)
        if response.HasField("shoppingListsResponse"):
            self.apply_shopping_lists(response.shoppingListsResponse)
        if response.HasField("listFoldersResponse"):
            self.apply_list_folders(response.listFoldersResponse)
        if response.HasField("recipeDataResponse"):
            self.apply_recipes(response.recipeDataResponse)
        if response.HasField("mealPlanningCalendarResponse"):
            self.apply_meal_plan(response.mealPlanningCalendarResponse)
        if response.HasField("userCategoriesResponse"):
            self.apply_user_categories(response.userCategoriesResponse)
        if response.HasField("categorizedItemsResponse"):
            self.apply_categorized_items(response.categorizedItemsResponse)
        if response.HasField("listSettingsResponse"):
            self.apply_list_settings(response.listSettingsResponse)
        if response.HasField("starterListSettingsResponse"):
            self.apply_list_settings(response.starterListSettingsResponse, starter=True)
        if response.HasField("starterListsResponse"):
            self.apply_starter_lists(response.starterListsResponse)
        if response.HasField("orderedStarterListIdsResponse"):
            self.apply_ordered_starter_ids(response.orderedStarterListIdsResponse)
        self.loaded_once = True

    def shopping_list_timestamps(self) -> Message:
        out = PB.PBTimestampList()
        for value in self.shopping_lists.values():
            ts = out.timestamps.add()
            ts.identifier = value.identifier
            ts.timestamp = value.timestamp
        return out

    def shopping_list_logical_timestamps(self) -> Message:
        out = PB.PBLogicalTimestampList()
        for value in self.shopping_lists.values():
            ts = out.timestamps.add()
            ts.identifier = value.identifier
            ts.logicalTimestamp = value.logicalClockTime
        return out

    def list_folder_timestamps(self) -> Message:
        out = PB.PBListFolderTimestamps()
        if self.root_folder_id:
            out.rootFolderId = self.root_folder_id
        for value in self.list_folders.values():
            ts = out.folderTimestamps.add()
            ts.identifier = value.identifier
            ts.timestamp = value.timestamp
        return out

    def _starter_timestamps(self, source: dict[str, Message]) -> Message:
        out = PB.PBTimestampList()
        for value in source.values():
            ts = out.timestamps.add()
            ts.identifier = value.identifier
            ts.timestamp = value.timestamp
        return out

    def user_data_timestamps(self) -> Message:
        out = PB.PBUserDataClientTimestamps()
        out.shoppingListTimestamps.CopyFrom(self.shopping_list_timestamps())
        out.shoppingListLogicalTimestamps.CopyFrom(self.shopping_list_logical_timestamps())
        out.listFolderTimestamps.CopyFrom(self.list_folder_timestamps())
        if self.recipe_data_id:
            out.userRecipeDataTimestamp.identifier = self.recipe_data_id
            out.userRecipeDataTimestamp.timestamp = self.recipe_timestamp
        if self.meal_plan_calendar_id:
            out.mealPlanningCalendarTimestamp.identifier = self.meal_plan_calendar_id
            out.mealPlanningCalendarTimestamp.logicalTimestamp = self.meal_plan_logical_timestamp
        if self.user_category_data_id:
            out.userCategoriesTimestamp.identifier = self.user_category_data_id
            out.userCategoriesTimestamp.timestamp = self.user_categories_timestamp
        if self.categorized_items_timestamp_id:
            out.categorizedItemsTimestamp.identifier = self.categorized_items_timestamp_id
            out.categorizedItemsTimestamp.timestamp = self.categorized_items_timestamp
        if self.list_settings_timestamp_id:
            out.listSettingsTimestamp.identifier = self.list_settings_timestamp_id
            out.listSettingsTimestamp.timestamp = self.list_settings_timestamp
        if self.starter_list_settings_timestamp_id:
            out.starterListSettingsTimestamp.identifier = self.starter_list_settings_timestamp_id
            out.starterListSettingsTimestamp.timestamp = self.starter_list_settings_timestamp
        out.starterListTimestamps.CopyFrom(self._starter_timestamps(self.starter_lists))
        out.recentItemTimestamps.CopyFrom(self._starter_timestamps(self.recent_item_lists))
        out.favoriteItemTimestamps.CopyFrom(self._starter_timestamps(self.favorite_item_lists))
        if self.ordered_starter_list_ids_timestamp_id:
            out.orderedStarterListIdsTimestamp.identifier = self.ordered_starter_list_ids_timestamp_id
            out.orderedStarterListIdsTimestamp.timestamp = self.ordered_starter_list_ids_timestamp
        if self.mobile_app_settings is not None:
            out.mobileAppSettingsTimestamp.identifier = self.mobile_app_settings.identifier
            out.mobileAppSettingsTimestamp.timestamp = self.mobile_app_settings.timestamp
        return out

    def user_data_client_info(self) -> Message:
        out = PB.PBUserDataClientInfo()
        out.mealPlanningCalendarClientInfo.supportedResponseVersion = 1
        out.mealPlanningCalendarClientInfo.processedResponseVersion = self.meal_plan_response_version
        return out
