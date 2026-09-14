from __future__ import annotations

import pytest

from anylist_sdk.client import AnyListClient
from anylist_sdk.proto import PB
from anylist_sdk.realtime import RealtimeEvent
from anylist_sdk.types import AuthTokens, Domain


def tokens(user="user", locale="en-US"):
    return AuthTokens(user, "access", "refresh", True, locale)


def test_authenticated_constructor_installs_complete_service_surface() -> None:
    client = AnyListClient(tokens=tokens())
    assert client.user_id == "user"
    for name in (
        "lists",
        "recipes",
        "folders",
        "categories",
        "categorized_items",
        "list_settings",
        "starter_list_settings",
        "mobile_settings",
        "starter_lists",
        "meal_plan",
        "account",
        "photos",
        "maps",
        "products",
        "sharing",
        "alexa",
        "web_state",
        "config",
        "visuals",
        "raw",
    ):
        assert getattr(client, name) is not None


def test_unauthenticated_constructor_keeps_public_config_surface_only() -> None:
    client = AnyListClient()
    assert client.config is not None
    assert client.visuals is not None
    assert client.raw is not None
    assert client.products is None


def test_constructor_exposes_token_callback_without_transport_reachthrough() -> None:
    seen = []

    def callback(value: AuthTokens | None) -> None:
        seen.append(value)

    client = AnyListClient(tokens=tokens(), token_callback=callback)
    assert client.transport.token_callback is callback


@pytest.mark.asyncio
async def test_sign_in_to_different_account_replaces_all_state(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens("old"))
    client.state.loaded_once = True
    client.state.shopping_lists["leak"] = PB.ShoppingList(identifier="leak")

    async def signin(email, password):
        value = tokens("new", "de-DE")
        client.transport.tokens = value
        return value

    monkeypatch.setattr(client.transport, "sign_in", signin)
    result = await client.sign_in("x", "y")
    assert result.user_id == "new" and client.state.user_id == "new"
    assert client.state.shopping_lists == {} and not client.state.loaded_once
    assert client.tag_data.locale == "de-DE"
    assert client.lists.user_id == "new"


@pytest.mark.asyncio
async def test_load_runs_sync_and_tag_data_then_replays_restored_operations(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens())
    order = []

    async def refresh(*, full=False):
        order.append(("sync", full))
        client.state.loaded_once = True
        return PB.PBUserDataResponse()

    async def tags():
        order.append(("tags",))
        return (None, None)

    monkeypatch.setattr(client.sync, "refresh", refresh)
    monkeypatch.setattr(client.tag_data, "active_and_english", tags)

    class Service:
        async def restore(self):
            order.append(("restore",))
            return 1

        async def flush(self):
            order.append(("flush",))

    service = Service()
    monkeypatch.setattr(client, "_operation_services", lambda: [service])
    await client.load(load_tag_data=True, restore_pending=True)
    assert ("sync", True) in order and ("tags",) in order
    assert order.count(("restore",)) == 1 and order.count(("flush",)) == 1
    assert client.ready.is_set()


@pytest.mark.asyncio
async def test_reconnect_does_user_data_and_account_catchup(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens())
    calls = []

    async def refresh(*args, **kwargs):
        calls.append("sync")
        return PB.PBUserDataResponse()

    async def account():
        calls.append("account")
        return PB.PBAccountInfoResponse()

    monkeypatch.setattr(client.sync, "refresh", refresh)
    monkeypatch.setattr(client.account, "get", account)
    await client._on_reconnect()
    assert calls == ["sync", "account"]


@pytest.mark.asyncio
async def test_account_invalidation_fetches_account_without_full_sync(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens())
    calls = []

    async def account():
        calls.append("account")

    async def refresh(*args, **kwargs):
        calls.append("sync")

    monkeypatch.setattr(client.account, "get", account)
    monkeypatch.setattr(client.sync, "refresh", refresh)
    await client._on_realtime(RealtimeEvent("refresh-account-info", Domain.ACCOUNT))
    assert calls == ["account"]


@pytest.mark.asyncio
async def test_logout_clears_account_state_and_authenticated_services(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens())
    client.state.shopping_lists["x"] = PB.ShoppingList(identifier="x")

    async def stop():
        pass

    async def logout(*, push_token=None, push_token_type=None):
        client.transport.tokens = None

    monkeypatch.setattr(client.realtime, "stop", stop)
    monkeypatch.setattr(client.transport, "logout", logout)
    await client.logout()
    assert client.state.user_id is None and client.state.shopping_lists == {}
    assert client.lists is None and client.recipes is None and client.account is None
    assert client.products is None
    assert client.config is not None and client.raw is not None and not client.ready.is_set()


@pytest.mark.asyncio
async def test_clear_session_clears_account_state_without_remote_logout(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens())
    calls: list[str] = []

    async def stop() -> None:
        calls.append("stop")

    async def clear_session() -> None:
        calls.append("clear")
        client.transport.tokens = None

    monkeypatch.setattr(client.realtime, "stop", stop)
    monkeypatch.setattr(client.transport, "clear_session", clear_session)
    await client.clear_session()

    assert calls == ["stop", "clear"]
    assert client.state.user_id is None
    assert client.lists is None


@pytest.mark.asyncio
async def test_store_filter_delete_clears_selected_list_setting(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens())
    client.state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", storeFilterId="filter"
    )
    calls = []

    async def clear(list_id, *, flush=True):
        calls.append((list_id, flush))
        client.state.list_settings[list_id].ClearField("storeFilterId")
        return client.state.list_settings[list_id]

    monkeypatch.setattr(client.list_settings, "clear_store_filter_id", clear)
    await client._clear_selected_store_filter("list", "filter", False)
    assert calls == [("list", False)]
    assert not client.state.list_settings["list"].HasField("storeFilterId")


@pytest.mark.asyncio
async def test_shopping_recent_callback_routes_to_starter_lists(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens())
    seen = []

    async def record(list_id, items, *, skip_existing=False, flush=True):
        seen.append((list_id, [x.identifier for x in items], skip_existing, flush))
        return []

    monkeypatch.setattr(client.starter_lists, "record_recent_items", record)
    await client.lists.on_items_became_recent(
        "list", [PB.ListItem(identifier="item")], False, False
    )
    assert seen == [("list", ["item"], False, False)]


@pytest.mark.asyncio
async def test_new_shopping_list_routes_official_settings_initialization(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens(), user_email="user@example.com")
    client.state.root_folder_id = "root"
    client.state.list_folders["root"] = PB.PBListFolder(identifier="root")
    seen = []

    async def initialize(list_id, category_group_id, *, list_type=0, flush=True):
        seen.append((list_id, category_group_id, list_type, flush))
        return PB.PBListSettings(identifier="settings", listId=list_id)

    monkeypatch.setattr(client.list_settings, "initialize_new_list", initialize)
    await client.lists.create(
        "List",
        list_id="list",
        list_type=2,
        initialize_starter_lists=False,
        flush=False,
    )

    assert len(seen) == 1
    assert seen[0][0] == "list"
    assert seen[0][1]
    assert seen[0][2:] == (2, False)


@pytest.mark.asyncio
async def test_new_shopping_list_routes_official_starter_side_effects(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens(), user_email="user@example.com")
    client.state.root_folder_id = "root"
    client.state.list_folders["root"] = PB.PBListFolder(identifier="root")
    seen = []

    async def starter_init(list_id, *, favorite_name="Favorite Items", flush=True):
        seen.append((list_id, favorite_name, flush))
        return (PB.StarterList(), PB.StarterList())

    monkeypatch.setattr(client.starter_lists, "initialize_for_shopping_list", starter_init)
    await client.lists.create("List", list_id="list", flush=False)

    assert seen == [("list", "Favorite Items", False)]


@pytest.mark.asyncio
async def test_pending_new_list_defers_folder_snapshot_and_refreshes_folders_after_ack(
    monkeypatch,
) -> None:
    client = AnyListClient(tokens=tokens())
    operation = client.lists.legacy_queue.new_operation(
        "new-shopping-list", listId="list", list=PB.ShoppingList(identifier="list")
    )
    await client.lists.legacy_queue.enqueue(operation, flush=False)
    response = PB.PBUserDataResponse()
    response.listFoldersResponse.listDataId = "data"
    response.listFoldersResponse.rootFolderId = "root"
    refreshed = []

    async def refresh_folders():
        refreshed.append("folders")
        return PB.PBListFoldersResponse()

    monkeypatch.setattr(client.folders, "refresh", refresh_folders)
    client.lists.on_folder_refresh_requested = client.folders.refresh

    filtered = client.sync._filter_busy_fields(response)

    assert not filtered.HasField("listFoldersResponse")
    assert client.lists._refresh_folders_after_legacy_queue is True
    client.lists.legacy_queue._pending.clear()
    await client.lists._on_legacy_response(PB.PBEditOperationResponse())
    assert refreshed == ["folders"]
    assert client.lists._refresh_folders_after_legacy_queue is False


@pytest.mark.asyncio
async def test_pending_folder_delete_defers_shopping_snapshot_and_refreshes_lists_after_ack(
    monkeypatch,
) -> None:
    client = AnyListClient(tokens=tokens())
    operation = client.folders.queue.new_operation("delete-folder-items")
    await client.folders.queue.enqueue(operation, flush=False)
    response = PB.PBUserDataResponse()
    response.shoppingListsResponse.newLists.add(identifier="server-list")
    refreshed = []

    async def refresh_lists():
        refreshed.append("lists")
        return PB.ShoppingListsResponse()

    monkeypatch.setattr(client.lists, "refresh", refresh_lists)
    client.folders.on_shopping_refresh_requested = client.lists.refresh

    filtered = client.sync._filter_busy_fields(response)

    assert not filtered.HasField("shoppingListsResponse")
    assert client.folders._refresh_shopping_after_queue is True
    client.folders.queue._pending.clear()
    await client.folders._on_response(PB.PBEditOperationResponse())
    assert refreshed == ["lists"]
    assert client.folders._refresh_shopping_after_queue is False


@pytest.mark.asyncio
async def test_pending_starter_edit_defers_snapshot_and_refreshes_after_ack(monkeypatch) -> None:
    client = AnyListClient(tokens=tokens())
    operation = client.starter_lists.queue.new_operation("set-list-name", listId="starter")
    await client.starter_lists.queue.enqueue(operation, flush=False)
    response = PB.PBUserDataResponse()
    response.starterListsResponse.userListsResponse.includesAllLists = True
    refreshed = []

    async def refresh_starter():
        refreshed.append("starter")
        return PB.StarterListsResponseV2()

    monkeypatch.setattr(client.starter_lists, "refresh", refresh_starter)

    filtered = client.sync._filter_busy_fields(response)

    assert not filtered.HasField("starterListsResponse")
    assert client.starter_lists._refresh_after_queue is True
    client.starter_lists.queue._pending.clear()
    await client.starter_lists._on_response(PB.PBEditOperationResponse())
    assert refreshed == ["starter"]
    assert client.starter_lists._refresh_after_queue is False
