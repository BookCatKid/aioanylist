from __future__ import annotations

import os
from uuid import uuid4

import pytest

from anylist_sdk import AnyListClient
from anylist_sdk.proto import PB, ListItem, ShoppingList


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


async def _remove_without_recents(live_client, list_id: str, item_ids: list[str]) -> None:
    assert live_client.lists is not None
    existing = [item_id for item_id in item_ids if live_client.lists.item(list_id, item_id) is not None]
    if existing:
        await live_client.lists.bulk_remove_items(
            list_id,
            existing,
            remember_recent=False,
        )


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
