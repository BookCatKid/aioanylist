from __future__ import annotations

import pytest

from anylist_sdk.proto import PB
from anylist_sdk.services.shopping import ShoppingListsService
from anylist_sdk.state import AnyListState


def service(fake_transport):
    return ShoppingListsService(fake_transport, AnyListState(user_id="user"), user_id="user")


@pytest.mark.asyncio
async def test_ordinary_list_and_item_operations_use_official_legacy_queue(fake_transport) -> None:
    svc = service(fake_transport)

    await svc.create("Groceries", list_id="list")
    assert fake_transport.calls[-1][0] == "/data/shopping-lists/update"
    create_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert create_op.metadata.handlerId == "new-shopping-list"
    assert not create_op.metadata.HasField("operationClass")

    await svc.add_item("list", "Milk", item_id="item")
    assert fake_transport.calls[-1][0] == "/data/shopping-lists/update"
    item_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert item_op.metadata.handlerId == "add-shopping-list-item"
    assert not item_op.metadata.HasField("operationClass")


@pytest.mark.asyncio
async def test_list_local_resource_operations_use_v2_queue_with_operation_class(fake_transport) -> None:
    svc = service(fake_transport)
    store = PB.PBStore(identifier="store", listId="list", name="Market")

    await svc.save_store("list", store, is_new=True)

    assert fake_transport.calls[-1][0] == "/data/shopping-lists/update-v2"
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "new-store"
    assert (
        operation.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.StoreOperation
    )


@pytest.mark.asyncio
async def test_new_store_gets_next_sort_index_and_is_saved_optimistically(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.list_stores["list"] = {
        "a": PB.PBStore(identifier="a", listId="list", sortIndex=2),
        "b": PB.PBStore(identifier="b", listId="list", sortIndex=7),
    }
    store = PB.PBStore(identifier="c", listId="list", name="Market")

    await svc.save_store("list", store, is_new=True)

    assert svc.state.list_stores["list"]["c"].sortIndex == 8
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "new-store"
    assert op.updatedStore.sortIndex == 8


@pytest.mark.asyncio
async def test_store_delete_and_sort_update_local_state(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.list_stores["list"] = {
        "a": PB.PBStore(identifier="a", listId="list", sortIndex=9),
        "b": PB.PBStore(identifier="b", listId="list", sortIndex=9),
    }

    await svc.set_sorted_store_ids("list", ["b", "a"])
    assert svc.state.list_stores["list"]["b"].sortIndex == 0
    assert svc.state.list_stores["list"]["a"].sortIndex == 1

    await svc.delete_store("list", svc.state.list_stores["list"]["a"])
    assert set(svc.state.list_stores["list"]) == {"b"}


@pytest.mark.asyncio
async def test_new_store_filter_gets_next_sort_index_and_sorting_is_optimistic(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.list_store_filters["list"] = {
        "a": PB.PBStoreFilter(identifier="a", listId="list", sortIndex=3)
    }
    filt = PB.PBStoreFilter(identifier="b", listId="list", name="Nearby")

    await svc.save_store_filter("list", filt, is_new=True)
    assert svc.state.list_store_filters["list"]["b"].sortIndex == 4

    await svc.set_sorted_store_filter_ids("list", ["b", "a"])
    assert svc.state.list_store_filters["list"]["b"].sortIndex == 0
    assert svc.state.list_store_filters["list"]["a"].sortIndex == 1

    await svc.delete_store_filter("list", svc.state.list_store_filters["list"]["a"])
    assert set(svc.state.list_store_filters["list"]) == {"b"}


@pytest.mark.asyncio
async def test_category_rename_and_icon_update_local_index(fake_transport) -> None:
    svc = service(fake_transport)
    cat = PB.PBListCategory(
        identifier="cat", listId="list", categoryGroupId="group", name="Old", icon="old"
    )
    svc.state.list_categories["list"] = {"cat": cat}

    await svc.rename_list_category(cat, "New")
    assert svc.state.list_categories["list"]["cat"].name == "New"

    await svc.set_list_category_icon(svc.state.list_categories["list"]["cat"], "produce")
    assert svc.state.list_categories["list"]["cat"].icon == "produce"


@pytest.mark.asyncio
async def test_category_sort_sends_all_group_ids_and_appends_unspecified_categories(fake_transport) -> None:
    svc = service(fake_transport)
    group = PB.PBListCategoryGroup(identifier="group", listId="list", name="G")
    svc.state.list_category_groups["list"] = {"group": group}
    svc.state.list_categories["list"] = {
        "a": PB.PBListCategory(identifier="a", listId="list", categoryGroupId="group", sortIndex=4),
        "b": PB.PBListCategory(identifier="b", listId="list", categoryGroupId="group", sortIndex=5),
        "c": PB.PBListCategory(identifier="c", listId="list", categoryGroupId="group", sortIndex=6),
    }

    await svc.set_sorted_category_ids(group, ["c", "a"])

    assert svc.state.list_categories["list"]["c"].sortIndex == 0
    assert svc.state.list_categories["list"]["a"].sortIndex == 1
    assert svc.state.list_categories["list"]["b"].sortIndex == 2
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "set-sorted-category-ids"
    assert [x.identifier for x in op.updatedCategoryGroup.categories] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_removing_categories_prunes_rules_and_queues_both_official_operations(fake_transport) -> None:
    svc = service(fake_transport)
    group = PB.PBListCategoryGroup(identifier="group", listId="list")
    remove = PB.PBListCategory(identifier="cat", listId="list", categoryGroupId="group")
    keep = PB.PBListCategory(identifier="keep", listId="list", categoryGroupId="group")
    svc.state.list_categories["list"] = {"cat": remove, "keep": keep}
    svc.state.list_categorization_rules["list"] = {
        "drop": PB.PBListCategorizationRule(
            identifier="drop", listId="list", categoryGroupId="group", categoryId="cat"
        ),
        "keep": PB.PBListCategorizationRule(
            identifier="keep", listId="list", categoryGroupId="group", categoryId="keep"
        ),
    }

    await svc.remove_category_ids(group, [remove])

    assert set(svc.state.list_categories["list"]) == {"keep"}
    assert set(svc.state.list_categorization_rules["list"]) == {"keep"}
    # Both operations are flushed together by the second enqueue.
    batch = fake_transport.calls[-1][1]["operations"].operations
    assert [op.metadata.handlerId for op in batch] == [
        "remove-category-ids",
        "remove-categorization-rules-for-category-ids",
    ]


@pytest.mark.asyncio
async def test_bulk_categorization_rules_update_local_state_and_bucket_at_25(fake_transport) -> None:
    svc = service(fake_transport)
    rules = [
        PB.PBListCategorizationRule(
            identifier=f"r{i}", listId="list", categoryGroupId="group", categoryId="cat"
        )
        for i in range(27)
    ]

    await svc.bulk_save_categorization_rules("list", rules)

    assert len(svc.state.list_categorization_rules["list"]) == 27
    batches = [call[1]["operations"].operations for call in fake_transport.calls]
    # Queue flushing after the second enqueue sends both queued operations in one request.
    assert sum(len(batch) for batch in batches) >= 2

@pytest.mark.asyncio
async def test_final_category_group_is_not_deleted(fake_transport) -> None:
    svc = service(fake_transport)
    group = PB.PBListCategoryGroup(identifier="only", listId="list", name="Only")
    svc.state.list_category_groups["list"] = {"only": group}

    result = await svc.delete_category_group(group)

    assert result is None
    assert set(svc.state.list_category_groups["list"]) == {"only"}
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_delete_category_group_migrates_filters_to_official_default_group(fake_transport) -> None:
    svc = service(fake_transport)
    # The deterministic group ID is preferred by AnyList's Q.G fallback selector.
    from anylist_sdk.identifiers import uuid5_hex
    from uuid import UUID
    default_id = uuid5_hex("list", UUID(hex="f656a81f0e0a419aa45121f4f2eac51b"))
    doomed = PB.PBListCategoryGroup(identifier="doomed", listId="list", name="Old")
    fallback = PB.PBListCategoryGroup(identifier=default_id, listId="list", name="Default")
    svc.state.list_category_groups["list"] = {"doomed": doomed, default_id: fallback}
    svc.state.list_categories["list"] = {
        "gone": PB.PBListCategory(identifier="gone", listId="list", categoryGroupId="doomed")
    }
    svc.state.list_store_filters["list"] = {
        "filter": PB.PBStoreFilter(
            identifier="filter", listId="list", listCategoryGroupId="doomed"
        )
    }
    seen = []

    async def removed(list_id, group_id, flush):
        seen.append((list_id, group_id, flush))

    svc.on_category_group_removed = removed
    await svc.delete_category_group(doomed)

    assert set(svc.state.list_category_groups["list"]) == {default_id}
    assert svc.state.list_categories["list"] == {}
    assert svc.state.list_store_filters["list"]["filter"].listCategoryGroupId == default_id
    assert seen == [("list", "doomed", True)]
    # update-store-filter is queued before delete-category-group and flushed with the delete.
    handlers = [op.metadata.handlerId for op in fake_transport.calls[-1][1]["operations"].operations]
    assert handlers == ["update-store-filter", "delete-category-group"]

@pytest.mark.asyncio
async def test_add_item_honors_manual_top_position_and_sends_partial_list(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="old", listId="list", name="Old")],
        newListItemPosition=PB.ShoppingList.NewListItemPosition.Top,
    )

    created = await svc.add_item("list", "New", item_id="new")

    assert created.identifier == "new"
    assert [x.identifier for x in svc.state.shopping_lists["list"].items] == ["new", "old"]
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "add-shopping-list-item"
    assert op.list.newListItemPosition == PB.ShoppingList.NewListItemPosition.Top


@pytest.mark.asyncio
async def test_add_item_ignores_top_position_while_alphabetically_sorted(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="old", listId="list", name="Old")],
        newListItemPosition=PB.ShoppingList.NewListItemPosition.Top,
    )
    svc.state.list_settings["list"] = PB.PBListSettings(
        identifier="settings",
        listId="list",
        listItemSortOrder="ALListItemSortOrderAlphabetical",
    )

    await svc.add_item("list", "New", item_id="new")

    assert [x.identifier for x in svc.state.shopping_lists["list"].items] == ["old", "new"]
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert not op.HasField("list")


@pytest.mark.asyncio
async def test_revive_matching_autocomplete_item_uncrosses_and_applies_filter_context(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="item", listId="list", name="Milk", checked=True)],
    )
    source = PB.ListItem(identifier="suggestion-copy", listId="list", name="Milk", checked=True)
    store_filter = PB.PBStoreFilter(
        identifier="filter", listId="list", showsAllItems=False, storeIds=["market"]
    )

    revived = await svc.revive_matching_item(
        "list", source, store_filter=store_filter, flush=False
    )

    assert revived is svc.item("list", "item")
    assert revived.checked is False
    assert list(revived.storeIds) == ["market"]
    assert [op.metadata.handlerId for op in svc.legacy_queue._pending] == [
        "set-list-item-checked",
        "add-store-ids-to-items",
    ]


@pytest.mark.asyncio
async def test_revive_matching_item_requires_full_item_equality(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="item", listId="list", name="Milk", details="2%")],
    )

    result = await svc.revive_matching_item(
        "list", PB.ListItem(identifier="source", name="Milk", details="Whole")
    )

    assert result is None
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_bulk_add_at_top_preserves_visible_order_and_reverses_wire_items(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="old", listId="list", name="Old")],
        newListItemPosition=PB.ShoppingList.NewListItemPosition.Top,
    )
    incoming = [
        PB.ListItem(identifier="a", name="A"),
        PB.ListItem(identifier="b", name="B"),
        PB.ListItem(identifier="c", name="C"),
    ]

    await svc.add_items("list", incoming)

    assert [x.identifier for x in svc.state.shopping_lists["list"].items] == ["a", "b", "c", "old"]
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.list.newListItemPosition == PB.ShoppingList.NewListItemPosition.Top
    assert [x.identifier for x in op.list.items] == ["c", "b", "a"]


@pytest.mark.asyncio
async def test_set_password_omits_original_value_like_web_client(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(identifier="list", password="old")

    await svc.set_password("list", "new")

    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.updatedValue == "new"
    assert not op.HasField("originalValue")


@pytest.mark.asyncio
async def test_notification_location_dedupes_by_coordinates_without_queueing(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        notificationLocations=[PB.PBNotificationLocation(
            identifier="one", name="Market", address="A", latitude=32.1, longitude=-117.2
        )],
    )

    result = await svc.add_notification_location(
        "list", name="Different", address="B", latitude=32.1, longitude=-117.2
    )

    assert result is None
    assert len(svc.state.shopping_lists["list"].notificationLocations) == 1
    assert fake_transport.calls == []

@pytest.mark.asyncio
async def test_quantity_update_keeps_legacy_quantity_and_skips_identical_updates(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list", items=[PB.ListItem(identifier="item", listId="list", name="Flour")]
    )
    quantity = PB.PBItemQuantity(amount="1 1/2", unit="pounds")

    await svc.set_quantity("list", "item", quantity)

    item = svc.item("list", "item")
    assert item.deprecatedQuantity == "1½ lb"
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.listItem.quantityPb == quantity
    assert op.listItem.deprecatedQuantity == "1½ lb"
    calls = len(fake_transport.calls)
    await svc.set_quantity("list", "item", quantity)
    assert len(fake_transport.calls) == calls


@pytest.mark.asyncio
async def test_redundant_store_and_override_mutations_do_not_queue(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(
            identifier="item", listId="list", name="Milk", storeIds=["store"],
            itemQuantityShouldOverrideIngredientQuantity=True,
        )],
    )

    await svc.add_store("list", "item", "store")
    await svc.remove_store("list", "item", "missing")
    await svc.set_quantity_override("list", "item", True)

    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_category_assignment_uses_deterministic_group_assignment_and_full_item(fake_transport) -> None:
    from anylist_sdk.identifiers import uuid5_hex
    from uuid import UUID

    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="item", listId="list", name="Milk", details="keep")],
    )
    assignment = PB.PBListItemCategoryAssignment(categoryGroupId="group", categoryId="category")

    await svc.assign_category("list", "item", assignment)

    expected = uuid5_hex("group", UUID(hex="08e5c5bdcd694454a1ffd611b6d9abc0"))
    item = svc.item("list", "item")
    assert item.categoryAssignments[0].identifier == expected
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.listItem.details == "keep"
    assert op.listItem.categoryAssignments[0].identifier == expected


@pytest.mark.asyncio
async def test_category_match_id_uses_full_item_and_official_post_mutation_original_value(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="item", listId="list", name="Milk", categoryMatchId="old")],
    )

    await svc.set_category_match_id("list", "item", "produce")

    item = svc.item("list", "item")
    assert item.category == "produce"
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.listItem.categoryMatchId == "produce"
    assert op.listItem.category == "produce"
    assert op.originalValue == "produce"
    assert not op.HasField("updatedValue")

    await svc.set_category_match_id("list", "item", "custom-category")
    assert svc.item("list", "item").category == "other"


@pytest.mark.asyncio
async def test_save_price_updates_and_removes_optimistic_price_state(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list", items=[PB.ListItem(identifier="item", listId="list", name="Milk")]
    )

    await svc.save_price("list", "item", PB.PBItemPrice(amount=3.5, details="sale", storeId="store"))
    item = svc.item("list", "item")
    assert len(item.prices) == 1 and item.prices[0].amount == 3.5

    await svc.save_price("list", "item", PB.PBItemPrice(storeId="store"))
    assert len(item.prices) == 0
    calls = len(fake_transport.calls)
    await svc.save_price("list", "item", PB.PBItemPrice(storeId="missing"))
    assert len(fake_transport.calls) == calls


@pytest.mark.asyncio
async def test_price_matchup_tag_matches_web_clients_post_mutation_original_value(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="item", listId="list", name="Milk", priceMatchupTag="old")],
    )

    await svc.set_price_matchup_tag("list", "item", "new")

    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.updatedValue == "new"
    assert op.originalValue == "new"

@pytest.mark.asyncio
async def test_bulk_cross_wire_includes_all_requested_ids_when_only_some_change(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[
            PB.ListItem(identifier="already", listId="list", checked=True),
            PB.ListItem(identifier="change", listId="list", checked=False),
        ],
    )

    await svc.bulk_set_checked("list", ["already", "change"], True)

    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert [x.identifier for x in op.list.items] == ["already", "change"]
    assert all(x.checked for x in op.list.items)


@pytest.mark.asyncio
async def test_checked_and_removed_items_emit_recent_item_callback(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[
            PB.ListItem(identifier="a", listId="list", name="A"),
            PB.ListItem(identifier="b", listId="list", name="B"),
        ],
    )
    seen = []

    async def recent(list_id, items, skip_existing, flush):
        seen.append((list_id, [x.identifier for x in items], skip_existing, flush))

    svc.on_items_became_recent = recent
    await svc.set_checked("list", "a", True, flush=False)
    await svc.remove_item("list", "b", flush=False)

    assert seen == [
        ("list", ["a"], False, False),
        ("list", ["b"], False, False),
    ]

@pytest.mark.asyncio
async def test_clear_promotes_with_skip_existing_then_removes_without_duplicate_callback(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[
            PB.ListItem(identifier="a", listId="list", name="A"),
            PB.ListItem(identifier="b", listId="list", name="B", checked=True),
        ],
    )
    seen = []

    async def recent(list_id, items, skip_existing, flush):
        seen.append((list_id, [x.identifier for x in items], skip_existing, flush))

    svc.on_items_became_recent = recent
    removed = await svc.clear("list", flush=False)

    assert [x.identifier for x in removed] == ["a", "b"]
    assert list(svc.state.shopping_lists["list"].items) == []
    assert seen == [("list", ["a", "b"], True, False)]
    assert svc.legacy_queue._pending[-1].metadata.handlerId == "bulk-remove-list-items"


@pytest.mark.asyncio
async def test_remove_checked_only_promotes_and_removes_crossed_items(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[
            PB.ListItem(identifier="a", listId="list", checked=False),
            PB.ListItem(identifier="b", listId="list", checked=True),
            PB.ListItem(identifier="c", listId="list", checked=True),
        ],
    )
    seen = []

    async def recent(list_id, items, skip_existing, flush):
        seen.append(([x.identifier for x in items], skip_existing, flush))

    svc.on_items_became_recent = recent
    removed = await svc.remove_checked("list", flush=False)

    assert [x.identifier for x in removed] == ["b", "c"]
    assert [x.identifier for x in svc.state.shopping_lists["list"].items] == ["a"]
    assert seen == [(["b", "c"], True, False)]
