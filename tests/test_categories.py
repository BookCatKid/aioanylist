from __future__ import annotations

import hashlib

import pytest

from anylist_sdk.proto import PB
from anylist_sdk.services.categories import CategorizedItemsService, UserCategoriesService
from anylist_sdk.state import AnyListState


class DummyTransport:
    pass


@pytest.fixture
def state():
    return AnyListState(user_id="user1")


@pytest.mark.asyncio
async def test_user_grouping_mutations_are_optimistic(state):
    service = UserCategoriesService(DummyTransport(), state, user_id="user1")
    service.queue.flush = lambda: None  # not used with flush=False

    grouping = await service.add_grouping("My Group", ["a", "b"], flush=False)
    assert grouping.identifier in state.category_groupings
    assert list(grouping.categoryIds) == ["a", "b"]
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "add-grouping"
    assert list(op.grouping.categoryIds) == ["a", "b"]

    await service.set_grouping_categories(grouping.identifier, ["b", "a"], ordering_only=True, flush=False)
    assert list(state.category_groupings[grouping.identifier].categoryIds) == ["b", "a"]
    assert service.queue._pending[-1].metadata.handlerId == "set-grouping-category-order"

    await service.rename_grouping(grouping.identifier, "Renamed", flush=False)
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "set-grouping-name"
    assert op.grouping.name == "Renamed"
    assert not op.grouping.categoryIds

    await service.remove_grouping(grouping.identifier, flush=False)
    assert grouping.identifier not in state.category_groupings
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "remove-grouping"
    assert not op.grouping.categoryIds


@pytest.mark.asyncio
async def test_categorized_item_uses_official_md5_memory_key(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    item = PB.ListItem(identifier="shopping-id", listId="list1", name="Milk", categoryMatchId="dairy", category="other")

    await service.categorize(item, flush=False)

    expected = hashlib.md5(b"milk-list1-user1").hexdigest()
    assert expected in state.categorized_items
    assert "shopping-id" not in state.categorized_items
    remembered = state.categorized_items[expected]
    assert remembered.identifier == expected
    assert remembered.name == "milk"
    assert remembered.userId == "user1"
    assert remembered.listId == "list1"
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "categorize-item"
    assert op.listItem.identifier == expected


@pytest.mark.asyncio
async def test_categorized_item_lookup_falls_back_to_global(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    global_item = PB.ListItem(
        identifier=service.memory_id("Milk", ""), listId="", userId="user1", name="milk", categoryMatchId="dairy"
    )
    state.categorized_items[global_item.identifier] = global_item
    assert service.lookup("MILK", "another-list") is global_item


@pytest.mark.asyncio
async def test_global_categorization_replaces_list_specific_memory(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    local_id = service.memory_id("Milk", "list1")
    state.categorized_items[local_id] = PB.ListItem(
        identifier=local_id, listId="list1", userId="user1", name="milk", categoryMatchId="dairy", category="other"
    )
    item = PB.ListItem(identifier="shopping-id", listId="list1", name="Milk", categoryMatchId="beverages", category="other")

    await service.categorize(item, global_scope=True, flush=False)

    global_id = service.memory_id("milk", "")
    assert global_id in state.categorized_items
    assert local_id not in state.categorized_items
    assert [op.metadata.handlerId for op in service.queue._pending[-2:]] == [
        "categorize-item",
        "remove-categorized-item",
    ]
    removal = service.queue._pending[-1].listItem
    assert removal.identifier == local_id
    assert removal.listId == "list1"


@pytest.mark.asyncio
async def test_remove_categorized_item_uses_memory_key_not_shopping_id(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    item = PB.ListItem(identifier="shopping-id", listId="list1", name="Milk", categoryMatchId="dairy", category="other")
    key = service.memory_id("Milk", "list1")
    state.categorized_items[key] = PB.ListItem(
        identifier=key, listId="list1", userId="user1", name="milk", categoryMatchId="dairy", category="other"
    )

    await service.remove(item, flush=False)
    assert key not in state.categorized_items
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "remove-categorized-item"
    assert op.listItem.identifier == key
    assert op.listItem.name == "milk"
