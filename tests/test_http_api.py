from __future__ import annotations

import pytest

from anylist_sdk.proto import PB, encode
from anylist_sdk.services.http_api import AccountService, SharingService
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
    response = PB.PBAccountInfoResponse(
        firstName="Ada", lastName="Lovelace", email="ada@example.com"
    )
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


@pytest.mark.asyncio
async def test_share_list_mirrors_shared_user_and_matching_timestamp() -> None:
    state = AnyListState(user_id="user")
    state.shopping_lists["list"] = PB.ShoppingList(identifier="list", timestamp=5.0)
    response = PB.PBShareListOperationResponse(
        statusCode=0,
        originalListTimestamp=5.0,
        updatedListTimestamp=6.0,
        sharedUser=PB.PBEmailUserIDPair(
            email="friend@example.com", userId="friend", fullName="Friend"
        ),
    )
    transport = FakeTransport(response)
    service = SharingService(transport, "user", state)

    result = await service.share_list("list", "friend@example.com")

    assert result.statusCode == 0
    lst = state.shopping_lists["list"]
    assert [u.email for u in lst.sharedUsers] == ["friend@example.com"]
    # vK writes updatedListTimestamp only to a runtime-only JS property, not protobuf time.
    assert lst.timestamp == 5.0
    path, fields, response_type = transport.posts[-1]
    assert path == "/data/shopping-lists/share-list"
    assert fields["operation"].metadata.handlerId == "share-shopping-list"
    assert response_type == "PBShareListOperationResponse"


@pytest.mark.asyncio
async def test_share_list_rejects_email_mismatch_and_does_not_advance_stale_timestamp() -> None:
    state = AnyListState(user_id="user")
    state.shopping_lists["list"] = PB.ShoppingList(identifier="list", timestamp=9.0)
    response = PB.PBShareListOperationResponse(
        statusCode=0,
        originalListTimestamp=5.0,
        updatedListTimestamp=6.0,
        sharedUser=PB.PBEmailUserIDPair(email="other@example.com", userId="other"),
    )
    service = SharingService(FakeTransport(response), "user", state)

    await service.share_list("list", "friend@example.com")

    assert not state.shopping_lists["list"].sharedUsers
    assert state.shopping_lists["list"].timestamp == 9.0


@pytest.mark.asyncio
async def test_share_list_stale_response_adds_user_then_requests_refresh() -> None:
    state = AnyListState(user_id="user")
    state.shopping_lists["list"] = PB.ShoppingList(identifier="list", timestamp=9.0)
    response = PB.PBShareListOperationResponse(
        statusCode=0,
        originalListTimestamp=5.0,
        updatedListTimestamp=6.0,
        sharedUser=PB.PBEmailUserIDPair(email="friend@example.com", userId="friend"),
    )
    service = SharingService(FakeTransport(response), "user", state)
    refreshed = []

    async def refresh():
        refreshed.append("refresh")

    service.on_refresh_requested = refresh
    await service.share_list("list", "friend@example.com")

    assert [u.email for u in state.shopping_lists["list"].sharedUsers] == ["friend@example.com"]
    assert state.shopping_lists["list"].timestamp == 9.0
    assert refreshed == ["refresh"]


@pytest.mark.asyncio
async def test_share_list_deduplicates_by_existing_user_id_after_email_check() -> None:
    state = AnyListState(user_id="user")
    lst = PB.ShoppingList(identifier="list", timestamp=5.0)
    lst.sharedUsers.add(email="old@example.com", userId="same-user")
    state.shopping_lists["list"] = lst
    response = PB.PBShareListOperationResponse(
        statusCode=0,
        originalListTimestamp=5.0,
        updatedListTimestamp=6.0,
        sharedUser=PB.PBEmailUserIDPair(email="new@example.com", userId="same-user"),
    )
    service = SharingService(FakeTransport(response), "user", state)

    await service.share_list("list", "new@example.com")

    assert [u.email for u in lst.sharedUsers] == ["old@example.com"]


@pytest.mark.asyncio
async def test_share_list_response_email_uses_localized_compare() -> None:
    state = AnyListState(user_id="user")
    state.shopping_lists["list"] = PB.ShoppingList(identifier="list", timestamp=5.0)
    response = PB.PBShareListOperationResponse(
        statusCode=0,
        originalListTimestamp=5.0,
        updatedListTimestamp=6.0,
        sharedUser=PB.PBEmailUserIDPair(email="café@example.com", userId="friend"),
    )
    service = SharingService(FakeTransport(response), "user", state)

    await service.share_list("list", "cafe@example.com")

    assert [u.userId for u in state.shopping_lists["list"].sharedUsers] == ["friend"]


def test_photo_url_uses_official_s3_base_with_separator() -> None:
    from anylist_sdk.services.http_api import PhotosService

    assert PhotosService.url("abc") == "https://photos.anylist.com/abc.jpg"


@pytest.mark.asyncio
async def test_photo_byte_upload_refreshes_401_and_retries_same_server_filename() -> None:
    from aiohttp import web

    from anylist_sdk.services.http_api import PhotosService
    from anylist_sdk.transport import AnyListTransport
    from anylist_sdk.types import AuthTokens

    seen = []
    refreshes = []

    async def upload(request: web.Request):
        form = await request.post()
        photo = form["photo"]
        seen.append(
            (
                request.headers.get("Authorization"),
                form["filename"],
                photo.filename,
                photo.content_type,
                photo.file.read(),
            )
        )
        if request.headers.get("Authorization") == "Bearer old":
            return web.Response(status=401)
        return web.Response(body=b"ok")

    async def refresh(request: web.Request):
        refreshes.append(dict(await request.post()))
        return web.json_response({"access_token": "new", "refresh_token": "rotated"})

    app = web.Application()
    app.router.add_post("/data/photos/upload", upload)
    app.router.add_post("/auth/token/refresh", refresh)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = site._server.sockets[0].getsockname()[:2]
    try:
        transport = AnyListTransport(
            base_url=f"http://{host}:{port}",
            tokens=AuthTokens("user", "old", "refresh"),
            client_id="client",
        )
        try:
            photo_id = await PhotosService(transport).upload_bytes(
                b"image-bytes", content_type="image/png", filename="photo.png"
            )
        finally:
            await transport.close()
    finally:
        await runner.cleanup()

    assert len(photo_id) == 32 and "-" not in photo_id
    assert refreshes == [{"refresh_token": "refresh"}]
    assert [entry[0] for entry in seen] == ["Bearer old", "Bearer new"]
    assert seen[0][1] != seen[1][1]
    assert seen[1][1] == f"{photo_id}.jpg"
    assert seen[0][2] == seen[1][2] == "photo.png"
    assert seen[0][3] == seen[1][3] == "image/png"
    assert seen[0][4] == seen[1][4] == b"image-bytes"
