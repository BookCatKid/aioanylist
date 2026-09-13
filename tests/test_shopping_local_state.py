from __future__ import annotations

import pytest

from anylist_sdk.proto import PB
from anylist_sdk.services.shopping import ShoppingListsService, category_rule_identifier
from anylist_sdk.state import AnyListState


def service(fake_transport):
    return ShoppingListsService(
        fake_transport, AnyListState(user_id="user"), user_id="user", user_email="user@example.com"
    )


@pytest.mark.asyncio
async def test_shopping_refresh_defers_until_legacy_queue_drains(fake_transport) -> None:
    svc = service(fake_transport)
    svc.legacy_queue.pause()
    await svc.legacy_queue.enqueue(svc.legacy_queue.new_operation("rename-list"), flush=False)

    result = await svc.refresh()

    assert result is None
    assert fake_transport.calls == []
    await svc.legacy_queue.resume()
    assert [call[0] for call in fake_transport.calls] == [
        "/data/shopping-lists/update",
        "/data/shopping-lists/all",
    ]


@pytest.mark.asyncio
async def test_shopping_refresh_defers_until_v2_queue_drains(fake_transport) -> None:
    svc = service(fake_transport)
    svc.queue.pause()
    operation = svc.queue.new_operation(
        "set-store-name",
        operation_class=PB.PBOperationMetadata.OperationClass.StoreOperation,
    )
    await svc.queue.enqueue(operation, flush=False)

    result = await svc.refresh()

    assert result is None
    assert fake_transport.calls == []
    await svc.queue.resume()
    assert [call[0] for call in fake_transport.calls] == [
        "/data/shopping-lists/update-v2",
        "/data/shopping-lists/all",
    ]


@pytest.mark.asyncio
async def test_ordinary_list_and_item_operations_use_official_legacy_queue(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.root_folder_id = "root"
    svc.state.list_folders["root"] = PB.PBListFolder(identifier="root")

    await svc.create("Groceries", list_id="list")
    assert fake_transport.calls[-1][0] == "/data/shopping-lists/update"
    create_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert create_op.metadata.handlerId == "new-shopping-list"
    assert not create_op.metadata.HasField("operationClass")
    assert create_op.listFolderId == "root"
    assert create_op.updatedCategoryGroup.identifier
    assert create_op.updatedCategoryGroup.listId == "list"
    assert len(create_op.updatedCategoryGroup.categories) == 20
    assert create_op.updatedCategoryGroup.defaultCategoryId
    assert create_op.list.listItemSortOrder == PB.ShoppingList.ListItemSortOrder.Alphabetical
    assert create_op.list.creator == "user"
    assert len(create_op.list.sharedUsers) == 1
    assert create_op.list.sharedUsers[0].userId == "user"
    assert create_op.list.sharedUsers[0].email == "user@example.com"
    assert svc.state.list_folders["root"].items[0].identifier == "list"

    await svc.add_item("list", "Milk", item_id="item")
    assert fake_transport.calls[-1][0] == "/data/shopping-lists/update"
    item_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert item_op.metadata.handlerId == "add-shopping-list-item"
    assert not item_op.metadata.HasField("operationClass")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("list_type", "expected_categories", "expected_sort"),
    [
        (0, 20, PB.ShoppingList.ListItemSortOrder.Alphabetical),
        (1, 1, PB.ShoppingList.ListItemSortOrder.Manual),
        (2, 1, PB.ShoppingList.ListItemSortOrder.Manual),
    ],
)
async def test_new_list_type_controls_initial_categories_and_sort_order(
    fake_transport, list_type: int, expected_categories: int, expected_sort: int
) -> None:
    svc = service(fake_transport)
    svc.state.root_folder_id = "root"
    svc.state.list_folders["root"] = PB.PBListFolder(identifier="root")

    await svc.create("List", list_id="list", list_type=list_type)

    operation = fake_transport.calls[0][1]["operations"].operations[0]
    assert len(operation.updatedCategoryGroup.categories) == expected_categories
    assert operation.list.listItemSortOrder == expected_sort
    if list_type != 0:
        assert [x.systemCategory for x in operation.updatedCategoryGroup.categories] == ["other"]
        assert operation.updatedCategoryGroup.defaultCategoryId


@pytest.mark.asyncio
async def test_create_requires_account_email_for_official_owner_pair(fake_transport) -> None:
    state = AnyListState(user_id="user", root_folder_id="root")
    state.list_folders["root"] = PB.PBListFolder(identifier="root")
    svc = ShoppingListsService(fake_transport, state, user_id="user")

    with pytest.raises(RuntimeError, match="account email"):
        await svc.create("Groceries", list_id="list")

    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_create_uses_official_german_category_strings(fake_transport) -> None:
    class Response:
        status = 200

        async def text(self):
            import json

            return json.dumps({"Other": "Sonstiges", "Produce": "Obst & Gemüse"})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Session:
        def get(self, url):
            assert url.endswith("/static/webapp/strings/de/translation.json?v=1")
            return Response()

    fake_transport.base_url = "https://www.anylist.com"
    fake_transport.session = Session()
    state = AnyListState(user_id="user", root_folder_id="root")
    state.list_folders["root"] = PB.PBListFolder(identifier="root")
    svc = ShoppingListsService(
        fake_transport, state, user_id="user", user_email="user@example.com", user_locale="de-DE"
    )

    await svc.create("Einkauf", list_id="list")

    op = fake_transport.calls[-1][1]["operations"].operations[0]
    by_system = {c.systemCategory: c.name for c in op.updatedCategoryGroup.categories}
    assert by_system["other"] == "Sonstiges"
    assert by_system["produce"] == "Obst & Gemüse"
    assert by_system["dairy"] == "Dairy"  # missing translation keys fall back to English


@pytest.mark.asyncio
async def test_list_local_resource_operations_use_v2_queue_with_operation_class(
    fake_transport,
) -> None:
    svc = service(fake_transport)
    store = PB.PBStore(identifier="store", listId="list", name="Market")

    await svc.save_store("list", store, is_new=True)

    assert fake_transport.calls[-1][0] == "/data/shopping-lists/update-v2"
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "new-store"
    assert operation.metadata.operationClass == PB.PBOperationMetadata.OperationClass.StoreOperation


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
async def test_new_store_filter_gets_next_sort_index_and_sorting_is_optimistic(
    fake_transport,
) -> None:
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
async def test_unchanged_allows_multiple_category_groups_is_official_noop(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list", allowsMultipleListCategoryGroups=True
    )

    await svc.set_allows_multiple_category_groups("list", True)

    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_absent_allows_multiple_category_groups_to_false_is_not_noop(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(identifier="list")

    await svc.set_allows_multiple_category_groups("list", False)

    assert len(fake_transport.calls) == 1
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "set-allows-multiple-category-groups"
    assert op.list.HasField("allowsMultipleListCategoryGroups")
    assert op.list.allowsMultipleListCategoryGroups is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "field", "handler"),
    [
        (
            "set_quantity_override",
            "itemQuantityShouldOverrideIngredientQuantity",
            "set-item-quantity-should-override-ingredient-quantity",
        ),
        (
            "set_package_override",
            "itemPackageSizeShouldOverrideIngredientPackageSize",
            "set-item-package-size-should-override-ingredient-package-size",
        ),
        (
            "set_price_quantity_override",
            "priceQuantityShouldOverrideItemQuantity",
            "set-list-item-price-quantity-should-override-item-quantity",
        ),
        (
            "set_price_package_override",
            "pricePackageSizeShouldOverrideItemPackageSize",
            "set-list-item-price-package-size-should-override-item-package-size",
        ),
    ],
)
async def test_absent_override_flag_to_false_is_not_noop(
    fake_transport, method_name: str, field: str, handler: str
) -> None:
    svc = service(fake_transport)
    shopping = PB.ShoppingList(identifier="list")
    shopping.items.add(identifier="item", listId="list")
    svc.state.shopping_lists["list"] = shopping

    await getattr(svc, method_name)("list", "item", False)

    assert len(fake_transport.calls) == 1
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == handler
    assert op.listItem.HasField(field)
    assert getattr(op.listItem, field) is False


@pytest.mark.asyncio
async def test_new_item_position_uses_effective_default_for_noop(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(identifier="list")

    await svc.set_new_item_position("list", PB.ShoppingList.NewListItemPosition.Bottom)

    assert fake_transport.calls == []

    await svc.set_new_item_position("list", PB.ShoppingList.NewListItemPosition.Top)
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "set-new-list-item-position"
    assert op.list.newListItemPosition == PB.ShoppingList.NewListItemPosition.Top


@pytest.mark.asyncio
async def test_reorder_items_sends_full_mutated_shopping_list(fake_transport) -> None:
    svc = service(fake_transport)
    shopping = PB.ShoppingList(
        identifier="list",
        name="Groceries",
        password="secret",
        allowsMultipleListCategoryGroups=True,
    )
    shopping.items.add(identifier="a", listId="list", name="A")
    shopping.items.add(identifier="b", listId="list", name="B")
    svc.state.shopping_lists["list"] = shopping

    await svc.reorder_items("list", ["b", "a"])

    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "set-ordered-list-items"
    assert op.list.identifier == "list"
    assert op.list.name == "Groceries"
    assert op.list.password == "secret"
    assert op.list.allowsMultipleListCategoryGroups is True
    assert [item.identifier for item in op.list.items] == ["b", "a"]


@pytest.mark.asyncio
async def test_core_shopping_item_operation_contracts(fake_transport) -> None:
    svc = service(fake_transport)
    shopping = PB.ShoppingList(identifier="list", name="Groceries")
    shopping.items.add(identifier="a", listId="list", name="A", details="detail")
    shopping.items.add(identifier="b", listId="list", name="B", checked=True)
    svc.state.shopping_lists["list"] = shopping

    await svc.set_password("list", "secret")
    password = fake_transport.calls[-1][1]["operations"].operations[0]
    assert password.metadata.handlerId == "set-list-password"
    assert password.updatedValue == "secret"
    assert not password.HasField("originalValue")

    package = PB.PBItemPackageSize(size="12", unit="oz", packageType="jar")
    await svc.set_package_size("list", "a", package)
    package_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert package_op.metadata.handlerId == "set-list-item-package-size"
    assert package_op.listItem.identifier == "a"
    assert package_op.listItem.listId == "list"
    assert package_op.listItem.packageSizePb == package
    assert not package_op.listItem.HasField("name")

    await svc.set_price_matchup_tag("list", "a", "unit-price")
    matchup = fake_transport.calls[-1][1]["operations"].operations[0]
    assert matchup.metadata.handlerId == "set-list-item-price-matchup-tag"
    assert matchup.updatedValue == "unit-price"
    assert matchup.originalValue == "unit-price"

    await svc.move_item("list", "a", 1)
    moved = fake_transport.calls[-1][1]["operations"].operations[0]
    assert moved.metadata.handlerId == "move-shopping-list-item-to-index"
    assert moved.listItemId == "a"
    assert moved.originalValue == "0"
    assert moved.updatedValue == "1"
    assert [item.identifier for item in svc.state.shopping_lists["list"].items] == ["b", "a"]

    await svc.remove_item("list", "a")
    removed = fake_transport.calls[-1][1]["operations"].operations[0]
    assert removed.metadata.handlerId == "remove-shopping-list-item"
    assert removed.listItemId == "a"
    assert removed.listItem.name == "A"
    assert removed.listItem.details == "detail"


@pytest.mark.asyncio
async def test_bulk_cross_uncross_and_uncheck_all_operation_contracts(fake_transport) -> None:
    svc = service(fake_transport)
    shopping = PB.ShoppingList(identifier="list")
    shopping.items.add(identifier="a", listId="list", checked=False)
    shopping.items.add(identifier="b", listId="list", checked=True)
    svc.state.shopping_lists["list"] = shopping

    await svc.bulk_set_checked("list", ["a", "b"], True)
    crossed = fake_transport.calls[-1][1]["operations"].operations[0]
    assert crossed.metadata.handlerId == "bulk-cross-off-list-items"
    assert [item.identifier for item in crossed.list.items] == ["a", "b"]
    assert all(item.checked for item in crossed.list.items)

    await svc.bulk_set_checked("list", ["a", "b"], False)
    uncrossed = fake_transport.calls[-1][1]["operations"].operations[0]
    assert uncrossed.metadata.handlerId == "bulk-uncross-list-items"
    assert [item.identifier for item in uncrossed.list.items] == ["a", "b"]
    assert all(not item.HasField("checked") for item in uncrossed.list.items)

    svc.state.shopping_lists["list"].items[0].checked = True
    await svc.uncheck_all("list")
    uncheck = fake_transport.calls[-1][1]["operations"].operations[0]
    assert uncheck.metadata.handlerId == "uncheck-all"
    assert uncheck.listId == "list"


@pytest.mark.asyncio
async def test_store_and_filter_v2_operation_contracts(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(identifier="list")
    store_a = PB.PBStore(identifier="a", listId="list", name="A", sortIndex=2)
    store_b = PB.PBStore(identifier="b", listId="list", name="B", sortIndex=3)
    svc.state.list_stores["list"] = {"a": store_a, "b": store_b}

    await svc.set_sorted_store_ids("list", ["b", "a"])
    sorted_stores = fake_transport.calls[-1][1]["operations"].operations[0]
    assert sorted_stores.metadata.handlerId == "set-sorted-store-ids"
    assert (
        sorted_stores.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.StoreOperation
    )
    assert list(sorted_stores.sortedStoreIds) == ["b", "a"]

    await svc.delete_store("list", svc.state.list_stores["list"]["a"])
    deleted_store = fake_transport.calls[-1][1]["operations"].operations[0]
    assert deleted_store.metadata.handlerId == "delete-store"
    assert deleted_store.updatedStore.identifier == "a"
    assert deleted_store.updatedStore.name == "A"

    filt_a = PB.PBStoreFilter(identifier="fa", listId="list", name="A", sortIndex=4)
    svc.state.list_store_filters["list"] = {"fa": filt_a}
    filt_b = PB.PBStoreFilter(identifier="fb", listId="list", name="B")
    await svc.save_store_filter("list", filt_b, is_new=True)
    new_filter = fake_transport.calls[-1][1]["operations"].operations[0]
    assert new_filter.metadata.handlerId == "new-store-filter"
    assert (
        new_filter.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.StoreFilterOperation
    )
    assert new_filter.updatedStoreFilter.sortIndex == 5

    await svc.set_sorted_store_filter_ids("list", ["fb", "fa"])
    sorted_filters = fake_transport.calls[-1][1]["operations"].operations[0]
    assert sorted_filters.metadata.handlerId == "set-sorted-store-filter-ids"
    assert list(sorted_filters.sortedStoreFilterIds) == ["fb", "fa"]

    await svc.delete_store_filter("list", svc.state.list_store_filters["list"]["fa"])
    deleted_filter = fake_transport.calls[-1][1]["operations"].operations[0]
    assert deleted_filter.metadata.handlerId == "delete-store-filter"
    assert deleted_filter.updatedStoreFilter.identifier == "fa"


@pytest.mark.asyncio
async def test_remove_store_id_from_all_items_always_sends_handler(fake_transport) -> None:
    svc = service(fake_transport)
    shopping = PB.ShoppingList(identifier="list")
    shopping.items.add(identifier="a", listId="list", storeIds=["store", "other"])
    shopping.items.add(identifier="b", listId="list", storeIds=["store"])
    svc.state.shopping_lists["list"] = shopping

    await svc.remove_store_id_from_all_items("list", "store")

    assert [list(item.storeIds) for item in shopping.items] == [["other"], []]
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "remove-store-id-from-all-items"
    assert op.updatedValue == "store"


@pytest.mark.asyncio
async def test_list_category_v2_operation_contracts(fake_transport) -> None:
    svc = service(fake_transport)
    category = PB.PBListCategory(
        identifier="cat", listId="list", categoryGroupId="group", name="Produce", icon="produce"
    )

    await svc.save_list_category(category)
    create = fake_transport.calls[-1][1]["operations"].operations[0]
    assert fake_transport.calls[-1][0] == "/data/shopping-lists/update-v2"
    assert create.metadata.handlerId == "create-category"
    assert (
        create.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.ListCategoryOperation
    )
    assert create.listId == "list"
    assert create.updatedCategory == category

    await svc.migrate_list_category(category)
    migrate = fake_transport.calls[-1][1]["operations"].operations[0]
    assert migrate.metadata.handlerId == "migrate-list-category"
    assert (
        migrate.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.ListCategoryOperation
    )

    await svc.rename_list_category(category, "Fresh Produce")
    rename = fake_transport.calls[-1][1]["operations"].operations[0]
    assert rename.metadata.handlerId == "set-category-name"
    assert rename.updatedCategory.name == "Fresh Produce"

    await svc.set_list_category_icon(category, "leaf")
    icon = fake_transport.calls[-1][1]["operations"].operations[0]
    assert icon.metadata.handlerId == "set-category-icon"
    assert icon.updatedCategory.icon == "leaf"


@pytest.mark.asyncio
async def test_list_category_group_v2_operation_contracts(fake_transport) -> None:
    svc = service(fake_transport)
    group = PB.PBListCategoryGroup(
        identifier="group", listId="list", name="Food", defaultCategoryId="cat"
    )
    group.categories.add(identifier="cat", listId="list", categoryGroupId="group", name="Produce")

    await svc.save_category_group(group)
    create = fake_transport.calls[-1][1]["operations"].operations[0]
    assert fake_transport.calls[-1][0] == "/data/shopping-lists/update-v2"
    assert create.metadata.handlerId == "create-category-group"
    assert (
        create.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.ListCategoryGroupOperation
    )
    assert create.listId == "list"
    assert create.updatedCategoryGroup.name == "Food"
    assert [c.identifier for c in create.updatedCategoryGroup.categories] == ["cat"]

    await svc.migrate_category_group(group)
    migrate = fake_transport.calls[-1][1]["operations"].operations[0]
    assert migrate.metadata.handlerId == "migrate-list-category-group"
    assert (
        migrate.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.ListCategoryGroupOperation
    )

    await svc.rename_category_group(group, "Groceries")
    rename = fake_transport.calls[-1][1]["operations"].operations[0]
    assert rename.metadata.handlerId == "set-category-group-name"
    assert rename.updatedCategoryGroup.name == "Groceries"

    await svc.set_default_category(group, "other")
    default = fake_transport.calls[-1][1]["operations"].operations[0]
    assert default.metadata.handlerId == "set-default-category-id"
    assert default.updatedCategoryGroup.defaultCategoryId == "other"


@pytest.mark.asyncio
async def test_list_item_category_operation_contracts(fake_transport) -> None:
    svc = service(fake_transport)
    shopping = PB.ShoppingList(identifier="list")
    shopping.items.add(identifier="item", listId="list", name="Milk")
    svc.state.shopping_lists["list"] = shopping
    assignment = PB.PBListItemCategoryAssignment(categoryGroupId="group", categoryId="cat")

    await svc.assign_category("list", "item", assignment)
    assignment_op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert assignment_op.metadata.handlerId == "update-list-item-category-assignment"
    assert assignment_op.listId == "list"
    assert assignment_op.listItemId == "item"
    assert len(assignment_op.listItem.categoryAssignments) == 1

    await svc.set_category_match_id("list", "item", "dairy")
    match = fake_transport.calls[-1][1]["operations"].operations[0]
    assert match.metadata.handlerId == "set-list-item-category-match-id"
    assert match.listItem.categoryMatchId == "dairy"
    assert match.listItem.category == "dairy"
    assert match.originalValue == "dairy"


@pytest.mark.asyncio
async def test_list_categorization_rule_v2_operation_contracts(fake_transport) -> None:
    svc = service(fake_transport)
    rule = PB.PBListCategorizationRule(
        identifier="rule", listId="list", categoryGroupId="group", categoryId="cat", itemName="milk"
    )

    await svc.save_categorization_rule(rule)
    save = fake_transport.calls[-1][1]["operations"].operations[0]
    assert save.metadata.handlerId == "save-categorization-rule"
    assert (
        save.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation
    )
    expected_id = category_rule_identifier("milk", "group", "list")
    assert save.updatedCategorizationRule.identifier == expected_id
    assert save.updatedCategorizationRule.itemName == "milk"
    assert save.updatedCategorizationRule.categoryGroupId == "group"
    assert save.updatedCategorizationRule.categoryId == "cat"
    assert expected_id in svc.state.list_categorization_rules["list"]
    assert "rule" not in svc.state.list_categorization_rules["list"]

    rules = [
        PB.PBListCategorizationRule(
            identifier=f"bulk-{i}",
            listId="list",
            categoryGroupId="group",
            categoryId="cat",
            itemName=f"item-{i}",
        )
        for i in range(26)
    ]
    await svc.bulk_save_categorization_rules("list", rules)
    bulk_operations = [
        op
        for call in fake_transport.calls
        if call[0] == "/data/shopping-lists/update-v2"
        for op in call[1]["operations"].operations
        if op.metadata.handlerId == "bulk-save-categorization-rules"
    ]
    assert [len(op.updatedCategorizationRules) for op in bulk_operations] == [25, 1]
    bulk_expected_ids = [
        category_rule_identifier(rule.itemName, rule.categoryGroupId, rule.listId) for rule in rules
    ]
    assert [
        value.identifier for op in bulk_operations for value in op.updatedCategorizationRules
    ] == bulk_expected_ids
    assert set(svc.state.list_categorization_rules["list"]) == {
        expected_id,
        *bulk_expected_ids,
    }
    assert all(
        op.metadata.operationClass
        == PB.PBOperationMetadata.OperationClass.ListCategorizationRuleOperation
        for op in bulk_operations
    )

    await svc.migrate_categorization_rules("list", rules[:2])
    migrate = fake_transport.calls[-1][1]["operations"].operations[0]
    assert migrate.metadata.handlerId == "migrate-per-user-categorization-rules"
    assert len(migrate.updatedCategorizationRules) == 2
    assert [value.identifier for value in migrate.updatedCategorizationRules] == bulk_expected_ids[
        :2
    ]


@pytest.mark.asyncio
async def test_category_sort_sends_all_group_ids_and_appends_unspecified_categories(
    fake_transport,
) -> None:
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
async def test_removing_categories_prunes_rules_and_queues_both_official_operations(
    fake_transport,
) -> None:
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
async def test_bulk_categorization_rules_update_local_state_and_bucket_at_25(
    fake_transport,
) -> None:
    svc = service(fake_transport)
    rules = [
        PB.PBListCategorizationRule(
            identifier=f"r{i}",
            listId="list",
            categoryGroupId="group",
            categoryId="cat",
            itemName=f"item-{i}",
        )
        for i in range(27)
    ]

    await svc.bulk_save_categorization_rules("list", rules)

    expected_ids = {
        category_rule_identifier(rule.itemName, rule.categoryGroupId, rule.listId) for rule in rules
    }
    assert set(svc.state.list_categorization_rules["list"]) == expected_ids
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
async def test_delete_category_group_migrates_filters_to_official_default_group(
    fake_transport,
) -> None:
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
        "filter": PB.PBStoreFilter(identifier="filter", listId="list", listCategoryGroupId="doomed")
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
    handlers = [
        op.metadata.handlerId for op in fake_transport.calls[-1][1]["operations"].operations
    ]
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
async def test_revive_matching_autocomplete_item_uncrosses_and_applies_filter_context(
    fake_transport,
) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="item", listId="list", name="Milk", checked=True)],
    )
    source = PB.ListItem(identifier="suggestion-copy", listId="list", name="Milk", checked=True)
    store_filter = PB.PBStoreFilter(
        identifier="filter", listId="list", showsAllItems=False, storeIds=["market"]
    )

    revived = await svc.revive_matching_item("list", source, store_filter=store_filter, flush=False)

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
async def test_bulk_add_at_top_preserves_visible_order_and_reverses_wire_items(
    fake_transport,
) -> None:
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
async def test_notification_location_dedupes_by_coordinates_without_queueing(
    fake_transport,
) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        notificationLocations=[
            PB.PBNotificationLocation(
                identifier="one", name="Market", address="A", latitude=32.1, longitude=-117.2
            )
        ],
    )

    result = await svc.add_notification_location(
        "list", name="Different", address="B", latitude=32.1, longitude=-117.2
    )

    assert result is None
    assert len(svc.state.shopping_lists["list"].notificationLocations) == 1
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_notification_location_add_operation_contract(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(identifier="list")

    location = await svc.add_notification_location(
        "list",
        name="Market",
        address="123 Main",
        latitude=32.1,
        longitude=-117.2,
        location_id="location",
    )

    assert location.identifier == "location"
    op = fake_transport.calls[-1][1]["operations"].operations[0]
    assert op.metadata.handlerId == "add-list-notification-location"
    assert op.listId == "list"
    assert op.notificationLocation.identifier == "location"
    assert op.notificationLocation.name == "Market"


@pytest.mark.asyncio
async def test_quantity_update_keeps_legacy_quantity_and_skips_identical_updates(
    fake_transport,
) -> None:
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
        items=[
            PB.ListItem(
                identifier="item",
                listId="list",
                name="Milk",
                storeIds=["store"],
                itemQuantityShouldOverrideIngredientQuantity=True,
            )
        ],
    )

    await svc.add_store("list", "item", "store")
    await svc.remove_store("list", "item", "missing")
    await svc.set_quantity_override("list", "item", True)

    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_category_assignment_uses_deterministic_group_assignment_and_full_item(
    fake_transport,
) -> None:
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
async def test_category_match_id_uses_full_item_and_official_post_mutation_original_value(
    fake_transport,
) -> None:
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

    await svc.save_price(
        "list", "item", PB.PBItemPrice(amount=3.5, details="sale", storeId="store")
    )
    item = svc.item("list", "item")
    assert len(item.prices) == 1 and item.prices[0].amount == 3.5

    await svc.save_price("list", "item", PB.PBItemPrice(storeId="store"))
    assert len(item.prices) == 0
    calls = len(fake_transport.calls)
    await svc.save_price("list", "item", PB.PBItemPrice(storeId="missing"))
    assert len(fake_transport.calls) == calls


@pytest.mark.asyncio
async def test_price_matchup_tag_matches_web_clients_post_mutation_original_value(
    fake_transport,
) -> None:
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
async def test_bulk_cross_wire_includes_all_requested_ids_when_only_some_change(
    fake_transport,
) -> None:
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
async def test_clear_promotes_with_skip_existing_then_removes_without_duplicate_callback(
    fake_transport,
) -> None:
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
async def test_clear_propagates_requested_flush_to_recent_promotion(fake_transport) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="a", listId="list", name="A")],
    )
    seen = []

    async def recent(list_id, items, skip_existing, flush):
        seen.append((list_id, [x.identifier for x in items], skip_existing, flush))

    svc.on_items_became_recent = recent
    await svc.clear("list", flush=True)

    assert seen == [("list", ["a"], True, True)]


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


@pytest.mark.asyncio
async def test_remove_checked_propagates_requested_flush_to_recent_promotion(
    fake_transport,
) -> None:
    svc = service(fake_transport)
    svc.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list",
        items=[PB.ListItem(identifier="a", listId="list", checked=True)],
    )
    seen = []

    async def recent(list_id, items, skip_existing, flush):
        seen.append((list_id, [x.identifier for x in items], skip_existing, flush))

    svc.on_items_became_recent = recent
    await svc.remove_checked("list", flush=True)

    assert seen == [("list", ["a"], True, True)]


@pytest.mark.asyncio
async def test_has_pending_new_list_tracks_legacy_queue(fake_transport) -> None:
    svc = service(fake_transport)
    assert svc.has_pending_new_list() is False

    operation = svc.legacy_queue.new_operation(
        "new-shopping-list",
        listId="list",
        list=PB.ShoppingList(identifier="list"),
    )
    await svc.legacy_queue.enqueue(operation, flush=False)
    assert svc.has_pending_new_list() is True

    svc.legacy_queue._pending.clear()
    assert svc.has_pending_new_list() is False


@pytest.mark.asyncio
async def test_unshare_unknown_email_is_official_noop(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.shopping_lists["list"] = PB.ShoppingList(identifier="list")
    state.shopping_lists["list"].sharedUsers.add(email="known@example.com", userId="known")
    service = ShoppingListsService(fake_transport, state, user_id="user")

    await service.unshare("list", "missing@example.com")

    assert [u.email for u in state.shopping_lists["list"].sharedUsers] == ["known@example.com"]
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_unshare_removes_user_optimistically_and_queues_exact_operation(
    fake_transport,
) -> None:
    state = AnyListState(user_id="user")
    state.shopping_lists["list"] = PB.ShoppingList(identifier="list")
    state.shopping_lists["list"].sharedUsers.add(email="friend@example.com", userId="friend")
    service = ShoppingListsService(fake_transport, state, user_id="user")

    await service.unshare("list", "friend@example.com")

    assert list(state.shopping_lists["list"].sharedUsers) == []
    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/shopping-lists/update"
    operation = fields["operations"].operations[0]
    assert operation.metadata.handlerId == "unshare-shopping-list"
    assert operation.listId == "list"
    assert operation.updatedValue == "friend@example.com"
    assert response_type == "PBEditOperationResponse"
