from __future__ import annotations

import pytest


pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_auth_full_sync_and_direct_refreshes(live_client) -> None:
    state = await live_client.load(
        realtime=False,
        load_tag_data=True,
        restore_pending=False,
    )

    assert live_client.tokens is not None
    assert live_client.user_id
    assert live_client.ready.is_set()
    assert state.loaded_once

    # These are all read-only manager refreshes used by the official web client. Running them
    # after the aggregate load validates the real server's timestamp/logical-timestamp forms.
    await live_client.lists.refresh()
    await live_client.folders.refresh()
    await live_client.categories.refresh()
    await live_client.categorized_items.refresh()
    await live_client.list_settings.refresh()
    await live_client.starter_list_settings.refresh()
    await live_client.mobile_settings.refresh()
    await live_client.starter_lists.refresh()
    await live_client.starter_lists.refresh_order()
    await live_client.recipes.refresh()
    await live_client.recipes.refresh(desktop_import_extension=True)
    await live_client.meal_plan.refresh()
    await live_client.account.get()


@pytest.mark.asyncio
async def test_live_token_refresh_round_trip(live_client) -> None:
    before = live_client.tokens
    assert before is not None

    after = await live_client.transport.refresh_access_token(force=True)

    assert after.user_id == before.user_id
    assert after.access_token
    assert after.refresh_token
    assert live_client.tokens == after


@pytest.mark.asyncio
async def test_live_realtime_connects_and_stops_cleanly(live_client) -> None:
    await live_client.realtime.start()
    try:
        assert live_client.realtime.connected.is_set()
    finally:
        await live_client.realtime.stop()
