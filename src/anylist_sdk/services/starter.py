from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from google.protobuf.message import Message

from ..identifiers import uuid4_hex, uuid5_hex
from ..item_semantics import quantity_to_deprecated_string
from ..operations import OperationQueue, QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message

_FAVORITE_NAMESPACE = UUID(hex="839503408980408581879d73a33ec4c1")
_RECENT_NAMESPACE = UUID(hex="f915bc60200c4c61b4b0a17bd9978960")
_AGGREGATE_FAVORITES_ID = "dbd9354fd9e847b398882e823a8fd388"
_RECENT_LIMIT = 200
_BULK_BUCKET = 25


def favorite_list_id(list_id: str) -> str:
    return uuid5_hex(list_id, _FAVORITE_NAMESPACE)


def recent_list_id(list_id: str) -> str:
    return uuid5_hex(list_id, _RECENT_NAMESPACE)


def aggregate_favorites_id() -> str:
    return _AGGREGATE_FAVORITES_ID


class StarterListsService(OperationService):
    def __init__(
        self,
        transport: AnyListTransport,
        state: AnyListState,
        *,
        user_id: str,
        journal=None,
    ) -> None:
        super().__init__(
            transport,
            state,
            user_id=user_id,
            spec=QueueSpec(
                f"{user_id}:starter-lists",
                "/data/starter-lists/update",
                "PBStarterListOperation",
                "PBStarterListOperationList",
            ),
            journal=journal,
        )
        self.order_queue = OperationQueue(
            transport,
            QueueSpec(
                f"{user_id}:starter-list-order",
                "/data/starter-lists/update-ordered-ids",
                "PBOrderedStarterListIDsOperation",
                "PBOrderedStarterListIDsOperationList",
            ),
            user_id=user_id,
            journal=journal,
        )
        self.user_id = user_id
        self.queue.on_response = self._on_response
        self.order_queue.on_response = self._on_order_response

    async def _on_response(self, response: Message) -> None:
        all_lists = {
            **self.state.starter_lists,
            **self.state.recent_item_lists,
            **self.state.favorite_item_lists,
        }
        mismatch = False
        for index, original in enumerate(response.originalTimestamps):
            current = all_lists.get(str(original.identifier))
            if current is not None:
                if float(current.timestamp) == float(original.timestamp):
                    if index < len(response.newTimestamps):
                        current.timestamp = response.newTimestamps[index].timestamp
                else:
                    mismatch = True
        if mismatch:
            await self.refresh()

    async def _on_order_response(self, response: Message) -> None:
        if not response.originalTimestamps or not response.newTimestamps:
            return
        original = float(response.originalTimestamps[0].timestamp)
        if original == float(self.state.ordered_starter_list_ids_timestamp):
            self.state.ordered_starter_list_ids_timestamp = float(
                response.newTimestamps[0].timestamp
            )
        else:
            await self.refresh_order()

    def all(self) -> list[Message]:
        return list(self.state.starter_lists.values())

    def recent(self) -> list[Message]:
        return list(self.state.recent_item_lists.values())

    def favorites(self) -> list[Message]:
        return list(self.state.favorite_item_lists.values())

    def get(self, list_id: str) -> Message | None:
        return (
            self.state.starter_lists.get(list_id)
            or self.state.recent_item_lists.get(list_id)
            or self.state.favorite_item_lists.get(list_id)
        )

    async def refresh(self) -> Message:
        response = await self.transport.post_proto(
            "/data/starter-lists/all-v2",
            fields={
                "user_lists_timestamps": self.state._starter_timestamps(
                    self.state.starter_lists
                ),
                "recent_item_lists_timestamps": self.state._starter_timestamps(
                    self.state.recent_item_lists
                ),
                "favorite_item_lists_timestamps": self.state._starter_timestamps(
                    self.state.favorite_item_lists
                ),
            },
            response_type="StarterListsResponseV2",
        )
        assert isinstance(response, Message)
        self.state.apply_starter_lists(response)
        return response

    async def refresh_order(self) -> Message:
        timestamp = PB.PBTimestamp(
            identifier=self.user_id,
            timestamp=self.state.ordered_starter_list_ids_timestamp,
        )
        response = await self.transport.post_proto(
            "/data/starter-lists/ordered-ids",
            fields={"timestamp": timestamp},
            response_type="PBIdentifierList",
        )
        assert isinstance(response, Message)
        self.state.apply_ordered_starter_ids(response)
        return response

    async def create(
        self,
        name: str,
        *,
        list_id: str | None = None,
        user_list_id: str | None = None,
        starter_type: int | None = None,
        flush: bool = True,
    ) -> Message:
        lst = PB.StarterList(
            identifier=list_id or uuid4_hex(), name=name, userId=self.user_id
        )
        if user_list_id:
            lst.listId = user_list_id
        if starter_type is not None:
            lst.starterListType = starter_type
        self.state.starter_lists[lst.identifier] = clone(lst)
        if lst.identifier not in self.state.ordered_starter_list_ids:
            self.state.ordered_starter_list_ids.append(lst.identifier)
        await self.operation(
            "new-starter-list",
            listId=lst.identifier,
            list=lst,
            flush=flush,
        )
        return self.state.starter_lists[lst.identifier]

    async def ensure_favorites(self, shopping_list_id: str, *, flush: bool = True) -> Message:
        identifier = favorite_list_id(shopping_list_id)
        existing = self.state.favorite_item_lists.get(identifier)
        if existing is not None:
            return existing
        lst = PB.StarterList(
            identifier=identifier,
            listId=shopping_list_id,
            name="Favorite Items",
            starterListType=PB.StarterList.Type.FavoriteItemsType,
        )
        self.state.favorite_item_lists[identifier] = clone(lst)
        await self.operation(
            "new-starter-list",
            listId=identifier,
            list=lst,
            flush=flush,
        )
        return self.state.favorite_item_lists[identifier]

    async def ensure_recents(self, shopping_list_id: str, *, flush: bool = True) -> Message:
        identifier = recent_list_id(shopping_list_id)
        existing = self.state.recent_item_lists.get(identifier)
        if existing is not None:
            return existing
        lst = PB.StarterList(
            identifier=identifier,
            listId=shopping_list_id,
            name="Recent Items",
            starterListType=PB.StarterList.Type.RecentItemsType,
        )
        self.state.recent_item_lists[identifier] = clone(lst)
        await self.operation(
            "new-starter-list",
            listId=identifier,
            list=lst,
            flush=flush,
        )
        return self.state.recent_item_lists[identifier]

    async def remove(self, list_id: str, *, flush: bool = True) -> None:
        self.state.starter_lists.pop(list_id, None)
        self.state.recent_item_lists.pop(list_id, None)
        self.state.favorite_item_lists.pop(list_id, None)
        if list_id in self.state.ordered_starter_list_ids:
            self.state.ordered_starter_list_ids.remove(list_id)
        await self.operation("remove-starter-list", listId=list_id, flush=flush)

    async def add_item(
        self, list_id: str, item: Message, *, flush: bool = True
    ) -> Message:
        lst = self._require(list_id)
        await self._trim_recents_for_add(lst, 1, flush=False)
        x = clone_message(item)
        if not x.identifier:
            x.identifier = uuid4_hex()
        x.listId = list_id
        lst.items.add().CopyFrom(x)
        await self.operation(
            "add-item",
            listId=list_id,
            listItemId=x.identifier,
            listItem=x,
            flush=flush,
        )
        return lst.items[-1]

    async def bulk_add_items(
        self, list_id: str, items: Sequence[Message], *, flush: bool = True
    ) -> list[Message]:
        if not items:
            return []
        lst = self._require(list_id)
        incoming = list(items)
        if self._is_recent(lst) and len(incoming) > _RECENT_LIMIT:
            incoming = incoming[-_RECENT_LIMIT:]
        await self._trim_recents_for_add(lst, len(incoming), flush=False)

        added: list[Message] = []
        clones: list[Message] = []
        for item in incoming:
            x = clone_message(item)
            if not x.identifier:
                x.identifier = uuid4_hex()
            x.listId = list_id
            lst.items.add().CopyFrom(x)
            added.append(lst.items[-1])
            clones.append(clone_message(x))

        for start in range(0, len(clones), _BULK_BUCKET):
            partial = PB.StarterList(identifier=list_id)
            for item in clones[start : start + _BULK_BUCKET]:
                partial.items.add().CopyFrom(item)
            await self.operation(
                "bulk-add-list-items",
                listId=list_id,
                list=partial,
                flush=False,
            )
        if flush:
            await self.flush()
        return added

    async def remove_item(
        self, list_id: str, item_id: str, *, flush: bool = True
    ) -> None:
        lst = self._require(list_id)
        original = None
        for i, x in enumerate(lst.items):
            if x.identifier == item_id:
                original = clone_message(x)
                del lst.items[i]
                break
        if original is None:
            raise KeyError(item_id)
        await self.operation(
            "remove-item",
            listId=list_id,
            listItemId=item_id,
            listItem=original,
            flush=flush,
        )

    async def bulk_remove_items(
        self, list_id: str, item_ids: Sequence[str], *, flush: bool = True
    ) -> None:
        if not item_ids:
            return
        lst = self._require(list_id)
        selected = set(item_ids)
        removed = [clone_message(x) for x in lst.items if x.identifier in selected]
        if not removed:
            return
        kept = [clone_message(x) for x in lst.items if x.identifier not in selected]
        del lst.items[:]
        for item in kept:
            lst.items.add().CopyFrom(item)
        partial = PB.StarterList(identifier=list_id)
        for item in removed:
            partial.items.add().CopyFrom(item)
        await self.operation(
            "bulk-remove-list-items", listId=list_id, list=partial, flush=flush
        )

    async def clear(self, list_id: str, *, flush: bool = True) -> None:
        lst = self._require(list_id)
        del lst.items[:]
        await self.operation("clear-starter-list", listId=list_id, flush=flush)

    async def rename(self, list_id: str, name: str, *, flush: bool = True) -> None:
        lst = self._require(list_id)
        lst.name = name
        await self.operation(
            "rename-list", listId=list_id, updatedValue=name, flush=flush
        )

    async def set_item_name(
        self, list_id: str, item_id: str, name: str, *, flush: bool = True
    ) -> Message:
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
        return item

    async def set_item_details(
        self, list_id: str, item_id: str, details: str, *, flush: bool = True
    ) -> Message:
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
        return item

    async def set_product_upc(
        self, list_id: str, item_id: str, upc: str, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        original = str(item.productUpc)
        if upc == original:
            return item
        item.productUpc = upc
        await self.operation(
            "set-list-item-product-upc",
            listId=list_id,
            listItemId=item_id,
            updatedValue=upc,
            originalValue=original,
            flush=flush,
        )
        return item

    async def set_photo(
        self, list_id: str, item_id: str, photo_id: str | None, *, flush: bool = True
    ) -> Message:
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
        return item

    async def set_quantity(
        self, list_id: str, item_id: str, quantity: Message, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        item.quantityPb.CopyFrom(quantity)
        item.deprecatedQuantity = quantity_to_deprecated_string(quantity)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.quantityPb.CopyFrom(quantity)
        partial.deprecatedQuantity = item.deprecatedQuantity
        await self.operation(
            "set-list-item-quantity-v2",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )
        return item

    async def set_quantity_override(
        self, list_id: str, item_id: str, value: bool, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        if bool(item.itemQuantityShouldOverrideIngredientQuantity) == value:
            return item
        item.itemQuantityShouldOverrideIngredientQuantity = value
        partial = PB.ListItem(
            identifier=item_id,
            listId=list_id,
            itemQuantityShouldOverrideIngredientQuantity=value,
        )
        await self.operation(
            "set-item-quantity-should-override-ingredient-quantity",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )
        return item

    async def set_price_quantity(
        self, list_id: str, item_id: str, quantity: Message, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
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
        return item

    async def set_price_quantity_override(
        self, list_id: str, item_id: str, value: bool, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        if bool(item.priceQuantityShouldOverrideItemQuantity) == value:
            return item
        item.priceQuantityShouldOverrideItemQuantity = value
        partial = PB.ListItem(
            identifier=item_id,
            listId=list_id,
            priceQuantityShouldOverrideItemQuantity=value,
        )
        await self.operation(
            "set-list-item-price-quantity-should-override-item-quantity",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )
        return item

    async def set_package_size(
        self, list_id: str, item_id: str, package_size: Message, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
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
        return item

    async def set_package_override(
        self, list_id: str, item_id: str, value: bool, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        if bool(item.itemPackageSizeShouldOverrideIngredientPackageSize) == value:
            return item
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
        return item

    async def set_price_package_size(
        self, list_id: str, item_id: str, package_size: Message, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        item.pricePackageSizePb.CopyFrom(package_size)
        partial = PB.ListItem(identifier=item_id, listId=list_id)
        partial.pricePackageSizePb.CopyFrom(package_size)
        await self.operation(
            "set-list-item-price-package-size",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )
        return item

    async def set_price_package_override(
        self, list_id: str, item_id: str, value: bool, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        if bool(item.pricePackageSizeShouldOverrideItemPackageSize) == value:
            return item
        item.pricePackageSizeShouldOverrideItemPackageSize = value
        partial = PB.ListItem(
            identifier=item_id,
            listId=list_id,
            pricePackageSizeShouldOverrideItemPackageSize=value,
        )
        await self.operation(
            "set-list-item-price-package-size-should-override-item-package-size",
            listId=list_id,
            listItemId=item_id,
            listItem=partial,
            flush=flush,
        )
        return item

    async def add_store(
        self, list_id: str, item_id: str, store_id: str, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        if store_id in item.storeIds:
            return item
        item.storeIds.append(store_id)
        await self.operation(
            "add-list-item-store-id",
            listId=list_id,
            listItemId=item_id,
            updatedValue=store_id,
            flush=flush,
        )
        return item

    async def remove_store(
        self, list_id: str, item_id: str, store_id: str, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        if store_id not in item.storeIds:
            return item
        item.storeIds.remove(store_id)
        await self.operation(
            "remove-list-item-store-id",
            listId=list_id,
            listItemId=item_id,
            updatedValue=store_id,
            flush=flush,
        )
        return item

    async def add_store_ids_to_items(
        self,
        list_id: str,
        item_ids: Sequence[str],
        store_ids: Sequence[str],
        *,
        flush: bool = True,
    ) -> None:
        if not store_ids or not item_ids:
            return
        lst = self._require(list_id)
        selected = set(item_ids)
        changed = False
        for item in lst.items:
            if item.identifier not in selected:
                continue
            for store_id in store_ids:
                if store_id not in item.storeIds:
                    item.storeIds.append(store_id)
                    changed = True
        if not changed:
            return
        partial = PB.StarterList(identifier=list_id)
        for item_id in item_ids:
            item = partial.items.add(identifier=item_id, listId=list_id)
            item.storeIds.extend(store_ids)
        await self.operation(
            "add-store-ids-to-items", listId=list_id, list=partial, flush=flush
        )

    async def remove_store_ids_from_items(
        self,
        list_id: str,
        item_ids: Sequence[str],
        store_ids: Sequence[str],
        *,
        flush: bool = True,
    ) -> None:
        if not store_ids or not item_ids:
            return
        lst = self._require(list_id)
        selected = set(item_ids)
        remove_set = set(store_ids)
        changed = False
        for item in lst.items:
            if item.identifier not in selected:
                continue
            before = list(item.storeIds)
            kept = [sid for sid in before if sid not in remove_set]
            if kept != before:
                del item.storeIds[:]
                item.storeIds.extend(kept)
                changed = True
        if not changed:
            return
        partial = PB.StarterList(identifier=list_id)
        for item_id in item_ids:
            item = partial.items.add(identifier=item_id, listId=list_id)
            item.storeIds.extend(store_ids)
        await self.operation(
            "remove-store-ids-from-items", listId=list_id, list=partial, flush=flush
        )

    async def remove_store_from_all_items(
        self, list_id: str, store_id: str, *, flush: bool = True
    ) -> None:
        lst = self._require(list_id)
        for item in lst.items:
            if store_id in item.storeIds:
                item.storeIds.remove(store_id)
        await self.operation(
            "remove-store-id-from-all-items",
            listId=list_id,
            updatedValue=store_id,
            flush=flush,
        )

    async def save_price(
        self, list_id: str, item_id: str, price: Message, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        store_id = str(price.storeId)
        replaced = False
        for index, existing in enumerate(item.prices):
            if str(existing.storeId) == store_id:
                item.prices[index].CopyFrom(price)
                replaced = True
                break
        if not replaced:
            item.prices.add().CopyFrom(price)
        await self.operation(
            "save-item-price",
            listId=list_id,
            listItemId=item_id,
            itemPrice=price,
            flush=flush,
        )
        return item

    async def remove_price(
        self, list_id: str, item_id: str, store_id: str | None, *, flush: bool = True
    ) -> Message:
        item = self._require_item(list_id, item_id)
        kept = [clone_message(p) for p in item.prices if str(p.storeId) != str(store_id or "")]
        del item.prices[:]
        for price in kept:
            item.prices.add().CopyFrom(price)
        marker = PB.PBItemPrice()
        if store_id:
            marker.storeId = store_id
        await self.operation(
            "save-item-price",
            listId=list_id,
            listItemId=item_id,
            itemPrice=marker,
            flush=flush,
        )
        return item

    async def reorder_lists(
        self, ids: Sequence[str], *, flush: bool = True
    ) -> str:
        self.state.ordered_starter_list_ids = list(ids)
        op = self.order_queue.new_operation(
            "set-ordered-list-ids", orderedListIds=list(ids)
        )
        return await self.order_queue.enqueue(op, flush=flush)

    async def flush(self):
        a = await super().flush()
        b = await self.order_queue.flush()
        return b or a

    async def restore(self) -> int:
        return await super().restore() + await self.order_queue.restore()

    async def _trim_recents_for_add(
        self, lst: Message, count: int, *, flush: bool
    ) -> None:
        if not self._is_recent(lst) or count <= 0:
            return
        excess = len(lst.items) + count - _RECENT_LIMIT
        if excess <= 0:
            return
        ids = [str(item.identifier) for item in list(lst.items)[:excess]]
        await self.bulk_remove_items(lst.identifier, ids, flush=flush)

    @staticmethod
    def _is_recent(lst: Message) -> bool:
        return int(lst.starterListType) == int(PB.StarterList.Type.RecentItemsType)

    def _require(self, list_id: str) -> Message:
        x = self.get(list_id)
        if x is None:
            raise KeyError(list_id)
        return x

    def _require_item(self, list_id: str, item_id: str) -> Message:
        lst = self._require(list_id)
        for item in lst.items:
            if item.identifier == item_id:
                return item
        raise KeyError(item_id)
