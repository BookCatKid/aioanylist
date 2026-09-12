from __future__ import annotations

import os
from uuid import uuid4

import pytest

from anylist_sdk import AnyListClient
from anylist_sdk.derived import (
    event_list_item_to_item_ingredient,
    ingredient_to_item_ingredient,
)
from anylist_sdk.proto import PB, ListItem, ShoppingList
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
