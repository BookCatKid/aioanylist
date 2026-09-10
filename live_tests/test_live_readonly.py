from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import pytest
from google.protobuf.message import Message


pytestmark = pytest.mark.live


def _message_digest(value: Message | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.SerializeToString(deterministic=True)).hexdigest()


def _mapping_digest(values: dict[str, Message]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, _message_digest(value) or "") for key, value in values.items()))


def _nested_mapping_digest(
    values: dict[str, dict[str, Message]],
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    return tuple(sorted((key, _mapping_digest(value)) for key, value in values.items()))


def _state_fingerprint(
    state: Any,
    *,
    include_category_marker: bool = True,
    include_legacy_order_marker: bool = True,
) -> tuple[object, ...]:
    # Compare the synchronized model without printing account data. The category response
    # identifier is optionally excluded because the direct /all response uses the literal
    # marker "all" while aggregate sync may carry another non-empty response identifier; the
    # official manager uses that marker for clear-vs-merge behavior rather than persisted data.
    return (
        state.user_id,
        _mapping_digest(state.shopping_lists),
        (
            tuple(state.ordered_shopping_list_ids)
            if include_legacy_order_marker
            else ("<ignored-legacy-order-marker>",)
        ),
        _mapping_digest(state.list_responses),
        _nested_mapping_digest(state.list_stores),
        _nested_mapping_digest(state.list_store_filters),
        _nested_mapping_digest(state.list_category_groups),
        _nested_mapping_digest(state.list_categories),
        _nested_mapping_digest(state.list_categorization_rules),
        _mapping_digest(state.list_folders),
        state.root_folder_id,
        state.list_data_id,
        state.has_migrated_list_ordering,
        _mapping_digest(state.recipes),
        _mapping_digest(state.recipe_collections),
        tuple(state.recipe_collection_ids),
        _message_digest(state.all_recipes_collection),
        state.recipe_data_id,
        state.recipe_timestamp,
        state.recipe_max_count,
        tuple(_message_digest(x) for x in state.pending_recipe_link_requests),
        tuple(_message_digest(x) for x in state.recipe_link_requests_to_confirm),
        tuple(_message_digest(x) for x in state.linked_recipe_users),
        _mapping_digest(state.system_recipe_collection_settings),
        state.meal_plan_calendar_id,
        state.meal_plan_logical_timestamp,
        state.meal_plan_response_version,
        _mapping_digest(state.meal_plan_events),
        _mapping_digest(state.meal_plan_labels),
        _mapping_digest(state.meal_plan_templates),
        _mapping_digest(state.meal_plan_template_events),
        _mapping_digest(state.meal_plan_template_groups),
        _mapping_digest(state.categorized_items),
        state.categorized_items_timestamp,
        state.categorized_items_timestamp_id,
        _mapping_digest(state.user_categories),
        _mapping_digest(state.category_groupings),
        state.user_categories_timestamp,
        state.user_category_data_id if include_category_marker else "<ignored>",
        state.user_categories_requires_refresh_timestamp,
        state.has_migrated_category_orderings,
        _mapping_digest(state.list_settings),
        state.list_settings_timestamp,
        state.list_settings_timestamp_id,
        _mapping_digest(state.starter_list_settings),
        state.starter_list_settings_timestamp,
        state.starter_list_settings_timestamp_id,
        _mapping_digest(state.starter_lists),
        _mapping_digest(state.recent_item_lists),
        _mapping_digest(state.favorite_item_lists),
        tuple(state.ordered_starter_list_ids),
        state.ordered_starter_list_ids_timestamp,
        state.ordered_starter_list_ids_timestamp_id,
        state.has_migrated_user_favorites,
        _message_digest(state.mobile_app_settings),
    )


async def _direct_refresh_round(live_client: Any) -> None:
    assert live_client.lists is not None
    assert live_client.folders is not None
    assert live_client.categories is not None
    assert live_client.categorized_items is not None
    assert live_client.list_settings is not None
    assert live_client.starter_list_settings is not None
    assert live_client.mobile_settings is not None
    assert live_client.starter_lists is not None
    assert live_client.recipes is not None
    assert live_client.meal_plan is not None
    assert live_client.account is not None

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
async def test_live_auth_full_sync_and_direct_refreshes(live_client: Any) -> None:
    state = await live_client.load(
        realtime=False,
        load_tag_data=True,
        restore_pending=False,
    )

    assert live_client.tokens is not None
    assert live_client.user_id
    assert live_client.ready.is_set()
    assert state.loaded_once

    await _direct_refresh_round(live_client)


@pytest.mark.asyncio
async def test_live_incremental_sync_is_structurally_stable(live_client: Any) -> None:
    state = await live_client.load(
        realtime=False,
        load_tag_data=False,
        restore_pending=False,
    )
    # ShoppingListsResponse.orderedIds is legacy runtime state: the real server sends it on
    # full sync and sends an empty array on unchanged deltas. app.js stores that array in $oj$JK
    # but never reads $oj$JK anywhere, so it is not synchronized user-visible ordering state.
    before = _state_fingerprint(state, include_legacy_order_marker=False)
    await live_client.refresh()
    after = _state_fingerprint(state, include_legacy_order_marker=False)
    assert after == before


@pytest.mark.asyncio
async def test_live_repeated_direct_refreshes_converge(live_client: Any) -> None:
    state = await live_client.load(
        realtime=False,
        load_tag_data=False,
        restore_pending=False,
    )
    await _direct_refresh_round(live_client)
    first = _state_fingerprint(state, include_category_marker=False)
    await _direct_refresh_round(live_client)
    second = _state_fingerprint(state, include_category_marker=False)
    assert second == first


@pytest.mark.asyncio
async def test_live_concurrent_incremental_refreshes_coalesce(live_client: Any) -> None:
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    original = live_client.transport.post_proto
    aggregate_calls = 0

    async def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal aggregate_calls
        endpoint = args[0] if args else kwargs.get("endpoint")
        if endpoint == "/data/user-data/get":
            aggregate_calls += 1
        return await original(*args, **kwargs)

    live_client.transport.post_proto = counted
    try:
        await asyncio.gather(*(live_client.refresh() for _ in range(8)))
    finally:
        live_client.transport.post_proto = original

    assert aggregate_calls == 1


@pytest.mark.asyncio
async def test_live_official_tag_data_languages_and_memory_cache(live_client: Any) -> None:
    english = await live_client.tag_data.get("en", refresh=True)
    german = await live_client.tag_data.get("de", refresh=True)
    assert english.language == "en"
    assert german.language == "de"
    assert english.tags
    assert german.tags
    assert await live_client.tag_data.get("en") is english
    assert await live_client.tag_data.get("de") is german


@pytest.mark.asyncio
async def test_live_account_read_is_stable(live_client: Any) -> None:
    assert live_client.account is not None
    first = await live_client.account.get()
    second = await live_client.account.get()
    assert _message_digest(second) == _message_digest(first)


@pytest.mark.asyncio
async def test_live_token_refresh_round_trip(live_client: Any) -> None:
    before = live_client.tokens
    assert before is not None

    after = await live_client.transport.refresh_access_token(force=True)

    assert after.user_id == before.user_id
    assert after.access_token
    assert after.refresh_token
    assert live_client.tokens == after


@pytest.mark.asyncio
async def test_live_realtime_survives_heartbeats_and_restarts(live_client: Any) -> None:
    await live_client.realtime.start()
    try:
        assert live_client.realtime.connected.is_set()
        # Two heartbeat intervals prove this is more than a successful opening handshake.
        await asyncio.sleep(11)
        assert live_client.realtime.connected.is_set()
    finally:
        await live_client.realtime.stop()

    assert not live_client.realtime.connected.is_set()
    await live_client.realtime.start()
    try:
        assert live_client.realtime.connected.is_set()
    finally:
        await live_client.realtime.stop()


@pytest.mark.asyncio
async def test_live_logout_is_local_only_and_clears_state(live_client: Any) -> None:
    await live_client.load(realtime=False, load_tag_data=False, restore_pending=False)
    request = live_client.transport.request
    called_logout_endpoint = False

    async def watched(method: str, endpoint: str, **kwargs: Any) -> bytes:
        nonlocal called_logout_endpoint
        if endpoint == "/auth/logout":
            called_logout_endpoint = True
        return await request(method, endpoint, **kwargs)

    live_client.transport.request = watched
    await live_client.logout()

    assert not called_logout_endpoint
    assert live_client.tokens is None
    assert live_client.user_id is None
    assert not live_client.ready.is_set()
    assert live_client.state.user_id is None
    assert not live_client.state.loaded_once
    assert live_client.lists is None
