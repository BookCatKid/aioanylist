from __future__ import annotations

import os
from uuid import uuid4

import pytest


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ANYLIST_LIVE_MUTATIONS") != "1",
        reason="set ANYLIST_LIVE_MUTATIONS=1 to enable mutation conformance tests",
    ),
]


@pytest.mark.asyncio
async def test_live_add_remove_item_round_trip(live_client, live_mutation_list_id: str) -> None:
    await live_client.load(
        realtime=False,
        load_tag_data=False,
        restore_pending=False,
    )
    shopping_list = live_client.lists.get(live_mutation_list_id)
    if shopping_list is None:
        pytest.fail(
            "ANYLIST_LIVE_LIST_ID was not present after synchronization; use an explicitly "
            "approved disposable shopping list"
        )

    marker = f"anylist-sdk-live-{uuid4().hex}"
    created = await live_client.lists.add_item(live_mutation_list_id, marker)
    item_id = str(created.identifier)

    try:
        await live_client.lists.refresh()
        persisted = live_client.lists.item(live_mutation_list_id, item_id)
        assert persisted is not None
        assert persisted.name == marker
    finally:
        # Cleanup is deliberately attempted even if persistence verification fails. A failure
        # here is important live-conformance evidence because it may leave the marker visible.
        # Use bulk-remove with remember_recent=False so the cleanup itself does not create a
        # Recent Items artifact for the disposable marker.
        if live_client.lists.item(live_mutation_list_id, item_id) is not None:
            await live_client.lists.bulk_remove_items(
                live_mutation_list_id,
                [item_id],
                remember_recent=False,
            )

    await live_client.lists.refresh()
    assert live_client.lists.item(live_mutation_list_id, item_id) is None
