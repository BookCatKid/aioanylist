from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from google.protobuf.message import Message

from .state import AnyListState
from .transport import AnyListTransport
from .types import Domain

SyncListener = Callable[[set[Domain]], Awaitable[None] | None]


class SyncCoordinator:
    """Coalesced aggregate sync matching /data/user-data/get in the official web app."""

    def __init__(self, transport: AnyListTransport, state: AnyListState) -> None:
        self.transport = transport
        self.state = state
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[Message] | None = None
        self._listeners: list[SyncListener] = []

    def add_listener(self, listener: SyncListener) -> None:
        self._listeners.append(listener)

    async def _notify(self, domains: set[Domain]) -> None:
        for listener in tuple(self._listeners):
            result = listener(domains)
            if asyncio.iscoroutine(result):
                await result

    @staticmethod
    def _domains_in(response: Message) -> set[Domain]:
        mapping = {
            "shoppingListsResponse": Domain.SHOPPING_LISTS,
            "listFoldersResponse": Domain.LIST_FOLDERS,
            "recipeDataResponse": Domain.RECIPES,
            "mealPlanningCalendarResponse": Domain.MEAL_PLAN,
            "categorizedItemsResponse": Domain.CATEGORIZED_ITEMS,
            "userCategoriesResponse": Domain.USER_CATEGORIES,
            "starterListsResponse": Domain.STARTER_LISTS,
            "listSettingsResponse": Domain.LIST_SETTINGS,
            "starterListSettingsResponse": Domain.STARTER_LIST_SETTINGS,
            "mobileAppSettingsResponse": Domain.MOBILE_SETTINGS,
        }
        return {domain for field, domain in mapping.items() if response.HasField(field)}

    async def _refresh_once(self, *, full: bool) -> Message:
        fields: dict[str, Message] = {"client_info": self.state.user_data_client_info()}
        if not full and self.state.loaded_once:
            fields["timestamps"] = self.state.user_data_timestamps()
        response = await self.transport.post_proto(
            "/data/user-data/get", fields=fields, response_type="PBUserDataResponse"
        )
        assert isinstance(response, Message)
        domains = self._domains_in(response)
        self.state.apply_user_data(response)
        await self._notify(domains)
        return response

    async def refresh(self, *, full: bool = False) -> Message:
        # Coalesce callers so HA-like consumers cannot accidentally trigger N identical refreshes.
        async with self._lock:
            current = self._inflight
            if current is None or current.done() or full:
                current = asyncio.create_task(self._refresh_once(full=full))
                self._inflight = current
        try:
            return await asyncio.shield(current)
        finally:
            if current.done():
                async with self._lock:
                    if self._inflight is current:
                        self._inflight = None
