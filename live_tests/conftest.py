from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio

from anylist_sdk import AnyListClient
from anylist_sdk.transport import AnyListTransport
from anylist_sdk.types import AuthTokens


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required for live AnyList conformance tests")
    return value


@pytest.fixture(scope="session")
def live_credentials() -> tuple[str, str, str]:
    if os.environ.get("ANYLIST_LIVE") != "1":
        pytest.skip("set ANYLIST_LIVE=1 to enable live AnyList conformance tests")
    return (
        _required_env("ANYLIST_EMAIL"),
        _required_env("ANYLIST_PASSWORD"),
        os.environ.get("ANYLIST_BASE_URL", "https://www.anylist.com"),
    )


@dataclass(slots=True)
class LiveAuthSession:
    email: str
    base_url: str
    tokens: AuthTokens


@pytest.fixture(scope="session")
def live_auth_session(live_credentials: tuple[str, str, str]) -> LiveAuthSession:
    """Authenticate once per live pytest run and reuse/rotate that token set safely."""

    email, password, base_url = live_credentials

    async def authenticate() -> AuthTokens:
        transport = AnyListTransport(base_url=base_url)
        try:
            return await transport.sign_in(email, password)
        finally:
            await transport.close()

    try:
        tokens = asyncio.run(authenticate())
    except Exception as exc:
        # Suppress the normal traceback here: sign_in's Python frame contains the credential
        # arguments, and live-test failures must never echo those values to test output.
        pytest.fail(
            f"live AnyList authentication failed: {type(exc).__name__}: {exc}",
            pytrace=False,
        )
    return LiveAuthSession(email=email, base_url=base_url, tokens=tokens)


@pytest.fixture(scope="session")
def live_mutation_list_id() -> str:
    if os.environ.get("ANYLIST_LIVE_MUTATIONS") != "1":
        pytest.skip("set ANYLIST_LIVE_MUTATIONS=1 to enable mutation conformance tests")
    return _required_env("ANYLIST_LIVE_LIST_ID")


@pytest_asyncio.fixture
async def live_client(live_auth_session: LiveAuthSession) -> AsyncIterator[AnyListClient]:
    session = live_auth_session
    client = AnyListClient(
        base_url=session.base_url, tokens=session.tokens, user_email=session.email
    )

    def remember_rotated_tokens(tokens: AuthTokens) -> None:
        session.tokens = tokens

    client.transport.token_callback = remember_rotated_tokens
    try:
        yield client
    finally:
        if client.transport.tokens is not None:
            session.tokens = client.transport.tokens
        await client.close()
