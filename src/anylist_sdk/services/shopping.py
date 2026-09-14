from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

import aiohttp
from google.protobuf.message import Message

from ..derived import (
    active_package_size,
    active_quantity_for_total_cost,
    add_item_ingredient,
    event_list_item_to_item_ingredient,
    ingredient_to_item_ingredient,
    normalized_raw_package_size,
    recipe_list_item_identifier,
    remove_item_ingredient,
    unit_price,
)
from ..identifiers import uuid4_hex, uuid5_hex
from ..item_semantics import (
    EXCLUDE_DETAILS,
    EXCLUDE_ITEM_QUANTITY,
    EXCLUDE_PACKAGE_SIZE,
    EXCLUDE_PRICE_QUANTITY,
    apply_properties_from_item,
    items_equal,
    package_size_empty,
    package_size_equal,
    quantity_equal,
    quantity_to_deprecated_string,
)
from ..normalization import canonical_category_match_id
from ..operations import OperationJournal, QueueSpec
from ..parsing.quantity import abbreviate_units_in_text, normalize_unit, singularize_units_in_text
from ..proto import (
    PB,
    ListItem,
    PBCalendarEvent,
    PBItemIngredient,
    PBItemPackageSize,
    PBItemPrice,
    PBItemQuantity,
    PBListCategorizationRule,
    PBListCategory,
    PBListCategoryGroup,
    PBListItemCategoryAssignment,
    PBNotificationLocation,
    PBRecipe,
    PBStore,
    PBStoreFilter,
    ShoppingList,
    ShoppingListsResponse,
)
from ..state import AnyListState, clone
from ..stemming import stem_words
from ..transport import AnyListTransport
from ..types import OperationAck
from .base import OperationService, clone_message
from .starter import favorite_list_id, recent_list_id

# Official web client namespace used by list categorization-rule IDs.
_CATEGORY_RULE_NAMESPACE = UUID(hex="f4338133428d4f0b94027c9b23243f14")
_CATEGORY_GROUP_NAMESPACE = UUID(hex="f656a81f0e0a419aa45121f4f2eac51b")
_CATEGORY_ASSIGNMENT_NAMESPACE = UUID(hex="08e5c5bdcd694454a1ffd611b6d9abc0")
_DEFAULT_SYSTEM_CATEGORIES = (
    "baby",
    "bakery",
    "beverages",
    "breakfast-and-cereal",
    "condiments-oils-and-salad-dressings",
    "cooking-and-baking",
    "dairy",
    "deli",
    "frozen-foods",
    "grains-pasta-and-side-dishes",
    "health-and-personal-care",
    "household-and-cleaning",
    "meat",
    "pet-supplies",
    "produce",
    "seafood",
    "snacks-cookies-and-candy",
    "soups-and-canned-goods",
    "wine-beer-spirits",
    "other",
)
_DEFAULT_SYSTEM_CATEGORY_NAMES = {
    "baby": "Baby",
    "bakery": "Bakery",
    "beverages": "Beverages",
    "breakfast-and-cereal": "Breakfast & Cereal",
    "condiments-oils-and-salad-dressings": "Condiments & Dressings",
    "cooking-and-baking": "Cooking & Baking",
    "dairy": "Dairy",
    "deli": "Deli",
    "frozen-foods": "Frozen Foods",
    "grains-pasta-and-side-dishes": "Grains, Pasta & Sides",
    "health-and-personal-care": "Health & Personal Care",
    "household-and-cleaning": "Household & Cleaning",
    "meat": "Meat",
    "pet-supplies": "Pet Supplies",
    "produce": "Produce",
    "seafood": "Seafood",
    "snacks-cookies-and-candy": "Snacks",
    "soups-and-canned-goods": "Soups & Canned Goods",
    "wine-beer-spirits": "Wine, Beer & Spirits",
    "other": "Other",
}
_SYSTEM_ITEM_CATEGORIES = {
    "baby",
    "bakery",
    "beverages",
    "breakfast-and-cereal",
    "condiments-oils-and-salad-dressings",
    "cooking-and-baking",
    "dairy",
    "frozen-foods",
    "grains-pasta-and-side-dishes",
    "health-and-personal-care",
    "household-and-cleaning",
    "meat",
    "pet-supplies",
    "produce",
    "seafood",
    "snacks-cookies-and-candy",
    "soups-and-canned-goods",
    "wine-beer-spirits",
    "other",
}
_PRICE_QUANTITY_UNITS = {
    "cup",
    "fl oz",
    "oz",
    "tbsp",
    "tsp",
    "g",
    "mg",
    "l",
    "dl",
    "ml",
    "slice",
    "clove",
    "pinch",
    "drop",
    "dash",
    "inch",
}


def category_rule_identifier(item_name: str, category_group_id: str, list_id: str) -> str:
    return uuid5_hex(item_name.lower() + category_group_id + list_id, _CATEGORY_RULE_NAMESPACE)


class ShoppingListsService(OperationService):
    """Shopping-list API with optimistic protobuf-backed state."""

    def __init__(
        self,
        transport: AnyListTransport,
        state: AnyListState,
        *,
        user_id: str,
        user_email: str | None = None,
        user_locale: str | None = None,
        journal: OperationJournal | None = None,
    ) -> None:
        super().__init__(
            transport,
            state,
            user_id=user_id,
            spec=QueueSpec(
                f"{user_id}:shopping-lists-v2",
                "/data/shopping-lists/update-v2",
                "PBListOperation",
                "PBListOperationList",
            ),
            journal=journal,
        )
        # The official web client retains the older queue too; expose it for operations
        # that are still routed there by a given web build.
        from ..operations import OperationQueue

        self.legacy_queue = OperationQueue(
            transport,
            QueueSpec(
                f"{user_id}:shopping-lists",
                "/data/shopping-lists/update",
                "PBListOperation",
                "PBListOperationList",
            ),
            user_id=user_id,
            journal=journal,
        )
        self.user_id = user_id
        self.user_email = user_email
        self.user_locale = user_locale or "en"
        self._localized_strings: dict[str, object] | None = None
        self.queue.on_response = self._on_v2_response
        self.legacy_queue.on_response = self._on_legacy_response
        self.on_store_filter_removed: Callable[[str, str, bool], Awaitable[None]] | None = None
        self.on_category_group_removed: Callable[[str, str, bool], Awaitable[None]] | None = None
        self.on_items_became_recent: (
            Callable[[str, Sequence[ListItem], bool, bool], Awaitable[None]] | None
        ) = None
        # AnyList Web constructs new shopping items locally before queueing them. The tag-data
        # classifier lives above this service in AnyListClient, so the client wires this callback
        # to the same classifier used by autocomplete/categorization.
        self.on_classify_grocery_item: (
            Callable[[str], Awaitable[tuple[str | None, str | None]]] | None
        ) = None
        self.on_folder_refresh_requested: Callable[[], Awaitable[object]] | None = None
        self.on_new_list_settings: Callable[[str, str, int, bool], Awaitable[None]] | None = None
        self.on_new_list_starter_lists: Callable[[str, str, bool], Awaitable[None]] | None = None
        self._refresh_after_legacy_queue = False
        self._refresh_after_v2_queue = False
        self._refresh_folders_after_legacy_queue = False

    async def _translation_strings(self) -> dict[str, object]:
        """Load the AnyList-owned i18next resource used by this web build."""
        language = "de" if self.user_locale.replace("_", "-").lower().startswith("de") else "en"
        if language == "en":
            return {}
        if self._localized_strings is not None:
            return self._localized_strings

        # bt.Et() in this web build supports exactly en/de and i18next loads this AnyList-owned
        # resource before the UI starts. Missing strings fall back to their English source key.
        url = f"{self.transport.base_url}/static/webapp/strings/{language}/translation.json?v=1"
        try:
            async with self.transport.session.get(url) as response:
                if response.status >= 400:
                    raise ValueError(f"HTTP {response.status}")
                payload = json.loads(await response.text())
            if not isinstance(payload, dict):
                raise TypeError("translation payload is not an object")
        except (aiohttp.ClientError, ValueError, TypeError, AttributeError):
            payload = {}
        self._localized_strings = payload
        return payload

    async def _localized_string(self, english: str) -> str:
        payload = await self._translation_strings()
        return str(payload.get(english) or english)

    async def _default_category_names(self) -> dict[str, str]:
        """Return the official i18next names used when constructing a new grocery list."""
        payload = await self._translation_strings()
        return {
            system: str(payload.get(english_name) or english_name)
            for system, english_name in _DEFAULT_SYSTEM_CATEGORY_NAMES.items()
        }

    async def operation(
        self,
        handler_id: str,
        *,
        flush: bool = True,
        operation_class: int | None = None,
        operation_version: int | None = None,
        **fields: Any,
    ) -> str:
        """Route PBListOperation to the exact official shopping queue.

        ShoppingListManager's ordinary list/item mutations enqueue through PJ/UK on
        ``/data/shopping-lists/update``.  The v2 queue is used by the separate store,
        store-filter, list-category-group, list-category, and categorization-rule managers;
        every operation from those managers carries a PBOperationMetadata operationClass.
        """
        if operation_class is None:
            op = self.legacy_queue.new_operation(
                handler_id,
                operation_version=operation_version,
                **fields,
            )
            return await self.legacy_queue.enqueue(op, flush=flush)
        return await super().operation(
            handler_id,
            flush=flush,
            operation_class=operation_class,
            operation_version=operation_version,
            **fields,
        )

    async def _on_legacy_response(self, response: Message) -> None:
        needs_refresh = False
        new_by_id = {str(x.identifier): x for x in response.newTimestamps}
        for original in response.originalTimestamps:
            current = self.state.shopping_lists.get(str(original.identifier))
            if current is None or float(current.timestamp) != float(original.timestamp):
                needs_refresh = True
                continue
            new = new_by_id.get(str(original.identifier))
            if new is None or float(new.timestamp) == 0:
                needs_refresh = True
            else:
                current.timestamp = new.timestamp
        refresh_lists = needs_refresh or self._refresh_after_legacy_queue
        self._refresh_after_legacy_queue = False
        if refresh_lists:
            await self.refresh()
        if self._refresh_folders_after_legacy_queue:
            self._refresh_folders_after_legacy_queue = False
            if self.on_folder_refresh_requested is not None:
                await self.on_folder_refresh_requested()

    async def _on_v2_response(self, response: Message) -> None:
        refresh_ids = {str(x) for x in response.fullRefreshTimestampIds}
        for original in response.originalLogicalTimestamps:
            list_id = str(original.identifier)
            current = self.state.shopping_lists.get(list_id)
            if current is None:
                if int(original.logicalTimestamp) != 0:
                    refresh_ids.add(list_id)
            elif int(current.logicalClockTime) != int(original.logicalTimestamp):
                refresh_ids.add(list_id)
        for current_ts in response.currentLogicalTimestamps:
            list_id = str(current_ts.identifier)
            if list_id in refresh_ids:
                continue
            current = self.state.shopping_lists.get(list_id)
            if current is None or int(current_ts.logicalTimestamp) == 0:
                refresh_ids.add(list_id)
            else:
                current.logicalClockTime = current_ts.logicalTimestamp
        refresh_lists = bool(refresh_ids) or self._refresh_after_v2_queue
        self._refresh_after_v2_queue = False
        if refresh_lists:
            await self.refresh()

    def all(self) -> list[ShoppingList]:
        return list(self.state.shopping_lists.values())

    def get(self, list_id: str) -> ShoppingList | None:
        return self.state.shopping_lists.get(list_id)

    def item(self, list_id: str, item_id: str) -> ListItem | None:
        return self.state.get_item(list_id, item_id)

    def _web_number_format(self) -> tuple[str, str]:
        settings = self.state.mobile_app_settings
        decimal_separator = "."
        currency_symbol = "$"
        if settings is not None:
            if settings.HasField("webDecimalSeparator") and settings.webDecimalSeparator:
                decimal_separator = str(settings.webDecimalSeparator)
            if settings.HasField("webCurrencySymbol") and settings.webCurrencySymbol:
                currency_symbol = str(settings.webCurrencySymbol)
        return decimal_separator, currency_symbol

    @staticmethod
    def _js_to_fixed(value: float, digits: int) -> str:
        """Match JavaScript Number.toFixed rounding for ordinary finite AnyList prices."""

        quantum = Decimal(1).scaleb(-digits)
        decimal = Decimal.from_float(float(value)).quantize(quantum, rounding=ROUND_HALF_UP)
        return f"{decimal:.{digits}f}"

    def _currency_string(self, amount: float, max_decimal_places: int) -> str:
        # AnyList Web's formatter returns an empty string for zero/falsy amounts.
        if not amount:
            return ""
        decimal_separator, symbol = self._web_number_format()
        if symbol == "kr":
            number = self._js_to_fixed(amount, 2).replace(".", decimal_separator)
            return f"{number} {symbol}"
        number = self._js_to_fixed(amount, max_decimal_places).replace(".", decimal_separator)
        return f"{symbol}{number}"

    async def _format_currency_with_price_unit(
        self,
        amount: float,
        price_unit: str | None,
        *,
        compact: bool,
        max_decimal_places: int,
    ) -> str:
        value = self._currency_string(amount, max_decimal_places)
        if price_unit is None:
            return value
        if compact:
            template = (
                "{{currencyString}}/ea" if price_unit == "" else "{{currencyString}}/{{priceUnit}}"
            )
        else:
            template = (
                "{{currencyString}} each"
                if price_unit == ""
                else "{{currencyString}} per {{priceUnit}}"
            )
        translated = await self._localized_string(template)
        return (
            translated.replace("{{currencyString}}", value)
            .replace("{{- currencyString}}", value)
            .replace("{{priceUnit}}", price_unit)
            .replace("{{- priceUnit}}", price_unit)
        )

    @staticmethod
    def _abbreviated_singular_unit(unit: str) -> str:
        return singularize_units_in_text(abbreviate_units_in_text(unit or ""))

    async def item_price_string(self, item: ListItem, price: PBItemPrice | None) -> str | None:
        """Port ``ListItem.itemPriceStringForItemPrice`` from AnyList Web."""

        if price is None or not price.HasField("amount"):
            return None
        quantity = active_quantity_for_total_cost(item)
        unit = self._abbreviated_singular_unit(str(quantity.unit or ""))
        return await self._format_currency_with_price_unit(
            float(price.amount), unit, compact=True, max_decimal_places=2
        )

    async def unit_price_string(self, item: ListItem, price: PBItemPrice | None) -> str | None:
        """Port ``ListItem.unitPriceStringForItemPrice`` from AnyList Web."""

        if price is None or not price.HasField("amount"):
            return None
        package = active_package_size(item)
        if not package_size_empty(package):
            raw_unit = str(package.unit or "")
        else:
            raw_unit = str(active_quantity_for_total_cost(item).unit or "")
        unit = self._abbreviated_singular_unit(raw_unit)
        return await self._format_currency_with_price_unit(
            unit_price(item, price), unit, compact=False, max_decimal_places=3
        )

    def has_pending_new_list(self) -> bool:
        return any(
            str(op.metadata.handlerId) == "new-shopping-list" for op in self.legacy_queue._pending
        )

    def remove_list_local(self, list_id: str) -> ShoppingList | None:
        """Apply ShoppingListManager.qB's local list-removal side effects.

        Folder membership and PBListSettings queueing are coordinated by the client/folder
        service; this method owns the shopping/list-local indexed state.
        """
        removed = self.state.shopping_lists.pop(list_id, None)
        while list_id in self.state.ordered_shopping_list_ids:
            self.state.ordered_shopping_list_ids.remove(list_id)
        self.state._drop_list_local_state(list_id)
        return removed

    async def refresh(self) -> ShoppingListsResponse | None:
        # gQ() never fetches list snapshots over pending local edits.  Legacy and v2 queues
        # share one deferred-refresh flag in the web manager; keep one flag per Python queue
        # so the acknowledgement that actually drains the pending work resumes the fetch.
        if self.legacy_queue.pending_count:
            self._refresh_after_legacy_queue = True
            await self.legacy_queue.flush()
            return None
        if self.queue.pending_count:
            self._refresh_after_v2_queue = True
            await self.queue.flush()
            return None
        response = await self.transport.post_proto(
            "/data/shopping-lists/all",
            fields={
                "timestamps": self.state.shopping_list_timestamps(),
                "logical_timestamps": self.state.shopping_list_logical_timestamps(),
            },
            response_type="ShoppingListsResponse",
        )
        if response is None:
            return None
        assert isinstance(response, PB.ShoppingListsResponse)
        self.state.apply_shopping_lists(response)
        return response

    @staticmethod
    def _find_item_index(lst: ShoppingList, item_id: str) -> int:
        for idx, item in enumerate(lst.items):
            if item.identifier == item_id:
                return idx
        return -1

    async def create(
        self,
        name: str,
        *,
        list_id: str | None = None,
        folder_id: str | None = None,
        list_type: int = 0,
        initialize_starter_lists: bool = True,
        flush: bool = True,
    ) -> ShoppingList:
        list_id = list_id or uuid4_hex()
        folder_id = folder_id or self.state.root_folder_id
        if not folder_id:
            raise RuntimeError("Load list folders before creating a shopping list")
        if not self.user_email:
            raise RuntimeError(
                "Shopping-list creation requires the authenticated account email; use "
                "AnyListClient.sign_in() or pass user_email= with pre-existing tokens"
            )
        if list_type not in {0, 1, 2}:
            raise ValueError("list_type must be 0 (grocery), 1 (categorized), or 2 (basic)")

        # Sdt.Cf constructs the category set before ShoppingListManager.EQ. Grocery mode
        # (0) gets the full built-in category set; categorized/manual and basic modes get a
        # single "other" category. All three use the deterministic default group ID.
        category_names = await self._default_category_names()
        group_id = uuid5_hex(list_id, _CATEGORY_GROUP_NAMESPACE)
        group = PB.PBListCategoryGroup(identifier=group_id, listId=list_id)
        system_categories = _DEFAULT_SYSTEM_CATEGORIES if list_type == 0 else ("other",)
        for sort_index, system_category in enumerate(system_categories):
            category = group.categories.add(
                identifier=uuid4_hex(),
                categoryGroupId=group_id,
                listId=list_id,
                systemCategory=system_category,
                name=category_names[system_category],
                icon=system_category,
                sortIndex=sort_index,
            )
            if system_category == "other":
                group.defaultCategoryId = category.identifier

        lst = PB.ShoppingList(
            identifier=list_id,
            name=name,
            creator=self.user_id,
            listItemSortOrder=(
                PB.ShoppingList.ListItemSortOrder.Alphabetical
                if list_type == 0
                else PB.ShoppingList.ListItemSortOrder.Manual
            ),
        )
        lst.sharedUsers.add(userId=self.user_id, email=self.user_email)
        self.state.shopping_lists[list_id] = clone(lst)
        self._store_category_group(group)

        folder = self.state.list_folders.get(folder_id)
        if folder is not None and all(str(item.identifier) != list_id for item in folder.items):
            folder.items.add(
                identifier=list_id,
                itemType=PB.PBListFolderItem.ItemType.ListType,
            )

        await self.operation(
            "new-shopping-list",
            listId=list_id,
            list=lst,
            listFolderId=folder_id,
            updatedCategoryGroup=group,
            flush=flush,
        )
        if self.on_new_list_settings is not None:
            await self.on_new_list_settings(list_id, group_id, list_type, flush)
        if initialize_starter_lists and self.on_new_list_starter_lists is not None:
            await self.on_new_list_starter_lists(
                list_id,
                await self._localized_string("Favorite Items"),
                flush,
            )
        return self.state.shopping_lists[list_id]

    async def rename(self, list_id: str, name: str, *, flush: bool = True) -> None:
        lst = self._require_list(list_id)
        old = str(lst.name)
        lst.name = name
        await self.operation(
            "rename-list", listId=list_id, updatedValue=name, originalValue=old, flush=flush
        )

    async def set_password(self, list_id: str, password: str, *, flush: bool = True) -> None:
        lst = self._require_list(list_id)
        if "password" in lst.DESCRIPTOR.fields_by_name:
            lst.password = password
        # AnyList Web sends only updatedValue for this handler. Unlike rename-list and
        # item text mutations, set-list-password does not carry originalValue.
        await self.operation(
            "set-list-password",
            listId=list_id,
            updatedValue=password,
            flush=flush,
        )

    def _new_items_at_top(self, list_id: str) -> bool:
        """Return the web client's effective new-item-position policy.

        A list-local ``listItemSortOrder`` overrides PBListSettings.  Otherwise the
        settings string defaults to manual.  New-item-position only applies while the
        effective sort order is manual.
        """
        lst = self._require_list(list_id)
        sort_order = PB.ShoppingList.ListItemSortOrder.Manual
        if lst.HasField("listItemSortOrder"):
            sort_order = int(lst.listItemSortOrder)
        else:
            settings = self.state.list_settings.get(list_id)
            if (
                settings is not None
                and settings.HasField("listItemSortOrder")
                and settings.listItemSortOrder == "ALListItemSortOrderAlphabetical"
            ):
                sort_order = PB.ShoppingList.ListItemSortOrder.Alphabetical
        if sort_order != PB.ShoppingList.ListItemSortOrder.Manual:
            return False
        position = (
            int(lst.newListItemPosition)
            if lst.HasField("newListItemPosition")
            else PB.ShoppingList.NewListItemPosition.Bottom
        )
        return position == PB.ShoppingList.NewListItemPosition.Top

    def _effective_list_setting_bool(self, list_id: str, field: str) -> bool:
        """Return a per-list boolean with the same default-settings fallback as app.js."""

        for settings_id in (list_id, ""):
            settings = self.state.list_settings.get(settings_id)
            if settings is None or field not in settings.DESCRIPTOR.fields_by_name:
                continue
            try:
                if settings.HasField(field):
                    return bool(getattr(settings, field))
            except ValueError:
                return bool(getattr(settings, field))
        return False

    @staticmethod
    def _category_match_id(category: PBListCategory) -> str:
        return str(category.systemCategory or "") or canonical_category_match_id(str(category.name))

    def _selected_category_group(self, list_id: str) -> PBListCategoryGroup | None:
        groups = self._category_group_index(list_id)
        settings = self.state.list_settings.get(list_id)
        if settings is not None and settings.HasField("listCategoryGroupId"):
            selected = groups.get(str(settings.listCategoryGroupId))
            if selected is not None:
                return selected
        return self._default_category_group(list_id)

    def category_id_for_group(self, list_id: str, item: ListItem, category_group_id: str) -> str:
        """Resolve ``ListItem.categoryIDForCategoryGroupID`` using synchronized list context.

        AnyList first honors an explicit per-item assignment. When no explicit assignment exists,
        it uses ``categoryMatchId`` to find the category in the requested group whose system/
        normalized match ID is the same. ``other`` and missing match IDs deliberately do not
        synthesize a fallback category.
        """

        for assignment in item.categoryAssignments:
            if str(assignment.categoryGroupId) == category_group_id:
                return str(assignment.categoryId or "")

        match_id = str(item.categoryMatchId or "")
        if not match_id or match_id == "other":
            return ""
        for category in self._category_index(list_id).values():
            if str(category.categoryGroupId) != category_group_id:
                continue
            if self._category_match_id(category) == match_id:
                return str(category.identifier)
        return ""

    def item_has_recipe(self, item: ListItem) -> bool:
        """Return AnyList Web's state-aware ``ListItem.hasRecipe()`` value.

        Ingredient-derived shopping items count as recipe items immediately. Otherwise a stored
        ``recipeId`` only counts while that ID resolves in the synchronized recipe manager.
        """

        if item.ingredients:
            return True
        recipe_id = str(item.recipeId or "")
        return bool(recipe_id and recipe_id in self.state.recipes)

    def _category_assignments_for_new_item(
        self,
        list_id: str,
        name: str,
        *,
        generic_root_category: str | None,
        generic_enabled: bool,
    ) -> list[PBListItemCategoryAssignment]:
        """Port ShoppingList.zL(name, tag) from the current web client.

        Each category group receives, in order of precedence, the explicit per-item rule,
        the generic grocery root category, or the group's default category. When a generic
        root exists but a category group has no matching system category, the web client
        deliberately leaves that group unassigned instead of falling back to its default.
        """

        lowered = name.lower()
        groups = list(self._category_group_index(list_id).values())
        matching_rules = [
            rule
            for rule in self._categorization_rule_index(list_id).values()
            if str(rule.itemName).lower() == lowered
        ]
        rules_by_group = {str(rule.categoryGroupId): rule for rule in matching_rules}
        use_generic = len(matching_rules) != len(groups) and generic_enabled
        categories = self._category_index(list_id)
        result: list[PBListItemCategoryAssignment] = []

        for group in groups:
            group_id = str(group.identifier)
            category_id = ""
            rule = rules_by_group.get(group_id)
            if rule is not None:
                category_id = str(rule.categoryId or "")
            elif use_generic and generic_root_category is not None:
                for category in categories.values():
                    if (
                        str(category.categoryGroupId) == group_id
                        and str(category.systemCategory or "") == generic_root_category
                    ):
                        category_id = str(category.identifier)
            else:
                category_id = str(group.defaultCategoryId or "")

            if not category_id:
                continue
            result.append(
                PB.PBListItemCategoryAssignment(
                    identifier=uuid5_hex(group_id, _CATEGORY_ASSIGNMENT_NAMESPACE),
                    categoryGroupId=group_id,
                    categoryId=category_id,
                )
            )
        return result

    async def prepare_item_for_add(
        self,
        list_id: str,
        name: str,
        *,
        item_id: str | None = None,
    ) -> ListItem:
        """Construct the exact fresh ListItem shape used by ShoppingList.TJ/NJ in app.js."""

        self._require_list(list_id)
        item = PB.ListItem(
            identifier=item_id or uuid4_hex(),
            listId=list_id,
            userId=self.user_id,
            name=name or "-",
        )

        generic_enabled = self._effective_list_setting_bool(
            list_id, "genericGroceryAutocompleteEnabled"
        )
        grocery_tag: str | None = None
        generic_root: str | None = None
        if generic_enabled and self.on_classify_grocery_item is not None:
            grocery_tag, generic_root = await self.on_classify_grocery_item(name)
            if grocery_tag:
                item.priceMatchupTag = grocery_tag

        assignments = self._category_assignments_for_new_item(
            list_id,
            name,
            generic_root_category=generic_root,
            generic_enabled=generic_enabled,
        )
        for assignment in assignments:
            item.categoryAssignments.add().CopyFrom(assignment)

        selected_group = self._selected_category_group(list_id)
        match_id = "other"
        if selected_group is not None:
            selected_group_id = str(selected_group.identifier)
            selected_assignment = next(
                (
                    value
                    for value in item.categoryAssignments
                    if str(value.categoryGroupId) == selected_group_id
                ),
                None,
            )
            if selected_assignment is not None:
                category = self._category_index(list_id).get(str(selected_assignment.categoryId))
                if category is not None:
                    match_id = self._category_match_id(category)

        item.categoryMatchId = match_id
        item.category = match_id if match_id in _SYSTEM_ITEM_CATEGORIES else "other"
        return item

    @staticmethod
    def _merge_autocomplete_item(base: ListItem, source: ListItem) -> None:
        """Port ShoppingList.xJ's protobuf-field merge for non-current autocomplete rows."""

        for field in base.DESCRIPTOR.fields:
            name = field.name
            if field.is_repeated:
                target = getattr(base, name)
                if len(target) != 0:
                    continue
                incoming = getattr(source, name)
                if field.message_type is not None:
                    for value in incoming:
                        target.add().CopyFrom(value)
                else:
                    target.extend(incoming)
                continue

            try:
                if base.HasField(name) or not source.HasField(name):
                    continue
            except ValueError:
                continue
            if field.message_type is not None:
                getattr(base, name).CopyFrom(getattr(source, name))
            else:
                setattr(base, name, getattr(source, name))

    async def prepare_autocomplete_item_for_add(
        self,
        list_id: str,
        source_item: ListItem,
    ) -> ListItem:
        """Construct the new-item branch used for Favorite/Recent/generic autocomplete rows."""

        item = await self.prepare_item_for_add(list_id, str(source_item.name))
        self._merge_autocomplete_item(item, source_item)
        return item

    def apply_category_to_prepared_item(
        self,
        list_id: str,
        item: ListItem,
        category: PBListCategory,
    ) -> None:
        """Apply the add-item controller's active-category context before queueing the item."""

        if str(category.listId or list_id) != list_id:
            raise ValueError("category does not belong to the target shopping list")
        group_id = str(category.categoryGroupId)
        assignment = PB.PBListItemCategoryAssignment(
            identifier=uuid5_hex(group_id, _CATEGORY_ASSIGNMENT_NAMESPACE),
            categoryGroupId=group_id,
            categoryId=str(category.identifier),
        )
        existing = next(
            (
                index
                for index, value in enumerate(item.categoryAssignments)
                if str(value.categoryGroupId) == group_id
            ),
            -1,
        )
        if existing >= 0:
            item.categoryAssignments[existing].CopyFrom(assignment)
        else:
            item.categoryAssignments.add().CopyFrom(assignment)

        match_id = self._category_match_id(category)
        item.categoryMatchId = match_id
        item.category = match_id if match_id in _SYSTEM_ITEM_CATEGORIES else "other"

    async def add_prepared_item(
        self,
        list_id: str,
        item: ListItem,
        *,
        flush: bool = True,
        handler_id: str = "add-shopping-list-item",
    ) -> ListItem:
        """Queue one already-constructed item through ShoppingList.oK/Mz."""

        lst = self._require_list(list_id)
        prepared = clone_message(item)
        prepared.listId = list_id
        if not prepared.identifier:
            prepared.identifier = uuid4_hex()
        if not prepared.userId:
            prepared.userId = self.user_id

        at_top = self._new_items_at_top(list_id)
        if at_top:
            lst.items.insert(0, prepared)
            stored = lst.items[0]
        else:
            lst.items.add().CopyFrom(prepared)
            stored = lst.items[-1]

        fields: dict[str, Any] = {
            "listId": list_id,
            "listItemId": prepared.identifier,
            "listItem": clone_message(prepared),
        }
        if at_top:
            fields["list"] = PB.ShoppingList(
                identifier=list_id,
                newListItemPosition=PB.ShoppingList.NewListItemPosition.Top,
            )
        await self.operation(handler_id, flush=flush, **fields)
        return stored

    async def add_item(
        self,
        list_id: str,
        name: str,
        *,
        item_id: str | None = None,
        details: str | None = None,
        quantity: PBItemQuantity | None = None,
        package_size: PBItemPackageSize | None = None,
        category_match_id: str | None = None,
        store_ids: Sequence[str] = (),
        product_upc: str | None = None,
        flush: bool = True,
        handler_id: str = "add-shopping-list-item",
    ) -> ListItem:
        item = await self.prepare_item_for_add(list_id, name, item_id=item_id)
        if details is not None:
            item.details = details
        if quantity is not None:
            item.quantityPb.CopyFrom(quantity)
        if package_size is not None:
            item.packageSizePb.CopyFrom(package_size)
        if category_match_id is not None:
            item.categoryMatchId = category_match_id
            item.category = (
                category_match_id if category_match_id in _SYSTEM_ITEM_CATEGORIES else "other"
            )
        if store_ids:
            item.storeIds.extend(store_ids)
        if product_upc is not None:
            item.productUpc = product_upc
        return await self.add_prepared_item(
            list_id,
            item,
            flush=flush,
            handler_id=handler_id,
        )

    async def add_items(
        self,
        list_id: str,
        items: Iterable[ListItem],
        *,
        flush: bool = True,
        handler_id: str = "bulk-add-list-items",
    ) -> list[ListItem]:
        lst = self._require_list(list_id)
        clones: list[ListItem] = []
        for source in items:
            item = clone_message(source)
            item.listId = list_id
            if not item.identifier:
                item.identifier = uuid4_hex()
            clones.append(item)
        at_top = self._new_items_at_top(list_id)
        # The web client reverses the input before repeated index-0 insertion.  This
        # preserves the caller-visible order while also making the queued item payload
        # match the exact insertion sequence.
        operation_items = list(reversed(clones)) if at_top else clones
        for item in operation_items:
            if at_top:
                lst.items.insert(0, item)
            else:
                lst.items.add().CopyFrom(item)
        # Official app chunks bulk additions at 25 list items per operation.
        for start in range(0, len(operation_items), 25):
            chunk = operation_items[start : start + 25]
            partial = PB.ShoppingList(identifier=list_id)
            for item in chunk:
                partial.items.add().CopyFrom(item)
            if at_top:
                partial.newListItemPosition = PB.ShoppingList.NewListItemPosition.Top
            await self.operation(handler_id, listId=list_id, list=partial, flush=False)
        if flush:
            await self.flush()
        result: list[ListItem] = []
        for item in clones:
            stored = self.item(list_id, item.identifier)
            if stored is not None:
                result.append(stored)
        return result

    async def revive_matching_item(
        self,
        list_id: str,
        source_item: ListItem,
        *,
        store_filter: PBStoreFilter | None = None,
        selected_category: PBListCategory | None = None,
        flush: bool = True,
    ) -> ListItem | None:
        """Revive the current-list item represented by an autocomplete item.

        The web add-item controller treats current-list autocomplete rows specially: it
        finds the fully-equal ListItem already on the list, uncrosses it when necessary,
        then applies the active store-filter/category context.  Favorite, recent, and
        generic rows instead go through the new-item path, so this method intentionally
        returns ``None`` when no fully-equal current item exists.
        """
        lst = self._require_list(list_id)
        current = next((item for item in lst.items if items_equal(source_item, item)), None)
        if current is None:
            return None

        queued = False
        if bool(current.checked):
            await self.set_checked(list_id, str(current.identifier), False, flush=False)
            queued = True

        if (
            store_filter is not None
            and not bool(getattr(store_filter, "showsAllItems", False))
            and list(getattr(store_filter, "storeIds", ()))
        ):
            before = tuple(current.storeIds)
            await self.add_store_ids_to_items(
                list_id,
                [str(current.identifier)],
                list(store_filter.storeIds),
                flush=False,
            )
            queued = queued or tuple(current.storeIds) != before

        if selected_category is not None:
            group_id = str(getattr(selected_category, "categoryGroupId", "") or "")
            category_id = str(getattr(selected_category, "identifier", "") or "")
            if group_id and category_id:
                assignment = PB.PBListItemCategoryAssignment(
                    categoryGroupId=group_id,
                    categoryId=category_id,
                )
                await self.assign_category(
                    list_id, str(current.identifier), assignment, flush=False
                )
                match_id = str(getattr(selected_category, "systemCategory", "") or "")
                if not match_id:
                    match_id = canonical_category_match_id(
                        str(getattr(selected_category, "name", "") or "")
                    )
                await self.set_category_match_id(
                    list_id, str(current.identifier), match_id, flush=False
                )
                queued = True

        if flush and queued:
            await self.flush()
        return current

    async def remove_item(self, list_id: str, item_id: str, *, flush: bool = True) -> None:
        lst = self._require_list(list_id)
        idx = self._find_item_index(lst, item_id)
        if idx < 0:
            raise KeyError(item_id)
        original = clone_message(lst.items[idx])
        del lst.items[idx]
        await self.operation(
            "remove-shopping-list-item",
            listId=list_id,
            listItemId=item_id,
            listItem=original,
            flush=flush,
        )
        if self.on_items_became_recent is not None:
            await self.on_items_became_recent(list_id, [original], False, flush)

    async def set_checked(
        self, list_id: str, item_id: str, checked: bool, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        item.checked = checked
        await self.operation(
            "set-list-item-checked",
            listId=list_id,
            listItemId=item_id,
            updatedValue="y" if checked else "n",
            flush=flush,
        )
        if checked and self.on_items_became_recent is not None:
            await self.on_items_became_recent(list_id, [clone_message(item)], False, flush)

    async def rename_item(
        self, list_id: str, item_id: str, name: str, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        original = str(item.name)
        item.name = name
        await self.operation(
            "set-list-item-name",
            listId=list_id,
            listItemId=item_id,
            updatedValue=name,
            originalValue=original,
            flush=flush,
        )

    async def set_details(
        self, list_id: str, item_id: str, details: str, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        original = str(item.details)
        item.details = details
        await self.operation(
            "set-list-item-details",
            listId=list_id,
            listItemId=item_id,
            updatedValue=details,
            originalValue=original,
            flush=flush,
        )

    async def set_product_upc(
        self, list_id: str, item_id: str, upc: str, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        original = str(item.productUpc)
        if upc == original:
            return
        item.productUpc = upc
        await self.operation(
            "set-list-item-product-upc",
            listId=list_id,
            listItemId=item_id,
            updatedValue=upc,
            originalValue=original,
            flush=flush,
        )

    async def set_photo(
        self, list_id: str, item_id: str, photo_id: str | None, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        del item.photoIds[:]
        if photo_id:
            item.photoIds.append(photo_id)
        await self.operation(
            "set-list-item-photo-id",
            listId=list_id,
            listItemId=item_id,
            updatedValue=photo_id or "",
            originalValue="",
            flush=flush,
        )

    async def set_quantity(
        self, list_id: str, item_id: str, quantity: PBItemQuantity, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        current = item.quantityPb if item.HasField("quantityPb") else PB.PBItemQuantity()
        if quantity_equal(current, quantity):
            return
        item.quantityPb.CopyFrom(quantity)
        deprecated = quantity_to_deprecated_string(quantity)
        if deprecated:
            item.deprecatedQuantity = deprecated.strip()
        else:
            item.ClearField("deprecatedQuantity")
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.quantityPb.CopyFrom(quantity)
        if item.HasField("deprecatedQuantity"):
            partial.deprecatedQuantity = item.deprecatedQuantity
        await self.operation(
            "set-list-item-quantity-v2",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )

    async def set_package_size(
        self, list_id: str, item_id: str, package_size: PBItemPackageSize, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        current = item.packageSizePb if item.HasField("packageSizePb") else PB.PBItemPackageSize()
        if package_size_equal(current, package_size):
            return
        item.packageSizePb.CopyFrom(package_size)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.packageSizePb.CopyFrom(package_size)
        await self.operation(
            "set-list-item-package-size",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )

    async def set_quantity_override(
        self, list_id: str, item_id: str, value: bool, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        if (
            item.HasField("itemQuantityShouldOverrideIngredientQuantity")
            and bool(item.itemQuantityShouldOverrideIngredientQuantity) == value
        ):
            return
        item.itemQuantityShouldOverrideIngredientQuantity = value
        partial = PB.ListItem(
            identifier=item_id, listId=list_id, itemQuantityShouldOverrideIngredientQuantity=value
        )
        await self.operation(
            "set-item-quantity-should-override-ingredient-quantity",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )

    async def set_package_override(
        self, list_id: str, item_id: str, value: bool, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        if (
            item.HasField("itemPackageSizeShouldOverrideIngredientPackageSize")
            and bool(item.itemPackageSizeShouldOverrideIngredientPackageSize) == value
        ):
            return
        item.itemPackageSizeShouldOverrideIngredientPackageSize = value
        partial = PB.ListItem(
            identifier=item_id,
            listId=list_id,
            itemPackageSizeShouldOverrideIngredientPackageSize=value,
        )
        await self.operation(
            "set-item-package-size-should-override-ingredient-package-size",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )

    async def set_price_quantity(
        self, list_id: str, item_id: str, quantity: PBItemQuantity, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        current = item.priceQuantityPb if item.HasField("priceQuantityPb") else PB.PBItemQuantity()
        if quantity_equal(current, quantity):
            return
        item.priceQuantityPb.CopyFrom(quantity)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.priceQuantityPb.CopyFrom(quantity)
        await self.operation(
            "set-list-item-price-quantity",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )

    async def set_price_package_size(
        self, list_id: str, item_id: str, package: PBItemPackageSize, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        current = (
            item.pricePackageSizePb
            if item.HasField("pricePackageSizePb")
            else PB.PBItemPackageSize()
        )
        if package_size_equal(current, package):
            return
        item.pricePackageSizePb.CopyFrom(package)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.pricePackageSizePb.CopyFrom(package)
        await self.operation(
            "set-list-item-price-package-size",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )

    async def set_price_quantity_override(
        self, list_id: str, item_id: str, value: bool, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        if (
            item.HasField("priceQuantityShouldOverrideItemQuantity")
            and bool(item.priceQuantityShouldOverrideItemQuantity) == value
        ):
            return
        item.priceQuantityShouldOverrideItemQuantity = value
        partial = PB.ListItem(
            identifier=item_id, listId=list_id, priceQuantityShouldOverrideItemQuantity=value
        )
        await self.operation(
            "set-list-item-price-quantity-should-override-item-quantity",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )

    async def set_price_package_override(
        self, list_id: str, item_id: str, value: bool, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        if (
            item.HasField("pricePackageSizeShouldOverrideItemPackageSize")
            and bool(item.pricePackageSizeShouldOverrideItemPackageSize) == value
        ):
            return
        item.pricePackageSizeShouldOverrideItemPackageSize = value
        partial = PB.ListItem(
            identifier=item_id, listId=list_id, pricePackageSizeShouldOverrideItemPackageSize=value
        )
        await self.operation(
            "set-list-item-price-package-size-should-override-item-package-size",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )

    async def assign_category(
        self,
        list_id: str,
        item_id: str,
        assignment: PBListItemCategoryAssignment,
        *,
        flush: bool = True,
    ) -> None:
        item = self._require_item(list_id, item_id)
        if not assignment.categoryGroupId:
            return
        normalized = clone_message(assignment)
        normalized.identifier = uuid5_hex(
            normalized.categoryGroupId, _CATEGORY_ASSIGNMENT_NAMESPACE
        )
        existing = next(
            (
                idx
                for idx, value in enumerate(item.categoryAssignments)
                if value.identifier == normalized.identifier
            ),
            -1,
        )
        if existing >= 0:
            item.categoryAssignments[existing].CopyFrom(normalized)
        else:
            item.categoryAssignments.add().CopyFrom(normalized)
        # The web client serializes the complete mutated item, not a one-assignment partial.
        await self.operation(
            "update-list-item-category-assignment",
            listId=list_id,
            listItemId=item_id,
            listItem=clone_message(item),
            flush=flush,
        )

    async def set_category_match_id(
        self, list_id: str, item_id: str, category_match_id: str, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        item.categoryMatchId = category_match_id
        item.category = (
            category_match_id if category_match_id in _SYSTEM_ITEM_CATEGORIES else "other"
        )
        # AnyList mutates first, then sends the full item and calls categoryID() for
        # originalValue.  Since categoryMatchId is now populated, originalValue is the
        # new match ID; updatedValue is not used by this handler.
        await self.operation(
            "set-list-item-category-match-id",
            listId=list_id,
            listItemId=item_id,
            listItem=clone_message(item),
            originalValue=category_match_id,
            flush=flush,
        )

    async def add_store(
        self, list_id: str, item_id: str, store_id: str, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        if store_id in item.storeIds:
            return
        item.storeIds.append(store_id)
        await self.operation(
            "add-list-item-store-id",
            listId=list_id,
            listItemId=item_id,
            updatedValue=store_id,
            flush=flush,
        )

    async def remove_store(
        self, list_id: str, item_id: str, store_id: str, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        if store_id not in item.storeIds:
            return
        item.storeIds.remove(store_id)
        await self.operation(
            "remove-list-item-store-id",
            listId=list_id,
            listItemId=item_id,
            updatedValue=store_id,
            flush=flush,
        )

    async def save_price(
        self, list_id: str, item_id: str, price: PBItemPrice, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        store_id = str(getattr(price, "storeId", "") or "")
        empty = (not price.HasField("amount") or float(price.amount) == 0.0) and not (
            price.details or ""
        )
        existing_index = next(
            (idx for idx, value in enumerate(item.prices) if (value.storeId or "") == store_id),
            -1,
        )
        if empty:
            if existing_index < 0:
                return
            del item.prices[existing_index]
        elif existing_index >= 0:
            item.prices[existing_index].CopyFrom(price)
        else:
            item.prices.add().CopyFrom(price)
        await self.operation(
            "save-item-price", listId=list_id, listItemId=item_id, itemPrice=price, flush=flush
        )

    async def set_price_matchup_tag(
        self, list_id: str, item_id: str, tag: str, *, flush: bool = True
    ) -> None:
        item = self._require_item(list_id, item_id)
        # AnyList Web mutates first and then reads priceMatchupTag for originalValue,
        # so the wire-level originalValue is intentionally the same as updatedValue.
        item.priceMatchupTag = tag
        await self.operation(
            "set-list-item-price-matchup-tag",
            listId=list_id,
            listItemId=item_id,
            updatedValue=tag,
            originalValue=tag,
            flush=flush,
        )

    async def set_allows_multiple_category_groups(
        self, list_id: str, value: bool, *, flush: bool = True
    ) -> None:
        lst = self._require_list(list_id)
        if (
            lst.HasField("allowsMultipleListCategoryGroups")
            and bool(lst.allowsMultipleListCategoryGroups) == value
        ):
            return
        lst.allowsMultipleListCategoryGroups = value
        partial = PB.ShoppingList(identifier=list_id, allowsMultipleListCategoryGroups=value)
        await self.operation(
            "set-allows-multiple-category-groups", listId=list_id, list=partial, flush=flush
        )

    async def set_new_item_position(
        self, list_id: str, position: int, *, flush: bool = True
    ) -> None:
        lst = self._require_list(list_id)
        current = (
            int(lst.newListItemPosition)
            if lst.HasField("newListItemPosition")
            else PB.ShoppingList.NewListItemPosition.Bottom
        )
        if current == position:
            return
        lst.newListItemPosition = position
        partial = PB.ShoppingList(identifier=list_id, newListItemPosition=position)
        await self.operation(
            "set-new-list-item-position", listId=list_id, list=partial, flush=flush
        )

    async def move_item(
        self, list_id: str, item_id: str, new_index: int, *, flush: bool = True
    ) -> None:
        lst = self._require_list(list_id)
        old_index = self._find_item_index(lst, item_id)
        if old_index < 0:
            raise KeyError(item_id)
        if new_index < 0 or new_index >= len(lst.items):
            raise IndexError(new_index)
        if old_index == new_index:
            return
        item = clone_message(lst.items[old_index])
        del lst.items[old_index]
        lst.items.insert(new_index, item)
        await self.operation(
            "move-shopping-list-item-to-index",
            listId=list_id,
            listItemId=item_id,
            originalValue=str(old_index),
            updatedValue=str(new_index),
            flush=flush,
        )

    async def bulk_set_checked(
        self, list_id: str, item_ids: Sequence[str], checked: bool, *, flush: bool = True
    ) -> None:
        lst = self._require_list(list_id)
        requested = list(dict.fromkeys(item_ids))
        selected = set(requested)
        changed_items: list[ListItem] = []
        for item in lst.items:
            if item.identifier in selected and bool(item.checked) != checked:
                item.checked = checked
                changed_items.append(clone_message(item))
        if not changed_items:
            return
        # The official operation includes every requested ID in the partial list, not
        # only IDs whose local checked bit actually changed.  Change detection merely
        # decides whether an operation is needed at all.
        partial = PB.ShoppingList(identifier=list_id)
        for item_id in requested:
            item = partial.items.add(identifier=item_id, listId=list_id)
            if checked:
                item.checked = True
        await self.operation(
            "bulk-cross-off-list-items" if checked else "bulk-uncross-list-items",
            listId=list_id,
            list=partial,
            flush=flush,
        )
        if checked and self.on_items_became_recent is not None:
            await self.on_items_became_recent(list_id, changed_items, False, flush)

    async def bulk_remove_items(
        self,
        list_id: str,
        item_ids: Sequence[str],
        *,
        remember_recent: bool = True,
        flush: bool = True,
    ) -> list[ListItem]:
        lst = self._require_list(list_id)
        selected = set(item_ids)
        removed = [clone_message(x) for x in lst.items if x.identifier in selected]
        kept = [clone_message(x) for x in lst.items if x.identifier not in selected]
        if not removed:
            return []
        del lst.items[:]
        for item in kept:
            lst.items.add().CopyFrom(item)
        partial = PB.ShoppingList(identifier=list_id)
        for item in removed:
            partial.items.add().CopyFrom(item)
        await self.operation("bulk-remove-list-items", listId=list_id, list=partial, flush=flush)
        if remember_recent and self.on_items_became_recent is not None:
            await self.on_items_became_recent(list_id, removed, False, flush)
        return removed

    async def clear(self, list_id: str, *, flush: bool = True) -> list[ListItem]:
        """Remove every item while mirroring AnyList Web's recents behavior."""
        lst = self._require_list(list_id)
        items = [clone_message(item) for item in lst.items]
        if not items:
            return []
        if self.on_items_became_recent is not None:
            await self.on_items_became_recent(list_id, items, True, flush)
        return await self.bulk_remove_items(
            list_id,
            [str(item.identifier) for item in items],
            remember_recent=False,
            flush=flush,
        )

    async def remove_checked(self, list_id: str, *, flush: bool = True) -> list[ListItem]:
        """Remove all crossed-off items, preserving their recents entries."""
        lst = self._require_list(list_id)
        items = [clone_message(item) for item in lst.items if bool(item.checked)]
        if not items:
            return []
        if self.on_items_became_recent is not None:
            await self.on_items_became_recent(list_id, items, True, flush)
        return await self.bulk_remove_items(
            list_id,
            [str(item.identifier) for item in items],
            remember_recent=False,
            flush=flush,
        )

    async def uncheck_all(self, list_id: str, *, flush: bool = True) -> None:
        lst = self._require_list(list_id)
        changed = False
        for item in lst.items:
            if item.checked:
                item.checked = False
                changed = True
        if changed:
            await self.operation("uncheck-all", listId=list_id, flush=flush)

    async def unshare(self, list_id: str, email: str, *, flush: bool = True) -> None:
        lst = self._require_list(list_id)
        found = False
        for index, user in enumerate(lst.sharedUsers):
            if getattr(user, "email", "") == email:
                del lst.sharedUsers[index]
                found = True
                break
        # ShoppingList.gK returns without queueing when the email is not shared.
        if not found:
            return
        await self.operation(
            "unshare-shopping-list", listId=list_id, updatedValue=email, flush=flush
        )

    async def add_notification_location(
        self,
        list_id: str,
        *,
        name: str,
        address: str,
        latitude: float,
        longitude: float,
        location_id: str | None = None,
        flush: bool = True,
    ) -> PBNotificationLocation | None:
        lst = self._require_list(list_id)
        # AnyList's AK() helper treats latitude+longitude as the location identity.
        # A duplicate is rejected locally and no operation is queued.
        for existing in lst.notificationLocations:
            if float(existing.latitude) == float(latitude) and float(existing.longitude) == float(
                longitude
            ):
                return None
        location = PB.PBNotificationLocation(
            identifier=location_id or uuid4_hex(),
            name=name,
            address=address,
            latitude=latitude,
            longitude=longitude,
        )
        lst.notificationLocations.add().CopyFrom(location)
        await self.operation(
            "add-list-notification-location",
            listId=list_id,
            notificationLocation=location,
            flush=flush,
        )
        return lst.notificationLocations[-1]

    async def remove_notification_location(
        self,
        list_id: str,
        location_id: str,
        *,
        flush: bool = True,
    ) -> PBNotificationLocation | None:
        """Remove a list notification location using Android's native operation contract."""

        lst = self._require_list(list_id)
        removed: PBNotificationLocation | None = None
        kept: list[PBNotificationLocation] = []
        for location in lst.notificationLocations:
            if removed is None and str(location.identifier) == location_id:
                removed = clone_message(location)
            else:
                kept.append(clone_message(location))
        if removed is None:
            return None
        del lst.notificationLocations[:]
        for location in kept:
            lst.notificationLocations.add().CopyFrom(location)
        await self.operation(
            "remove-list-notification-location",
            listId=list_id,
            notificationLocation=removed,
            flush=flush,
        )
        return removed

    async def add_store_ids_to_items(
        self, list_id: str, item_ids: Sequence[str], store_ids: Sequence[str], *, flush: bool = True
    ) -> None:
        await self._set_store_ids_on_items(
            list_id, item_ids, store_ids, add=True, handler_id="add-store-ids-to-items", flush=flush
        )

    async def remove_store_ids_from_items(
        self, list_id: str, item_ids: Sequence[str], store_ids: Sequence[str], *, flush: bool = True
    ) -> None:
        await self._set_store_ids_on_items(
            list_id,
            item_ids,
            store_ids,
            add=False,
            handler_id="remove-store-ids-from-items",
            flush=flush,
        )

    async def _set_store_ids_on_items(
        self,
        list_id: str,
        item_ids: Sequence[str],
        store_ids: Sequence[str],
        *,
        add: bool,
        handler_id: str,
        flush: bool,
    ) -> None:
        lst = self._require_list(list_id)
        wanted = set(item_ids)
        changed: list[ListItem] = []
        for item in lst.items:
            if item.identifier not in wanted:
                continue
            current = list(item.storeIds)
            if add:
                new_ids = current + [x for x in store_ids if x not in current]
            else:
                remove = set(store_ids)
                new_ids = [x for x in current if x not in remove]
            if new_ids == current:
                continue
            del item.storeIds[:]
            item.storeIds.extend(new_ids)
            partial_item = PB.ListItem(identifier=item.identifier, listId=list_id)
            partial_item.storeIds.extend(store_ids)
            changed.append(partial_item)
        if not changed:
            return
        partial = PB.ShoppingList(identifier=list_id)
        for item in changed:
            partial.items.add().CopyFrom(item)
        await self.operation(handler_id, listId=list_id, list=partial, flush=flush)

    async def remove_store_id_from_all_items(
        self, list_id: str, store_id: str, *, flush: bool = True
    ) -> None:
        lst = self._require_list(list_id)
        for item in lst.items:
            while store_id in item.storeIds:
                item.storeIds.remove(store_id)
        await self.operation(
            "remove-store-id-from-all-items", listId=list_id, updatedValue=store_id, flush=flush
        )

    def _store_index(self, list_id: str) -> dict[str, PBStore]:
        return self.state.list_stores.setdefault(list_id, {})

    def _store_filter_index(self, list_id: str) -> dict[str, PBStoreFilter]:
        return self.state.list_store_filters.setdefault(list_id, {})

    def _category_index(self, list_id: str) -> dict[str, PBListCategory]:
        return self.state.list_categories.setdefault(list_id, {})

    def _category_group_index(self, list_id: str) -> dict[str, PBListCategoryGroup]:
        return self.state.list_category_groups.setdefault(list_id, {})

    def _categorization_rule_index(self, list_id: str) -> dict[str, PBListCategorizationRule]:
        return self.state.list_categorization_rules.setdefault(list_id, {})

    async def save_store(
        self, list_id: str, store: PBStore, *, is_new: bool = False, flush: bool = True
    ) -> str:
        updated = clone_message(store)
        if not updated.listId:
            updated.listId = list_id
        stores = self._store_index(list_id)
        if is_new:
            # AnyList Web assigns a new store to the end of the current store ordering
            # before saving the model into its local manager.
            updated.sortIndex = max((int(x.sortIndex) for x in stores.values()), default=-1) + 1
        stores[str(updated.identifier)] = clone_message(updated)
        return await self.operation(
            "new-store" if is_new else "set-store-name",
            listId=list_id,
            updatedStore=clone_message(updated),
            operation_class=PB.PBOperationMetadata.OperationClass.StoreOperation,
            flush=flush,
        )

    async def delete_store(self, list_id: str, store: PBStore, *, flush: bool = True) -> str:
        self._store_index(list_id).pop(str(store.identifier), None)
        return await self.operation(
            "delete-store",
            listId=list_id,
            updatedStore=clone_message(store),
            operation_class=PB.PBOperationMetadata.OperationClass.StoreOperation,
            flush=flush,
        )

    async def set_sorted_store_ids(
        self, list_id: str, store_ids: Sequence[str], *, flush: bool = True
    ) -> str:
        stores = self._store_index(list_id)
        for sort_index, store_id in enumerate(store_ids):
            store = stores.get(str(store_id))
            if store is not None:
                store.sortIndex = sort_index
        return await self.operation(
            "set-sorted-store-ids",
            listId=list_id,
            sortedStoreIds=list(store_ids),
            operation_class=PB.PBOperationMetadata.OperationClass.StoreOperation,
            flush=flush,
        )

    async def save_store_filter(
        self, list_id: str, store_filter: PBStoreFilter, *, is_new: bool = False, flush: bool = True
    ) -> str:
        updated = clone_message(store_filter)
        if not updated.listId:
            updated.listId = list_id
        filters = self._store_filter_index(list_id)
        if is_new:
            updated.sortIndex = max((int(x.sortIndex) for x in filters.values()), default=-1) + 1
        filters[str(updated.identifier)] = clone_message(updated)
        return await self.operation(
            "new-store-filter" if is_new else "update-store-filter",
            listId=list_id,
            updatedStoreFilter=clone_message(updated),
            operation_class=PB.PBOperationMetadata.OperationClass.StoreFilterOperation,
            flush=flush,
        )

    async def delete_store_filter(
        self, list_id: str, store_filter: PBStoreFilter, *, flush: bool = True
    ) -> str:
        filter_id = str(store_filter.identifier)
        self._store_filter_index(list_id).pop(filter_id, None)
        # StoreFilterManager clears the selected per-list filter through ListSettingsManager
        # before it sends the delete operation.  The client wires this callback to the
        # list-settings queue so the two synchronized domains remain consistent.
        if self.on_store_filter_removed is not None:
            await self.on_store_filter_removed(list_id, filter_id, flush)
        return await self.operation(
            "delete-store-filter",
            listId=list_id,
            updatedStoreFilter=clone_message(store_filter),
            operation_class=PB.PBOperationMetadata.OperationClass.StoreFilterOperation,
            flush=flush,
        )

    async def set_sorted_store_filter_ids(
        self, list_id: str, store_filter_ids: Sequence[str], *, flush: bool = True
    ) -> str:
        filters = self._store_filter_index(list_id)
        for sort_index, filter_id in enumerate(store_filter_ids):
            store_filter = filters.get(str(filter_id))
            if store_filter is not None:
                store_filter.sortIndex = sort_index
        return await self.operation(
            "set-sorted-store-filter-ids",
            listId=list_id,
            sortedStoreFilterIds=list(store_filter_ids),
            operation_class=PB.PBOperationMetadata.OperationClass.StoreFilterOperation,
            flush=flush,
        )

    async def save_list_category(
        self,
        category: PBListCategory,
        *,
        handler_id: str = "create-category",
        flush: bool = True,
    ) -> str:
        updated = clone_message(category)
        self._category_index(str(updated.listId))[str(updated.identifier)] = clone_message(updated)
        return await self.operation(
            handler_id,
            listId=str(updated.listId),
            updatedCategory=clone_message(updated),
            operation_class=PB.PBOperationMetadata.OperationClass.ListCategoryOperation,
            flush=flush,
        )

    async def migrate_list_category(self, category: PBListCategory, *, flush: bool = True) -> str:
        return await self.save_list_category(
            category, handler_id="migrate-list-category", flush=flush
        )

    async def rename_list_category(
        self, category: PBListCategory, name: str, *, flush: bool = True
    ) -> str:
        updated = clone_message(category)
        updated.name = name
        return await self.save_list_category(updated, handler_id="set-category-name", flush=flush)

    async def set_list_category_icon(
        self, category: PBListCategory, icon: str, *, flush: bool = True
    ) -> str:
        updated = clone_message(category)
        updated.icon = icon
        return await self.save_list_category(updated, handler_id="set-category-icon", flush=flush)

    def _store_category_group(self, group: PBListCategoryGroup) -> None:
        list_id = str(group.listId)
        categories = self._category_index(list_id)
        for category in group.categories:
            categories[str(category.identifier)] = clone_message(category)
        stored = clone_message(group)
        del stored.categories[:]
        self._category_group_index(list_id)[str(stored.identifier)] = stored

    async def save_category_group(
        self,
        group: PBListCategoryGroup,
        *,
        handler_id: str = "create-category-group",
        flush: bool = True,
    ) -> str:
        updated = clone_message(group)
        if handler_id == "delete-category-group":
            self._category_group_index(str(updated.listId)).pop(str(updated.identifier), None)
            for category_id, category in tuple(self._category_index(str(updated.listId)).items()):
                if str(category.categoryGroupId) == str(updated.identifier):
                    self._category_index(str(updated.listId)).pop(category_id, None)
        else:
            self._store_category_group(updated)
        return await self.operation(
            handler_id,
            listId=str(updated.listId),
            updatedCategoryGroup=clone_message(updated),
            operation_class=PB.PBOperationMetadata.OperationClass.ListCategoryGroupOperation,
            flush=flush,
        )

    async def migrate_category_group(
        self, group: PBListCategoryGroup, *, flush: bool = True
    ) -> str:
        return await self.save_category_group(
            group, handler_id="migrate-list-category-group", flush=flush
        )

    def _default_category_group(self, list_id: str) -> PBListCategoryGroup | None:
        groups = self._category_group_index(list_id)
        preferred_id = uuid5_hex(list_id, _CATEGORY_GROUP_NAMESPACE)
        preferred = groups.get(preferred_id)
        if preferred is not None:
            return preferred
        if not groups:
            return None
        # Q.G falls back to the first category set after localized name sorting.  Python's
        # casefold ordering is the deterministic locale-neutral approximation used by the SDK
        # until live conformance can exercise locale-specific collation.
        return min(
            groups.values(),
            key=lambda value: (str(value.name or "").casefold(), str(value.identifier)),
        )

    async def delete_category_group(
        self, group: PBListCategoryGroup, *, flush: bool = True
    ) -> str | None:
        list_id = str(group.listId)
        groups = self._category_group_index(list_id)
        if len(groups) <= 1:
            # The web client refuses to delete the final category group.
            return None

        group_id = str(group.identifier)
        original = clone_message(groups.get(group_id, group))
        groups.pop(group_id, None)
        for category_id, category in tuple(self._category_index(list_id).items()):
            if str(category.categoryGroupId) == group_id:
                self._category_index(list_id).pop(category_id, None)

        replacement = self._default_category_group(list_id)
        replacement_id = str(replacement.identifier) if replacement is not None else ""

        # Any store filter pinned to the deleted category set is immediately repointed to
        # the fallback set and saved before the category-group delete is queued.
        affected_filters = [
            clone_message(value)
            for value in self._store_filter_index(list_id).values()
            if str(value.listCategoryGroupId or "") == group_id
        ]
        for store_filter in affected_filters:
            if replacement_id:
                store_filter.listCategoryGroupId = replacement_id
            else:
                store_filter.ClearField("listCategoryGroupId")
            await self.save_store_filter(list_id, store_filter, is_new=False, flush=False)

        if self.on_category_group_removed is not None:
            await self.on_category_group_removed(list_id, group_id, flush)

        return await self.operation(
            "delete-category-group",
            listId=list_id,
            updatedCategoryGroup=original,
            operation_class=PB.PBOperationMetadata.OperationClass.ListCategoryGroupOperation,
            flush=flush,
        )

    async def rename_category_group(
        self, group: PBListCategoryGroup, name: str, *, flush: bool = True
    ) -> str:
        updated = clone_message(group)
        updated.name = name
        return await self.save_category_group(
            updated, handler_id="set-category-group-name", flush=flush
        )

    async def set_default_category(
        self, group: PBListCategoryGroup, category_id: str, *, flush: bool = True
    ) -> str:
        updated = clone_message(group)
        updated.defaultCategoryId = category_id
        return await self.save_category_group(
            updated, handler_id="set-default-category-id", flush=flush
        )

    async def set_sorted_category_ids(
        self, group: PBListCategoryGroup, category_ids: Sequence[str], *, flush: bool = True
    ) -> str:
        list_id = str(group.listId)
        category_index = self._category_index(list_id)
        requested = [str(x) for x in category_ids]
        next_index = len(requested)
        for category in category_index.values():
            category_id = str(category.identifier)
            if str(category.categoryGroupId) != str(group.identifier):
                continue
            try:
                category.sortIndex = requested.index(category_id)
            except ValueError:
                category.sortIndex = next_index
                next_index += 1

        # The web client sends identifiers for *all* categories in the group after applying
        # the ordering, not only the IDs explicitly supplied by the caller.
        ordered_categories = sorted(
            (c for c in category_index.values() if str(c.categoryGroupId) == str(group.identifier)),
            key=lambda c: int(c.sortIndex),
        )
        updated = clone_message(group)
        del updated.categories[:]
        for category in ordered_categories:
            updated.categories.add(identifier=str(category.identifier))
        stored_group = clone_message(updated)
        del stored_group.categories[:]
        self._category_group_index(list_id)[str(stored_group.identifier)] = stored_group
        return await self.operation(
            "set-sorted-category-ids",
            listId=list_id,
            updatedCategoryGroup=clone_message(updated),
            operation_class=PB.PBOperationMetadata.OperationClass.ListCategoryGroupOperation,
            flush=flush,
        )

    async def remove_category_ids(
        self,
        group: PBListCategoryGroup,
        categories: Sequence[PBListCategory],
        *,
        flush: bool = True,
    ) -> str:
        list_id = str(group.listId)
        index = self._category_index(list_id)
        for category in categories:
            index.pop(str(category.identifier), None)
        updated = clone_message(group)
        del updated.categories[:]
        for category in categories:
            updated.categories.add().CopyFrom(category)
        operation_id = await self.operation(
            "remove-category-ids",
            listId=list_id,
            updatedCategoryGroup=updated,
            operation_class=PB.PBOperationMetadata.OperationClass.ListCategoryGroupOperation,
            flush=False,
        )
        # AnyList Web immediately prunes categorization rules that reference any removed
        # category, using a second ListCategorizationRuleOperation on the same queue.
        await self.remove_categorization_rules_for_category_ids(
            group, [str(category.identifier) for category in categories], flush=flush
        )
        return operation_id

    async def save_categorization_rule(
        self, rule: PBListCategorizationRule, *, flush: bool = True
    ) -> str:
        updated = self._canonical_categorization_rule(rule)
        list_id = str(updated.listId)
        self._categorization_rule_index(list_id)[str(updated.identifier)] = clone_message(updated)
        return await self.operation(
            "save-categorization-rule",
            listId=list_id,
            updatedCategorizationRule=clone_message(updated),
            operation_class=PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation,
            flush=flush,
        )

    def _canonical_categorization_rule(
        self, rule: PBListCategorizationRule, *, list_id: str | None = None
    ) -> PBListCategorizationRule:
        """Build the deterministic rule shape used by every official creation path."""
        resolved_list_id = list_id or str(rule.listId)
        category_group_id = str(rule.categoryGroupId)
        item_name = str(rule.itemName)
        identifier = category_rule_identifier(item_name, category_group_id, resolved_list_id)
        existing = self._categorization_rule_index(resolved_list_id).get(identifier)
        if existing is not None:
            updated = clone_message(existing)
        else:
            updated = PB.PBListCategorizationRule(
                identifier=identifier,
                listId=resolved_list_id,
                categoryGroupId=category_group_id,
                itemName=item_name.lower(),
            )
        if rule.categoryId:
            updated.categoryId = rule.categoryId
        else:
            updated.ClearField("categoryId")
        return updated

    async def bulk_save_categorization_rules(
        self, list_id: str, rules: Sequence[PBListCategorizationRule], *, flush: bool = True
    ) -> None:
        index = self._categorization_rule_index(list_id)
        canonical = [self._canonical_categorization_rule(rule, list_id=list_id) for rule in rules]
        for rule in canonical:
            index[str(rule.identifier)] = clone_message(rule)
        for start in range(0, len(canonical), 25):
            await self.operation(
                "bulk-save-categorization-rules",
                listId=list_id,
                updatedCategorizationRules=[
                    clone_message(x) for x in canonical[start : start + 25]
                ],
                operation_class=PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation,
                flush=False,
            )
        if flush:
            await self.flush()

    async def migrate_categorization_rules(
        self, list_id: str, rules: Sequence[PBListCategorizationRule], *, flush: bool = True
    ) -> None:
        index = self._categorization_rule_index(list_id)
        canonical = [self._canonical_categorization_rule(rule, list_id=list_id) for rule in rules]
        for rule in canonical:
            index[str(rule.identifier)] = clone_message(rule)
        for start in range(0, len(canonical), 25):
            await self.operation(
                "migrate-per-user-categorization-rules",
                listId=list_id,
                updatedCategorizationRules=[
                    clone_message(x) for x in canonical[start : start + 25]
                ],
                operation_class=PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation,
                flush=False,
            )
        if flush:
            await self.flush()

    async def remove_categorization_rules_for_category_ids(
        self, group: PBListCategoryGroup, category_ids: Sequence[str], *, flush: bool = True
    ) -> str:
        list_id = str(group.listId)
        category_ids_set = {str(x) for x in category_ids}
        rules = self._categorization_rule_index(list_id)
        for identifier, rule in tuple(rules.items()):
            if (
                str(rule.categoryGroupId) == str(group.identifier)
                and str(rule.categoryId) in category_ids_set
            ):
                rules.pop(identifier, None)
        updated = clone_message(group)
        del updated.categories[:]
        for category_id in category_ids:
            updated.categories.add(identifier=category_id)
        return await self.operation(
            "remove-categorization-rules-for-category-ids",
            listId=list_id,
            updatedCategoryGroup=updated,
            operation_class=PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation,
            flush=flush,
        )

    async def reorder_items(
        self, list_id: str, ordered_item_ids: Sequence[str], *, flush: bool = True
    ) -> None:
        lst = self._require_list(list_id)
        by_id = {item.identifier: clone_message(item) for item in lst.items}
        ordered = [by_id[item_id] for item_id in ordered_item_ids if item_id in by_id]
        ordered.extend(item for item_id, item in by_id.items() if item_id not in ordered_item_ids)
        del lst.items[:]
        for item in ordered:
            lst.items.add().CopyFrom(item)
        # ShoppingList.oF clones the complete mutated list after replacing its item array.
        await self.operation(
            "set-ordered-list-items", listId=list_id, list=clone_message(lst), flush=flush
        )

    def _saved_item_for_recipe_ingredient(
        self, list_id: str, item_ingredient: PBItemIngredient
    ) -> ListItem | None:
        """Find the favorite/recent item AnyList Web uses to enrich a new recipe item."""
        ingredient = (
            item_ingredient.ingredient
            if item_ingredient.HasField("ingredient")
            else PB.PBIngredient()
        )
        wanted_words = stem_words(((ingredient.name or "").lower()).split(" "))
        package = (
            item_ingredient.packageSizePb
            if item_ingredient.HasField("packageSizePb")
            else PB.PBItemPackageSize()
        )
        wanted_package = normalized_raw_package_size(package)
        favorite_id = favorite_list_id(list_id)
        recent_id = recent_list_id(list_id)
        for starter_id, source in (
            (favorite_id, self.state.favorite_item_lists),
            (recent_id, self.state.recent_item_lists),
        ):
            starter = source.get(starter_id)
            if starter is None:
                continue
            values = list(starter.items)
            if starter_id == recent_id:
                values.reverse()
            for candidate in values:
                candidate_words = stem_words(((candidate.name or "").lower()).split(" "))
                if candidate_words != wanted_words:
                    continue
                candidate_package = (
                    candidate.packageSizePb
                    if candidate.HasField("packageSizePb")
                    else PB.PBItemPackageSize()
                )
                if normalized_raw_package_size(candidate_package) == wanted_package:
                    return candidate
        return None

    async def add_recipe_ingredient(
        self, list_id: str, item_ingredient: PBItemIngredient, *, flush: bool = True
    ) -> ListItem:
        """Merge a recipe ingredient into its deterministic shopping-list item.

        Mirrors the web client's provenance model: the deterministic ID is derived from
        normalized/stemmed ingredient identity, unit and package size. Existing items gain
        provenance instead of duplicating.
        """
        lst = self._require_list(list_id)
        item_id = recipe_list_item_identifier(item_ingredient, list_id)
        item = self.item(list_id, item_id)
        if item is None:
            ingredient = item_ingredient.ingredient
            item = PB.ListItem(
                identifier=item_id,
                listId=list_id,
                name=(ingredient.name or "-"),
                userId=self.user_id,
            )
            item.ingredients.add().CopyFrom(item_ingredient)
            if item_ingredient.HasField("packageSizePb"):
                item.packageSizePb.CopyFrom(item_ingredient.packageSizePb)
            if item_ingredient.HasField("quantityPb"):
                normalized = normalize_unit(item_ingredient.quantityPb.unit or "").lower()
                if normalized in _PRICE_QUANTITY_UNITS:
                    item.priceQuantityShouldOverrideItemQuantity = True

            saved = self._saved_item_for_recipe_ingredient(list_id, item_ingredient)
            if saved is not None:
                # Official mask starts by preserving the recipe-derived quantity/package.
                mask = EXCLUDE_ITEM_QUANTITY | EXCLUDE_PACKAGE_SIZE
                if not bool(saved.priceQuantityShouldOverrideItemQuantity):
                    mask |= EXCLUDE_PRICE_QUANTITY
                if getattr(saved, "recipeId", ""):
                    mask |= EXCLUDE_DETAILS
                apply_properties_from_item(item, saved, mask)

            lst.items.add().CopyFrom(item)
            await self.operation(
                "add-item-ingredient-to-list-item",
                listId=list_id,
                listItemId=item_id,
                listItem=item,
                flush=flush,
            )
            stored = self.item(list_id, item_id)
            assert stored is not None
            return stored

        add_item_ingredient(item, item_ingredient)
        # Re-adding a checked recipe item revives it and drops ingredient override state,
        # matching the official client before the provenance mutation is sent.
        if item.checked:
            await self.set_checked(list_id, item_id, False, flush=False)
            if item.itemQuantityShouldOverrideIngredientQuantity:
                await self.set_quantity_override(list_id, item_id, False, flush=False)
            if item.itemPackageSizeShouldOverrideIngredientPackageSize:
                await self.set_package_override(list_id, item_id, False, flush=False)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.ingredients.add().CopyFrom(item_ingredient)
        await self.operation(
            "add-item-ingredient-to-list-item",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )
        return item

    async def _remove_recipe_ingredient_from_item(
        self, list_id: str, item_id: str, item_ingredient: PBItemIngredient, *, flush: bool = True
    ) -> bool:
        """Remove provenance from a known item ID, matching the web client's ``BK`` path."""
        item = self.item(list_id, item_id)
        if item is None:
            return False
        removed = remove_item_ingredient(item, item_ingredient)
        if not removed:
            return False
        if not item.ingredients:
            lst = self._require_list(list_id)
            idx = self._find_item_index(lst, item_id)
            if idx >= 0:
                del lst.items[idx]
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.ingredients.add().CopyFrom(item_ingredient)
        await self.operation(
            "remove-ingredient-id-from-list-item",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )
        return True

    async def remove_recipe_ingredient(
        self, list_id: str, item_ingredient: PBItemIngredient, *, flush: bool = True
    ) -> bool:
        item_id = recipe_list_item_identifier(item_ingredient, list_id)
        return await self._remove_recipe_ingredient_from_item(
            list_id, item_id, item_ingredient, flush=flush
        )

    async def _update_recipe_ingredient_at_item(
        self, list_id: str, item_id: str, item_ingredient: PBItemIngredient, *, flush: bool = True
    ) -> bool:
        """Replace/add provenance without reviving a checked item.

        This is the web client's ``HK`` path, used during recipe edits when the
        deterministic list-item identity has not changed.
        """
        item = self.item(list_id, item_id)
        if item is None:
            return False
        add_item_ingredient(item, item_ingredient)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.ingredients.add().CopyFrom(item_ingredient)
        await self.operation(
            "add-item-ingredient-to-list-item",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )
        return True

    async def sync_recipe_update(
        self,
        list_id: str,
        new_recipe: PBRecipe,
        old_recipe: PBRecipe,
        *,
        events: dict[str, PBCalendarEvent] | None = None,
        flush: bool = True,
    ) -> int:
        """Reconcile recipe-derived shopping items after a recipe edit.

        AnyList Web walks a snapshot of every list item's provenance. Deleted
        ingredients are removed, identity-changing edits move provenance to the new
        deterministic item (unless the old item was already checked), and identity-stable
        edits replace the provenance in place.
        """
        lst = self.get(list_id)
        if lst is None or not getattr(new_recipe, "identifier", ""):
            return 0

        # The web helper only does work when name, ingredient payloads or scale factor
        # changed.  Comparing serialized ingredient messages preserves protobuf presence.
        same_name = (getattr(new_recipe, "name", "") or "") == (
            getattr(old_recipe, "name", "") or ""
        )
        same_scale = float(getattr(new_recipe, "scaleFactor", 0.0) or 0.0) == float(
            getattr(old_recipe, "scaleFactor", 0.0) or 0.0
        )
        new_ingredients = list(getattr(new_recipe, "ingredients", ()))
        old_ingredients = list(getattr(old_recipe, "ingredients", ()))
        same_ingredients = len(new_ingredients) == len(old_ingredients) and all(
            a.SerializeToString() == b.SerializeToString()
            for a, b in zip(new_ingredients, old_ingredients)
        )
        if same_name and same_scale and same_ingredients:
            return 0

        ingredient_by_id = {
            str(ingredient.identifier): ingredient
            for ingredient in new_ingredients
            if getattr(ingredient, "identifier", "")
        }
        event_index = events or {}
        # Snapshot first because operations below may remove items from the live list.
        snapshots = [clone_message(item) for item in lst.items]
        changed = 0
        for snapshot in snapshots:
            old_item_id = str(snapshot.identifier)
            was_checked = bool(getattr(snapshot, "checked", False))
            for source in list(snapshot.ingredients):
                if (getattr(source, "recipeId", "") or "") != new_recipe.identifier:
                    continue
                ingredient_id = (
                    source.ingredient.identifier if source.HasField("ingredient") else ""
                )
                updated_ingredient = ingredient_by_id.get(str(ingredient_id))
                if updated_ingredient is None:
                    if await self._remove_recipe_ingredient_from_item(
                        list_id, old_item_id, source, flush=False
                    ):
                        changed += 1
                    continue

                event = None
                event_id = getattr(source, "eventId", "") or ""
                if event_id:
                    event = event_index.get(str(event_id))
                    # The official code skips event-linked provenance if that event no
                    # longer exists; event deletion has its own cleanup path.
                    if event is None:
                        continue
                updated_source = ingredient_to_item_ingredient(
                    updated_ingredient, new_recipe, event
                )
                new_item_id = recipe_list_item_identifier(updated_source, list_id)
                if new_item_id != old_item_id:
                    removed = await self._remove_recipe_ingredient_from_item(
                        list_id, old_item_id, source, flush=False
                    )
                    if removed:
                        changed += 1
                    if not was_checked:
                        await self.add_recipe_ingredient(list_id, updated_source, flush=False)
                        changed += 1
                else:
                    if await self._update_recipe_ingredient_at_item(
                        list_id, old_item_id, updated_source, flush=False
                    ):
                        changed += 1

        if flush and changed:
            await self.flush()
        return changed

    async def sync_recipe_event_update(
        self,
        list_id: str,
        new_event: PBCalendarEvent,
        old_event: PBCalendarEvent,
        recipe: PBRecipe,
        *,
        flush: bool = True,
    ) -> int:
        """Update recipe provenance after an event date or scale-factor change (official PR path)."""
        if self.get(list_id) is None:
            return 0
        if (getattr(new_event, "date", "") or "") == (
            getattr(old_event, "date", "") or ""
        ) and float(getattr(new_event, "recipeScaleFactor", 0.0) or 0.0) == float(
            getattr(old_event, "recipeScaleFactor", 0.0) or 0.0
        ):
            return 0
        ingredients = {
            str(x.identifier): x for x in recipe.ingredients if getattr(x, "identifier", "")
        }
        snapshots = [clone_message(x) for x in self._require_list(list_id).items]
        changed = 0
        for snapshot in snapshots:
            for source in snapshot.ingredients:
                if (getattr(source, "eventId", "") or "") != new_event.identifier:
                    continue
                ingredient_id = (
                    source.ingredient.identifier if source.HasField("ingredient") else ""
                )
                ingredient = ingredients.get(str(ingredient_id))
                if ingredient is None:
                    continue
                updated = ingredient_to_item_ingredient(ingredient, recipe, new_event)
                if await self._update_recipe_ingredient_at_item(
                    list_id, str(snapshot.identifier), updated, flush=False
                ):
                    changed += 1
        if flush and changed:
            await self.flush()
        return changed

    async def sync_event_list_update(
        self,
        list_id: str,
        new_event: PBCalendarEvent,
        old_event: PBCalendarEvent,
        *,
        flush: bool = True,
    ) -> int:
        """Reconcile free-form meal event list items with shopping provenance (official bR path)."""
        lst = self.get(list_id)
        if lst is None:
            return 0
        same_title = (getattr(new_event, "title", "") or "") == (
            getattr(old_event, "title", "") or ""
        )
        same_date = (getattr(new_event, "date", "") or "") == (getattr(old_event, "date", "") or "")
        new_items = list(getattr(new_event, "eventListItems", ()))
        old_items = list(getattr(old_event, "eventListItems", ()))
        same_items = len(new_items) == len(old_items) and all(
            a.SerializeToString() == b.SerializeToString() for a, b in zip(new_items, old_items)
        )
        if same_title and same_date and same_items:
            return 0
        item_by_id = {str(x.identifier): x for x in new_items if getattr(x, "identifier", "")}
        snapshots = [clone_message(x) for x in lst.items]
        changed = 0
        for snapshot in snapshots:
            old_item_id = str(snapshot.identifier)
            was_checked = bool(getattr(snapshot, "checked", False))
            for source in snapshot.ingredients:
                if (getattr(source, "eventId", "") or "") != new_event.identifier:
                    continue
                ingredient_id = (
                    source.ingredient.identifier if source.HasField("ingredient") else ""
                )
                event_item = item_by_id.get(str(ingredient_id))
                if event_item is None:
                    if await self._remove_recipe_ingredient_from_item(
                        list_id, old_item_id, source, flush=False
                    ):
                        changed += 1
                    continue
                updated = event_list_item_to_item_ingredient(event_item, new_event)
                new_item_id = recipe_list_item_identifier(updated, list_id)
                if new_item_id != old_item_id:
                    if await self._remove_recipe_ingredient_from_item(
                        list_id, old_item_id, source, flush=False
                    ):
                        changed += 1
                    if not was_checked:
                        await self.add_recipe_ingredient(list_id, updated, flush=False)
                        changed += 1
                elif await self._update_recipe_ingredient_at_item(
                    list_id, old_item_id, updated, flush=False
                ):
                    changed += 1
        if flush and changed:
            await self.flush()
        return changed

    async def remove_event_references(
        self, list_id: str, event_id: str, *, flush: bool = True
    ) -> int:
        lst = self.get(list_id)
        if lst is None:
            return 0
        matches: list[tuple[str, PBItemIngredient]] = []
        for item in list(lst.items):
            for source in list(item.ingredients):
                if (getattr(source, "eventId", "") or "") == event_id:
                    matches.append((str(item.identifier), clone_message(source)))
        removed = 0
        for item_id, source in matches:
            if await self._remove_recipe_ingredient_from_item(
                list_id, item_id, source, flush=False
            ):
                removed += 1
        if flush and removed:
            await self.flush()
        return removed

    async def remove_recipe_references(
        self, list_id: str, recipe_id: str, *, flush: bool = True
    ) -> int:
        """Remove every provenance entry for a recipe from the designated recipe list."""
        lst = self.get(list_id)
        if lst is None:
            return 0
        matches: list[PBItemIngredient] = []
        for item in list(lst.items):
            for source in list(item.ingredients):
                if (getattr(source, "recipeId", "") or "") == recipe_id:
                    matches.append(clone_message(source))
        removed = 0
        for source in matches:
            if await self.remove_recipe_ingredient(list_id, source, flush=False):
                removed += 1
        if flush and removed:
            await self.flush()
        return removed

    async def raw_legacy_operation(
        self, handler_id: str, *, flush: bool = True, **fields: Any
    ) -> str:
        op = self.legacy_queue.new_operation(handler_id, **fields)
        return await self.legacy_queue.enqueue(op, flush=flush)

    async def flush(self) -> OperationAck | None:
        # Flush both queues just as the web manager checks both queues.
        a = await self.legacy_queue.flush()
        b = await self.queue.flush()
        return b or a

    async def restore(self) -> int:
        return await self.legacy_queue.restore() + await self.queue.restore()

    def _require_list(self, list_id: str) -> ShoppingList:
        value = self.get(list_id)
        if value is None:
            raise KeyError(f"Unknown shopping list {list_id}")
        return value

    def _require_item(self, list_id: str, item_id: str) -> ListItem:
        value = self.item(list_id, item_id)
        if value is None:
            raise KeyError(f"Unknown list item {item_id} in {list_id}")
        return value
