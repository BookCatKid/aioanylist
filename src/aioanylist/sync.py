from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from google.protobuf.message import Message

from .proto import PB, PBUserDataResponse
from .state import AnyListState
from .transport import AnyListTransport
from .types import Domain

SyncListener = Callable[[set[Domain]], Awaitable[None] | None]
SyncStatusListener = Callable[[Exception | None], Awaitable[None] | None]
FieldGuard = Callable[[], bool]
BusyCallback = Callable[[], None]

_DOMAIN_FIELDS = (
    ("shoppingListsResponse", Domain.SHOPPING_LISTS),
    ("listFoldersResponse", Domain.LIST_FOLDERS),
    ("recipeDataResponse", Domain.RECIPES),
    ("mealPlanningCalendarResponse", Domain.MEAL_PLAN),
    ("categorizedItemsResponse", Domain.CATEGORIZED_ITEMS),
    ("userCategoriesResponse", Domain.USER_CATEGORIES),
    ("starterListsResponse", Domain.STARTER_LISTS),
    ("listSettingsResponse", Domain.LIST_SETTINGS),
    ("starterListSettingsResponse", Domain.STARTER_LIST_SETTINGS),
    ("mobileAppSettingsResponse", Domain.MOBILE_SETTINGS),
)


class SyncCoordinator:
    """Coalesced aggregate sync matching /data/user-data/get in the official web app."""

    def __init__(self, transport: AnyListTransport, state: AnyListState) -> None:
        self.transport = transport
        self.state = state
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[PBUserDataResponse | None] | None = None
        self._listeners: list[SyncListener] = []
        self._status_listeners: list[SyncStatusListener] = []
        self._field_guards: dict[str, FieldGuard] = {}
        self._busy_callbacks: dict[str, BusyCallback] = {}

    def set_field_guard(
        self, field: str, guard: FieldGuard, *, on_busy: BusyCallback | None = None
    ) -> None:
        """Prevent one aggregate-response field from applying while its manager is busy."""
        self._field_guards[field] = guard
        if on_busy is None:
            self._busy_callbacks.pop(field, None)
        else:
            self._busy_callbacks[field] = on_busy

    def add_listener(self, listener: SyncListener) -> None:
        self._listeners.append(listener)

    def add_status_listener(self, listener: SyncStatusListener) -> None:
        """Register a listener for aggregate sync success and failure."""
        self._status_listeners.append(listener)

    async def _notify(self, domains: set[Domain]) -> None:
        for listener in tuple(self._listeners):
            result = listener(domains)
            if asyncio.iscoroutine(result):
                await result

    async def _notify_status(self, error: Exception | None) -> None:
        for listener in tuple(self._status_listeners):
            try:
                result = listener(error)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001,S112 - status listeners are observers
                continue

    @staticmethod
    def _domains_in(response: PBUserDataResponse) -> set[Domain]:
        return {domain for field, domain in _DOMAIN_FIELDS if response.HasField(field)}

    def _filter_busy_fields(self, response: PBUserDataResponse) -> PBUserDataResponse:
        """Mirror each official manager's `queue.bl() -> return` snapshot guard."""
        blocked: list[str] = []
        for field, guard in self._field_guards.items():
            if not response.HasField(field):
                continue
            if guard():
                continue
            blocked.append(field)
            callback = self._busy_callbacks.get(field)
            if callback is not None:
                callback()
        if not blocked:
            return response
        filtered = response.__class__()
        filtered.CopyFrom(response)
        for field in blocked:
            filtered.ClearField(field)
        return filtered

    async def _refresh_once(self, *, full: bool) -> PBUserDataResponse | None:
        fields: dict[str, Message] = {"client_info": self.state.user_data_client_info()}
        if not full and self.state.loaded_once:
            fields["timestamps"] = self.state.user_data_timestamps()
        try:
            response = await self.transport.post_proto(
                "/data/user-data/get", fields=fields, response_type="PBUserDataResponse"
            )
            if response is None:
                await self._notify_status(None)
                return None
            assert isinstance(response, PB.PBUserDataResponse)
            filtered = self._filter_busy_fields(response)
            domains = self._domains_in(filtered)
            self.state.apply_user_data(filtered)
        except Exception as err:
            await self._notify_status(err)
            raise
        await self._notify_status(None)
        await self._notify(domains)
        return response

    async def refresh(self, *, full: bool = False) -> PBUserDataResponse | None:
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
