from __future__ import annotations

import asyncio

import pytest

from anylist_sdk.proto import PB
from anylist_sdk.state import AnyListState
from anylist_sdk.sync import SyncCoordinator
from anylist_sdk.types import Domain


class BlockingTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def post_proto(self, endpoint, *, fields, response_type=None):
        self.calls.append((endpoint, fields, response_type))
        self.entered.set()
        await self.release.wait()
        return self.response


@pytest.mark.asyncio
async def test_initial_sync_sends_client_info_without_timestamps(fake_transport) -> None:
    response = PB.PBUserDataResponse()
    fake_transport.responses.append(response)
    state = AnyListState(user_id="user")
    sync = SyncCoordinator(fake_transport, state)
    await sync.refresh()
    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/user-data/get"
    assert response_type == "PBUserDataResponse"
    assert set(fields) == {"client_info"}
    assert fields["client_info"].mealPlanningCalendarClientInfo.supportedResponseVersion == 1
    assert state.loaded_once


@pytest.mark.asyncio
async def test_incremental_sync_sends_domain_timestamps(fake_transport) -> None:
    first = PB.PBUserDataResponse()
    second = PB.PBUserDataResponse()
    fake_transport.responses.extend([first, second])
    state = AnyListState(user_id="user")
    sync = SyncCoordinator(fake_transport, state)
    await sync.refresh(full=True)
    state.recipe_data_id = "recipes"
    state.recipe_timestamp = 42
    await sync.refresh()
    fields = fake_transport.calls[-1][1]
    assert fields["timestamps"].userRecipeDataTimestamp.identifier == "recipes"
    assert fields["timestamps"].userRecipeDataTimestamp.timestamp == 42


@pytest.mark.asyncio
async def test_concurrent_refreshes_coalesce_to_one_request() -> None:
    transport = BlockingTransport(PB.PBUserDataResponse())
    sync = SyncCoordinator(transport, AnyListState(user_id="user"))
    tasks = [asyncio.create_task(sync.refresh()) for _ in range(8)]
    await transport.entered.wait()
    await asyncio.sleep(0)
    assert len(transport.calls) == 1
    transport.release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_sync_notifies_only_domains_present_in_response(fake_transport) -> None:
    response = PB.PBUserDataResponse()
    response.listFoldersResponse.includesAllFolders = True
    response.mobileAppSettingsResponse.identifier = "settings"
    fake_transport.responses.append(response)
    sync = SyncCoordinator(fake_transport, AnyListState(user_id="user"))
    seen = []
    sync.add_listener(lambda domains: seen.append(domains))
    await sync.refresh()
    assert seen == [{Domain.LIST_FOLDERS, Domain.MOBILE_SETTINGS}]


@pytest.mark.asyncio
async def test_aggregate_sync_defers_busy_manager_snapshot_until_guard_clears(fake_transport) -> None:
    first = PB.PBUserDataResponse()
    first.listSettingsResponse.timestamp.identifier = "all"
    first.listSettingsResponse.timestamp.timestamp = 10
    first.listSettingsResponse.settings.add(
        identifier="settings", userId="user", listId="list", shouldHidePrices=True
    )
    fake_transport.responses.append(first)
    state = AnyListState(user_id="user")
    state.list_settings["list"] = PB.PBListSettings(
        identifier="settings", userId="user", listId="list", shouldHidePrices=False
    )
    sync = SyncCoordinator(fake_transport, state)
    busy = True
    sync.set_field_guard("listSettingsResponse", lambda: not busy)
    seen = []
    sync.add_listener(lambda domains: seen.append(domains))

    await sync.refresh(full=True)

    assert state.list_settings["list"].shouldHidePrices is False
    assert seen == [set()]
    # Unlike shopping/starter managers, list settings has no deferred-refresh flag in the
    # official client. The busy aggregate snapshot is simply ignored; the queue delegate's
    # normal timestamp conflict path decides whether a direct settings refresh is necessary.
