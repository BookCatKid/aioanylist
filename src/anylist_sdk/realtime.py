from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from .exceptions import AuthenticationError, TransportError
from .transport import AnyListTransport
from .types import Domain

HEARTBEAT = "--heartbeat--"

INVALIDATION_DOMAINS: dict[str, Domain | None] = {
    "refresh-shopping-lists": Domain.SHOPPING_LISTS,
    "refresh-categorized-items": Domain.CATEGORIZED_ITEMS,
    "refresh-list-folders": Domain.LIST_FOLDERS,
    "refresh-list-settings": Domain.LIST_SETTINGS,
    "refresh-starter-lists": Domain.STARTER_LISTS,
    "refresh-ordered-starter-list-ids": Domain.STARTER_LISTS,
    "refresh-starter-list-settings": Domain.STARTER_LIST_SETTINGS,
    "refresh-mobile-app-settings": Domain.MOBILE_SETTINGS,
    "refresh-user-categories": Domain.USER_CATEGORIES,
    "refresh-user-recipe-data": Domain.RECIPES,
    "refresh-meal-plan-calendar": Domain.MEAL_PLAN,
    "refresh-account-info": Domain.ACCOUNT,
    "refresh-subscription-info": Domain.SUBSCRIPTION,
    "did-delete-account": None,
}


@dataclass(slots=True, frozen=True)
class RealtimeEvent:
    message: str
    domain: Domain | None


EventCallback = Callable[[RealtimeEvent], Awaitable[None] | None]
ReconnectCallback = Callable[[], Awaitable[None] | None]


class RealtimeClient:
    """AnyList's text invalidation WebSocket, including its heartbeat and retry policy."""

    def __init__(self, transport: AnyListTransport) -> None:
        self.transport = transport
        self._callbacks: list[EventCallback] = []
        self._reconnect_callbacks: list[ReconnectCallback] = []
        self._events: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.connected = asyncio.Event()
        self._retry_delay = 0.5
        self._has_connected_once = False

    def add_listener(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    def add_reconnect_listener(self, callback: ReconnectCallback) -> None:
        """Run after a successful automatic reconnect, matching AnyList Web's catch-up load."""
        self._reconnect_callbacks.append(callback)

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        while True:
            yield await self._events.get()

    async def start(self) -> None:
        if self.transport.tokens is None:
            raise AuthenticationError("Realtime connection requires authentication")
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="anylist-realtime")
        if self.connected.is_set():
            return
        # Wait for the first successful open, but also observe an unexpectedly terminated
        # runner so callers never hang forever on a task that has already failed.
        connected_wait = asyncio.create_task(self.connected.wait())
        done, pending = await asyncio.wait(
            {connected_wait, self._task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            if task is connected_wait:
                task.cancel()
        if self.connected.is_set():
            return
        if self._task.done():
            exc = self._task.exception()
            if exc is not None:
                raise exc
            raise TransportError("AnyList realtime task stopped before connecting")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.connected.clear()
        # An explicit stop/start is a new realtime session, not an automatic reconnect.
        self._has_connected_once = False
        self._retry_delay = 0.5

    def _url(self) -> str:
        if self.transport.tokens is None:
            raise AuthenticationError("Realtime connection requires authentication")
        base = self.transport.base_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return (
            f"{base}/data/add-user-listener"
            f"?client_id={quote(self.transport.client_id, safe='')}"
            f"&access_token={quote(self.transport.tokens.access_token, safe='')}"
        )

    async def _dispatch(self, message: str) -> None:
        event = RealtimeEvent(message, INVALIDATION_DOMAINS.get(message))
        await self._events.put(event)
        for callback in tuple(self._callbacks):
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                # External SDK listeners are not part of AnyList's transport protocol. A
                # consumer callback failure must not tear down an otherwise healthy socket.
                continue

    async def _connection(self) -> int:
        try:
            async with self.transport.session.ws_connect(self._url(), heartbeat=None) as ws:
                was_reconnect = self._has_connected_once
                self._has_connected_once = True
                self.connected.set()
                if was_reconnect:
                    for callback in tuple(self._reconnect_callbacks):
                        try:
                            result = callback()
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            # Catch-up is best-effort. A transient account/user-data fetch
                            # must not tear down an otherwise healthy WebSocket.
                            continue
                missed = 0
                opened_at = asyncio.get_running_loop().time()
                next_heartbeat = opened_at + 5.0
                while not self._stop.is_set():
                    timeout = max(0.0, next_heartbeat - asyncio.get_running_loop().time())
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
                    except asyncio.TimeoutError:
                        missed += 1
                        if missed >= 3:
                            await ws.close()
                            return 1006
                        await ws.send_str(HEARTBEAT)
                        next_heartbeat = asyncio.get_running_loop().time() + 5.0
                        continue

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        if msg.data == HEARTBEAT:
                            missed = 0
                        else:
                            await self._dispatch(str(msg.data))
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                        return int(ws.close_code or 1000)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        return int(ws.close_code or 1006)

                    now = asyncio.get_running_loop().time()
                    if now >= next_heartbeat:
                        missed += 1
                        if missed >= 3:
                            await ws.close()
                            return 1006
                        await ws.send_str(HEARTBEAT)
                        next_heartbeat = now + 5.0

                    # Official web resets the retry delay two seconds after a successful open.
                    if now - opened_at >= 2.0:
                        self._retry_delay = 0.5
                await ws.close(code=1000)
                return 1000
        except aiohttp.ClientError as exc:
            self.connected.clear()
            if self._stop.is_set():
                return 1000
            raise TransportError("AnyList WebSocket connection failed") from exc
        finally:
            self.connected.clear()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                code = await self._connection()
            except TransportError:
                code = 1006
            if self._stop.is_set() or code == 1000:
                return
            if code == 4010:
                try:
                    await self.transport.refresh_access_token(force=True)
                    # Official app reconnects immediately after successful 4010 refresh.
                    continue
                except Exception:
                    pass
            await asyncio.sleep(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 2.0, 120.0)
