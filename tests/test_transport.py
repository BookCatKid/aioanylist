from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from anylist_sdk.transport import AnyListTransport
from anylist_sdk.types import AuthTokens


@asynccontextmanager
async def server(app: web.Application):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sock = site._server.sockets[0]
    host, port = sock.getsockname()[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_sign_in_uses_official_form_and_parses_auth_payload() -> None:
    seen = {}

    async def token(request: web.Request):
        form = await request.post()
        seen.update(form)
        return web.json_response({
            "user_id": "user",
            "access_token": "access",
            "refresh_token": "refresh",
            "is_premium_user": True,
            "user_locale": "de-DE",
        })

    app = web.Application()
    app.router.add_post("/auth/token", token)
    async with server(app) as base:
        async with AnyListTransport(base_url=base) as transport:
            tokens = await transport.sign_in("a@example.com", "secret")
    assert seen == {"email": "a@example.com", "password": "secret"}
    assert tokens == AuthTokens("user", "access", "refresh", True, "de-DE")


@pytest.mark.asyncio
async def test_authenticated_request_refreshes_once_and_retries_with_new_token() -> None:
    protected_calls = []
    refresh_forms = []

    async def refresh(request: web.Request):
        refresh_forms.append(dict(await request.post()))
        return web.json_response({"access_token": "new", "refresh_token": "new-refresh"})

    async def protected(request: web.Request):
        protected_calls.append(dict(request.headers))
        if request.headers.get("Authorization") == "Bearer old":
            return web.Response(status=401)
        return web.Response(body=b"ok")

    app = web.Application()
    app.router.add_post("/auth/token/refresh", refresh)
    app.router.add_post("/protected", protected)
    async with server(app) as base:
        async with AnyListTransport(
            base_url=base,
            client_id="0123456789abcdef0123456789abcdef",
            tokens=AuthTokens("user", "old", "refresh"),
        ) as transport:
            raw = await transport.request("POST", "/protected", fields={"x": "y"})
            assert transport.tokens.access_token == "new"
    assert raw == b"ok"
    assert refresh_forms == [{"refresh_token": "refresh"}]
    assert len(protected_calls) == 2
    for headers in protected_calls:
        assert headers["X-AnyLeaf-API-Version"] == "3"
        assert headers["X-AnyLeaf-Client-Identifier"] == "0123456789abcdef0123456789abcdef"
    assert protected_calls[0]["Authorization"] == "Bearer old"
    assert protected_calls[1]["Authorization"] == "Bearer new"


@pytest.mark.asyncio
async def test_concurrent_refreshes_are_serialized_and_share_rotated_token() -> None:
    calls = 0

    async def refresh(request: web.Request):
        nonlocal calls
        calls += 1
        return web.json_response({"access_token": "new", "refresh_token": "new-refresh"})

    app = web.Application()
    app.router.add_post("/auth/token/refresh", refresh)
    async with server(app) as base:
        async with AnyListTransport(
            base_url=base,
            tokens=AuthTokens("user", "old", "refresh"),
        ) as transport:
            import asyncio
            a, b = await asyncio.gather(
                transport.refresh_access_token(stale_token="old"),
                transport.refresh_access_token(stale_token="old"),
            )
    assert calls == 1
    assert a.access_token == b.access_token == "new"
