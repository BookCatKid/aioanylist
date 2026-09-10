from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from anylist_sdk import AnyListClient


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


@pytest.fixture(scope="session")
def live_mutation_list_id() -> str:
    if os.environ.get("ANYLIST_LIVE_MUTATIONS") != "1":
        pytest.skip("set ANYLIST_LIVE_MUTATIONS=1 to enable mutation conformance tests")
    return _required_env("ANYLIST_LIVE_LIST_ID")


@pytest_asyncio.fixture
async def live_client(live_credentials: tuple[str, str, str]) -> AsyncIterator[AnyListClient]:
    email, password, base_url = live_credentials
    client = AnyListClient(base_url=base_url)
    try:
        await client.sign_in(email, password)
        yield client
    finally:
        await client.close()
