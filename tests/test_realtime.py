from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import aiohttp
import pytest

from aioanylist.exceptions import AnyListError, AuthenticationError, TransportError
from aioanylist.realtime import HEARTBEAT, INITIAL_RETRY_DELAY, RealtimeClient
from aioanylist.types import AuthTokens, Domain


@dataclass
class DummyTransport:
    base_url: str = "https://www.anylist.com"
    client_id: str = "client"
    tokens: AuthTokens = field(
        default_factory=lambda: AuthTokens("user", "access token", "refresh")
    )


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
        def __init__(self, type_, data=None):
            self.type = type_
            self.data = data

    class WS:
        close_code = 1000

        async def receive(self):
            return Message(__import__("aiohttp").WSMsgType.CLOSED)

        async def close(self, code=1000):
            self.close_code = code

        async def send_str(self, value):
            pass

    class Context:
        async def __aenter__(self):
            return WS()

        async def __aexit__(self, *args):
            return False

    class Session:
        def ws_connect(self, *args, **kwargs):
            return Context()

    transport = DummyTransport()
    transport.session = Session()
    realtime = RealtimeClient(transport)
    calls = []
    realtime.add_reconnect_listener(lambda: calls.append("reconnect"))
    errors: list[Exception] = []

    def on_error(error: Exception) -> None:
        assert not realtime.connected.is_set()
        errors.append(error)

    realtime.add_error_listener(on_error)

    assert await realtime._connection() == 1000
    assert calls == []
    assert len(errors) == 1
    assert isinstance(errors[0], TransportError)
    assert await realtime._connection() == 1000
    assert calls == ["reconnect"]
    assert len(errors) == 2


@pytest.mark.asyncio
async def test_reconnect_callback_failure_does_not_kill_healthy_socket() -> None:
    class Message:
        def __init__(self, type_, data=None):
            self.type = type_
            self.data = data

    class WS:
        close_code = 1000

        async def receive(self):
            return Message(__import__("aiohttp").WSMsgType.CLOSED)

        async def close(self, code=1000):
            self.close_code = code

        async def send_str(self, value):
            pass

    class Context:
        async def __aenter__(self):
            return WS()

        async def __aexit__(self, *args):
            return False

    class Session:
        def ws_connect(self, *args, **kwargs):
            return Context()

    transport = DummyTransport()
    transport.session = Session()
    realtime = RealtimeClient(transport)
    realtime._has_connected_once = True

    async def broken():
        raise RuntimeError("catchup failed")

    realtime.add_reconnect_listener(broken)
    assert await realtime._connection() == 1000


@pytest.mark.asyncio
async def test_explicit_stop_resets_reconnect_session_state() -> None:
    realtime = RealtimeClient(DummyTransport())
    realtime._has_connected_once = True
    realtime._retry_delay = 8
    await realtime.stop()
    assert not realtime._has_connected_once and realtime._retry_delay == 0.5


@pytest.mark.asyncio
async def test_start_without_authentication_fails_instead_of_hanging() -> None:
    transport = DummyTransport()
    transport.tokens = None
    realtime = RealtimeClient(transport)
    from aioanylist.exceptions import AuthenticationError

    with pytest.raises(AuthenticationError):
        await realtime.start()


@pytest.mark.asyncio
async def test_listener_failure_does_not_prevent_other_listeners_or_kill_dispatch() -> None:
    realtime = RealtimeClient(DummyTransport())
    seen = []

    async def broken(_event):
        raise RuntimeError("consumer bug")

    realtime.add_listener(broken)
    realtime.add_listener(lambda event: seen.append(event.message))
    await realtime._dispatch("refresh-shopping-lists")
    assert seen == ["refresh-shopping-lists"]


@pytest.mark.asyncio
async def test_retry_delay_resets_two_seconds_after_open_independent_of_frames(monkeypatch) -> None:
    import aioanylist.realtime as realtime_module

    monkeypatch.setattr(realtime_module, "RETRY_RESET_DELAY", 0.01)

    class Message:
        def __init__(self, type_):
            self.type = type_
            self.data = None

    class WS:
        close_code = 1006

        async def receive(self):
            await asyncio.sleep(0.02)
            return Message(__import__("aiohttp").WSMsgType.CLOSED)

        async def close(self, code=1000):
            self.close_code = code

        async def send_str(self, value):
            pass

    class Context:
        async def __aenter__(self):
            return WS()

        async def __aexit__(self, *args):
            return False

    class Session:
        def ws_connect(self, *args, **kwargs):
            return Context()

    transport = DummyTransport()
    transport.session = Session()
    realtime = RealtimeClient(transport)
    realtime._retry_delay = 8.0

    assert await realtime._connection() == 1006
    assert realtime._retry_delay == 0.5


@pytest.mark.asyncio
async def test_retry_reset_timer_is_cancelled_when_socket_closes_early(monkeypatch) -> None:
    import aioanylist.realtime as realtime_module

    monkeypatch.setattr(realtime_module, "RETRY_RESET_DELAY", 0.02)

    class Message:
        def __init__(self, type_):
            self.type = type_
            self.data = None

    class WS:
        close_code = 1006

        async def receive(self):
            return Message(__import__("aiohttp").WSMsgType.CLOSED)

        async def close(self, code=1000):
            self.close_code = code

        async def send_str(self, value):
            pass

    class Context:
        async def __aenter__(self):
            return WS()

        async def __aexit__(self, *args):
            return False

    class Session:
        def ws_connect(self, *args, **kwargs):
            return Context()

    transport = DummyTransport()
    transport.session = Session()
    realtime = RealtimeClient(transport)
    realtime._retry_delay = 8.0

    assert await realtime._connection() == 1006
    await asyncio.sleep(0.03)
    assert realtime._retry_delay == 8.0


@pytest.mark.asyncio
async def test_slow_invalidation_callback_does_not_block_socket_dispatch() -> None:
    realtime = RealtimeClient(DummyTransport())
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(_event):
        started.set()
        await release.wait()

    realtime.add_listener(slow)
    await realtime._dispatch("refresh-shopping-lists")
    await asyncio.wait_for(started.wait(), timeout=0.1)
    assert realtime._callback_tasks
    # _dispatch returned before the slow refresh finished, matching the web manager's
    # fire-and-return AJAX invalidation behavior.
    release.set()
    await asyncio.gather(*tuple(realtime._callback_tasks))


@pytest.mark.asyncio
async def test_three_missed_heartbeats_force_close_after_two_sends(monkeypatch) -> None:
    import aioanylist.realtime as realtime_module

    monkeypatch.setattr(realtime_module, "HEARTBEAT_INTERVAL", 0.005)

    class WS:
        close_code = None

        def __init__(self):
            self.sent = []
            self.closed = False

        async def receive(self):
            await asyncio.Event().wait()

        async def close(self, code=1000):
            self.close_code = code
            self.closed = True

        async def send_str(self, value):
            self.sent.append(value)

    ws = WS()

    class Context:
        async def __aenter__(self):
            return ws

        async def __aexit__(self, *args):
            return False

    class Session:
        def ws_connect(self, *args, **kwargs):
            return Context()

    transport = DummyTransport()
    transport.session = Session()
    realtime = RealtimeClient(transport)

    assert await asyncio.wait_for(realtime._connection(), timeout=0.1) == 1006
    assert ws.sent == [HEARTBEAT, HEARTBEAT]
    assert ws.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refresh_failure", "reconnect_failure", "expected_error"),
    [
        (None, False, None),
        (AuthenticationError("Expired"), False, AuthenticationError),
        (TransportError("Refresh request failed"), False, TransportError),
        (RuntimeError("Unexpected refresh failure"), False, TransportError),
        (None, True, TransportError),
    ],
)
async def test_4010_token_refresh_reports_only_real_outages(
    monkeypatch, refresh_failure, reconnect_failure, expected_error
) -> None:
    class WS:
        close_code = 4010

        async def receive(self):
            return aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, self.close_code, "")

        async def close(self, *, code):
            assert code == 1000

    class Context:
        def __init__(self, attempt):
            self.attempt = attempt

        async def __aenter__(self):
            if self.attempt == 2 and reconnect_failure:
                raise aiohttp.ClientConnectionError("Reconnect failed")
            return WS()

        async def __aexit__(self, *args):
            return False

    class Session:
        attempts = 0

        def ws_connect(self, *args, **kwargs):
            self.attempts += 1
            return Context(self.attempts)

    class Transport(DummyTransport):
        session = Session()
        refreshes = 0

        async def refresh_access_token(self, *, force=False):
            assert force
            self.refreshes += 1
            if refresh_failure is not None:
                raise refresh_failure
            return self.tokens

    transport = Transport()
    realtime = RealtimeClient(transport)
    errors: list[AnyListError] = []
    reconnects = []
    backoffs = []
    realtime.add_error_listener(errors.append)

    def on_reconnect():
        reconnects.append(True)
        realtime._stop.set()

    realtime.add_reconnect_listener(on_reconnect)

    async def sleep(delay):
        backoffs.append(delay)
        realtime._stop.set()

    monkeypatch.setattr(asyncio, "sleep", sleep)
    await realtime._run()

    assert transport.refreshes == 1
    if expected_error is None:
        assert transport.session.attempts == 2
        assert reconnects == [True]
        assert not errors
        assert not backoffs
    else:
        assert transport.session.attempts == (2 if reconnect_failure else 1)
        assert not reconnects
        assert len(errors) == 1
        assert type(errors[0]) is expected_error
        if isinstance(refresh_failure, AnyListError):
            assert errors[0] is refresh_failure
        assert backoffs == [INITIAL_RETRY_DELAY]


@pytest.mark.asyncio
async def test_retry_backoff_caps_at_120_seconds(monkeypatch) -> None:
    realtime = RealtimeClient(DummyTransport())
    codes = iter([1006] * 9 + [None])

    async def connection():
        code = next(codes)
        if code is None:
            realtime._stop.set()
            return 1000
        return code

    realtime._connection = connection
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await realtime._run()

    assert sleeps == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 120.0]
    assert realtime._retry_delay == 120.0


@pytest.mark.asyncio
async def test_stop_during_backoff_cancels_runner_immediately(monkeypatch) -> None:
    realtime = RealtimeClient(DummyTransport())

    async def connection():
        return 1006

    realtime._connection = connection
    sleeping = asyncio.Event()

    async def blocking_sleep(_delay):
        sleeping.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "sleep", blocking_sleep)
    realtime._task = asyncio.create_task(realtime._run())
    await asyncio.wait_for(sleeping.wait(), timeout=0.1)

    await asyncio.wait_for(realtime.stop(), timeout=0.1)

    assert realtime._task is None
    assert not realtime.connected.is_set()
    assert realtime._retry_delay == 0.5


@pytest.mark.asyncio
async def test_explicit_stop_cancels_inflight_callback_tasks() -> None:
    realtime = RealtimeClient(DummyTransport())
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(_event):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    realtime.add_listener(slow)
    await realtime._dispatch("refresh-shopping-lists")
    await asyncio.wait_for(started.wait(), timeout=0.1)

    await realtime.stop()

    assert cancelled.is_set()
    assert not realtime._callback_tasks


@pytest.mark.asyncio
async def test_explicit_stop_does_not_report_outage() -> None:
    transport = DummyTransport()
    waiting = asyncio.Event()

    class WS:
        async def receive(self):
            waiting.set()
            await asyncio.Event().wait()

    class Context:
        async def __aenter__(self):
            return WS()

        async def __aexit__(self, *args):
            return False

    class Session:
        def ws_connect(self, *args, **kwargs):
            return Context()

    transport.session = Session()
    realtime = RealtimeClient(transport)
    errors: list[Exception] = []
    realtime.add_error_listener(errors.append)

    await realtime.start()
    await waiting.wait()
    await realtime.stop()

    assert not realtime.connected.is_set()
    assert not errors


@pytest.mark.asyncio
async def test_async_error_listeners_run_without_blocking_or_propagating() -> None:
    realtime = RealtimeClient(DummyTransport())
    error = TransportError("Socket lost")
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(received: AnyListError) -> None:
        assert received is error
        started.set()
        await release.wait()
        raise RuntimeError("Listener failed")

    seen: list[AnyListError] = []
    realtime.add_error_listener(slow)
    realtime.add_error_listener(seen.append)

    realtime._notify_error(error)

    assert seen == [error]
    assert not started.is_set()
    await asyncio.wait_for(started.wait(), timeout=0.1)
    (tracked,) = realtime._callback_tasks
    release.set()
    await asyncio.wait({tracked})
    assert not realtime._callback_tasks


@pytest.mark.asyncio
async def test_immediate_stop_cancels_scheduled_error_listener() -> None:
    realtime = RealtimeClient(DummyTransport())
    received = []

    async def listener(error: AnyListError) -> None:
        received.append(error)

    realtime.add_error_listener(listener)
    realtime._notify_error(TransportError("Socket lost"))
    (tracked,) = realtime._callback_tasks

    await realtime.stop()

    assert tracked.cancelled()
    assert not received
    assert not realtime._callback_tasks


@pytest.mark.asyncio
async def test_unrequested_normal_close_reconnects(monkeypatch) -> None:
    realtime = RealtimeClient(DummyTransport())
    calls = 0
    backoffs = []

    async def connection():
        nonlocal calls
        calls += 1
        if calls == 2:
            realtime._stop.set()
        return 1000

    async def sleep(delay):
        backoffs.append(delay)

    monkeypatch.setattr(realtime, "_connection", connection)
    monkeypatch.setattr(asyncio, "sleep", sleep)

    await realtime._run()

    assert calls == 2
    assert backoffs == [INITIAL_RETRY_DELAY]
