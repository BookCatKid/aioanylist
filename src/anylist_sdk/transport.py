from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Awaitable, Callable, Mapping

import aiohttp
from google.protobuf.message import Message

from .identifiers import uuid4_hex
from .exceptions import AuthenticationError, PermissionDeniedError, ProtocolError, TransportError
from .proto import decode, encode
from .types import AuthTokens

BASE_URL = "https://www.anylist.com"
PHOTOS_BASE_URL = "https://photos.anylist.com/"
API_VERSION = "3"

TokenCallback = Callable[[AuthTokens], Awaitable[None] | None]


class AnyListTransport:
    """Official AnyList web transport: multipart form-data + protobuf, with JSON auth."""

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        *,
        base_url: str = BASE_URL,
        client_id: str | None = None,
        tokens: AuthTokens | None = None,
        token_callback: TokenCallback | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or uuid4_hex()
        self.tokens = tokens
        self.token_callback = token_callback
        self.request_timeout = request_timeout
        self._refresh_lock = asyncio.Lock()

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> "AnyListTransport":
        _ = self.session
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _auth_headers(self) -> dict[str, str]:
        if self.tokens is None:
            raise AuthenticationError("Not authenticated")
        return {
            "Authorization": f"Bearer {self.tokens.access_token}",
            "X-AnyLeaf-API-Version": API_VERSION,
            "X-AnyLeaf-Client-Identifier": self.client_id,
        }

    async def _publish_tokens(self, tokens: AuthTokens) -> None:
        self.tokens = tokens
        if self.token_callback is not None:
            result = self.token_callback(tokens)
            if asyncio.iscoroutine(result):
                await result

    @staticmethod
    def _json_or_error(raw: bytes) -> Mapping[str, Any]:
        try:
            value = json.loads(raw)
        except Exception as exc:
            raise ProtocolError("Expected JSON response from AnyList auth endpoint") from exc
        if not isinstance(value, dict):
            raise ProtocolError("Expected AnyList auth response to be a JSON object")
        return value

    async def sign_in(self, email: str, password: str) -> AuthTokens:
        form = aiohttp.FormData()
        form.add_field("email", email)
        form.add_field("password", password)
        try:
            async with self.session.post(f"{self.base_url}/auth/token", data=form) as response:
                raw = await response.read()
                if response.status >= 400:
                    raise AuthenticationError(f"AnyList sign-in failed with HTTP {response.status}")
        except aiohttp.ClientError as exc:
            raise TransportError("AnyList sign-in request failed") from exc
        data = self._json_or_error(raw)
        try:
            tokens = AuthTokens(
                user_id=str(data["user_id"]),
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                is_premium_user=(
                    bool(data["is_premium_user"]) if "is_premium_user" in data else None
                ),
                user_locale=(str(data["user_locale"]) if data.get("user_locale") else None),
            )
        except KeyError as exc:
            raise ProtocolError(f"Missing AnyList auth field: {exc.args[0]}") from exc
        await self._publish_tokens(tokens)
        return tokens

    async def refresh_access_token(self, *, force: bool = False, stale_token: str | None = None) -> AuthTokens:
        async with self._refresh_lock:
            if self.tokens is None:
                raise AuthenticationError("Cannot refresh without an authenticated session")
            if not force and stale_token and self.tokens.access_token != stale_token:
                return self.tokens

            form = aiohttp.FormData()
            form.add_field("refresh_token", self.tokens.refresh_token)
            try:
                async with self.session.post(
                    f"{self.base_url}/auth/token/refresh", data=form
                ) as response:
                    raw = await response.read()
                    if response.status >= 400:
                        raise AuthenticationError(
                            f"AnyList token refresh failed with HTTP {response.status}"
                        )
            except aiohttp.ClientError as exc:
                raise TransportError("AnyList token refresh request failed") from exc
            data = self._json_or_error(raw)
            try:
                tokens = replace(
                    self.tokens,
                    access_token=str(data["access_token"]),
                    refresh_token=str(data["refresh_token"]),
                )
            except KeyError as exc:
                raise ProtocolError(f"Missing AnyList refresh field: {exc.args[0]}") from exc
            await self._publish_tokens(tokens)
            return tokens

    async def logout(self) -> None:
        # The official token client exposes /auth/token and /auth/token/refresh, but app.js
        # never sends a bearer-authenticated logout request. Its only /auth/logout usage is a
        # browser-session HTML form carrying _xsrf + next. An SDK token session therefore has
        # no source-backed remote logout operation to reproduce; discard the local credentials
        # without inventing a request that the official token flow does not make.
        self.tokens = None

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        fields: Mapping[str, bytes | str | int | float] | None = None,
        authenticated: bool = True,
        retry_auth: bool = True,
        extra_headers: Mapping[str, str] | None = None,
    ) -> bytes:
        headers = dict(extra_headers or {})
        stale_token: str | None = None
        if authenticated:
            headers.update(self._auth_headers())
            stale_token = self.tokens.access_token if self.tokens else None

        form = aiohttp.FormData()
        for name, value in (fields or {}).items():
            if isinstance(value, (bytes, bytearray, memoryview)):
                form.add_field(name, value, filename=name, content_type="application/octet-stream")
            else:
                # AnyList's multipart builder accepts ordinary scalar form values. In
                # particular recipe-email event_type is passed as a number in app.js.
                form.add_field(name, str(value))
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"

        try:
            async with self.session.request(method, url, data=form, headers=headers) as response:
                body = await response.read()
                if response.status in (401, 4010) and authenticated and retry_auth:
                    await self.refresh_access_token(stale_token=stale_token)
                    return await self.request(
                        method,
                        endpoint,
                        fields=fields,
                        authenticated=authenticated,
                        retry_auth=False,
                        extra_headers=extra_headers,
                    )
                if response.status in (401, 403):
                    raise PermissionDeniedError(f"AnyList rejected {endpoint}: HTTP {response.status}")
                if response.status >= 400:
                    raise TransportError(
                        f"AnyList request {method} {endpoint} failed with HTTP {response.status}: "
                        f"{body[:300]!r}"
                    )
                return body
        except aiohttp.ClientError as exc:
            raise TransportError(f"AnyList request {method} {endpoint} failed") from exc

    async def post_proto(
        self,
        endpoint: str,
        *,
        fields: Mapping[str, Message | bytes | str | int | float],
        response_type: str | None = None,
    ) -> Message | bytes:
        encoded_fields: dict[str, bytes | str | int | float] = {}
        for name, value in fields.items():
            encoded_fields[name] = encode(value) if isinstance(value, Message) else value
        raw = await self.request("POST", endpoint, fields=encoded_fields)
        return decode(response_type, raw) if response_type else raw
