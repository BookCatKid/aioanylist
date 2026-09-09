from __future__ import annotations

import pytest

from anylist_sdk.proto import PB
from anylist_sdk.services.starter import (
    StarterListsService,
    favorite_list_id,
    recent_list_id,
)
from anylist_sdk.state import AnyListState


class DummyTransport:
    pass


def make_service() -> tuple[StarterListsService, AnyListState]:
    state = AnyListState(user_id="user1")
    return StarterListsService(DummyTransport(), state, user_id="user1"), state


@pytest.mark.asyncio
async def test_deterministic_favorite_and_recent_lists_are_created_in_correct_indexes():
    service, state = make_service()

    fav = await service.ensure_favorites("shopping1", flush=False)
    rec = await service.ensure_recents("shopping1", flush=False)

    assert fav.identifier == favorite_list_id("shopping1")
    assert fav.identifier in state.favorite_item_lists
    assert fav.listId == "shopping1"
    assert fav.starterListType == PB.StarterList.Type.FavoriteItemsType
    assert rec.identifier == recent_list_id("shopping1")
    assert rec.identifier in state.recent_item_lists
    assert rec.listId == "shopping1"
    assert rec.starterListType == PB.StarterList.Type.RecentItemsType
    assert [op.metadata.handlerId for op in service.queue._pending] == [
        "new-starter-list",
        "new-starter-list",
    ]
    assert service.queue._pending[0].listId == fav.identifier
    assert service.queue._pending[0].list.identifier == fav.identifier


@pytest.mark.asyncio
async def test_recent_list_caps_at_200_and_removes_oldest_before_add():
    service, state = make_service()
    rec = PB.StarterList(
        identifier=recent_list_id("shopping1"),
        listId="shopping1",
        starterListType=PB.StarterList.Type.RecentItemsType,
    )
    for i in range(200):
        rec.items.add(identifier=f"old-{i}", name=f"Old {i}")
    state.recent_item_lists[rec.identifier] = rec

    added = await service.add_item(
        rec.identifier, PB.ListItem(identifier="new", name="New"), flush=False
    )

    assert len(rec.items) == 200
    assert rec.items[0].identifier == "old-1"
    assert rec.items[-1].identifier == "new"
    assert added.listId == rec.identifier
    assert [op.metadata.handlerId for op in service.queue._pending] == [
        "bulk-remove-list-items",
        "add-item",
    ]
    removal = service.queue._pending[0].list
    assert [item.identifier for item in removal.items] == ["old-0"]


@pytest.mark.asyncio
async def test_bulk_recent_add_keeps_last_200_and_buckets_operations_by_25():
    service, state = make_service()
    rec = PB.StarterList(
        identifier=recent_list_id("shopping1"),
        listId="shopping1",
        starterListType=PB.StarterList.Type.RecentItemsType,
    )
    state.recent_item_lists[rec.identifier] = rec
    incoming = [PB.ListItem(identifier=f"i-{i}", name=str(i)) for i in range(205)]

    added = await service.bulk_add_items(rec.identifier, incoming, flush=False)

    assert len(added) == 200
    assert len(rec.items) == 200
    assert rec.items[0].identifier == "i-5"
    assert rec.items[-1].identifier == "i-204"
    assert len(service.queue._pending) == 8
    assert all(op.metadata.handlerId == "bulk-add-list-items" for op in service.queue._pending)
    assert all(len(op.list.items) == 25 for op in service.queue._pending)


@pytest.mark.asyncio
async def test_starter_quantity_operation_uses_partial_item_and_legacy_string():
    service, state = make_service()
    lst = PB.StarterList(identifier="starter1", name="Starter")
    lst.items.add(identifier="item1", name="Milk")
    state.starter_lists[lst.identifier] = lst
    quantity = PB.PBItemQuantity(amount="2", unit="cup")

    item = await service.set_quantity("starter1", "item1", quantity, flush=False)

    assert item.quantityPb.amount == "2"
    assert item.deprecatedQuantity == ""
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "set-list-item-quantity-v2"
    assert op.listItem.identifier == "item1"
    assert op.listItem.listId == "starter1"
    assert op.listItem.quantityPb.amount == "2"
    assert op.listItem.deprecatedQuantity == item.deprecatedQuantity


@pytest.mark.asyncio
async def test_starter_item_edit_operations_are_optimistic_and_use_official_fields():
    service, state = make_service()
    lst = PB.StarterList(identifier="starter1", name="Starter")
    lst.items.add(identifier="item1", name="Milk", details="old", productUpc="111")
    state.starter_lists[lst.identifier] = lst

    await service.set_item_name("starter1", "item1", "Whole Milk", flush=False)
    await service.set_item_details("starter1", "item1", "2%", flush=False)
    await service.set_product_upc("starter1", "item1", "222", flush=False)
    await service.set_photo("starter1", "item1", "photo", flush=False)
    await service.add_store("starter1", "item1", "store1", flush=False)
    await service.remove_store("starter1", "item1", "store1", flush=False)

    item = lst.items[0]
    assert item.name == "Whole Milk"
    assert item.details == "2%"
    assert item.productUpc == "222"
    assert list(item.photoIds) == ["photo"]
    assert not item.storeIds
    assert [op.metadata.handlerId for op in service.queue._pending] == [
        "set-list-item-name",
        "set-list-item-details",
        "set-list-item-product-upc",
        "set-list-item-photo-id",
        "add-list-item-store-id",
        "remove-list-item-store-id",
    ]
    assert service.queue._pending[0].originalValue == "Milk"
    assert service.queue._pending[0].updatedValue == "Whole Milk"


@pytest.mark.asyncio
async def test_bulk_store_mutations_send_partial_items_and_mutate_local_state():
    service, state = make_service()
    lst = PB.StarterList(identifier="starter1")
    lst.items.add(identifier="a", storeIds=["x"])
    lst.items.add(identifier="b")
    state.starter_lists[lst.identifier] = lst

    await service.add_store_ids_to_items(
        "starter1", ["a", "b"], ["y", "z"], flush=False
    )
    assert list(lst.items[0].storeIds) == ["x", "y", "z"]
    assert list(lst.items[1].storeIds) == ["y", "z"]
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "add-store-ids-to-items"
    assert [x.identifier for x in op.list.items] == ["a", "b"]
    assert list(op.list.items[0].storeIds) == ["y", "z"]

    await service.remove_store_ids_from_items(
        "starter1", ["a", "b"], ["z"], flush=False
    )
    assert list(lst.items[0].storeIds) == ["x", "y"]
    assert list(lst.items[1].storeIds) == ["y"]
    assert service.queue._pending[-1].metadata.handlerId == "remove-store-ids-from-items"


@pytest.mark.asyncio
async def test_starter_price_save_and_remove_mutate_local_price_array():
    service, state = make_service()
    lst = PB.StarterList(identifier="starter1")
    lst.items.add(identifier="a")
    state.starter_lists[lst.identifier] = lst

    price = PB.PBItemPrice(storeId="store1", amount=3.5)
    await service.save_price("starter1", "a", price, flush=False)
    assert len(lst.items[0].prices) == 1
    assert lst.items[0].prices[0].amount == 3.5
    assert service.queue._pending[-1].itemPrice.storeId == "store1"

    await service.remove_price("starter1", "a", "store1", flush=False)
    assert not lst.items[0].prices
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "save-item-price"
    assert op.itemPrice.storeId == "store1"
    assert op.itemPrice.amount == 0
