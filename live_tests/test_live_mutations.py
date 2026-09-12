from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from anylist_sdk import AnyListClient
from anylist_sdk.derived import (
    event_list_item_to_item_ingredient,
    ingredient_to_item_ingredient,
)
from anylist_sdk.proto import PB, ListItem, ShoppingList
from anylist_sdk.services.generic import GenericDomainService
from anylist_sdk.services.meal_plan import MealPlanService
from anylist_sdk.services.recipes import RecipesService
from anylist_sdk.services.shopping import category_rule_identifier
from anylist_sdk.services.starter import favorite_list_id, recent_list_id

DISPOSABLE_LIST_NAME = "AnyList SDK Conformance Test"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ANYLIST_LIVE_MUTATIONS") != "1",
        reason="set ANYLIST_LIVE_MUTATIONS=1 to enable mutation conformance tests",
    ),
]


async def _load_and_require_disposable(live_client, list_id: str) -> ShoppingList:
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    assert live_client.lists is not None
    shopping_list = live_client.lists.get(list_id)
    if shopping_list is None:
        pytest.fail(
            "The reserved AnyList SDK conformance list is not present on the server; refusing all writes",
            pytrace=False,
        )
    if str(shopping_list.name) != DISPOSABLE_LIST_NAME:
        pytest.fail(
            "ANYLIST_LIVE_LIST_ID does not resolve to the exact disposable conformance-list name; refusing all writes",
            pytrace=False,
        )
    return shopping_list


async def _fresh_server_list(live_client, list_id: str) -> ShoppingList | None:
    """Read the list through a new SDK state mirror so optimistic state cannot satisfy a test."""
    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)
    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert fresh.lists is not None
        value = fresh.lists.get(list_id)
        if value is None:
            return None
        out = PB.ShoppingList()
        out.CopyFrom(value)
        return out
    finally:
        await fresh.close()


async def _fresh_list_scope(live_client, list_id: str) -> dict[str, object]:
    """Copy every server-backed resource that is scoped to the disposable shopping list."""
    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)

    def copied(value):
        out = value.__class__()
        out.CopyFrom(value)
        return out

    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        return {
            "stores": {
                key: copied(value)
                for key, value in fresh.state.list_stores.get(list_id, {}).items()
            },
            "store_filters": {
                key: copied(value)
                for key, value in fresh.state.list_store_filters.get(list_id, {}).items()
            },
            "category_groups": {
                key: copied(value)
                for key, value in fresh.state.list_category_groups.get(list_id, {}).items()
            },
            "categories": {
                key: copied(value)
                for key, value in fresh.state.list_categories.get(list_id, {}).items()
            },
            "categorization_rules": {
                key: copied(value)
                for key, value in fresh.state.list_categorization_rules.get(list_id, {}).items()
            },
            "settings": (
                copied(fresh.state.list_settings[list_id])
                if list_id in fresh.state.list_settings
                else None
            ),
        }
    finally:
        await fresh.close()


async def _remove_without_recents(live_client, list_id: str, item_ids: list[str]) -> None:
    assert live_client.lists is not None
    existing = [item_id for item_id in item_ids if live_client.lists.item(list_id, item_id) is not None]
    if existing:
        await live_client.lists.bulk_remove_items(
            list_id,
            existing,
            remember_recent=False,
        )


async def _fresh_disposable_starters(live_client, shopping_list_id: str) -> dict[str, object]:
    """Read only the deterministic starter lists associated with the disposable list."""
    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)

    def copied(value):
        if value is None:
            return None
        out = value.__class__()
        out.CopyFrom(value)
        return out

    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert fresh.starter_lists is not None
        return {
            "recent": copied(
                fresh.starter_lists.get(recent_list_id(shopping_list_id))
            ),
            "favorite": copied(
                fresh.starter_lists.get(favorite_list_id(shopping_list_id))
            ),
            "recent_settings": copied(
                fresh.state.starter_list_settings.get(recent_list_id(shopping_list_id))
            ),
            "favorite_settings": copied(
                fresh.state.starter_list_settings.get(favorite_list_id(shopping_list_id))
            ),
        }
    finally:
        await fresh.close()


async def _fresh_starter_order(live_client) -> list[str]:
    """Read starter-list ordering through a fresh client state mirror."""
    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)
    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert fresh.starter_lists is not None
        await fresh.starter_lists.refresh_order()
        return list(fresh.state.ordered_starter_list_ids)
    finally:
        await fresh.close()


async def _fresh_folder_state(live_client) -> tuple[str, dict[str, object]]:
    """Read the folder tree through a fresh client and return deep-copied folders."""
    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)
    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert fresh.folders is not None
        root_id = str(fresh.state.root_folder_id or "")
        copied: dict[str, object] = {}
        for identifier, value in fresh.state.list_folders.items():
            clone = PB.PBListFolder()
            clone.CopyFrom(value)
            copied[str(identifier)] = clone
        return root_id, copied
    finally:
        await fresh.close()


async def _fresh_global_category_state(live_client) -> dict[str, dict[str, object]]:
    """Read disposable-safe global category/categorization domains through fresh state."""
    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)

    def copied(value):
        out = value.__class__()
        out.CopyFrom(value)
        return out

    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        return {
            "categories": {
                key: copied(value) for key, value in fresh.state.user_categories.items()
            },
            "groupings": {
                key: copied(value) for key, value in fresh.state.category_groupings.items()
            },
            "categorized_items": {
                key: copied(value) for key, value in fresh.state.categorized_items.items()
            },
        }
    finally:
        await fresh.close()


async def _fresh_recipe_state(live_client) -> dict[str, object]:
    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)

    def copied(value):
        out = value.__class__()
        out.CopyFrom(value)
        return out

    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        return {
            "recipes": {key: copied(value) for key, value in fresh.state.recipes.items()},
            "collections": {
                key: copied(value) for key, value in fresh.state.recipe_collections.items()
            },
            "collection_ids": list(fresh.state.recipe_collection_ids),
        }
    finally:
        await fresh.close()


async def _fresh_meal_plan_state(live_client) -> dict[str, object]:
    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)

    def copied(value):
        out = value.__class__()
        out.CopyFrom(value)
        return out

    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        return {
            "calendar_id": fresh.state.meal_plan_calendar_id,
            "events": {key: copied(value) for key, value in fresh.state.meal_plan_events.items()},
            "template_events": {
                key: copied(value) for key, value in fresh.state.meal_plan_template_events.items()
            },
            "labels": {key: copied(value) for key, value in fresh.state.meal_plan_labels.items()},
            "templates": {
                key: copied(value) for key, value in fresh.state.meal_plan_templates.items()
            },
            "groups": {
                key: copied(value) for key, value in fresh.state.meal_plan_template_groups.items()
            },
        }
    finally:
        await fresh.close()


async def _cleanup_meal_plan_template_marker(
    live_client, service: MealPlanService, marker: str
) -> set[str]:
    """Delete only template resources carrying this test marker, leaf-first."""
    deleted_ids: set[str] = set()

    await service.refresh()
    for event_id, event in list(service.state.meal_plan_template_events.items()):
        if marker in str(getattr(event, "title", "") or ""):
            deleted_ids.add(str(event_id))
            await service.delete_event(str(event_id))
            await service.refresh()

    await service.refresh()
    for template_id, template in list(service.state.meal_plan_templates.items()):
        if marker not in str(getattr(template, "name", "") or ""):
            continue
        deleted_ids.add(str(template_id))
        await service.delete_template(str(template_id))
        await service.refresh()

    while True:
        await service.refresh()
        groups = {
            str(group_id): group
            for group_id, group in service.state.meal_plan_template_groups.items()
            if marker in str(getattr(group, "name", "") or "")
        }
        if not groups:
            break

        parent_by_child: dict[str, str] = {}
        for parent_id, parent in service.state.meal_plan_template_groups.items():
            for item in parent.items:
                if int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Group):
                    parent_by_child[str(item.identifier)] = str(parent_id)

        leaf_id = next(
            (
                group_id
                for group_id, group in groups.items()
                if not any(
                    int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Group)
                    and str(item.identifier) in groups
                    for item in group.items
                )
            ),
            None,
        )
        if leaf_id is None:
            raise AssertionError("disposable template-group graph unexpectedly contains a cycle")
        parent_id = parent_by_child.get(leaf_id)
        if not parent_id:
            raise AssertionError("disposable template group is missing its parent")
        deleted_ids.add(leaf_id)
        await service.delete_template_group(leaf_id, parent_id)

    await service.refresh()
    for parent_id, parent in list(service.state.meal_plan_template_groups.items()):
        if not any(str(item.identifier) in deleted_ids for item in parent.items):
            continue
        kept = [
            PB.PBMealPlanTemplateGroupItem(
                identifier=str(item.identifier), itemType=int(item.itemType)
            )
            for item in parent.items
            if str(item.identifier) not in deleted_ids
        ]
        await service.set_ordered_template_group_items(str(parent_id), kept)

    await service.refresh()
    assert not any(
        marker in str(getattr(value, "name", "") or "")
        for value in service.state.meal_plan_templates.values()
    )
    assert not any(
        marker in str(getattr(value, "name", "") or "")
        for value in service.state.meal_plan_template_groups.values()
    )
    assert not any(
        marker in str(getattr(value, "title", "") or "")
        for value in service.state.meal_plan_template_events.values()
    )
    assert not any(
        str(item.identifier) in deleted_ids
        for group in service.state.meal_plan_template_groups.values()
        for item in group.items
    )

    fresh = await _fresh_meal_plan_state(live_client)
    assert not any(
        marker in str(getattr(value, "name", "") or "")
        for value in fresh["templates"].values()
    )
    assert not any(
        marker in str(getattr(value, "name", "") or "")
        for value in fresh["groups"].values()
    )
    assert not any(
        marker in str(getattr(value, "title", "") or "")
        for value in fresh["template_events"].values()
    )
    assert not any(
        str(item.identifier) in deleted_ids
        for group in fresh["groups"].values()
        for item in group.items
    )
    return deleted_ids


async def _ensure_disposable_starters(live_client, shopping_list_id: str) -> None:
    await _load_and_require_disposable(live_client, shopping_list_id)
    assert live_client.starter_lists is not None
    await live_client.starter_lists.initialize_for_shopping_list(shopping_list_id)


async def _remove_named_starter_items(
    live_client, shopping_list_id: str, names: set[str]
) -> None:
    assert live_client.starter_lists is not None
    await live_client.starter_lists.refresh()
    for starter_id in (
        recent_list_id(shopping_list_id),
        favorite_list_id(shopping_list_id),
    ):
        starter = live_client.starter_lists.get(starter_id)
        if starter is None:
            continue
        ids = [str(item.identifier) for item in starter.items if str(item.name) in names]
        if ids:
            await live_client.starter_lists.bulk_remove_items(starter_id, ids)


async def _wait_for_shopping_invalidation(live_client, *, timeout: float = 15.0):
    async def next_matching():
        async for event in live_client.realtime.events():
            if event.message == "refresh-shopping-lists":
                return event
        raise AssertionError("realtime event iterator ended unexpectedly")

    return await asyncio.wait_for(next_matching(), timeout=timeout)


async def _wait_for_item_state(
    live_client,
    list_id: str,
    item_id: str,
    *,
    present: bool,
    timeout: float = 15.0,
) -> None:
    async def condition() -> None:
        while True:
            assert live_client.lists is not None
            exists = live_client.lists.item(list_id, item_id) is not None
            if exists is present:
                return
            await asyncio.sleep(0.05)

    await asyncio.wait_for(condition(), timeout=timeout)


async def _wait_for_realtime_connected_state(
    live_client, *, connected: bool, timeout: float = 15.0
) -> None:
    async def condition() -> None:
        while live_client.realtime.connected.is_set() is not connected:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(condition(), timeout=timeout)


@pytest.mark.asyncio
async def test_live_create_reserved_disposable_list_only(
    live_client, live_mutation_list_id: str
) -> None:
    """Create only the reserved shopping list; never create its Recent/Favorite side lists."""
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    assert live_client.lists is not None
    existing = live_client.lists.get(live_mutation_list_id)
    if existing is not None:
        if str(existing.name) != DISPOSABLE_LIST_NAME:
            pytest.fail(
                "reserved conformance-list ID already exists with another name; refusing to mutate it",
                pytrace=False,
            )
    else:
        await live_client.lists.create(
            DISPOSABLE_LIST_NAME,
            list_id=live_mutation_list_id,
            initialize_starter_lists=False,
        )

    fresh = await _fresh_server_list(live_client, live_mutation_list_id)
    assert fresh is not None
    assert fresh.name == DISPOSABLE_LIST_NAME


@pytest.mark.asyncio
async def test_live_disposable_list_guard(live_client, live_mutation_list_id: str) -> None:
    """Read-only prerequisite: mutation tests are impossible unless both ID and name match."""
    value = await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert value.identifier == live_mutation_list_id
    fresh = await _fresh_server_list(live_client, live_mutation_list_id)
    assert fresh is not None
    assert fresh.name == DISPOSABLE_LIST_NAME


@pytest.mark.asyncio
async def test_live_initialize_disposable_recent_and_favorite_lists(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    recent = snapshot["recent"]
    favorite = snapshot["favorite"]
    assert recent is not None
    assert favorite is not None
    assert recent.identifier == recent_list_id(live_mutation_list_id)
    assert favorite.identifier == favorite_list_id(live_mutation_list_id)
    assert recent.listId == live_mutation_list_id
    assert favorite.listId == live_mutation_list_id
    assert recent.starterListType == PB.StarterList.Type.RecentItemsType
    assert favorite.starterListType == PB.StarterList.Type.FavoriteItemsType


@pytest.mark.asyncio
async def test_live_cross_off_records_disposable_recent_item(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    marker = f"sdk-recent-cross-{uuid4().hex}"
    created = await live_client.lists.add_item(live_mutation_list_id, marker)
    item_id = str(created.identifier)
    try:
        await live_client.lists.set_checked(live_mutation_list_id, item_id, True)
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        persisted = next((item for item in fresh.items if item.identifier == item_id), None)
        assert persisted is not None and persisted.checked

        starters = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        recent = starters["recent"]
        assert recent is not None
        matches = [item for item in recent.items if item.name == marker]
        assert len(matches) == 1
        assert not matches[0].checked
        assert matches[0].identifier != item_id
        assert matches[0].listId == recent_list_id(live_mutation_list_id)

        await live_client.lists.set_checked(live_mutation_list_id, item_id, False)
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, [item_id])
        await _remove_named_starter_items(
            live_client, live_mutation_list_id, {marker}
        )


@pytest.mark.asyncio
async def test_live_normal_remove_records_disposable_recent_item(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    marker = f"sdk-recent-remove-{uuid4().hex}"
    created = await live_client.lists.add_item(live_mutation_list_id, marker)
    item_id = str(created.identifier)
    try:
        await live_client.lists.remove_item(live_mutation_list_id, item_id)
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        assert all(item.identifier != item_id for item in fresh.items)

        starters = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        recent = starters["recent"]
        assert recent is not None
        matches = [item for item in recent.items if item.name == marker]
        assert len(matches) == 1
        assert not matches[0].checked
    finally:
        await _remove_named_starter_items(
            live_client, live_mutation_list_id, {marker}
        )


@pytest.mark.asyncio
async def test_live_clear_and_remove_checked_preserve_disposable_recents(
    live_client, live_mutation_list_id: str
) -> None:
    shopping = await _load_and_require_disposable(live_client, live_mutation_list_id)
    if shopping.items:
        pytest.fail(
            "disposable shopping list is not empty; refusing destructive clear/remove-checked test",
            pytrace=False,
        )
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.lists is not None

    clear_names = {f"sdk-clear-{uuid4().hex}", f"sdk-clear-{uuid4().hex}"}
    checked_names = {
        f"sdk-remove-checked-{uuid4().hex}",
        f"sdk-remove-checked-{uuid4().hex}",
    }
    all_names = clear_names | checked_names
    try:
        clear_items = [PB.ListItem(name=name) for name in clear_names]
        await live_client.lists.add_items(live_mutation_list_id, clear_items)
        await live_client.lists.clear(live_mutation_list_id)
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None and not fresh.items

        starters = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        recent = starters["recent"]
        assert recent is not None
        assert clear_names.issubset({str(item.name) for item in recent.items})

        checked_items = [PB.ListItem(name=name) for name in checked_names]
        created = await live_client.lists.add_items(live_mutation_list_id, checked_items)
        ids = [str(item.identifier) for item in created]
        await live_client.lists.bulk_set_checked(live_mutation_list_id, ids, True)

        before = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        recent_before = before["recent"]
        assert recent_before is not None
        before_counts = {
            name: sum(1 for item in recent_before.items if item.name == name)
            for name in checked_names
        }
        assert before_counts == {name: 1 for name in checked_names}

        await live_client.lists.remove_checked(live_mutation_list_id)
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None and not fresh.items
        after = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        recent_after = after["recent"]
        assert recent_after is not None
        after_counts = {
            name: sum(1 for item in recent_after.items if item.name == name)
            for name in checked_names
        }
        assert after_counts == before_counts
    finally:
        await _remove_named_starter_items(
            live_client, live_mutation_list_id, all_names
        )
        current = live_client.lists.get(live_mutation_list_id)
        if current is not None and current.items:
            await _remove_without_recents(
                live_client,
                live_mutation_list_id,
                [str(item.identifier) for item in current.items],
            )


@pytest.mark.asyncio
async def test_live_favorite_starter_item_crud_and_fields(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.starter_lists is not None
    favorite_id = favorite_list_id(live_mutation_list_id)
    marker = f"sdk-favorite-{uuid4().hex}"
    created = await live_client.starter_lists.add_item(
        favorite_id, PB.ListItem(name=marker)
    )
    item_id = str(created.identifier)
    final_name = f"{marker}-renamed"
    details = f"details-{uuid4().hex}"
    upc = "012345678905"
    photo_id = f"sdk-favorite-photo-{uuid4().hex}"
    quantity = PB.PBItemQuantity(amount="2", unit="cup", rawQuantity="2 cups")
    package = PB.PBItemPackageSize(size="12", unit="oz", rawPackageSize="12 oz")
    price_quantity = PB.PBItemQuantity(amount="1", unit="each", rawQuantity="1 each")
    price_package = PB.PBItemPackageSize(size="6", unit="oz", rawPackageSize="6 oz")
    store_a = f"sdk-store-{uuid4().hex}"
    store_b = f"sdk-store-{uuid4().hex}"
    price = PB.PBItemPrice(storeId=store_a, amount=3.5, details="sdk-price")
    try:
        await live_client.starter_lists.set_item_name(favorite_id, item_id, final_name)
        await live_client.starter_lists.set_item_details(favorite_id, item_id, details)
        await live_client.starter_lists.set_product_upc(favorite_id, item_id, upc)
        await live_client.starter_lists.set_photo(favorite_id, item_id, photo_id)
        await live_client.starter_lists.set_quantity(favorite_id, item_id, quantity)
        await live_client.starter_lists.set_quantity_override(favorite_id, item_id, True)
        await live_client.starter_lists.set_package_size(favorite_id, item_id, package)
        await live_client.starter_lists.set_package_override(favorite_id, item_id, True)
        await live_client.starter_lists.set_price_quantity(favorite_id, item_id, price_quantity)
        await live_client.starter_lists.set_price_quantity_override(
            favorite_id, item_id, True
        )
        await live_client.starter_lists.set_price_package_size(
            favorite_id, item_id, price_package
        )
        await live_client.starter_lists.set_price_package_override(
            favorite_id, item_id, True
        )
        await live_client.starter_lists.add_store(favorite_id, item_id, store_a)
        await live_client.starter_lists.add_store_ids_to_items(
            favorite_id, [item_id], [store_b]
        )
        await live_client.starter_lists.save_price(favorite_id, item_id, price)

        snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        favorite = snapshot["favorite"]
        assert favorite is not None
        persisted = next((x for x in favorite.items if x.identifier == item_id), None)
        assert persisted is not None
        assert persisted.name == final_name
        assert persisted.details == details
        assert persisted.productUpc == upc
        assert list(persisted.photoIds) == [photo_id]
        assert persisted.quantityPb.SerializeToString() == quantity.SerializeToString()
        assert persisted.packageSizePb.SerializeToString() == package.SerializeToString()
        assert persisted.priceQuantityPb.SerializeToString() == price_quantity.SerializeToString()
        assert persisted.pricePackageSizePb.SerializeToString() == price_package.SerializeToString()
        assert set(persisted.storeIds) == {store_a, store_b}
        assert len(persisted.prices) == 1 and persisted.prices[0].amount == 3.5

        await live_client.starter_lists.remove_store(favorite_id, item_id, store_a)
        await live_client.starter_lists.remove_store_ids_from_items(
            favorite_id, [item_id], [store_b]
        )
        await live_client.starter_lists.remove_store_from_all_items(favorite_id, store_a)
        await live_client.starter_lists.remove_price(favorite_id, item_id, store_a)
        await live_client.starter_lists.set_photo(favorite_id, item_id, None)

        snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        favorite = snapshot["favorite"]
        assert favorite is not None
        persisted = next((x for x in favorite.items if x.identifier == item_id), None)
        assert persisted is not None
        assert not persisted.storeIds
        assert not persisted.prices
        assert not persisted.photoIds
    finally:
        await live_client.starter_lists.refresh()
        favorite = live_client.starter_lists.get(favorite_id)
        if favorite is not None and any(x.identifier == item_id for x in favorite.items):
            await live_client.starter_lists.remove_item(favorite_id, item_id)


@pytest.mark.asyncio
async def test_live_favorite_starter_rename_bulk_remove_and_clear(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.starter_lists is not None
    favorite_id = favorite_list_id(live_mutation_list_id)
    snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    favorite = snapshot["favorite"]
    assert favorite is not None
    if favorite.items:
        pytest.fail(
            "disposable Favorite Items list is not empty; refusing destructive starter clear test",
            pytrace=False,
        )
    original_name = str(favorite.name)
    temporary_name = f"SDK Favorite {uuid4().hex[:8]}"
    names = [f"sdk-favorite-bulk-{uuid4().hex}" for _ in range(3)]
    clear_names = [f"sdk-favorite-clear-{uuid4().hex}" for _ in range(2)]
    try:
        await live_client.starter_lists.rename(favorite_id, temporary_name)
        renamed = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        assert renamed["favorite"] is not None
        assert renamed["favorite"].name == temporary_name
        await live_client.starter_lists.rename(favorite_id, original_name)

        added = await live_client.starter_lists.bulk_add_items(
            favorite_id, [PB.ListItem(name=name) for name in names]
        )
        ids = [str(item.identifier) for item in added]
        snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        persisted = snapshot["favorite"]
        assert persisted is not None
        assert set(names).issubset({str(item.name) for item in persisted.items})

        await live_client.starter_lists.remove_item(favorite_id, ids[0])
        await live_client.starter_lists.bulk_remove_items(favorite_id, ids[1:])
        snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        persisted = snapshot["favorite"]
        assert persisted is not None
        assert not set(names) & {str(item.name) for item in persisted.items}

        await live_client.starter_lists.bulk_add_items(
            favorite_id, [PB.ListItem(name=name) for name in clear_names]
        )
        await live_client.starter_lists.clear(favorite_id)
        snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        persisted = snapshot["favorite"]
        assert persisted is not None and not persisted.items
    finally:
        await live_client.starter_lists.refresh()
        favorite = live_client.starter_lists.get(favorite_id)
        if favorite is not None and favorite.name != original_name:
            await live_client.starter_lists.rename(favorite_id, original_name)
        if favorite is not None:
            cleanup_names = set(names) | set(clear_names)
            cleanup_ids = [
                str(item.identifier)
                for item in favorite.items
                if str(item.name) in cleanup_names
            ]
            if cleanup_ids:
                await live_client.starter_lists.bulk_remove_items(
                    favorite_id, cleanup_ids
                )


@pytest.mark.asyncio
async def test_live_recent_items_200_cap_and_oldest_eviction(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.starter_lists is not None
    recent_id = recent_list_id(live_mutation_list_id)
    snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    recent = snapshot["recent"]
    assert recent is not None
    if recent.items:
        pytest.fail(
            "disposable Recent Items list is not empty; refusing 200-cap test",
            pytrace=False,
        )

    prefix = f"sdk-cap-{uuid4().hex}-"
    names = [f"{prefix}{index:03d}" for index in range(201)]
    try:
        added = await live_client.starter_lists.bulk_add_items(
            recent_id, [PB.ListItem(name=name) for name in names]
        )
        assert len(added) == 200
        snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        recent = snapshot["recent"]
        assert recent is not None
        persisted_names = [
            str(item.name)
            for item in recent.items
            if str(item.name).startswith(prefix)
        ]
        assert len(persisted_names) == 200
        assert names[0] not in persisted_names
        assert set(persisted_names) == set(names[1:])
    finally:
        await live_client.starter_lists.refresh()
        recent = live_client.starter_lists.get(recent_id)
        if recent is not None:
            ids = [
                str(item.identifier)
                for item in recent.items
                if str(item.name).startswith(prefix)
            ]
            if ids:
                await live_client.starter_lists.bulk_remove_items(recent_id, ids)


@pytest.mark.asyncio
async def test_live_remove_and_recreate_disposable_recent_and_favorite_lists(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.starter_lists is not None
    recent_id = recent_list_id(live_mutation_list_id)
    favorite_id = favorite_list_id(live_mutation_list_id)
    snapshot = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    recent = snapshot["recent"]
    favorite = snapshot["favorite"]
    assert recent is not None and favorite is not None
    if recent.items or favorite.items:
        pytest.fail(
            "disposable starter lists are not empty; refusing lifecycle removal test",
            pytrace=False,
        )

    await live_client.starter_lists.remove(recent_id)
    await live_client.starter_lists.remove(favorite_id)
    removed = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    assert removed["recent"] is None
    assert removed["favorite"] is None

    await live_client.starter_lists.initialize_for_shopping_list(live_mutation_list_id)
    recreated = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    assert recreated["recent"] is not None
    assert recreated["favorite"] is not None


@pytest.mark.asyncio
async def test_live_disposable_favorite_starter_settings_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.starter_list_settings is not None
    favorite_id = favorite_list_id(live_mutation_list_id)
    before = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    original = before["favorite_settings"]

    if original is None:
        await live_client.starter_list_settings.set(
            favorite_id, "shouldHideCategories", True
        )
        changed = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        settings = changed["favorite_settings"]
        assert settings is not None
        assert settings.HasField("shouldHideCategories")
        assert settings.shouldHideCategories is True

        await live_client.starter_list_settings.remove(favorite_id)
        restored = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        assert restored["favorite_settings"] is None
        return

    if not original.HasField("shouldHideCategories"):
        pytest.skip(
            "existing disposable Favorite settings omit the reversible test field"
        )

    original_value = bool(original.shouldHideCategories)
    try:
        await live_client.starter_list_settings.set(
            favorite_id, "shouldHideCategories", not original_value
        )
        changed = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        settings = changed["favorite_settings"]
        assert settings is not None
        assert settings.HasField("shouldHideCategories")
        assert bool(settings.shouldHideCategories) is (not original_value)
    finally:
        await live_client.starter_list_settings.set(
            favorite_id, "shouldHideCategories", original_value
        )
    restored = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    settings = restored["favorite_settings"]
    assert settings is not None
    assert settings.HasField("shouldHideCategories")
    assert bool(settings.shouldHideCategories) is original_value


@pytest.mark.asyncio
async def test_live_client_flush_sends_deferred_disposable_favorite_item(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.starter_lists is not None
    favorite_id = favorite_list_id(live_mutation_list_id)
    marker = f"sdk-client-flush-{uuid4().hex}"
    created = await live_client.starter_lists.add_item(
        favorite_id, PB.ListItem(name=marker), flush=False
    )
    item_id = str(created.identifier)
    try:
        assert live_client.starter_lists.queue.pending_count == 1
        before = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        favorite = before["favorite"]
        assert favorite is not None
        assert all(str(item.name) != marker for item in favorite.items)

        await live_client.flush()
        assert live_client.starter_lists.queue.pending_count == 0
        after = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        favorite = after["favorite"]
        assert favorite is not None
        assert sum(str(item.name) == marker for item in favorite.items) == 1
    finally:
        if live_client.starter_lists.queue.pending_count:
            await live_client.flush()
        await live_client.starter_lists.refresh()
        favorite = live_client.starter_lists.get(favorite_id)
        if favorite is not None and any(item.identifier == item_id for item in favorite.items):
            await live_client.starter_lists.remove_item(favorite_id, item_id)


@pytest.mark.asyncio
async def test_live_starter_queue_pause_resume_defers_disposable_favorite_item(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.starter_lists is not None
    favorite_id = favorite_list_id(live_mutation_list_id)
    marker = f"sdk-pause-resume-{uuid4().hex}"
    item_id = ""
    live_client.starter_lists.pause()
    try:
        created = await live_client.starter_lists.add_item(
            favorite_id, PB.ListItem(name=marker), flush=True
        )
        item_id = str(created.identifier)
        assert live_client.starter_lists.queue.paused
        assert live_client.starter_lists.queue.pending_count == 1
        before = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        favorite = before["favorite"]
        assert favorite is not None
        assert all(str(item.name) != marker for item in favorite.items)

        await live_client.starter_lists.resume(flush=True)
        assert not live_client.starter_lists.queue.paused
        assert live_client.starter_lists.queue.pending_count == 0
        after = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        favorite = after["favorite"]
        assert favorite is not None
        assert sum(str(item.name) == marker for item in favorite.items) == 1
    finally:
        while live_client.starter_lists.queue.paused:
            await live_client.starter_lists.resume(flush=True)
        if live_client.starter_lists.queue.pending_count:
            await live_client.starter_lists.flush()
        await live_client.starter_lists.refresh()
        favorite = live_client.starter_lists.get(favorite_id)
        if favorite is not None:
            ids = [
                str(item.identifier)
                for item in favorite.items
                if str(item.name) == marker or (item_id and str(item.identifier) == item_id)
            ]
            if ids:
                await live_client.starter_lists.bulk_remove_items(favorite_id, ids)


@pytest.mark.asyncio
async def test_live_journal_restore_replays_disposable_favorite_item(
    live_client, live_mutation_list_id: str, tmp_path
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    tokens = live_client.tokens
    assert tokens is not None
    favorite_id = favorite_list_id(live_mutation_list_id)
    marker = f"sdk-journal-replay-{uuid4().hex}"
    cache_dir = tmp_path / "live-journal-client"

    writer = AnyListClient(
        base_url=live_client.transport.base_url,
        tokens=tokens,
        cache_dir=cache_dir,
    )
    writer_tokens = tokens
    try:
        await writer.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert writer.starter_lists is not None
        await writer.starter_lists.add_item(
            favorite_id, PB.ListItem(name=marker), flush=False
        )
        assert writer.starter_lists.queue.pending_count == 1
        assert list((cache_dir / "operations").glob("*.json"))
        writer_tokens = writer.tokens or tokens
    finally:
        # Simulate an abrupt process/network loss. ``AnyListClient.close()`` is intentionally
        # graceful and flushes pending operations first, so use the transport close directly
        # here to leave the archived queue intact for the next client instance.
        await writer.realtime.stop()
        await writer.transport.close()

    before = AnyListClient(base_url=live_client.transport.base_url, tokens=writer_tokens)
    try:
        await before.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert before.starter_lists is not None
        favorite = before.starter_lists.get(favorite_id)
        assert favorite is not None
        assert all(str(item.name) != marker for item in favorite.items)
    finally:
        await before.close()

    restorer = AnyListClient(
        base_url=live_client.transport.base_url,
        tokens=writer_tokens,
        cache_dir=cache_dir,
    )
    try:
        await restorer.load(realtime=False, load_tag_data=False, restore_pending=True)
        assert restorer.starter_lists is not None
        assert restorer.starter_lists.queue.pending_count == 0
        assert not list((cache_dir / "operations").glob("*.json"))

        verify_tokens = restorer.tokens or writer_tokens
        verifier = AnyListClient(
            base_url=live_client.transport.base_url, tokens=verify_tokens
        )
        try:
            await verifier.load(realtime=False, load_tag_data=False, restore_pending=False)
            assert verifier.starter_lists is not None
            favorite = verifier.starter_lists.get(favorite_id)
            assert favorite is not None
            matches = [item for item in favorite.items if str(item.name) == marker]
            assert len(matches) == 1
        finally:
            await verifier.close()

        await restorer.starter_lists.refresh()
        favorite = restorer.starter_lists.get(favorite_id)
        if favorite is not None:
            ids = [
                str(item.identifier)
                for item in favorite.items
                if str(item.name) == marker
            ]
            if ids:
                await restorer.starter_lists.bulk_remove_items(favorite_id, ids)
    finally:
        await restorer.close()


@pytest.mark.asyncio
async def test_live_shopping_service_restore_replays_deferred_legacy_rename(
    live_client, live_mutation_list_id: str, tmp_path
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    tokens = live_client.tokens
    assert tokens is not None

    created = await live_client.lists.add_item(
        live_mutation_list_id,
        f"sdk-shopping-restore-{uuid4().hex}",
    )
    item_id = str(created.identifier)
    restored_name = f"sdk-shopping-restored-{uuid4().hex}"
    cache_dir = tmp_path / "shopping-restore-client"

    writer = AnyListClient(
        base_url=live_client.transport.base_url,
        tokens=tokens,
        cache_dir=cache_dir,
    )
    writer_tokens = tokens
    try:
        await writer.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert writer.lists is not None
        await writer.lists.raw_legacy_operation(
            "set-list-item-name",
            listId=live_mutation_list_id,
            listItemId=item_id,
            updatedValue=restored_name,
            flush=False,
        )
        assert writer.lists.legacy_queue.pending_count == 1
        writer_tokens = writer.tokens or tokens
    finally:
        # Preserve the pending archive exactly as an abrupt process/network loss would.
        await writer.realtime.stop()
        await writer.transport.close()

    before = AnyListClient(base_url=live_client.transport.base_url, tokens=writer_tokens)
    try:
        await before.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert before.lists is not None
        item = before.lists.item(live_mutation_list_id, item_id)
        assert item is not None and item.name != restored_name
    finally:
        await before.close()

    restorer = AnyListClient(
        base_url=live_client.transport.base_url,
        tokens=writer_tokens,
        cache_dir=cache_dir,
    )
    try:
        await restorer.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert restorer.lists is not None
        assert await restorer.lists.restore() == 1
        assert restorer.lists.legacy_queue.pending_count == 1
        await restorer.lists.flush()
        assert restorer.lists.legacy_queue.pending_count == 0

        fresh = await _fresh_server_list(restorer, live_mutation_list_id)
        item = (
            next((value for value in fresh.items if value.identifier == item_id), None)
            if fresh is not None
            else None
        )
        assert item is not None and item.name == restored_name
    finally:
        await restorer.close()

    await live_client.lists.refresh()
    await _remove_without_recents(live_client, live_mutation_list_id, [item_id])


@pytest.mark.asyncio
async def test_live_realtime_invalidation_refreshes_disposable_list_from_second_client(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    tokens = live_client.tokens
    assert tokens is not None
    assert live_client.lists is not None

    await live_client.realtime.start()
    mutator = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)
    item_id = ""
    event_task = asyncio.create_task(_wait_for_shopping_invalidation(live_client))
    try:
        await mutator.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert mutator.lists is not None
        created = await mutator.lists.add_item(
            live_mutation_list_id, f"sdk-realtime-{uuid4().hex}"
        )
        item_id = str(created.identifier)

        event = await event_task
        assert event.message == "refresh-shopping-lists"
        await _wait_for_item_state(
            live_client,
            live_mutation_list_id,
            item_id,
            present=True,
        )
    finally:
        if not event_task.done():
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
        if item_id:
            if mutator.lists is not None:
                await mutator.lists.refresh()
            if mutator.lists is not None and mutator.lists.item(live_mutation_list_id, item_id):
                await mutator.lists.bulk_remove_items(
                    live_mutation_list_id,
                    [item_id],
                    remember_recent=False,
                )
        await mutator.close()
        await live_client.realtime.stop()


@pytest.mark.asyncio
async def test_live_realtime_automatic_reconnect_catches_up_disposable_change(
    live_client, live_mutation_list_id: str, monkeypatch
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    tokens = live_client.tokens
    assert tokens is not None
    assert live_client.lists is not None

    session = live_client.transport.session
    original_ws_connect = session.ws_connect
    captured_ws = []
    captured = asyncio.Event()

    class CapturingContext:
        def __init__(self, inner):
            self.inner = inner

        async def __aenter__(self):
            ws = await self.inner.__aenter__()
            captured_ws.append(ws)
            captured.set()
            return ws

        async def __aexit__(self, *args):
            return await self.inner.__aexit__(*args)

    def capture_ws_connect(*args, **kwargs):
        return CapturingContext(original_ws_connect(*args, **kwargs))

    monkeypatch.setattr(session, "ws_connect", capture_ws_connect)
    reconnect_seen = asyncio.Event()
    reconnect_callback_count = len(live_client.realtime._reconnect_callbacks)
    live_client.realtime.add_reconnect_listener(reconnect_seen.set)

    mutator = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)
    item_id = ""
    try:
        await live_client.realtime.start()
        await asyncio.wait_for(captured.wait(), timeout=15.0)
        assert captured_ws

        # Keep enough retry window to make the disposable mutation while the listener is
        # definitely offline. The connection's +2s retry-reset timer is cancelled on close.
        live_client.realtime._retry_delay = 2.0
        connection = captured_ws[-1]._conn
        assert connection is not None
        transport = connection.transport
        assert transport is not None
        transport.abort()
        await _wait_for_realtime_connected_state(live_client, connected=False)

        await mutator.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert mutator.lists is not None
        created = await mutator.lists.add_item(
            live_mutation_list_id, f"sdk-reconnect-{uuid4().hex}"
        )
        item_id = str(created.identifier)
        assert live_client.lists.item(live_mutation_list_id, item_id) is None

        await asyncio.wait_for(reconnect_seen.wait(), timeout=15.0)
        await _wait_for_realtime_connected_state(live_client, connected=True)
        await _wait_for_item_state(
            live_client,
            live_mutation_list_id,
            item_id,
            present=True,
        )
    finally:
        del live_client.realtime._reconnect_callbacks[reconnect_callback_count:]
        if item_id and mutator.lists is not None:
            await mutator.lists.refresh()
            if mutator.lists.item(live_mutation_list_id, item_id) is not None:
                await mutator.lists.bulk_remove_items(
                    live_mutation_list_id,
                    [item_id],
                    remember_recent=False,
                )
        await mutator.close()
        await live_client.realtime.stop()


@pytest.mark.asyncio
async def test_live_bulk_remove_records_disposable_recent_items(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    names = {f"sdk-bulk-recent-{uuid4().hex}" for _ in range(2)}
    created = await live_client.lists.add_items(
        live_mutation_list_id, [PB.ListItem(name=name) for name in names]
    )
    ids = [str(item.identifier) for item in created]
    try:
        removed = await live_client.lists.bulk_remove_items(live_mutation_list_id, ids)
        assert {str(item.name) for item in removed} == names
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        assert not names & {str(item.name) for item in fresh.items}

        starters = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        recent = starters["recent"]
        assert recent is not None
        assert names.issubset({str(item.name) for item in recent.items})
    finally:
        await _remove_named_starter_items(live_client, live_mutation_list_id, names)
        current = live_client.lists.get(live_mutation_list_id)
        if current is not None:
            leftovers = [
                str(item.identifier) for item in current.items if str(item.name) in names
            ]
            if leftovers:
                await _remove_without_recents(live_client, live_mutation_list_id, leftovers)


@pytest.mark.asyncio
async def test_live_disposable_custom_starter_create_reorder_remove_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.starter_lists is not None
    await live_client.starter_lists.refresh()
    original_order = await _fresh_starter_order(live_client)
    first_id = uuid4().hex
    second_id = uuid4().hex
    first_name = f"SDK Disposable Starter A {first_id[:8]}"
    second_name = f"SDK Disposable Starter B {second_id[:8]}"
    created_ids = {first_id, second_id}

    try:
        await live_client.starter_lists.create(
            first_name,
            list_id=first_id,
            user_list_id=live_mutation_list_id,
            starter_type=PB.StarterList.Type.UserType,
        )
        await live_client.starter_lists.create(
            second_name,
            list_id=second_id,
            user_list_id=live_mutation_list_id,
            starter_type=PB.StarterList.Type.UserType,
        )

        tokens = live_client.tokens
        assert tokens is not None
        fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)
        try:
            await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
            assert fresh.starter_lists is not None
            first = fresh.starter_lists.get(first_id)
            second = fresh.starter_lists.get(second_id)
            assert first is not None and second is not None
            assert first.listId == live_mutation_list_id
            assert second.listId == live_mutation_list_id
            assert first.name == first_name and second.name == second_name
        finally:
            await fresh.close()

        after_create_order = await _fresh_starter_order(live_client)
        untouched = [identifier for identifier in after_create_order if identifier not in created_ids]
        if untouched != original_order:
            pytest.fail(
                "starter-list ordering changed outside the two disposable temporary IDs; refusing reorder",
                pytrace=False,
            )

        swapped_order = original_order + [second_id, first_id]
        await live_client.starter_lists.reorder_lists(swapped_order)
        assert await _fresh_starter_order(live_client) == swapped_order

    finally:
        # The server keeps every extant user starter list represented in ordered IDs, so
        # remove the disposable temporary lists before restoring the exact original order.
        await live_client.starter_lists.refresh()
        for identifier in (first_id, second_id):
            if live_client.starter_lists.get(identifier) is not None:
                await live_client.starter_lists.remove(identifier)
        current_order = await _fresh_starter_order(live_client)
        if current_order != original_order:
            await live_client.starter_lists.reorder_lists(original_order)

    tokens = live_client.tokens
    assert tokens is not None
    fresh = AnyListClient(base_url=live_client.transport.base_url, tokens=tokens)
    try:
        await fresh.load(realtime=False, load_tag_data=False, restore_pending=False)
        assert fresh.starter_lists is not None
        assert fresh.starter_lists.get(first_id) is None
        assert fresh.starter_lists.get(second_id) is None
        await fresh.starter_lists.refresh_order()
        assert list(fresh.state.ordered_starter_list_ids) == original_order
    finally:
        await fresh.close()


@pytest.mark.asyncio
async def test_live_disposable_folder_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.folders is not None
    root_id, initial_folders = await _fresh_folder_state(live_client)
    if not root_id:
        pytest.fail("server has no root folder; refusing folder mutation", pytrace=False)

    parent_matches: list[str] = []
    for folder_id, folder in initial_folders.items():
        assert isinstance(folder, PB.PBListFolder)
        if any(
            int(item.itemType) == 0 and str(item.identifier) == live_mutation_list_id
            for item in folder.items
        ):
            parent_matches.append(folder_id)
    if len(parent_matches) != 1:
        pytest.fail(
            "disposable shopping list does not have exactly one folder parent; refusing folder mutation",
            pytrace=False,
        )

    original_parent_id = parent_matches[0]
    original_parent = initial_folders[original_parent_id]
    assert isinstance(original_parent, PB.PBListFolder)
    original_items: list[object] = []
    for item in original_parent.items:
        clone = PB.PBListFolderItem()
        clone.CopyFrom(item)
        original_items.append(clone)

    temp_parent = await live_client.folders.create(
        f"SDK Disposable Folder {uuid4().hex[:8]}",
        parent_id=original_parent_id,
        hex_color="A1B2C3",
    )
    temp_parent_id = str(temp_parent.identifier)
    temp_child_id = ""
    list_item = PB.PBListFolderItem(identifier=live_mutation_list_id, itemType=0)

    try:
        await live_client.folders.rename(
            temp_parent_id, f"SDK Disposable Folder Renamed {uuid4().hex[:8]}"
        )
        await live_client.folders.set_hex_color(temp_parent_id, "C3B2A1")
        await live_client.folders.set_icon(temp_parent_id, "star")
        await live_client.folders.set_lists_sort_order(temp_parent_id, 2)
        await live_client.folders.set_folder_sort_position(temp_parent_id, 4)

        temp_child = await live_client.folders.create(
            f"SDK Disposable Child {uuid4().hex[:8]}", parent_id=temp_parent_id
        )
        temp_child_id = str(temp_child.identifier)

        _, folders = await _fresh_folder_state(live_client)
        parent = folders.get(original_parent_id)
        temp = folders.get(temp_parent_id)
        child = folders.get(temp_child_id)
        assert isinstance(parent, PB.PBListFolder)
        assert isinstance(temp, PB.PBListFolder)
        assert isinstance(child, PB.PBListFolder)
        untouched = [
            (int(item.itemType), str(item.identifier))
            for item in parent.items
            if not (int(item.itemType) == 1 and str(item.identifier) == temp_parent_id)
        ]
        assert untouched == [
            (int(item.itemType), str(item.identifier)) for item in original_items
        ]
        assert temp.folderSettings.folderHexColor == "C3B2A1"
        assert temp.folderSettings.icon.iconName == "star"
        assert temp.folderSettings.listsSortOrder == 2
        assert temp.folderSettings.folderSortPosition == 4

        await live_client.folders.move(
            [list_item], original_parent_id, temp_parent_id
        )
        _, folders = await _fresh_folder_state(live_client)
        parent = folders[original_parent_id]
        temp = folders[temp_parent_id]
        assert isinstance(parent, PB.PBListFolder)
        assert isinstance(temp, PB.PBListFolder)
        assert not any(
            int(item.itemType) == 0 and str(item.identifier) == live_mutation_list_id
            for item in parent.items
        )
        assert any(
            int(item.itemType) == 0 and str(item.identifier) == live_mutation_list_id
            for item in temp.items
        )

        desired_temp_items = [
            PB.PBListFolderItem(identifier=temp_child_id, itemType=1),
            PB.PBListFolderItem(identifier=live_mutation_list_id, itemType=0),
        ]
        await live_client.folders.reorder(temp_parent_id, desired_temp_items)
        _, folders = await _fresh_folder_state(live_client)
        temp = folders[temp_parent_id]
        assert isinstance(temp, PB.PBListFolder)
        assert [
            (int(item.itemType), str(item.identifier)) for item in temp.items
        ] == [(1, temp_child_id), (0, live_mutation_list_id)]

        await live_client.folders.move([list_item], temp_parent_id, original_parent_id)

        # Keep every original parent item in its exact original order while the temporary
        # folder still exists as one disposable trailing entry.
        parent_with_temp = [PB.PBListFolderItem() for _ in original_items]
        for target, source in zip(parent_with_temp, original_items):
            target.CopyFrom(source)
        parent_with_temp.append(
            PB.PBListFolderItem(identifier=temp_parent_id, itemType=1)
        )
        await live_client.folders.reorder(original_parent_id, parent_with_temp)

        # The temporary child is empty; recursive delete therefore exercises child + parent
        # folder deletion without deleting any shopping list.
        await live_client.folders.delete_folder(temp_parent_id, original_parent_id)
    finally:
        await live_client.folders.refresh()
        # If a failure occurred while the disposable list was inside a temporary folder,
        # move only that list back before deleting the disposable folder tree.
        if temp_parent_id and live_client.folders.get(temp_parent_id) is not None:
            temp = live_client.folders.get(temp_parent_id)
            assert temp is not None
            if any(
                int(item.itemType) == 0 and str(item.identifier) == live_mutation_list_id
                for item in temp.items
            ):
                await live_client.folders.move(
                    [list_item], temp_parent_id, original_parent_id
                )
            if live_client.folders.get(temp_parent_id) is not None:
                await live_client.folders.delete_folder(
                    temp_parent_id, original_parent_id
                )
        await live_client.folders.refresh()
        parent = live_client.folders.get(original_parent_id)
        if parent is not None:
            current = [
                (int(item.itemType), str(item.identifier)) for item in parent.items
            ]
            expected = [
                (int(item.itemType), str(item.identifier)) for item in original_items
            ]
            if current != expected:
                await live_client.folders.reorder(
                    original_parent_id,
                    [
                        PB.PBListFolderItem(
                            identifier=str(item.identifier), itemType=int(item.itemType)
                        )
                        for item in original_items
                    ],
                )

    _, final_folders = await _fresh_folder_state(live_client)
    assert temp_parent_id not in final_folders
    assert temp_child_id not in final_folders
    final_parent = final_folders[original_parent_id]
    assert isinstance(final_parent, PB.PBListFolder)
    assert [
        (int(item.itemType), str(item.identifier)) for item in final_parent.items
    ] == [(int(item.itemType), str(item.identifier)) for item in original_items]


@pytest.mark.asyncio
async def test_live_list_rename_round_trip(live_client, live_mutation_list_id: str) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    temporary = f"{DISPOSABLE_LIST_NAME} [{uuid4().hex[:8]}]"
    try:
        await live_client.lists.rename(live_mutation_list_id, temporary)
        assert live_client.lists.legacy_queue.pending_count == 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None and fresh.name == temporary
    finally:
        # Restore the exact guard name so subsequent tests can run. This is still the same
        # explicitly authorized disposable list.
        current = live_client.lists.get(live_mutation_list_id)
        if current is not None and str(current.name) != DISPOSABLE_LIST_NAME:
            await live_client.lists.rename(live_mutation_list_id, DISPOSABLE_LIST_NAME)
    restored = await _fresh_server_list(live_client, live_mutation_list_id)
    assert restored is not None and restored.name == DISPOSABLE_LIST_NAME


@pytest.mark.asyncio
async def test_live_add_remove_item_round_trip(live_client, live_mutation_list_id: str) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    marker = f"anylist-sdk-live-{uuid4().hex}"
    created = await live_client.lists.add_item(live_mutation_list_id, marker)
    item_id = str(created.identifier)

    try:
        assert live_client.lists.legacy_queue.pending_count == 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        persisted = next((item for item in fresh.items if item.identifier == item_id), None) if fresh else None
        assert persisted is not None and persisted.name == marker
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, [item_id])

    fresh = await _fresh_server_list(live_client, live_mutation_list_id)
    assert fresh is not None
    assert all(item.identifier != item_id for item in fresh.items)


@pytest.mark.asyncio
async def test_live_direct_shopping_queue_wrappers_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    created = await live_client.lists.add_item(
        live_mutation_list_id,
        f"sdk-direct-queue-{uuid4().hex}",
    )
    item_id = str(created.identifier)
    names = [
        f"sdk-operation-{uuid4().hex}",
        f"sdk-raw-legacy-{uuid4().hex}",
        f"sdk-queue-add-{uuid4().hex}",
    ]
    try:
        await live_client.lists.operation(
            "set-list-item-name",
            listId=live_mutation_list_id,
            listItemId=item_id,
            updatedValue=names[0],
            flush=False,
        )
        assert live_client.lists.legacy_queue.pending_count == 1
        await live_client.lists.flush()
        first = await _fresh_server_list(live_client, live_mutation_list_id)
        first_item = (
            next((item for item in first.items if item.identifier == item_id), None)
            if first is not None
            else None
        )
        assert first_item is not None and first_item.name == names[0]

        await live_client.lists.raw_legacy_operation(
            "set-list-item-name",
            listId=live_mutation_list_id,
            listItemId=item_id,
            updatedValue=names[1],
            flush=False,
        )
        assert live_client.lists.legacy_queue.pending_count == 1
        await live_client.lists.flush()
        second = await _fresh_server_list(live_client, live_mutation_list_id)
        second_item = (
            next((item for item in second.items if item.identifier == item_id), None)
            if second is not None
            else None
        )
        assert second_item is not None and second_item.name == names[1]

        await live_client.lists.legacy_queue.add(
            "set-list-item-name",
            listId=live_mutation_list_id,
            listItemId=item_id,
            updatedValue=names[2],
            flush=True,
        )
        third = await _fresh_server_list(live_client, live_mutation_list_id)
        third_item = (
            next((item for item in third.items if item.identifier == item_id), None)
            if third is not None
            else None
        )
        assert third_item is not None and third_item.name == names[2]
    finally:
        await live_client.lists.refresh()
        await _remove_without_recents(live_client, live_mutation_list_id, [item_id])


@pytest.mark.asyncio
async def test_live_generic_domain_service_round_trip_on_disposable_starter(
    live_client, live_mutation_list_id: str
) -> None:
    await _ensure_disposable_starters(live_client, live_mutation_list_id)
    assert live_client.starter_lists is not None
    user_id = live_client.user_id
    assert user_id is not None

    starter_id = favorite_list_id(live_mutation_list_id)
    starter = live_client.starter_lists.get(starter_id)
    assert starter is not None
    original_name = str(starter.name)
    temporary_name = f"SDK Generic Starter {uuid4().hex[:8]}"

    generic = GenericDomainService(
        live_client.transport,
        live_client.state,
        user_id=user_id,
        queue_id=f"{user_id}:sdk-generic-starter-live",
        endpoint="/data/starter-lists/update",
        operation_type="PBStarterListOperation",
        operation_list_type="PBStarterListOperationList",
    )

    generic.pause()
    try:
        await generic.operation(
            "rename-list",
            listId=starter_id,
            updatedValue=temporary_name,
            flush=True,
        )
        assert generic.queue.paused
        assert generic.queue.pending_count == 1

        before = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        before_starter = before["favorite"]
        assert before_starter is not None
        assert str(before_starter.name) == original_name

        await generic.resume(flush=True)
        assert not generic.queue.paused
        assert generic.queue.pending_count == 0

        after = await _fresh_disposable_starters(live_client, live_mutation_list_id)
        after_starter = after["favorite"]
        assert after_starter is not None
        assert str(after_starter.name) == temporary_name
    finally:
        while generic.queue.paused:
            await generic.resume(flush=True)
        if generic.queue.pending_count:
            await generic.flush()
        await live_client.starter_lists.refresh()
        current = live_client.starter_lists.get(starter_id)
        if current is not None and str(current.name) != original_name:
            await live_client.starter_lists.rename(starter_id, original_name)

    restored = await _fresh_disposable_starters(live_client, live_mutation_list_id)
    restored_starter = restored["favorite"]
    assert restored_starter is not None
    assert str(restored_starter.name) == original_name


@pytest.mark.asyncio
async def test_live_disposable_global_categories_and_categorized_memory_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    """Exercise only globally disposable objects created by this test, then remove them."""
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    assert live_client.categories is not None
    assert live_client.categorized_items is not None

    marker = uuid4().hex
    category_a_name = f"SDK Disposable Category A {marker}"
    category_b_name = f"SDK Disposable Category B {marker}"
    renamed_a_name = f"SDK Disposable Category A Renamed {marker}"
    grouping_name = f"SDK Disposable Group {marker}"
    renamed_grouping_name = f"SDK Disposable Group Renamed {marker}"
    learned_name = f"sdk-disposable-memory-{marker}"

    category_a_id = category_b_id = grouping_id = ""
    memory_id = ""
    try:
        category_a = await live_client.categories.add_category(category_a_name)
        category_a_id = str(category_a.identifier)
        category_b = await live_client.categories.add_category(category_b_name)
        category_b_id = str(category_b.identifier)

        await live_client.categories.rename_category(category_a_id, renamed_a_name)
        await live_client.categories.set_category_icon(category_a_id, "produce")

        grouping = await live_client.categories.add_grouping(
            grouping_name, [category_a_id, category_b_id]
        )
        grouping_id = str(grouping.identifier)
        await live_client.categories.set_grouping_categories(
            grouping_id, [category_b_id, category_a_id], ordering_only=True
        )
        await live_client.categories.set_grouping_categories(
            grouping_id, [category_a_id, category_b_id]
        )
        await live_client.categories.rename_grouping(grouping_id, renamed_grouping_name)
        await live_client.categories.hide_grouping_from_browse(grouping_id)

        fresh = await _fresh_global_category_state(live_client)
        fresh_a = fresh["categories"].get(category_a_id)
        fresh_b = fresh["categories"].get(category_b_id)
        fresh_group = fresh["groupings"].get(grouping_id)
        assert fresh_a is not None and fresh_b is not None and fresh_group is not None
        assert str(fresh_a.name) == renamed_a_name
        assert str(fresh_a.icon) == "produce"
        assert str(fresh_b.name) == category_b_name
        assert str(fresh_group.name) == renamed_grouping_name
        assert list(fresh_group.categoryIds) == [category_a_id, category_b_id]
        assert bool(fresh_group.shouldHideFromBrowseListCategoryGroupsScreen)

        learned = PB.ListItem(
            listId=live_mutation_list_id,
            name=learned_name,
            categoryMatchId=str(fresh_a.categoryMatchId),
            category="other",
        )
        await live_client.categorized_items.categorize(learned)
        memory_id = live_client.categorized_items.memory_id(learned_name, live_mutation_list_id)

        fresh = await _fresh_global_category_state(live_client)
        remembered = fresh["categorized_items"].get(memory_id)
        assert remembered is not None
        assert str(remembered.name) == learned_name.lower()
        assert str(remembered.categoryMatchId) == str(fresh_a.categoryMatchId)

        assert live_client.categorized_items.lookup(learned_name, live_mutation_list_id) is not None
        changed = await live_client.categorized_items.migrate_category(
            str(fresh_a.categoryMatchId), str(fresh_b.categoryMatchId)
        )
        assert changed >= 1

        fresh = await _fresh_global_category_state(live_client)
        remembered = fresh["categorized_items"].get(memory_id)
        assert remembered is not None
        assert str(remembered.categoryMatchId) == str(fresh_b.categoryMatchId)

        await live_client.categorized_items.refresh()
        current = live_client.state.categorized_items.get(memory_id)
        assert current is not None
        await live_client.categorized_items.remove(current)
        fresh = await _fresh_global_category_state(live_client)
        assert memory_id not in fresh["categorized_items"]
    finally:
        await live_client.categorized_items.refresh()
        if memory_id and memory_id in live_client.state.categorized_items:
            await live_client.categorized_items.remove(
                live_client.state.categorized_items[memory_id]
            )

        await live_client.categories.refresh()
        if grouping_id and grouping_id in live_client.state.category_groupings:
            await live_client.categories.remove_grouping(grouping_id)
        await live_client.categories.refresh()
        for category_id in (category_a_id, category_b_id):
            if category_id and category_id in live_client.state.user_categories:
                await live_client.categories.remove_category(category_id)

    fresh = await _fresh_global_category_state(live_client)
    assert category_a_id not in fresh["categories"]
    assert category_b_id not in fresh["categories"]
    assert grouping_id not in fresh["groupings"]
    if memory_id:
        assert memory_id not in fresh["categorized_items"]
    assert not any(
        marker in str(value.name)
        for value in (*fresh["categories"].values(), *fresh["groupings"].values())
    )


@pytest.mark.asyncio
async def test_live_disposable_recipes_and_collections_round_trip(live_client) -> None:
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    user_id = live_client.user_id
    assert user_id is not None

    service = RecipesService(live_client.transport, live_client.state, user_id=user_id)
    marker = uuid4().hex
    recipe_a_name = f"SDK Disposable Recipe A {marker}"
    recipe_b_name = f"SDK Disposable Recipe B {marker}"
    recipe_a_updated_name = f"SDK Disposable Recipe A Updated {marker}"
    collection_a_name = f"SDK Disposable Collection A {marker}"
    collection_b_name = f"SDK Disposable Collection B {marker}"
    collection_a_renamed = f"SDK Disposable Collection A Renamed {marker}"

    before = await _fresh_recipe_state(live_client)
    original_collection_order = list(before["collection_ids"])
    recipe_a_id = recipe_b_id = collection_a_id = collection_b_id = ""

    try:
        recipe_a = await service.create(
            recipe_a_name,
            ingredients=[PB.PBIngredient(name="SDK Ingredient", rawIngredient="1 cup SDK Ingredient")],
            preparation_steps=["SDK step one", "SDK step two"],
            servings="2",
            source_name="SDK Test",
            source_url="https://example.com/sdk-test",
        )
        recipe_a_id = str(recipe_a.identifier)
        recipe_b = await service.create(recipe_b_name)
        recipe_b_id = str(recipe_b.identifier)

        updated_a = PB.PBRecipe()
        updated_a.CopyFrom(recipe_a)
        updated_a.name = recipe_a_updated_name
        updated_a.note = "SDK disposable note"
        updated_a.rating = 4
        await service.save(updated_a)

        collection_a = await service.create_collection(collection_a_name)
        collection_a_id = str(collection_a.identifier)
        collection_b = await service.create_collection(collection_b_name)
        collection_b_id = str(collection_b.identifier)

        await service.rename_collection(collection_a_id, collection_a_renamed)
        await service.add_to_collection(collection_a_id, [recipe_a_id, recipe_b_id])
        await service.reorder_recipes(collection_a_id, [recipe_b_id, recipe_a_id])
        await service.set_collection_icon(collection_a_id, "produce")
        await service.set_collection_sort(collection_a_id, 1, reversed=True)

        await service.remove_from_collection(collection_a_id, [recipe_a_id])
        await service.add_to_collection(collection_a_id, [recipe_a_id])
        await service.reorder_recipes(collection_a_id, [recipe_a_id, recipe_b_id])

        temporary_order = original_collection_order + [collection_b_id, collection_a_id]
        await service.reorder_collections(temporary_order)

        fresh = await _fresh_recipe_state(live_client)
        fresh_a = fresh["recipes"].get(recipe_a_id)
        fresh_b = fresh["recipes"].get(recipe_b_id)
        fresh_collection_a = fresh["collections"].get(collection_a_id)
        fresh_collection_b = fresh["collections"].get(collection_b_id)
        assert fresh_a is not None and fresh_b is not None
        assert fresh_collection_a is not None and fresh_collection_b is not None
        assert str(fresh_a.name) == recipe_a_updated_name
        assert str(fresh_a.note) == "SDK disposable note"
        assert int(fresh_a.rating) == 4
        assert str(fresh_b.name) == recipe_b_name
        assert str(fresh_collection_a.name) == collection_a_renamed
        assert list(fresh_collection_a.recipeIds) == [recipe_a_id, recipe_b_id]
        assert str(fresh_collection_a.collectionSettings.icon.iconName) == "produce"
        assert int(fresh_collection_a.collectionSettings.recipesSortOrder) == 1
        assert fresh["collection_ids"] == temporary_order

        await service.reorder_collections(
            original_collection_order + [collection_a_id, collection_b_id]
        )
        await service.remove_many([recipe_a_id, recipe_b_id])
        recipe_a_id = recipe_b_id = ""
        await service.remove_collection(collection_a_id)
        collection_a_id = ""
        await service.remove_collection(collection_b_id)
        collection_b_id = ""
    finally:
        await service.refresh()
        for recipe_id in (recipe_a_id, recipe_b_id):
            if recipe_id and recipe_id in live_client.state.recipes:
                await service.remove(recipe_id)

        await service.refresh()
        for collection_id in (collection_a_id, collection_b_id):
            if collection_id and collection_id in live_client.state.recipe_collections:
                await service.remove_collection(collection_id)

        await service.refresh()
        current_order = list(live_client.state.recipe_collection_ids)
        if current_order != original_collection_order:
            await service.reorder_collections(original_collection_order)

    fresh = await _fresh_recipe_state(live_client)
    assert fresh["collection_ids"] == original_collection_order
    assert not any(marker in str(recipe.name) for recipe in fresh["recipes"].values())
    assert not any(marker in str(collection.name) for collection in fresh["collections"].values())


@pytest.mark.asyncio
async def test_live_disposable_meal_plan_events_labels_and_items_round_trip(live_client) -> None:
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    user_id = live_client.user_id
    assert user_id is not None
    assert live_client.state.meal_plan_calendar_id

    service = MealPlanService(live_client.transport, live_client.state, user_id=user_id)
    marker = uuid4().hex
    label_id = event_a_id = event_b_id = item_a_id = item_b_id = ""

    try:
        label = await service.save_label(
            PB.PBCalendarLabel(name=f"SDK Disposable Label {marker}", hexColor="#123456")
        )
        label_id = str(label.identifier)
        updated_label = PB.PBCalendarLabel()
        updated_label.CopyFrom(label)
        updated_label.name = f"SDK Disposable Label Updated {marker}"
        updated_label.hexColor = "#654321"
        await service.save_label(updated_label, is_new=False)
        event_a = await service.save_event(
            PB.PBCalendarEvent(
                eventType=PB.PBCalendarEventType.MealPlanQueueEvent,
                title=f"SDK Disposable Event A {marker}",
            )
        )
        event_a_id = str(event_a.identifier)
        await service.set_event_title(event_a_id, f"SDK Disposable Event A Updated {marker}")
        await service.set_event_details(event_a_id, "SDK disposable event details")
        await service.set_event_icon(event_a_id, "produce")
        await service.set_event_label(event_a_id, label_id)
        await service.set_event_label_sort_index(event_a_id, 7)

        item_a = await service.add_event_list_item(
            event_a_id, PB.PBCalendarEventListItem(name="SDK Item A")
        )
        item_a_id = str(item_a.identifier)
        item_b = await service.add_event_list_item(
            event_a_id, PB.PBCalendarEventListItem(name="SDK Item B")
        )
        item_b_id = str(item_b.identifier)
        await service.set_event_list_item_name(event_a_id, item_a_id, "SDK Item A Renamed")
        await service.set_event_list_item_details(event_a_id, item_a_id, "SDK item details")
        await service.set_event_list_item_quantity(
            event_a_id,
            item_a_id,
            PB.PBItemQuantity(amount="2", unit="count", rawQuantity="2"),
        )
        await service.set_event_list_item_package_size(
            event_a_id,
            item_a_id,
            PB.PBItemPackageSize(size="3", unit="count", rawPackageSize="3"),
        )
        await service.reorder_event_list_items(event_a_id, [item_b_id, item_a_id])

        await service.set_event_date([event_a_id], "2026-09-20")
        await service.set_event_date([event_a_id], None)

        event_b = PB.PBCalendarEvent(
            eventType=PB.PBCalendarEventType.MealPlanQueueEvent,
            title=f"SDK Disposable Event B {marker}",
        )
        await service.save_events([event_b])
        event_b_id = next(
            event_id
            for event_id, value in live_client.state.meal_plan_events.items()
            if str(value.title) == f"SDK Disposable Event B {marker}"
        )

        fresh = await _fresh_meal_plan_state(live_client)
        fresh_event_a = fresh["events"].get(event_a_id)
        fresh_event_b = fresh["events"].get(event_b_id)
        fresh_label = fresh["labels"].get(label_id)
        assert fresh_event_a is not None and fresh_event_b is not None and fresh_label is not None
        assert str(fresh_label.name) == f"SDK Disposable Label Updated {marker}"
        assert str(fresh_event_a.title) == f"SDK Disposable Event A Updated {marker}"
        assert str(fresh_event_a.details) == "SDK disposable event details"
        assert str(fresh_event_a.icon.iconName) == "produce"
        assert str(fresh_event_a.labelId) == label_id
        assert [str(item.identifier) for item in fresh_event_a.eventListItems] == [
            item_b_id,
            item_a_id,
        ]
        fresh_item_a = next(item for item in fresh_event_a.eventListItems if item.identifier == item_a_id)
        assert str(fresh_item_a.name) == "SDK Item A Renamed"
        assert str(fresh_item_a.details) == "SDK item details"
        assert float(fresh_item_a.quantityPb.amount) == 2
        assert float(fresh_item_a.packageSizePb.size) == 3

        await service.remove_event_list_item(event_a_id, item_b_id)
        item_b_id = ""
        await service.delete_event(event_b_id)
        event_b_id = ""
        await service.delete_event(event_a_id)
        event_a_id = ""
        await service.delete_label(label_id)
        label_id = ""
    finally:
        await service.refresh()
        for event_id in (event_a_id, event_b_id):
            if event_id and (
                event_id in live_client.state.meal_plan_events
                or event_id in live_client.state.meal_plan_template_events
            ):
                await service.delete_event(event_id)
        await service.refresh()
        if label_id and label_id in live_client.state.meal_plan_labels:
            await service.delete_label(label_id)

    fresh = await _fresh_meal_plan_state(live_client)
    assert not any(marker in str(event.title) for event in fresh["events"].values())
    assert not any(marker in str(label.name) for label in fresh["labels"].values())


@pytest.mark.asyncio
async def test_live_disposable_meal_plan_templates_and_groups_round_trip(live_client) -> None:
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    user_id = live_client.user_id
    assert user_id is not None
    assert live_client.state.meal_plan_calendar_id

    service = MealPlanService(live_client.transport, live_client.state, user_id=user_id)
    groups = live_client.state.meal_plan_template_groups
    child_group_ids = {
        str(item.identifier)
        for group in groups.values()
        for item in group.items
        if int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Group)
    }
    root_candidates = [
        group
        for group_id, group in groups.items()
        if group_id not in child_group_ids and not str(getattr(group, "name", "") or "")
    ]
    if not root_candidates:
        pytest.skip("no existing unnamed root meal-plan template group available for safe attachment")
    root = root_candidates[0]
    root_id = str(root.identifier)
    original_root_items = [
        PB.PBMealPlanTemplateGroupItem(identifier=str(item.identifier), itemType=int(item.itemType))
        for item in root.items
    ]

    marker = uuid4().hex
    day_a = uuid4().hex
    day_b = uuid4().hex
    day_c = uuid4().hex
    group_a_id = group_b_id = nested_group_id = template_id = template_event_id = ""
    try:
        group_a = await service.create_template_group(
            f"SDK Disposable Template Group A {marker}", root_id, icon="produce"
        )
        group_a_id = str(group_a.identifier)
        group_b = await service.create_template_group(
            f"SDK Disposable Template Group B {marker}", root_id
        )
        group_b_id = str(group_b.identifier)
        nested = await service.create_template_group(
            f"SDK Disposable Nested Group {marker}", group_a_id
        )
        nested_group_id = str(nested.identifier)

        await service.set_template_group_items_sort_order(group_a_id, 1)
        await service.set_template_group_groups_sort_position(group_a_id, 2)

        template = await service.save_template(
            PB.PBMealPlanTemplate(name=f"SDK Disposable Template {marker}"),
            parent_group_id=group_a_id,
        )
        template_id = str(template.identifier)
        await service.set_template_name(template_id, f"SDK Disposable Template Updated {marker}")
        await service.set_template_icon(template_id, "produce")
        await service.add_template_day_ids(template_id, [day_a, day_b])
        assert service.queue.pending_count == 0
        await service.remove_template_day_ids(template_id, [day_a])
        assert service.queue.pending_count == 0
        await service.set_template_day_ids(template_id, [day_b, day_c])
        assert service.queue.pending_count == 0

        group_a_current = live_client.state.meal_plan_template_groups[group_a_id]
        by_id = {str(item.identifier): item for item in group_a_current.items}
        await service.set_ordered_template_group_items(
            group_a_id,
            [by_id[nested_group_id], by_id[template_id]],
        )
        moved_item = PB.PBMealPlanTemplateGroupItem(
            identifier=nested_group_id,
            itemType=PB.PBMealPlanTemplateGroupItem.Type.Group,
        )
        assert await service.move_template_group_items(
            [moved_item], group_a_id, group_b_id
        )
        assert service.queue.pending_count == 0

        template_event = await service.save_event(
            PB.PBCalendarEvent(
                eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
                title=f"SDK Disposable Template Event {marker}",
                templateId=template_id,
                templateDayId=day_b,
            )
        )
        template_event_id = str(template_event.identifier)
        assert service.queue.pending_count == 0
        await service.set_template_day_id_for_events([template_event_id], day_c)
        assert service.queue.pending_count == 0

        fresh = await _fresh_meal_plan_state(live_client)
        fresh_template = fresh["templates"].get(template_id)
        fresh_event = fresh["template_events"].get(template_event_id)
        fresh_group_a = fresh["groups"].get(group_a_id)
        fresh_group_b = fresh["groups"].get(group_b_id)
        fresh_nested = fresh["groups"].get(nested_group_id)
        assert fresh_template is not None and fresh_event is not None
        assert fresh_group_a is not None and fresh_group_b is not None and fresh_nested is not None
        assert str(fresh_template.name) == f"SDK Disposable Template Updated {marker}"
        assert str(fresh_template.icon.iconName) == "produce"
        assert list(fresh_template.dayIds) == [day_b, day_c]
        assert str(fresh_event.templateDayId) == day_c
        assert [str(item.identifier) for item in fresh_group_a.items] == [template_id]
        assert nested_group_id in [str(item.identifier) for item in fresh_group_b.items]
        assert int(fresh_group_a.groupSettings.itemsSortOrder) == 1
        assert int(fresh_group_a.groupSettings.groupsSortPosition) == 2

        await service.delete_template(template_id)
        template_id = template_event_id = ""
        await service.delete_template_group(group_a_id, root_id)
        group_a_id = ""
        await service.delete_template_group(group_b_id, root_id)
        group_b_id = nested_group_id = ""
    finally:
        await _cleanup_meal_plan_template_marker(live_client, service, marker)
        await service.refresh()
        current_root = live_client.state.meal_plan_template_groups.get(root_id)
        if current_root is not None:
            current_items = [(str(item.identifier), int(item.itemType)) for item in current_root.items]
            original_items = [(str(item.identifier), int(item.itemType)) for item in original_root_items]
            if current_items != original_items:
                await service.set_ordered_template_group_items(root_id, original_root_items)

    fresh = await _fresh_meal_plan_state(live_client)
    assert not any(marker in str(template.name) for template in fresh["templates"].values())
    assert not any(marker in str(group.name) for group in fresh["groups"].values())
    assert not any(marker in str(event.title) for event in fresh["template_events"].values())
    fresh_root = fresh["groups"].get(root_id)
    assert fresh_root is not None
    assert [(str(item.identifier), int(item.itemType)) for item in fresh_root.items] == [
        (str(item.identifier), int(item.itemType)) for item in original_root_items
    ]


@pytest.mark.asyncio
async def test_live_disposable_delete_events_for_recipe_and_recipe_remove(live_client) -> None:
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    user_id = live_client.user_id
    assert user_id is not None
    assert live_client.state.meal_plan_calendar_id

    recipes = RecipesService(live_client.transport, live_client.state, user_id=user_id)
    meal_plan = MealPlanService(live_client.transport, live_client.state, user_id=user_id)
    groups = live_client.state.meal_plan_template_groups
    child_group_ids = {
        str(item.identifier)
        for group in groups.values()
        for item in group.items
        if int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Group)
    }
    roots = [
        group
        for group_id, group in groups.items()
        if group_id not in child_group_ids and not str(getattr(group, "name", "") or "")
    ]
    if not roots:
        pytest.skip("no existing unnamed root meal-plan template group available")
    root_id = str(roots[0].identifier)

    marker = uuid4().hex
    day_id = uuid4().hex
    recipe_id = group_id = template_id = normal_event_id = template_event_id = ""
    try:
        recipe = await recipes.create(f"SDK Disposable Delete Recipe {marker}")
        recipe_id = str(recipe.identifier)
        group = await meal_plan.create_template_group(
            f"SDK Disposable Delete Group {marker}", root_id
        )
        group_id = str(group.identifier)
        template = await meal_plan.save_template(
            PB.PBMealPlanTemplate(name=f"SDK Disposable Delete Template {marker}"),
            parent_group_id=group_id,
        )
        template_id = str(template.identifier)
        await meal_plan.add_template_day_ids(template_id, [day_id])
        assert meal_plan.queue.pending_count == 0

        normal_event = await meal_plan.save_event(
            PB.PBCalendarEvent(
                eventType=PB.PBCalendarEventType.MealPlanQueueEvent,
                title=f"SDK Disposable Recipe Event {marker}",
                recipeId=recipe_id,
            )
        )
        normal_event_id = str(normal_event.identifier)
        template_event = await meal_plan.save_event(
            PB.PBCalendarEvent(
                eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
                title=f"SDK Disposable Recipe Template Event {marker}",
                recipeId=recipe_id,
                templateId=template_id,
                templateDayId=day_id,
            )
        )
        template_event_id = str(template_event.identifier)
        assert meal_plan.queue.pending_count == 0

        fresh = await _fresh_meal_plan_state(live_client)
        assert normal_event_id in fresh["events"]
        assert template_event_id in fresh["template_events"]

        await meal_plan.delete_events_for_recipe_id(recipe_id)
        assert meal_plan.queue.pending_count == 0
        fresh = await _fresh_meal_plan_state(live_client)
        assert normal_event_id not in fresh["events"]
        assert template_event_id not in fresh["template_events"]
        normal_event_id = template_event_id = ""

        await recipes.remove(recipe_id)
        recipe_id = ""
        fresh_recipes = await _fresh_recipe_state(live_client)
        assert not any(marker in str(recipe.name) for recipe in fresh_recipes["recipes"].values())
    finally:
        await meal_plan.refresh()
        for event_id in (normal_event_id, template_event_id):
            if event_id and (
                event_id in live_client.state.meal_plan_events
                or event_id in live_client.state.meal_plan_template_events
            ):
                await meal_plan.delete_event(event_id)
        await _cleanup_meal_plan_template_marker(live_client, meal_plan, marker)
        await recipes.refresh()
        if recipe_id and recipe_id in live_client.state.recipes:
            await recipes.remove(recipe_id)

    fresh_meal = await _fresh_meal_plan_state(live_client)
    fresh_recipes = await _fresh_recipe_state(live_client)
    assert not any(marker in str(event.title) for event in fresh_meal["events"].values())
    assert not any(marker in str(event.title) for event in fresh_meal["template_events"].values())
    assert not any(marker in str(group.name) for group in fresh_meal["groups"].values())
    assert not any(marker in str(template.name) for template in fresh_meal["templates"].values())
    assert not any(marker in str(recipe.name) for recipe in fresh_recipes["recipes"].values())


@pytest.mark.asyncio
async def test_live_item_text_and_upc_round_trip(live_client, live_mutation_list_id: str) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    created = await live_client.lists.add_item(
        live_mutation_list_id,
        f"sdk-text-{uuid4().hex}",
    )
    item_id = str(created.identifier)
    final_name = f"sdk-renamed-{uuid4().hex}"
    details = f"details-{uuid4().hex}"
    upc = "012345678905"
    try:
        await live_client.lists.rename_item(live_mutation_list_id, item_id, final_name)
        await live_client.lists.set_details(live_mutation_list_id, item_id, details)
        await live_client.lists.set_product_upc(live_mutation_list_id, item_id, upc)
        assert live_client.lists.legacy_queue.pending_count == 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        item = next((x for x in fresh.items if x.identifier == item_id), None) if fresh else None
        assert item is not None
        assert item.name == final_name
        assert item.details == details
        assert item.productUpc == upc
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, [item_id])


@pytest.mark.asyncio
async def test_live_item_photo_reference_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    created = await live_client.lists.add_item(
        live_mutation_list_id, f"sdk-photo-ref-{uuid4().hex}"
    )
    item_id = str(created.identifier)
    photo_id = f"sdk-photo-{uuid4().hex}"
    try:
        await live_client.lists.set_photo(live_mutation_list_id, item_id, photo_id)
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        item = next((x for x in fresh.items if x.identifier == item_id), None) if fresh else None
        assert item is not None
        assert list(item.photoIds) == [photo_id]

        await live_client.lists.set_photo(live_mutation_list_id, item_id, None)
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        item = next((x for x in fresh.items if x.identifier == item_id), None) if fresh else None
        assert item is not None
        assert list(item.photoIds) == []
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, [item_id])


@pytest.mark.asyncio
async def test_live_list_password_round_trip_when_field_materialized(
    live_client, live_mutation_list_id: str
) -> None:
    value = await _load_and_require_disposable(live_client, live_mutation_list_id)
    if not value.HasField("password"):
        pytest.skip("server has not materialized password; refusing an irreversible presence change")
    assert live_client.lists is not None
    original = str(value.password)
    temporary = f"sdk-{uuid4().hex}"
    try:
        await live_client.lists.set_password(live_mutation_list_id, temporary)
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None and fresh.HasField("password")
        assert fresh.password == temporary
    finally:
        await live_client.lists.set_password(live_mutation_list_id, original)
    restored = await _fresh_server_list(live_client, live_mutation_list_id)
    assert restored is not None and restored.HasField("password")
    assert restored.password == original


@pytest.mark.asyncio
async def test_live_item_quantity_package_and_overrides_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    created = await live_client.lists.add_item(
        live_mutation_list_id,
        f"sdk-quantity-{uuid4().hex}",
    )
    item_id = str(created.identifier)
    quantity = PB.PBItemQuantity(amount="2", unit="cup", rawQuantity="2 cups")
    package = PB.PBItemPackageSize(size="12", unit="oz", rawPackageSize="12 oz")
    price_quantity = PB.PBItemQuantity(amount="1", unit="each", rawQuantity="1 each")
    price_package = PB.PBItemPackageSize(size="6", unit="oz", rawPackageSize="6 oz")
    try:
        await live_client.lists.set_quantity(live_mutation_list_id, item_id, quantity)
        await live_client.lists.set_package_size(live_mutation_list_id, item_id, package)
        await live_client.lists.set_quantity_override(live_mutation_list_id, item_id, True)
        await live_client.lists.set_package_override(live_mutation_list_id, item_id, True)
        await live_client.lists.set_price_quantity(live_mutation_list_id, item_id, price_quantity)
        await live_client.lists.set_price_package_size(live_mutation_list_id, item_id, price_package)
        await live_client.lists.set_price_quantity_override(live_mutation_list_id, item_id, True)
        await live_client.lists.set_price_package_override(live_mutation_list_id, item_id, True)
        assert live_client.lists.legacy_queue.pending_count == 0

        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        item = next((x for x in fresh.items if x.identifier == item_id), None) if fresh else None
        assert item is not None
        assert item.quantityPb.SerializeToString() == quantity.SerializeToString()
        assert item.packageSizePb.SerializeToString() == package.SerializeToString()
        assert item.priceQuantityPb.SerializeToString() == price_quantity.SerializeToString()
        assert item.pricePackageSizePb.SerializeToString() == price_package.SerializeToString()
        assert item.itemQuantityShouldOverrideIngredientQuantity is True
        assert item.itemPackageSizeShouldOverrideIngredientPackageSize is True
        assert item.priceQuantityShouldOverrideItemQuantity is True
        assert item.pricePackageSizeShouldOverrideItemPackageSize is True
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, [item_id])


@pytest.mark.asyncio
async def test_live_manual_item_order_round_trip(live_client, live_mutation_list_id: str) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    ids: list[str] = []
    items: list[ListItem] = []
    for index in range(3):
        item = PB.ListItem(name=f"sdk-order-{index}-{uuid4().hex}")
        items.append(item)
    created = await live_client.lists.add_items(live_mutation_list_id, items)
    ids = [str(item.identifier) for item in created]
    try:
        # Manual ordering is required for move/reorder semantics. Keep this setting on the
        # disposable list only; list sort behavior itself will be restored by later dedicated
        # settings tests before this matrix marks it fully live-verified.
        await live_client.lists.reorder_items(live_mutation_list_id, list(reversed(ids)))
        assert live_client.lists.legacy_queue.pending_count == 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        fresh_ids = [str(x.identifier) for x in fresh.items if str(x.identifier) in set(ids)]
        assert fresh_ids == list(reversed(ids))
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, ids)


@pytest.mark.asyncio
async def test_live_store_and_store_filter_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    store_id = uuid4().hex
    filter_id = uuid4().hex
    store = PB.PBStore(
        identifier=store_id,
        listId=live_mutation_list_id,
        name=f"SDK Store {store_id[:8]}",
    )
    store_filter = PB.PBStoreFilter(
        identifier=filter_id,
        listId=live_mutation_list_id,
        name=f"SDK Filter {filter_id[:8]}",
        storeIds=[store_id],
        includesUnassignedItems=True,
    )
    try:
        await live_client.lists.save_store(live_mutation_list_id, store, is_new=True)
        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        persisted_store = snapshot["stores"].get(store_id)
        assert persisted_store is not None
        assert persisted_store.name == store.name

        current_store = live_client.state.list_stores[live_mutation_list_id][store_id]
        renamed_store = PB.PBStore()
        renamed_store.CopyFrom(current_store)
        renamed_store.name = f"SDK Store Renamed {store_id[:8]}"
        await live_client.lists.save_store(
            live_mutation_list_id, renamed_store, is_new=False
        )
        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        assert snapshot["stores"][store_id].name == renamed_store.name

        await live_client.lists.save_store_filter(
            live_mutation_list_id, store_filter, is_new=True
        )
        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        persisted_filter = snapshot["store_filters"].get(filter_id)
        assert persisted_filter is not None
        assert list(persisted_filter.storeIds) == [store_id]

        current_filter = live_client.state.list_store_filters[live_mutation_list_id][filter_id]
        renamed_filter = PB.PBStoreFilter()
        renamed_filter.CopyFrom(current_filter)
        renamed_filter.name = f"SDK Filter Renamed {filter_id[:8]}"
        renamed_filter.includesUnassignedItems = False
        await live_client.lists.save_store_filter(
            live_mutation_list_id, renamed_filter, is_new=False
        )
        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        assert snapshot["store_filters"][filter_id].name == renamed_filter.name
        assert snapshot["store_filters"][filter_id].includesUnassignedItems is False
    finally:
        current_filter = live_client.state.list_store_filters.get(
            live_mutation_list_id, {}
        ).get(filter_id)
        if current_filter is not None:
            await live_client.lists.delete_store_filter(
                live_mutation_list_id, current_filter
            )
        current_store = live_client.state.list_stores.get(live_mutation_list_id, {}).get(
            store_id
        )
        if current_store is not None:
            await live_client.lists.delete_store(live_mutation_list_id, current_store)

    snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
    assert store_id not in snapshot["stores"]
    assert filter_id not in snapshot["store_filters"]


@pytest.mark.asyncio
async def test_live_store_and_filter_ordering_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    store_ids = [uuid4().hex, uuid4().hex]
    filter_ids = [uuid4().hex, uuid4().hex]
    stores = [
        PB.PBStore(
            identifier=store_id,
            listId=live_mutation_list_id,
            name=f"SDK Ordered Store {index} {store_id[:8]}",
        )
        for index, store_id in enumerate(store_ids)
    ]
    filters = [
        PB.PBStoreFilter(
            identifier=filter_id,
            listId=live_mutation_list_id,
            name=f"SDK Ordered Filter {index} {filter_id[:8]}",
            storeIds=[store_ids[index]],
        )
        for index, filter_id in enumerate(filter_ids)
    ]
    try:
        for store in stores:
            await live_client.lists.save_store(
                live_mutation_list_id, store, is_new=True
            )
        for store_filter in filters:
            await live_client.lists.save_store_filter(
                live_mutation_list_id, store_filter, is_new=True
            )

        await live_client.lists.set_sorted_store_ids(
            live_mutation_list_id, list(reversed(store_ids))
        )
        await live_client.lists.set_sorted_store_filter_ids(
            live_mutation_list_id, list(reversed(filter_ids))
        )

        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        assert snapshot["stores"][store_ids[1]].sortIndex < snapshot["stores"][store_ids[0]].sortIndex
        assert (
            snapshot["store_filters"][filter_ids[1]].sortIndex
            < snapshot["store_filters"][filter_ids[0]].sortIndex
        )
    finally:
        for filter_id in filter_ids:
            current = live_client.state.list_store_filters.get(
                live_mutation_list_id, {}
            ).get(filter_id)
            if current is not None:
                await live_client.lists.delete_store_filter(
                    live_mutation_list_id, current
                )
        for store_id in store_ids:
            current = live_client.state.list_stores.get(
                live_mutation_list_id, {}
            ).get(store_id)
            if current is not None:
                await live_client.lists.delete_store(live_mutation_list_id, current)

    snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
    assert not set(store_ids) & set(snapshot["stores"])
    assert not set(filter_ids) & set(snapshot["store_filters"])


@pytest.mark.asyncio
async def test_live_category_group_category_and_rule_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    group_id = uuid4().hex
    category_a_id = uuid4().hex
    category_b_id = uuid4().hex
    caller_rule_id = uuid4().hex
    group = PB.PBListCategoryGroup(
        identifier=group_id,
        listId=live_mutation_list_id,
        name=f"SDK Categories {group_id[:8]}",
        defaultCategoryId=category_a_id,
    )
    group.categories.add(
        identifier=category_a_id,
        categoryGroupId=group_id,
        listId=live_mutation_list_id,
        name=f"SDK Category A {category_a_id[:8]}",
        sortIndex=0,
    )
    group.categories.add(
        identifier=category_b_id,
        categoryGroupId=group_id,
        listId=live_mutation_list_id,
        name=f"SDK Category B {category_b_id[:8]}",
        sortIndex=1,
    )
    try:
        await live_client.lists.save_category_group(group)
        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        assert group_id in snapshot["category_groups"]
        assert category_a_id in snapshot["categories"]
        assert category_b_id in snapshot["categories"]

        current_group = live_client.state.list_category_groups[live_mutation_list_id][group_id]
        await live_client.lists.rename_category_group(
            current_group, f"SDK Categories Renamed {group_id[:8]}"
        )
        current_a = live_client.state.list_categories[live_mutation_list_id][category_a_id]
        await live_client.lists.rename_list_category(
            current_a, f"SDK Category A Renamed {category_a_id[:8]}"
        )
        current_b = live_client.state.list_categories[live_mutation_list_id][category_b_id]
        await live_client.lists.set_list_category_icon(current_b, "other")
        current_group = live_client.state.list_category_groups[live_mutation_list_id][group_id]
        await live_client.lists.set_default_category(current_group, category_b_id)
        current_group = live_client.state.list_category_groups[live_mutation_list_id][group_id]
        await live_client.lists.set_sorted_category_ids(
            current_group, [category_b_id, category_a_id]
        )

        rule_name = f"sdk-rule-{caller_rule_id[:8]}"
        persisted_rule_id = category_rule_identifier(
            rule_name, group_id, live_mutation_list_id
        )
        rule = PB.PBListCategorizationRule(
            identifier=caller_rule_id,
            listId=live_mutation_list_id,
            categoryGroupId=group_id,
            itemName=rule_name,
            categoryId=category_b_id,
        )
        await live_client.lists.save_categorization_rule(rule)

        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        assert snapshot["category_groups"][group_id].defaultCategoryId == category_b_id
        assert snapshot["categories"][category_a_id].name.startswith(
            "SDK Category A Renamed"
        )
        assert snapshot["categories"][category_b_id].icon == "other"
        assert snapshot["categories"][category_b_id].sortIndex == 0
        assert persisted_rule_id in snapshot["categorization_rules"]
        assert caller_rule_id not in snapshot["categorization_rules"]
    finally:
        current_group = live_client.state.list_category_groups.get(
            live_mutation_list_id, {}
        ).get(group_id)
        if current_group is not None:
            category_ids = [
                category_id
                for category_id, category in live_client.state.list_categories.get(
                    live_mutation_list_id, {}
                ).items()
                if str(category.categoryGroupId) == group_id
            ]
            if category_ids:
                await live_client.lists.remove_categorization_rules_for_category_ids(
                    current_group, category_ids
                )
            current_group = live_client.state.list_category_groups.get(
                live_mutation_list_id, {}
            ).get(group_id)
            if current_group is not None:
                await live_client.lists.delete_category_group(current_group)

    snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
    assert group_id not in snapshot["category_groups"]
    assert category_a_id not in snapshot["categories"]
    assert category_b_id not in snapshot["categories"]
    assert persisted_rule_id not in snapshot["categorization_rules"]


@pytest.mark.asyncio
async def test_live_category_migration_and_bulk_rule_handlers(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    group_id = uuid4().hex
    category_a_id = uuid4().hex
    category_b_id = uuid4().hex
    category_c_id = uuid4().hex
    caller_rule_ids = [uuid4().hex, uuid4().hex, uuid4().hex]
    group = PB.PBListCategoryGroup(
        identifier=group_id,
        listId=live_mutation_list_id,
        name=f"SDK Migrated Group {group_id[:8]}",
        defaultCategoryId=category_a_id,
    )
    category_a = PB.PBListCategory(
        identifier=category_a_id,
        categoryGroupId=group_id,
        listId=live_mutation_list_id,
        name=f"SDK Migrated A {category_a_id[:8]}",
        sortIndex=0,
    )
    category_b = PB.PBListCategory(
        identifier=category_b_id,
        categoryGroupId=group_id,
        listId=live_mutation_list_id,
        name=f"SDK Created B {category_b_id[:8]}",
        sortIndex=1,
    )
    category_c = PB.PBListCategory(
        identifier=category_c_id,
        categoryGroupId=group_id,
        listId=live_mutation_list_id,
        name=f"SDK Removable C {category_c_id[:8]}",
        sortIndex=2,
    )
    rules = [
            PB.PBListCategorizationRule(
                identifier=caller_rule_ids[0],
                listId=live_mutation_list_id,
                categoryGroupId=group_id,
                itemName=f"sdk-bulk-a-{caller_rule_ids[0][:8]}",
                categoryId=category_a_id,
            ),
            PB.PBListCategorizationRule(
                identifier=caller_rule_ids[1],
                listId=live_mutation_list_id,
                categoryGroupId=group_id,
                itemName=f"sdk-bulk-b-{caller_rule_ids[1][:8]}",
                categoryId=category_b_id,
            ),
            PB.PBListCategorizationRule(
                identifier=caller_rule_ids[2],
                listId=live_mutation_list_id,
                categoryGroupId=group_id,
                itemName=f"sdk-migrate-a-{caller_rule_ids[2][:8]}",
                categoryId=category_a_id,
            ),
        ]
    persisted_rule_ids = [
        category_rule_identifier(rule.itemName, group_id, live_mutation_list_id)
        for rule in rules
    ]
    try:
        await live_client.lists.migrate_category_group(group)
        await live_client.lists.migrate_list_category(category_a)
        await live_client.lists.save_list_category(category_b)
        await live_client.lists.save_list_category(category_c)
        await live_client.lists.bulk_save_categorization_rules(
            live_mutation_list_id, rules[:2]
        )
        await live_client.lists.migrate_categorization_rules(
            live_mutation_list_id, rules[2:]
        )

        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        assert group_id in snapshot["category_groups"]
        assert {category_a_id, category_b_id, category_c_id}.issubset(
            snapshot["categories"]
        )
        assert set(persisted_rule_ids).issubset(snapshot["categorization_rules"])
        assert not set(caller_rule_ids) & set(snapshot["categorization_rules"])

        current_group = live_client.state.list_category_groups[live_mutation_list_id][group_id]
        current_c = live_client.state.list_categories[live_mutation_list_id][category_c_id]
        await live_client.lists.remove_category_ids(current_group, [current_c])
        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        assert category_c_id not in snapshot["categories"]
    finally:
        current_group = live_client.state.list_category_groups.get(
            live_mutation_list_id, {}
        ).get(group_id)
        if current_group is not None:
            remaining_category_ids = [
                category_id
                for category_id, category in live_client.state.list_categories.get(
                    live_mutation_list_id, {}
                ).items()
                if str(category.categoryGroupId) == group_id
            ]
            if remaining_category_ids:
                await live_client.lists.remove_categorization_rules_for_category_ids(
                    current_group, remaining_category_ids
                )
            current_group = live_client.state.list_category_groups.get(
                live_mutation_list_id, {}
            ).get(group_id)
            if current_group is not None:
                await live_client.lists.delete_category_group(current_group)

    snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
    assert group_id not in snapshot["category_groups"]
    assert not {category_a_id, category_b_id, category_c_id} & set(snapshot["categories"])
    assert not set(persisted_rule_ids) & set(snapshot["categorization_rules"])


@pytest.mark.asyncio
async def test_live_per_list_settings_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.list_settings is not None
    settings = live_client.state.list_settings.get(live_mutation_list_id)
    if settings is None:
        pytest.fail("disposable list has no per-list settings; refusing mutation", pytrace=False)
    required = (
        "shouldHideCategories",
        "genericGroceryAutocompleteEnabled",
        "listItemSortOrder",
        "favoritesAutocompleteEnabled",
        "recentItemsAutocompleteEnabled",
    )
    if any(not settings.HasField(field) for field in required):
        pytest.fail(
            "disposable list lacks an initialized settings field needed for exact restoration",
            pytrace=False,
        )

    original = {
        field: getattr(settings, field)
        for field in required
    }
    try:
        await live_client.list_settings.set(
            live_mutation_list_id,
            "shouldHideCategories",
            not bool(original["shouldHideCategories"]),
        )
        await live_client.list_settings.set(
            live_mutation_list_id,
            "genericGroceryAutocompleteEnabled",
            not bool(original["genericGroceryAutocompleteEnabled"]),
        )
        alternate_sort = (
            "ALListItemSortOrderManual"
            if original["listItemSortOrder"] == "ALListItemSortOrderAlphabetical"
            else "ALListItemSortOrderAlphabetical"
        )
        await live_client.list_settings.set(
            live_mutation_list_id, "listItemSortOrder", alternate_sort
        )
        await live_client.list_settings.set(
            live_mutation_list_id,
            "favoritesAutocompleteEnabled",
            not bool(original["favoritesAutocompleteEnabled"]),
        )
        await live_client.list_settings.set(
            live_mutation_list_id,
            "recentItemsAutocompleteEnabled",
            not bool(original["recentItemsAutocompleteEnabled"]),
        )

        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        changed = snapshot["settings"]
        assert changed is not None
        assert changed.shouldHideCategories is (not bool(original["shouldHideCategories"]))
        assert changed.genericGroceryAutocompleteEnabled is (
            not bool(original["genericGroceryAutocompleteEnabled"])
        )
        assert changed.listItemSortOrder == alternate_sort
        assert changed.favoritesAutocompleteEnabled is (
            not bool(original["favoritesAutocompleteEnabled"])
        )
        assert changed.recentItemsAutocompleteEnabled is (
            not bool(original["recentItemsAutocompleteEnabled"])
        )
    finally:
        for field in required:
            await live_client.list_settings.set(
                live_mutation_list_id, field, original[field]
            )

    snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
    restored = snapshot["settings"]
    assert restored is not None
    for field in required:
        assert getattr(restored, field) == original[field]


@pytest.mark.asyncio
async def test_live_list_settings_filter_clear_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    assert live_client.list_settings is not None
    settings = live_client.state.list_settings.get(live_mutation_list_id)
    if settings is None:
        pytest.fail("disposable list has no per-list settings; refusing mutation", pytrace=False)

    original_filter_present = settings.HasField("storeFilterId")
    original_filter = str(settings.storeFilterId) if original_filter_present else ""
    filter_id = uuid4().hex
    store_filter = PB.PBStoreFilter(
        identifier=filter_id,
        listId=live_mutation_list_id,
        name=f"SDK Settings Filter {filter_id[:8]}",
        includesUnassignedItems=True,
    )
    try:
        await live_client.lists.save_store_filter(
            live_mutation_list_id, store_filter, is_new=True
        )
        await live_client.list_settings.set(
            live_mutation_list_id, "storeFilterId", filter_id
        )
        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        changed = snapshot["settings"]
        assert changed is not None and changed.HasField("storeFilterId")
        assert changed.storeFilterId == filter_id

        await live_client.list_settings.clear_store_filter_id(live_mutation_list_id)
        snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
        cleared = snapshot["settings"]
        assert cleared is not None
        # The official client sends an absent optional field for the clear operation,
        # but the live server may normalize it back to a present empty string.
        assert not str(cleared.storeFilterId)
    finally:
        current_filter = live_client.state.list_store_filters.get(
            live_mutation_list_id, {}
        ).get(filter_id)
        if current_filter is not None:
            await live_client.lists.delete_store_filter(
                live_mutation_list_id, current_filter
            )
        if original_filter_present:
            await live_client.list_settings.set(
                live_mutation_list_id, "storeFilterId", original_filter
            )
        else:
            await live_client.list_settings.clear_store_filter_id(live_mutation_list_id)

    snapshot = await _fresh_list_scope(live_client, live_mutation_list_id)
    restored = snapshot["settings"]
    assert restored is not None
    if original_filter_present:
        assert restored.HasField("storeFilterId")
        assert restored.storeFilterId == original_filter
    else:
        assert not str(restored.storeFilterId)


@pytest.mark.asyncio
async def test_live_reversible_list_local_flags_when_server_materializes_them(
    live_client, live_mutation_list_id: str
) -> None:
    shopping_list = await _load_and_require_disposable(
        live_client, live_mutation_list_id
    )
    assert live_client.lists is not None
    restore_multiple: bool | None = None
    restore_position: int | None = None

    if shopping_list.HasField("allowsMultipleListCategoryGroups"):
        restore_multiple = bool(shopping_list.allowsMultipleListCategoryGroups)
    if shopping_list.HasField("newListItemPosition"):
        restore_position = int(shopping_list.newListItemPosition)

    if restore_multiple is None and restore_position is None:
        pytest.skip(
            "server does not materialize reversible list-local flag fields on the disposable list"
        )

    try:
        if restore_multiple is not None:
            await live_client.lists.set_allows_multiple_category_groups(
                live_mutation_list_id, not restore_multiple
            )
        if restore_position is not None:
            alternate_position = (
                PB.ShoppingList.NewListItemPosition.Top
                if restore_position == PB.ShoppingList.NewListItemPosition.Bottom
                else PB.ShoppingList.NewListItemPosition.Bottom
            )
            await live_client.lists.set_new_item_position(
                live_mutation_list_id, alternate_position
            )

        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        if restore_multiple is not None:
            assert fresh.HasField("allowsMultipleListCategoryGroups")
            assert bool(fresh.allowsMultipleListCategoryGroups) is (not restore_multiple)
        if restore_position is not None:
            assert fresh.HasField("newListItemPosition")
            assert int(fresh.newListItemPosition) != restore_position
    finally:
        if restore_multiple is not None:
            await live_client.lists.set_allows_multiple_category_groups(
                live_mutation_list_id, restore_multiple
            )
        if restore_position is not None:
            await live_client.lists.set_new_item_position(
                live_mutation_list_id, restore_position
            )

    fresh = await _fresh_server_list(live_client, live_mutation_list_id)
    assert fresh is not None
    if restore_multiple is not None:
        assert bool(fresh.allowsMultipleListCategoryGroups) is restore_multiple
    if restore_position is not None:
        assert int(fresh.newListItemPosition) == restore_position


@pytest.mark.asyncio
async def test_live_uncheck_and_bulk_uncheck_without_recents(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    created = await live_client.lists.add_items(
        live_mutation_list_id,
        [
            PB.ListItem(name=f"sdk-uncheck-a-{uuid4().hex}", checked=True),
            PB.ListItem(name=f"sdk-uncheck-b-{uuid4().hex}", checked=True),
            PB.ListItem(name=f"sdk-uncheck-c-{uuid4().hex}", checked=True),
            PB.ListItem(name=f"sdk-uncheck-d-{uuid4().hex}", checked=True),
            PB.ListItem(name=f"sdk-uncheck-e-{uuid4().hex}", checked=True),
        ],
    )
    ids = [str(item.identifier) for item in created]
    try:
        await live_client.lists.set_checked(live_mutation_list_id, ids[0], False)
        await live_client.lists.bulk_set_checked(
            live_mutation_list_id, ids[1:3], False
        )
        await live_client.lists.uncheck_all(live_mutation_list_id)

        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        by_id = {str(item.identifier): item for item in fresh.items}
        assert all(item_id in by_id for item_id in ids)
        assert all(by_id[item_id].checked is False for item_id in ids)
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, ids)


@pytest.mark.asyncio
async def test_live_revive_matching_checked_item_without_recents(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    created = await live_client.lists.add_items(
        live_mutation_list_id,
        [PB.ListItem(name=f"sdk-revive-{uuid4().hex}", checked=True)],
    )
    item_id = str(created[0].identifier)
    source = PB.ListItem()
    source.CopyFrom(created[0])
    try:
        revived = await live_client.lists.revive_matching_item(
            live_mutation_list_id, source
        )
        assert revived is not None
        assert str(revived.identifier) == item_id
        assert revived.checked is False

        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        persisted = next(
            (value for value in fresh.items if str(value.identifier) == item_id), None
        )
        assert persisted is not None
        assert persisted.checked is False
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, [item_id])


@pytest.mark.asyncio
async def test_live_move_single_item_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    created = await live_client.lists.add_items(
        live_mutation_list_id,
        [PB.ListItem(name=f"sdk-move-{index}-{uuid4().hex}") for index in range(3)],
    )
    ids = [str(item.identifier) for item in created]
    try:
        local = live_client.lists.get(live_mutation_list_id)
        assert local is not None
        relevant_before = [
            str(item.identifier) for item in local.items if str(item.identifier) in set(ids)
        ]
        assert len(relevant_before) == 3
        await live_client.lists.move_item(
            live_mutation_list_id,
            relevant_before[-1],
            next(
                index
                for index, item in enumerate(local.items)
                if str(item.identifier) == relevant_before[0]
            ),
        )

        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        relevant_after = [
            str(item.identifier) for item in fresh.items if str(item.identifier) in set(ids)
        ]
        assert relevant_after == [
            relevant_before[-1],
            relevant_before[0],
            relevant_before[1],
        ]
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, ids)


@pytest.mark.asyncio
async def test_live_item_store_category_price_and_matchup_round_trip(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    store_id = uuid4().hex
    created = await live_client.lists.add_item(
        live_mutation_list_id, f"sdk-item-local-{uuid4().hex}"
    )
    item_id = str(created.identifier)
    store = PB.PBStore(
        identifier=store_id,
        listId=live_mutation_list_id,
        name=f"SDK Item Store {store_id[:8]}",
    )
    try:
        await live_client.lists.save_store(live_mutation_list_id, store, is_new=True)

        groups = live_client.state.list_category_groups.get(live_mutation_list_id, {})
        categories = live_client.state.list_categories.get(live_mutation_list_id, {})
        group = next(
            (
                value
                for value in groups.values()
                if any(
                    str(category.categoryGroupId) == str(value.identifier)
                    for category in categories.values()
                )
            ),
            None,
        )
        if group is None:
            pytest.fail("disposable list has no category group; refusing mutation", pytrace=False)
        category = next(
            value
            for value in categories.values()
            if str(value.categoryGroupId) == str(group.identifier)
        )

        await live_client.lists.assign_category(
            live_mutation_list_id,
            item_id,
            PB.PBListItemCategoryAssignment(
                categoryGroupId=str(group.identifier),
                categoryId=str(category.identifier),
            ),
        )
        await live_client.lists.set_category_match_id(
            live_mutation_list_id, item_id, "produce"
        )
        await live_client.lists.add_store(live_mutation_list_id, item_id, store_id)
        await live_client.lists.remove_store(live_mutation_list_id, item_id, store_id)
        await live_client.lists.add_store_ids_to_items(
            live_mutation_list_id, [item_id], [store_id]
        )
        await live_client.lists.remove_store_id_from_all_items(
            live_mutation_list_id, store_id
        )
        await live_client.lists.add_store_ids_to_items(
            live_mutation_list_id, [item_id], [store_id]
        )
        price = PB.PBItemPrice(
            amount=4.25,
            details=f"sdk-price-{uuid4().hex[:8]}",
            storeId=store_id,
        )
        await live_client.lists.save_price(live_mutation_list_id, item_id, price)
        matchup = f"sdk-matchup-{uuid4().hex[:8]}"
        await live_client.lists.set_price_matchup_tag(
            live_mutation_list_id, item_id, matchup
        )

        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        item = next((value for value in fresh.items if str(value.identifier) == item_id), None)
        assert item is not None
        assert list(item.storeIds) == [store_id]
        assert item.categoryMatchId == "produce"
        assert item.category == "produce"
        assert any(
            str(value.categoryGroupId) == str(group.identifier)
            and str(value.categoryId) == str(category.identifier)
            for value in item.categoryAssignments
        )
        persisted_price = next(
            (value for value in item.prices if str(value.storeId) == store_id), None
        )
        assert persisted_price is not None
        assert persisted_price.amount == 4.25
        assert persisted_price.details == price.details
        assert item.priceMatchupTag == matchup

        await live_client.lists.remove_store_ids_from_items(
            live_mutation_list_id, [item_id], [store_id]
        )
        await live_client.lists.save_price(
            live_mutation_list_id, item_id, PB.PBItemPrice(storeId=store_id)
        )
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        item = next((value for value in fresh.items if str(value.identifier) == item_id), None)
        assert item is not None
        assert store_id not in item.storeIds
        assert all(str(value.storeId) != store_id for value in item.prices)
    finally:
        await _remove_without_recents(live_client, live_mutation_list_id, [item_id])
        current_store = live_client.state.list_stores.get(live_mutation_list_id, {}).get(
            store_id
        )
        if current_store is not None:
            await live_client.lists.delete_store(live_mutation_list_id, current_store)


@pytest.mark.asyncio
async def test_live_recipe_ingredient_provenance_lifecycle(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    recipe_id = f"sdk-recipe-{uuid4().hex}"
    ingredient_id = uuid4().hex
    old_recipe = PB.PBRecipe(identifier=recipe_id, name="SDK Recipe", scaleFactor=1.0)
    old_ingredient = old_recipe.ingredients.add(
        identifier=ingredient_id,
        rawIngredient="1 cup sdk tomatoes",
        name=f"sdk tomatoes {uuid4().hex[:8]}",
        quantity="1 cup",
    )
    old_source = ingredient_to_item_ingredient(old_ingredient, old_recipe)

    created = await live_client.lists.add_recipe_ingredient(
        live_mutation_list_id, old_source
    )
    first_item_id = str(created.identifier)
    try:
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        first = next(
            (item for item in fresh.items if str(item.identifier) == first_item_id), None
        )
        assert first is not None
        assert any(str(source.recipeId) == recipe_id for source in first.ingredients)

        new_recipe = PB.PBRecipe(
            identifier=recipe_id, name="SDK Recipe Updated", scaleFactor=1.0
        )
        new_ingredient = new_recipe.ingredients.add(
            identifier=ingredient_id,
            rawIngredient="2 cups sdk roma tomatoes",
            name=f"sdk roma tomatoes {uuid4().hex[:8]}",
            quantity="2 cups",
        )
        changed = await live_client.lists.sync_recipe_update(
            live_mutation_list_id, new_recipe, old_recipe
        )
        assert changed > 0

        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        recipe_items = [
            item
            for item in fresh.items
            if any(str(source.recipeId) == recipe_id for source in item.ingredients)
        ]
        assert recipe_items
        assert any(
            source.HasField("ingredient")
            and source.ingredient.name == new_ingredient.name
            for item in recipe_items
            for source in item.ingredients
            if str(source.recipeId) == recipe_id
        )

        removed = await live_client.lists.remove_recipe_references(
            live_mutation_list_id, recipe_id
        )
        assert removed > 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        assert not any(
            str(source.recipeId) == recipe_id
            for item in fresh.items
            for source in item.ingredients
        )

        new_source = ingredient_to_item_ingredient(new_ingredient, new_recipe)
        created_again = await live_client.lists.add_recipe_ingredient(
            live_mutation_list_id, new_source
        )
        second_item_id = str(created_again.identifier)
        assert await live_client.lists.remove_recipe_ingredient(
            live_mutation_list_id, new_source
        )
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        assert all(str(item.identifier) != second_item_id for item in fresh.items)
    finally:
        await live_client.lists.remove_recipe_references(
            live_mutation_list_id, recipe_id
        )


@pytest.mark.asyncio
async def test_live_recipe_event_provenance_update_and_remove(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    recipe_id = f"sdk-event-recipe-{uuid4().hex}"
    event_id = f"sdk-event-{uuid4().hex}"
    ingredient_id = uuid4().hex
    recipe = PB.PBRecipe(identifier=recipe_id, name="SDK Event Recipe", scaleFactor=1.0)
    ingredient = recipe.ingredients.add(
        identifier=ingredient_id,
        rawIngredient="1 cup sdk rice",
        name=f"sdk rice {uuid4().hex[:8]}",
        quantity="1 cup",
    )
    old_event = PB.PBCalendarEvent(
        identifier=event_id,
        recipeId=recipe_id,
        date="2026-09-10",
        recipeScaleFactor=1.0,
    )
    new_event = PB.PBCalendarEvent(
        identifier=event_id,
        recipeId=recipe_id,
        date="2026-09-11",
        recipeScaleFactor=2.0,
    )
    source = ingredient_to_item_ingredient(ingredient, recipe, old_event)

    await live_client.lists.add_recipe_ingredient(live_mutation_list_id, source)
    try:
        changed = await live_client.lists.sync_recipe_event_update(
            live_mutation_list_id, new_event, old_event, recipe
        )
        assert changed > 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        event_sources = [
            source
            for item in fresh.items
            for source in item.ingredients
            if str(source.eventId) == event_id
        ]
        assert event_sources
        assert all(source.eventDate == "2026-09-11" for source in event_sources)

        removed = await live_client.lists.remove_event_references(
            live_mutation_list_id, event_id
        )
        assert removed > 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        assert not any(
            str(source.eventId) == event_id
            for item in fresh.items
            for source in item.ingredients
        )
    finally:
        await live_client.lists.remove_event_references(
            live_mutation_list_id, event_id
        )


@pytest.mark.asyncio
async def test_live_free_event_list_provenance_update_and_remove(
    live_client, live_mutation_list_id: str
) -> None:
    await _load_and_require_disposable(live_client, live_mutation_list_id)
    assert live_client.lists is not None
    event_id = f"sdk-free-event-{uuid4().hex}"
    event_item_id = uuid4().hex
    old_event = PB.PBCalendarEvent(
        identifier=event_id,
        title="SDK Free Event",
        date="2026-09-12",
    )
    old_item = old_event.eventListItems.add(
        identifier=event_item_id,
        name=f"sdk free item {uuid4().hex[:8]}",
        details="old details",
    )
    old_source = event_list_item_to_item_ingredient(old_item, old_event)
    await live_client.lists.add_recipe_ingredient(live_mutation_list_id, old_source)

    new_event = PB.PBCalendarEvent(
        identifier=event_id,
        title="SDK Free Event Updated",
        date="2026-09-13",
    )
    new_item = new_event.eventListItems.add(
        identifier=event_item_id,
        name=f"sdk free item updated {uuid4().hex[:8]}",
        details="new details",
    )
    try:
        changed = await live_client.lists.sync_event_list_update(
            live_mutation_list_id, new_event, old_event
        )
        assert changed > 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        event_sources = [
            source
            for item in fresh.items
            for source in item.ingredients
            if str(source.eventId) == event_id
        ]
        assert event_sources
        assert any(
            source.HasField("ingredient")
            and source.ingredient.name == new_item.name
            and source.eventDate == "2026-09-13"
            for source in event_sources
        )

        removed = await live_client.lists.remove_event_references(
            live_mutation_list_id, event_id
        )
        assert removed > 0
        fresh = await _fresh_server_list(live_client, live_mutation_list_id)
        assert fresh is not None
        assert not any(
            str(source.eventId) == event_id
            for item in fresh.items
            for source in item.ingredients
        )
    finally:
        await live_client.lists.remove_event_references(
            live_mutation_list_id, event_id
        )
