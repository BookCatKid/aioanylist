from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from anylist_sdk.realtime import HEARTBEAT, RealtimeClient
from anylist_sdk.types import AuthTokens, Domain


@dataclass
class DummyTransport:
    base_url: str = "https://www.anylist.com"
    client_id: str = "client"
    tokens: AuthTokens = AuthTokens("user", "access token", "refresh")


def test_realtime_url_uses_official_endpoint_and_escaping() -> None:
    realtime = RealtimeClient(DummyTransport())
    assert realtime._url() == (
        "wss://www.anylist.com/data/add-user-listener?client_id=client&access_token=access%20token"
    )


@pytest.mark.asyncio
async def test_dispatch_maps_invalidation_domain_and_heartbeat_is_not_an_invalidation() -> None:
    realtime = RealtimeClient(DummyTransport())
    seen = []
    realtime.add_listener(seen.append)
    await realtime._dispatch("refresh-shopping-lists")
    assert seen[-1].domain is Domain.SHOPPING_LISTS
    assert seen[-1].message == "refresh-shopping-lists"
    assert HEARTBEAT == "--heartbeat--"


@pytest.mark.asyncio
async def test_reconnect_listener_runs_only_after_first_successful_connection(monkeypatch) -> None:
    class Message:
        def __init__(self, type_, data=None): self.type=type_;self.data=data
    class WS:
        close_code = 1000
        async def receive(self):
            return Message(__import__("aiohttp").WSMsgType.CLOSED)
        async def close(self, code=1000): self.close_code=code
        async def send_str(self, value): pass
    class Context:
        async def __aenter__(self): return WS()
        async def __aexit__(self, *args): return False
    class Session:
        def ws_connect(self, *args, **kwargs): return Context()
    transport=DummyTransport()
    transport.session=Session()
    realtime=RealtimeClient(transport)
    calls=[]
    realtime.add_reconnect_listener(lambda: calls.append("reconnect"))

    assert await realtime._connection() == 1000
    assert calls == []
    realtime._stop.clear()
    assert await realtime._connection() == 1000
    assert calls == ["reconnect"]

@pytest.mark.asyncio
async def test_reconnect_callback_failure_does_not_kill_healthy_socket() -> None:
    class Message:
        def __init__(self, type_, data=None): self.type=type_;self.data=data
    class WS:
        close_code=1000
        async def receive(self): return Message(__import__('aiohttp').WSMsgType.CLOSED)
        async def close(self,code=1000): self.close_code=code
        async def send_str(self,value): pass
    class Context:
        async def __aenter__(self): return WS()
        async def __aexit__(self,*args): return False
    class Session:
        def ws_connect(self,*args,**kwargs): return Context()
    transport=DummyTransport();transport.session=Session()
    realtime=RealtimeClient(transport);realtime._has_connected_once=True
    async def broken(): raise RuntimeError('catchup failed')
    realtime.add_reconnect_listener(broken)
    assert await realtime._connection()==1000


@pytest.mark.asyncio
async def test_explicit_stop_resets_reconnect_session_state() -> None:
    realtime=RealtimeClient(DummyTransport())
    realtime._has_connected_once=True;realtime._retry_delay=8
    await realtime.stop()
    assert not realtime._has_connected_once and realtime._retry_delay==0.5
