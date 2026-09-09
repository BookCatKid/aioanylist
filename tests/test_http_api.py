from __future__ import annotations

import pytest

from anylist_sdk.proto import PB, encode
from anylist_sdk.services.http_api import AccountService
from anylist_sdk.state import AnyListState


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.posts = []

    async def request(self, method, path, fields=None):
        assert (method, path, fields) == ("GET", "/data/account/info", None)
        return encode(self.response)

    async def post_proto(self, path, *, fields, response_type):
        self.posts.append((path, fields, response_type))
        return self.response


@pytest.mark.asyncio
async def test_account_get_mirrors_account_info_into_state() -> None:
    state = AnyListState()
    response = PB.PBAccountInfoResponse(firstName="Ada", lastName="Lovelace", email="ada@example.com")
    service = AccountService(FakeTransport(response), state)
    result = await service.get()
    assert result.email == "ada@example.com"
    assert state.account_info is not result
    assert state.account_info.email == "ada@example.com"


@pytest.mark.asyncio
async def test_account_name_update_reuses_current_email_and_stores_response() -> None:
    state = AnyListState(account_info=PB.PBAccountInfoResponse(email="old@example.com"))
    response = PB.PBAccountInfoResponse(firstName="New", lastName="Name", email="old@example.com")
    transport = FakeTransport(response)
    service = AccountService(transport, state)
    result = await service.update_name("New", "Name")
    path, fields, response_type = transport.posts[0]
    assert path == "/data/account/info"
    assert response_type == "PBAccountInfoResponse"
    assert fields["account_info"].email == "old@example.com"
    assert result.firstName == "New"
    assert state.account_info.firstName == "New"
