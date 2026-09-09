from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any
from uuid import UUID

from google.protobuf.message import Message

from ..identifiers import uuid4_hex, uuid5_hex
from ..operations import QueueSpec
from ..derived import (
    add_item_ingredient,
    event_list_item_to_item_ingredient,
    ingredient_to_item_ingredient,
    normalized_raw_package_size,
    remove_item_ingredient,
    recipe_list_item_identifier,
)
from ..item_semantics import (
    EXCLUDE_DETAILS,
    EXCLUDE_ITEM_QUANTITY,
    EXCLUDE_PACKAGE_SIZE,
    EXCLUDE_PRICE_QUANTITY,
    apply_properties_from_item,
    items_equal,
    package_size_equal,
    quantity_equal,
    quantity_to_deprecated_string,
)
from ..normalization import canonical_category_match_id
from ..parsing.quantity import normalize_unit
from ..stemming import stem_words
from .starter import favorite_list_id, recent_list_id
from ..proto import PB, message_class
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message, partial_message

# Official web client namespace used by list categorization-rule IDs.
_CATEGORY_RULE_NAMESPACE = UUID(hex="f4338133428d4f0b94027c9b23243f14")
_CATEGORY_GROUP_NAMESPACE = UUID(hex="f656a81f0e0a419aa45121f4f2eac51b")
_CATEGORY_ASSIGNMENT_NAMESPACE = UUID(hex="08e5c5bdcd694454a1ffd611b6d9abc0")
_SYSTEM_ITEM_CATEGORIES = {
    "baby", "bakery", "beverages", "breakfast-and-cereal",
    "condiments-oils-and-salad-dressings", "cooking-and-baking", "dairy",
    "frozen-foods", "grains-pasta-and-side-dishes", "health-and-personal-care",
    "household-and-cleaning", "meat", "pet-supplies", "produce", "seafood",
    "snacks-cookies-and-candy", "soups-and-canned-goods", "wine-beer-spirits", "other",
}
_PRICE_QUANTITY_UNITS = {
    "cup", "fl oz", "oz", "tbsp", "tsp", "g", "mg", "l", "dl", "ml",
    "slice", "clove", "pinch", "drop", "dash", "inch",
}


def category_rule_identifier(item_name: str, category_group_id: str, list_id: str) -> str:
    return uuid5_hex(item_name.lower() + category_group_id + list_id, _CATEGORY_RULE_NAMESPACE)


class ShoppingListsService(OperationService):
    """Shopping-list API with optimistic protobuf-backed state."""

    def __init__(self, transport: AnyListTransport, state: AnyListState, *, user_id: str, journal=None):
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
        self.queue.on_response = self._on_v2_response
        self.legacy_queue.on_response = self._on_legacy_response
        self.on_store_filter_removed: Callable[[str, str, bool], Awaitable[None]] | None = None
        self.on_category_group_removed: Callable[[str, str, bool], Awaitable[None]] | None = None
        self.on_items_became_recent: Callable[[str, Sequence[Message], bool, bool], Awaitable[None]] | None = None
        self.on_folder_refresh_requested: Callable[[], Awaitable[None]] | None = None
        self._refresh_after_legacy_queue = False
        self._refresh_folders_after_legacy_queue = False

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
        if refresh_ids:
            await self.refresh()

    def all(self) -> list[Message]:
        return list(self.state.shopping_lists.values())

    def get(self, list_id: str) -> Message | None:
        return self.state.shopping_lists.get(list_id)

    def item(self, list_id: str, item_id: str) -> Message | None:
        return self.state.get_item(list_id, item_id)

    def has_pending_new_list(self) -> bool:
        return any(
            str(op.metadata.handlerId) == "new-shopping-list"
            for op in self.legacy_queue._pending
        )

    def remove_list_local(self, list_id: str) -> Message | None:
        """Apply ShoppingListManager.qB's local list-removal side effects.

        Folder membership and PBListSettings queueing are coordinated by the client/folder
        service; this method owns the shopping/list-local indexed state.
        """
        removed = self.state.shopping_lists.pop(list_id, None)
        while list_id in self.state.ordered_shopping_list_ids:
            self.state.ordered_shopping_list_ids.remove(list_id)
        self.state._drop_list_local_state(list_id)
        return removed

    async def refresh(self) -> Message:
        response = await self.transport.post_proto(
            "/data/shopping-lists/all",
            fields={
                "timestamps": self.state.shopping_list_timestamps(),
                "logical_timestamps": self.state.shopping_list_logical_timestamps(),
            },
            response_type="ShoppingListsResponse",
        )
        assert isinstance(response, Message)
        self.state.apply_shopping_lists(response)
        return response

    @staticmethod
    def _find_item_index(lst: Message, item_id: str) -> int:
        for idx, item in enumerate(lst.items):
            if item.identifier == item_id:
                return idx
        return -1

    async def create(self, name: str, *, list_id: str | None = None, flush: bool = True) -> Message:
        list_id = list_id or uuid4_hex()
        lst = PB.ShoppingList(identifier=list_id, name=name)
        self.state.shopping_lists[list_id] = clone(lst)
        await self.operation("new-shopping-list", listId=list_id, list=lst, flush=flush)
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
            if settings is not None and settings.HasField("listItemSortOrder"):
                if settings.listItemSortOrder == "ALListItemSortOrderAlphabetical":
                    sort_order = PB.ShoppingList.ListItemSortOrder.Alphabetical
        if sort_order != PB.ShoppingList.ListItemSortOrder.Manual:
            return False
        position = (
            int(lst.newListItemPosition)
            if lst.HasField("newListItemPosition")
            else PB.ShoppingList.NewListItemPosition.Bottom
        )
        return position == PB.ShoppingList.NewListItemPosition.Top

    async def add_item(
        self,
        list_id: str,
        name: str,
        *,
        item_id: str | None = None,
        details: str | None = None,
        quantity: Message | None = None,
        package_size: Message | None = None,
        category_match_id: str | None = None,
        store_ids: Sequence[str] = (),
        product_upc: str | None = None,
        flush: bool = True,
        handler_id: str = "add-shopping-list-item",
    ) -> Message:
        lst = self._require_list(list_id)
        item = PB.ListItem(identifier=item_id or uuid4_hex(), listId=list_id, name=name)
        if details is not None:
            item.details = details
        if quantity is not None:
            item.quantityPb.CopyFrom(quantity)
        if package_size is not None:
            item.packageSizePb.CopyFrom(package_size)
        if category_match_id is not None:
            item.categoryMatchId = category_match_id
        if store_ids:
            item.storeIds.extend(store_ids)
        if product_upc is not None:
            item.productUpc = product_upc
        at_top = self._new_items_at_top(list_id)
        if at_top:
            lst.items.insert(0, item)
        else:
            lst.items.add().CopyFrom(item)
        fields: dict[str, Any] = {
            "listId": list_id,
            "listItemId": item.identifier,
            "listItem": clone_message(item),
        }
        if at_top:
            fields["list"] = PB.ShoppingList(
                identifier=list_id,
                newListItemPosition=PB.ShoppingList.NewListItemPosition.Top,
            )
        await self.operation(handler_id, flush=flush, **fields)
        return lst.items[0] if at_top else lst.items[-1]

    async def add_items(
        self,
        list_id: str,
        items: Iterable[Message],
        *,
        flush: bool = True,
        handler_id: str = "bulk-add-list-items",
    ) -> list[Message]:
        lst = self._require_list(list_id)
        clones: list[Message] = []
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
            await self.operation(
                handler_id, listId=list_id, list=partial, flush=False
            )
        if flush:
            await self.flush()
        return [self.item(list_id, item.identifier) for item in clones if self.item(list_id, item.identifier)]

    async def revive_matching_item(
        self,
        list_id: str,
        source_item: Message,
        *,
        store_filter: Message | None = None,
        selected_category: Message | None = None,
        flush: bool = True,
    ) -> Message | None:
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

    async def set_checked(self, list_id: str, item_id: str, checked: bool, *, flush: bool = True) -> None:
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

    async def rename_item(self, list_id: str, item_id: str, name: str, *, flush: bool = True) -> None:
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

    async def set_details(self, list_id: str, item_id: str, details: str, *, flush: bool = True) -> None:
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

    async def set_product_upc(self, list_id: str, item_id: str, upc: str, *, flush: bool = True) -> None:
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

    async def set_photo(self, list_id: str, item_id: str, photo_id: str | None, *, flush: bool = True) -> None:
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

    async def set_quantity(self, list_id: str, item_id: str, quantity: Message, *, flush: bool = True) -> None:
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
            "set-list-item-quantity-v2", listId=list_id, listItemId=item_id, listItem=partial, flush=flush
        )

    async def set_package_size(self, list_id: str, item_id: str, package_size: Message, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        current = item.packageSizePb if item.HasField("packageSizePb") else PB.PBItemPackageSize()
        if package_size_equal(current, package_size):
            return
        item.packageSizePb.CopyFrom(package_size)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.packageSizePb.CopyFrom(package_size)
        await self.operation(
            "set-list-item-package-size", listId=list_id, listItemId=item_id, listItem=partial, flush=flush
        )

    async def set_quantity_override(self, list_id: str, item_id: str, value: bool, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        if bool(item.itemQuantityShouldOverrideIngredientQuantity) == value:
            return
        item.itemQuantityShouldOverrideIngredientQuantity = value
        partial = PB.ListItem(identifier=item_id, listId=list_id, itemQuantityShouldOverrideIngredientQuantity=value)
        await self.operation(
            "set-item-quantity-should-override-ingredient-quantity",
            listId=list_id, listItemId=item_id, listItem=partial, flush=flush,
        )

    async def set_package_override(self, list_id: str, item_id: str, value: bool, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        if bool(item.itemPackageSizeShouldOverrideIngredientPackageSize) == value:
            return
        item.itemPackageSizeShouldOverrideIngredientPackageSize = value
        partial = PB.ListItem(identifier=item_id, listId=list_id, itemPackageSizeShouldOverrideIngredientPackageSize=value)
        await self.operation(
            "set-item-package-size-should-override-ingredient-package-size",
            listId=list_id, listItemId=item_id, listItem=partial, flush=flush,
        )

    async def set_price_quantity(self, list_id: str, item_id: str, quantity: Message, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        current = item.priceQuantityPb if item.HasField("priceQuantityPb") else PB.PBItemQuantity()
        if quantity_equal(current, quantity):
            return
        item.priceQuantityPb.CopyFrom(quantity)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.priceQuantityPb.CopyFrom(quantity)
        await self.operation("set-list-item-price-quantity", listId=list_id, listItemId=item_id, listItem=partial, flush=flush)

    async def set_price_package_size(self, list_id: str, item_id: str, package: Message, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        current = item.pricePackageSizePb if item.HasField("pricePackageSizePb") else PB.PBItemPackageSize()
        if package_size_equal(current, package):
            return
        item.pricePackageSizePb.CopyFrom(package)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.pricePackageSizePb.CopyFrom(package)
        await self.operation("set-list-item-price-package-size", listId=list_id, listItemId=item_id, listItem=partial, flush=flush)

    async def set_price_quantity_override(self, list_id: str, item_id: str, value: bool, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        if bool(item.priceQuantityShouldOverrideItemQuantity) == value:
            return
        item.priceQuantityShouldOverrideItemQuantity = value
        partial = PB.ListItem(identifier=item_id, listId=list_id, priceQuantityShouldOverrideItemQuantity=value)
        await self.operation("set-list-item-price-quantity-should-override-item-quantity", listId=list_id, listItemId=item_id, listItem=partial, flush=flush)

    async def set_price_package_override(self, list_id: str, item_id: str, value: bool, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        if bool(item.pricePackageSizeShouldOverrideItemPackageSize) == value:
            return
        item.pricePackageSizeShouldOverrideItemPackageSize = value
        partial = PB.ListItem(identifier=item_id, listId=list_id, pricePackageSizeShouldOverrideItemPackageSize=value)
        await self.operation("set-list-item-price-package-size-should-override-item-package-size", listId=list_id, listItemId=item_id, listItem=partial, flush=flush)

    async def assign_category(self, list_id: str, item_id: str, assignment: Message, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        if not assignment.categoryGroupId:
            return
        normalized = clone_message(assignment)
        normalized.identifier = uuid5_hex(normalized.categoryGroupId, _CATEGORY_ASSIGNMENT_NAMESPACE)
        existing = next(
            (idx for idx, value in enumerate(item.categoryAssignments) if value.identifier == normalized.identifier),
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

    async def set_category_match_id(self, list_id: str, item_id: str, category_match_id: str, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        item.categoryMatchId = category_match_id
        item.category = category_match_id if category_match_id in _SYSTEM_ITEM_CATEGORIES else "other"
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

    async def add_store(self, list_id: str, item_id: str, store_id: str, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        if store_id in item.storeIds:
            return
        item.storeIds.append(store_id)
        await self.operation("add-list-item-store-id", listId=list_id, listItemId=item_id, updatedValue=store_id, flush=flush)

    async def remove_store(self, list_id: str, item_id: str, store_id: str, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        if store_id not in item.storeIds:
            return
        item.storeIds.remove(store_id)
        await self.operation("remove-list-item-store-id", listId=list_id, listItemId=item_id, updatedValue=store_id, flush=flush)

    async def save_price(self, list_id: str, item_id: str, price: Message, *, flush: bool = True) -> None:
        item = self._require_item(list_id, item_id)
        store_id = str(getattr(price, "storeId", "") or "")
        empty = (not price.HasField("amount") or float(price.amount) == 0.0) and not (price.details or "")
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
        await self.operation("save-item-price", listId=list_id, listItemId=item_id, itemPrice=price, flush=flush)

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
        lst.allowsMultipleListCategoryGroups = value
        partial = PB.ShoppingList(identifier=list_id, allowsMultipleListCategoryGroups=value)
        await self.operation(
            "set-allows-multiple-category-groups", listId=list_id, list=partial, flush=flush
        )

    async def set_new_item_position(
        self, list_id: str, position: int, *, flush: bool = True
    ) -> None:
        lst = self._require_list(list_id)
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
        changed_items: list[Message] = []
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
    ) -> list[Message]:
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

    async def clear(self, list_id: str, *, flush: bool = True) -> list[Message]:
        """Remove every item while mirroring AnyList Web's recents behavior."""
        lst = self._require_list(list_id)
        items = [clone_message(item) for item in lst.items]
        if not items:
            return []
        if self.on_items_became_recent is not None:
            await self.on_items_became_recent(list_id, items, True, False)
        return await self.bulk_remove_items(
            list_id,
            [str(item.identifier) for item in items],
            remember_recent=False,
            flush=flush,
        )

    async def remove_checked(self, list_id: str, *, flush: bool = True) -> list[Message]:
        """Remove all crossed-off items, preserving their recents entries."""
        lst = self._require_list(list_id)
        items = [clone_message(item) for item in lst.items if bool(item.checked)]
        if not items:
            return []
        if self.on_items_became_recent is not None:
            await self.on_items_became_recent(list_id, items, True, False)
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
        for index, user in enumerate(lst.sharedUsers):
            if getattr(user, "email", "") == email:
                del lst.sharedUsers[index]
                break
        await self.operation("unshare-shopping-list", listId=list_id, updatedValue=email, flush=flush)

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
    ) -> Message | None:
        lst = self._require_list(list_id)
        # AnyList's AK() helper treats latitude+longitude as the location identity.
        # A duplicate is rejected locally and no operation is queued.
        for existing in lst.notificationLocations:
            if float(existing.latitude) == float(latitude) and float(existing.longitude) == float(longitude):
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
            list_id, item_ids, store_ids, add=False, handler_id="remove-store-ids-from-items", flush=flush
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
        changed: list[Message] = []
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

    def _store_index(self, list_id: str) -> dict[str, Message]:
        return self.state.list_stores.setdefault(list_id, {})

    def _store_filter_index(self, list_id: str) -> dict[str, Message]:
        return self.state.list_store_filters.setdefault(list_id, {})

    def _category_index(self, list_id: str) -> dict[str, Message]:
        return self.state.list_categories.setdefault(list_id, {})

    def _category_group_index(self, list_id: str) -> dict[str, Message]:
        return self.state.list_category_groups.setdefault(list_id, {})

    def _categorization_rule_index(self, list_id: str) -> dict[str, Message]:
        return self.state.list_categorization_rules.setdefault(list_id, {})

    async def save_store(
        self, list_id: str, store: Message, *, is_new: bool = False, flush: bool = True
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

    async def delete_store(self, list_id: str, store: Message, *, flush: bool = True) -> str:
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
        self, list_id: str, store_filter: Message, *, is_new: bool = False, flush: bool = True
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
        self, list_id: str, store_filter: Message, *, flush: bool = True
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
        category: Message,
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

    async def migrate_list_category(self, category: Message, *, flush: bool = True) -> str:
        return await self.save_list_category(category, handler_id="migrate-list-category", flush=flush)

    async def rename_list_category(
        self, category: Message, name: str, *, flush: bool = True
    ) -> str:
        updated = clone_message(category)
        updated.name = name
        return await self.save_list_category(updated, handler_id="set-category-name", flush=flush)

    async def set_list_category_icon(
        self, category: Message, icon: str, *, flush: bool = True
    ) -> str:
        updated = clone_message(category)
        updated.icon = icon
        return await self.save_list_category(updated, handler_id="set-category-icon", flush=flush)

    def _store_category_group(self, group: Message) -> None:
        list_id = str(group.listId)
        categories = self._category_index(list_id)
        for category in group.categories:
            categories[str(category.identifier)] = clone_message(category)
        stored = clone_message(group)
        del stored.categories[:]
        self._category_group_index(list_id)[str(stored.identifier)] = stored

    async def save_category_group(
        self,
        group: Message,
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

    async def migrate_category_group(self, group: Message, *, flush: bool = True) -> str:
        return await self.save_category_group(
            group, handler_id="migrate-list-category-group", flush=flush
        )

    def _default_category_group(self, list_id: str) -> Message | None:
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
        return min(groups.values(), key=lambda value: (str(value.name or "").casefold(), str(value.identifier)))

    async def delete_category_group(
        self, group: Message, *, flush: bool = True
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
        self, group: Message, name: str, *, flush: bool = True
    ) -> str:
        updated = clone_message(group)
        updated.name = name
        return await self.save_category_group(
            updated, handler_id="set-category-group-name", flush=flush
        )

    async def set_default_category(
        self, group: Message, category_id: str, *, flush: bool = True
    ) -> str:
        updated = clone_message(group)
        updated.defaultCategoryId = category_id
        return await self.save_category_group(
            updated, handler_id="set-default-category-id", flush=flush
        )

    async def set_sorted_category_ids(
        self, group: Message, category_ids: Sequence[str], *, flush: bool = True
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
        self, group: Message, categories: Sequence[Message], *, flush: bool = True
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
        self, rule: Message, *, flush: bool = True
    ) -> str:
        updated = clone_message(rule)
        self._categorization_rule_index(str(updated.listId))[str(updated.identifier)] = clone_message(updated)
        return await self.operation(
            "save-categorization-rule",
            listId=str(updated.listId),
            updatedCategorizationRule=clone_message(updated),
            operation_class=PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation,
            flush=flush,
        )

    async def bulk_save_categorization_rules(
        self, list_id: str, rules: Sequence[Message], *, flush: bool = True
    ) -> None:
        index = self._categorization_rule_index(list_id)
        for rule in rules:
            index[str(rule.identifier)] = clone_message(rule)
        for start in range(0, len(rules), 25):
            await self.operation(
                "bulk-save-categorization-rules",
                listId=list_id,
                updatedCategorizationRules=[clone_message(x) for x in rules[start : start + 25]],
                operation_class=PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation,
                flush=False,
            )
        if flush:
            await self.flush()

    async def migrate_categorization_rules(
        self, list_id: str, rules: Sequence[Message], *, flush: bool = True
    ) -> None:
        index = self._categorization_rule_index(list_id)
        for rule in rules:
            index[str(rule.identifier)] = clone_message(rule)
        for start in range(0, len(rules), 25):
            await self.operation(
                "migrate-per-user-categorization-rules",
                listId=list_id,
                updatedCategorizationRules=[clone_message(x) for x in rules[start : start + 25]],
                operation_class=PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation,
                flush=False,
            )
        if flush:
            await self.flush()

    async def remove_categorization_rules_for_category_ids(
        self, group: Message, category_ids: Sequence[str], *, flush: bool = True
    ) -> str:
        list_id = str(group.listId)
        category_ids_set = {str(x) for x in category_ids}
        rules = self._categorization_rule_index(list_id)
        for identifier, rule in tuple(rules.items()):
            if str(rule.categoryGroupId) == str(group.identifier) and str(rule.categoryId) in category_ids_set:
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

    async def reorder_items(self, list_id: str, ordered_item_ids: Sequence[str], *, flush: bool = True) -> None:
        lst = self._require_list(list_id)
        by_id = {item.identifier: clone_message(item) for item in lst.items}
        ordered = [by_id[item_id] for item_id in ordered_item_ids if item_id in by_id]
        ordered.extend(item for item_id, item in by_id.items() if item_id not in ordered_item_ids)
        del lst.items[:]
        for item in ordered:
            lst.items.add().CopyFrom(item)
        # The official handler sends a partial ShoppingList whose *items* are in the new
        # order; there is no orderedListItemIds field in the wire schema.
        partial = PB.ShoppingList(identifier=list_id)
        for item in ordered:
            partial.items.add().CopyFrom(item)
        await self.operation("set-ordered-list-items", listId=list_id, list=partial, flush=flush)

    def _saved_item_for_recipe_ingredient(
        self, list_id: str, item_ingredient: Message
    ) -> Message | None:
        """Find the favorite/recent item AnyList Web uses to enrich a new recipe item."""
        ingredient = item_ingredient.ingredient if item_ingredient.HasField("ingredient") else PB.PBIngredient()
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
        self, list_id: str, item_ingredient: Message, *, flush: bool = True
    ) -> Message:
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
                identifier=item_id, listId=list_id, name=(ingredient.name or "-"), userId=self.user_id
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
                listId=list_id, listItemId=item_id, listItem=item, flush=flush,
            )
            return self.item(list_id, item_id)

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
            listId=list_id, listItemId=item_id, listItem=partial, flush=flush,
        )
        return item

    async def _remove_recipe_ingredient_from_item(
        self, list_id: str, item_id: str, item_ingredient: Message, *, flush: bool = True
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
            listId=list_id, listItemId=item_id, listItem=partial, flush=flush,
        )
        return True

    async def remove_recipe_ingredient(
        self, list_id: str, item_ingredient: Message, *, flush: bool = True
    ) -> bool:
        item_id = recipe_list_item_identifier(item_ingredient, list_id)
        return await self._remove_recipe_ingredient_from_item(
            list_id, item_id, item_ingredient, flush=flush
        )

    async def _update_recipe_ingredient_at_item(
        self, list_id: str, item_id: str, item_ingredient: Message, *, flush: bool = True
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
            listId=list_id, listItemId=item_id, listItem=partial, flush=flush,
        )
        return True

    async def sync_recipe_update(
        self,
        list_id: str,
        new_recipe: Message,
        old_recipe: Message,
        *,
        events: dict[str, Message] | None = None,
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
        same_name = (getattr(new_recipe, "name", "") or "") == (getattr(old_recipe, "name", "") or "")
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
                updated_source = ingredient_to_item_ingredient(updated_ingredient, new_recipe, event)
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
        new_event: Message,
        old_event: Message,
        recipe: Message,
        *,
        flush: bool = True,
    ) -> int:
        """Update recipe provenance after an event date or scale-factor change (official PR path)."""
        if self.get(list_id) is None:
            return 0
        if (getattr(new_event, "date", "") or "") == (getattr(old_event, "date", "") or "") and float(
            getattr(new_event, "recipeScaleFactor", 0.0) or 0.0
        ) == float(getattr(old_event, "recipeScaleFactor", 0.0) or 0.0):
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
                ingredient_id = source.ingredient.identifier if source.HasField("ingredient") else ""
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
        self, list_id: str, new_event: Message, old_event: Message, *, flush: bool = True
    ) -> int:
        """Reconcile free-form meal event list items with shopping provenance (official bR path)."""
        lst = self.get(list_id)
        if lst is None:
            return 0
        same_title = (getattr(new_event, "title", "") or "") == (getattr(old_event, "title", "") or "")
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
                ingredient_id = source.ingredient.identifier if source.HasField("ingredient") else ""
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
        matches: list[tuple[str, Message]] = []
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
        matches: list[Message] = []
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

    async def raw_legacy_operation(self, handler_id: str, *, flush: bool = True, **fields: Any) -> str:
        op = self.legacy_queue.new_operation(handler_id, **fields)
        return await self.legacy_queue.enqueue(op, flush=flush)

    async def flush(self):
        # Flush both queues just as the web manager checks both queues.
        a = await self.legacy_queue.flush()
        b = await self.queue.flush()
        return b or a

    async def restore(self) -> int:
        return await self.legacy_queue.restore() + await self.queue.restore()

    def _require_list(self, list_id: str) -> Message:
        value = self.get(list_id)
        if value is None:
            raise KeyError(f"Unknown shopping list {list_id}")
        return value

    def _require_item(self, list_id: str, item_id: str) -> Message:
        value = self.item(list_id, item_id)
        if value is None:
            raise KeyError(f"Unknown list item {item_id} in {list_id}")
        return value
