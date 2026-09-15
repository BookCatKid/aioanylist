from __future__ import annotations

import hashlib

import pytest

from aioanylist.proto import PB
from aioanylist.services.categories import CategorizedItemsService, UserCategoriesService
from aioanylist.state import AnyListState


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

    await service.set_grouping_categories(
        grouping.identifier, ["b", "a"], ordering_only=True, flush=False
    )
    assert list(state.category_groupings[grouping.identifier].categoryIds) == ["b", "a"]
    assert service.queue._pending[-1].metadata.handlerId == "set-grouping-category-order"

    await service.set_grouping_categories(grouping.identifier, ["a", "b"], flush=False)
    assert list(state.category_groupings[grouping.identifier].categoryIds) == ["a", "b"]
    assert service.queue._pending[-1].metadata.handlerId == "set-grouping-categories"

    await service.rename_grouping(grouping.identifier, "Renamed", flush=False)
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "set-grouping-name"
    assert op.grouping.name == "Renamed"
    assert not op.grouping.categoryIds

    await service.hide_grouping_from_browse(grouping.identifier, flush=False)
    op = service.queue._pending[-1]
    assert (
        op.metadata.handlerId
        == "set-should-hide-category-group-from-browse-list-category-groups-screen"
    )
    assert op.grouping.shouldHideFromBrowseListCategoryGroupsScreen is True
    assert not op.grouping.categoryIds

    await service.remove_grouping(grouping.identifier, flush=False)
    assert grouping.identifier not in state.category_groupings
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "remove-grouping"
    assert not op.grouping.categoryIds


@pytest.mark.asyncio
async def test_categorized_item_uses_official_md5_memory_key(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    item = PB.ListItem(
        identifier="shopping-id",
        listId="list1",
        name="Milk",
        categoryMatchId="dairy",
        category="other",
    )

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
        identifier=service.memory_id("Milk", ""),
        listId="",
        userId="user1",
        name="milk",
        categoryMatchId="dairy",
    )
    state.categorized_items[global_item.identifier] = global_item
    assert service.lookup("MILK", "another-list") is global_item


@pytest.mark.asyncio
async def test_global_categorization_replaces_list_specific_memory(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    local_id = service.memory_id("Milk", "list1")
    state.categorized_items[local_id] = PB.ListItem(
        identifier=local_id,
        listId="list1",
        userId="user1",
        name="milk",
        categoryMatchId="dairy",
        category="other",
    )
    item = PB.ListItem(
        identifier="shopping-id",
        listId="list1",
        name="Milk",
        categoryMatchId="beverages",
        category="other",
    )

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
    assert removal.category == "dairy"


@pytest.mark.asyncio
async def test_remove_categorized_item_uses_memory_key_not_shopping_id(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    item = PB.ListItem(
        identifier="shopping-id",
        listId="list1",
        name="Milk",
        categoryMatchId="dairy",
        category="other",
    )
    key = service.memory_id("Milk", "list1")
    state.categorized_items[key] = PB.ListItem(
        identifier=key,
        listId="list1",
        userId="user1",
        name="milk",
        categoryMatchId="dairy",
        category="other",
    )

    await service.remove(item, flush=False)
    assert key not in state.categorized_items
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "remove-categorized-item"
    assert op.listItem.identifier == key
    assert op.listItem.name == "milk"
    assert op.listItem.category == "dairy"


def test_categorized_sync_lowercases_server_names_before_indexing(state):
    response = PB.PBCategorizedItemsList()
    response.timestamp.identifier = "all"
    response.timestamp.timestamp = 4
    response.categorizedItems.add(identifier="memory", listId="list1", userId="user1", name="MiLK")

    state.apply_categorized_items(response)

    assert state.categorized_items["memory"].name == "milk"


@pytest.mark.asyncio
async def test_user_categories_refresh_returns_before_http_while_edit_queue_pending(
    fake_transport,
) -> None:
    state = AnyListState(user_id="user")
    service = UserCategoriesService(fake_transport, state, user_id="user")
    await service.queue.enqueue(service.queue.new_operation("add-category"), flush=False)

    result = await service.refresh()

    assert result is None
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_categorized_items_refresh_returns_before_http_while_edit_queue_pending(
    fake_transport,
) -> None:
    state = AnyListState(user_id="user")
    service = CategorizedItemsService(fake_transport, state, user_id="user")
    await service.queue.enqueue(service.queue.new_operation("categorize-item"), flush=False)

    result = await service.refresh()

    assert result is None
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_existing_memory_category_change_only_updates_match_id(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    key = service.memory_id("Milk", "list1")
    state.categorized_items[key] = PB.ListItem(
        identifier=key,
        listId="list1",
        userId="user1",
        name="milk",
        categoryMatchId="dairy",
        category="dairy",
    )

    await service.categorize(
        PB.ListItem(
            identifier="shopping",
            listId="list1",
            name="Milk",
            categoryMatchId="beverages",
            category="beverages",
        ),
        flush=False,
    )

    remembered = state.categorized_items[key]
    assert remembered.categoryMatchId == "beverages"
    # Official AA only calls setCategoryMatchId on an existing memory object.
    assert remembered.category == "dairy"


@pytest.mark.asyncio
async def test_custom_category_removal_uses_other_legacy_category_field(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    item = PB.ListItem(
        identifier="shopping",
        listId="list1",
        name="Thing",
        categoryMatchId="my-custom-category",
        category="my-custom-category",
    )

    await service.remove(item, flush=False)

    removal = service.queue._pending[-1].listItem
    assert removal.categoryMatchId == "my-custom-category"
    assert removal.category == "other"


@pytest.mark.asyncio
async def test_category_memory_migration_uses_exact_partial_payload_and_21_op_flush_cycle(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    for i in range(22):
        key = service.memory_id(f"Item {i}", "list1")
        state.categorized_items[key] = PB.ListItem(
            identifier=key,
            userId="user1",
            listId="list1",
            name=f"item {i}",
            categoryMatchId="old-custom",
            category="other",
            details="must-not-be-sent",
        )
    other_key = service.memory_id("Other", "list1")
    state.categorized_items[other_key] = PB.ListItem(
        identifier=other_key,
        userId="user1",
        listId="list1",
        name="other",
        categoryMatchId="dairy",
    )

    flush_sizes: list[int] = []

    async def fake_flush():
        flush_sizes.append(len(service.queue._pending))
        service.queue._pending.clear()

    service.queue.flush = fake_flush

    changed = await service.migrate_category("old-custom", "produce")

    assert changed == 22
    assert flush_sizes == [21, 1]
    assert not service.queue.paused
    assert service.queue._pending == []
    migrated = [
        value for value in state.categorized_items.values() if value.identifier != other_key
    ]
    assert all(value.categoryMatchId == "produce" for value in migrated)
    assert state.categorized_items[other_key].categoryMatchId == "dairy"


@pytest.mark.asyncio
async def test_category_memory_migration_can_remain_queued_for_caller_batching(state):
    service = CategorizedItemsService(DummyTransport(), state, user_id="user1")
    key = service.memory_id("One", "")
    state.categorized_items[key] = PB.ListItem(
        identifier=key, userId="user1", name="one", categoryMatchId="dairy"
    )

    changed = await service.migrate_category("dairy", "custom", flush=False)

    assert changed == 1
    assert not service.queue.paused
    assert len(service.queue._pending) == 1
    op = service.queue._pending[0]
    assert op.metadata.handlerId == "categorize-item"
    sent = op.listItem
    assert sent.identifier == key
    assert sent.userId == "user1"
    assert sent.name == "one"
    assert sent.categoryMatchId == "custom"
    assert sent.category == "other"
    assert not sent.HasField("details")
