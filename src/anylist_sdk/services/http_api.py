from __future__ import annotations

import json
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

import aiohttp
from google.protobuf.message import Message

from ..identifiers import uuid4_hex
from ..normalization import localized_sort_key
from ..proto import PB, PBAccountInfoResponse, PBShareListOperationResponse, decode, encode
from ..state import AnyListState, clone
from ..exceptions import TransportError
from ..transport import AnyListTransport, PHOTOS_BASE_URL
from ..types import JSONMapping


class AccountService:
    def __init__(self, transport: AnyListTransport, state: AnyListState):
        self.transport = transport
        self.state = state

    def _store(self, info: PBAccountInfoResponse) -> PBAccountInfoResponse:
        self.state.account_info = clone(info)
        return info

    async def get(self) -> PBAccountInfoResponse:
        raw = await self.transport.request("GET", "/data/account/info", fields=None)
        info = decode("PBAccountInfoResponse", raw)
        assert isinstance(info, PB.PBAccountInfoResponse)
        return self._store(info)

    async def update_name(
        self, first_name: str, last_name: str, email: str | None = None
    ) -> PBAccountInfoResponse:
        # AnyList Web always includes the current account email when updating the name.
        # Prefer an explicitly supplied email, then the most recently synchronized account
        # info.  If neither exists we leave it absent rather than inventing an address.
        if email is None and self.state.account_info is not None:
            email = str(getattr(self.state.account_info, "email", "") or "")
        info = PB.PBAccountInfoResponse(firstName=first_name, lastName=last_name)
        if email:
            info.email = email
        response = await self.transport.post_proto(
            "/data/account/info",
            fields={"account_info": info},
            response_type="PBAccountInfoResponse",
        )
        assert isinstance(response, PB.PBAccountInfoResponse)
        return self._store(response)


class PhotosService:
    ACCEPTED_CONTENT_TYPES = frozenset(
        {
            "image/jpeg",
            "image/bmp",
            "image/gif",
            "image/png",
            "image/tiff",
            "image/webp",
            "image/avif",
        }
    )
    MAX_BYTES = 10 * 1024 * 1024

    def __init__(self, transport: AnyListTransport):
        self.transport = transport

    async def upload_bytes(
        self, data: bytes, *, content_type: str = "image/jpeg", filename: str | None = None
    ) -> str:
        if content_type not in self.ACCEPTED_CONTENT_TYPES:
            raise ValueError(f"Unsupported AnyList photo type {content_type}")
        if len(data) > self.MAX_BYTES:
            raise ValueError("AnyList Web limits photos to 10 MB")
        upload_filename = filename or "photo.jpg"

        def form(server_filename: str) -> aiohttp.FormData:
            value = aiohttp.FormData()
            value.add_field("photo", data, filename=upload_filename, content_type=content_type)
            # Dropzone's `sending` callback appends this separate server-side filename field.
            value.add_field("filename", server_filename)
            return value

        # Dropzone retries the same file after refreshing the access token on HTTP 401.
        # Rebuild FormData for the retry because aiohttp multipart writers are one-shot.
        stale_token = self.transport.tokens.access_token if self.transport.tokens else None
        for attempt in range(2):
            # Dropzone.uploadFile() re-emits `sending` on the 401 retry. AnyList's sending
            # callback generates a fresh UUID each time, so the retried server filename is
            # intentionally different from the failed attempt's filename.
            photo_id = uuid4_hex()
            server_filename = f"{photo_id}.jpg"
            headers = self.transport._auth_headers()
            try:
                async with self.transport.session.post(
                    f"{self.transport.base_url}/data/photos/upload",
                    data=form(server_filename),
                    headers=headers,
                ) as resp:
                    body = await resp.read()
                    if resp.status == 401 and attempt == 0:
                        await self.transport.refresh_access_token(stale_token=stale_token)
                        continue
                    if resp.status >= 400:
                        raise TransportError(
                            f"AnyList photo upload failed: HTTP {resp.status}: {body[:200]!r}"
                        )
                    return photo_id
            except aiohttp.ClientError as exc:
                raise TransportError("AnyList photo upload request failed") from exc
        raise TransportError("AnyList photo upload failed after token refresh")

    async def upload_url(self, url: str, *, photo_id: str | None = None) -> str:
        photo_id = photo_id or uuid4_hex()
        await self.transport.request(
            "POST", "/data/photos/upload-url", fields={"photo_url": url, "photo_id": photo_id}
        )
        return photo_id

    @staticmethod
    def url(photo_id: str) -> str:
        return f"{PHOTOS_BASE_URL}{photo_id}.jpg"


class SharingService:
    def __init__(
        self, transport: AnyListTransport, user_id: str, state: AnyListState | None = None
    ) -> None:
        self.transport = transport
        self.user_id = user_id
        self.state = state
        self.on_refresh_requested: Callable[[], Awaitable[object] | None] | None = None

    async def share_list(self, list_id: str, email: str) -> PBShareListOperationResponse:
        op = PB.PBListOperation(listId=list_id, updatedValue=email)
        op.metadata.operationId = uuid4_hex()
        op.metadata.handlerId = "share-shopping-list"
        op.metadata.userId = self.user_id
        response = await self.transport.post_proto(
            "/data/shopping-lists/share-list",
            fields={"operation": op},
            response_type="PBShareListOperationResponse",
        )
        assert isinstance(response, PB.PBShareListOperationResponse)
        if (
            self.state is not None
            and int(response.statusCode) == 0
            and response.HasField("sharedUser")
        ):
            lst = self.state.shopping_lists.get(list_id)
            shared = response.sharedUser
            # vK rejects an email-mismatched response using localizedCompare (Intl.Collator
            # numeric/base semantics), rather than poisoning local state.
            if lst is not None and localized_sort_key(str(shared.email)) == localized_sort_key(
                email
            ):
                stale = float(lst.timestamp) != float(response.originalListTimestamp)
                existing_emails = {str(user.email).casefold() for user in lst.sharedUsers}
                existing_user_ids = {
                    str(user.userId) for user in lst.sharedUsers if str(user.userId or "")
                }
                if str(shared.email).casefold() not in existing_emails and (
                    not str(shared.userId or "") or str(shared.userId) not in existing_user_ids
                ):
                    lst.sharedUsers.add().CopyFrom(shared)
                # The web client assigns updatedListTimestamp to a runtime-only
                # ``updatedTimestamp`` property that is not part of ShoppingList protobuf and
                # is never read elsewhere in app.js. Do not invent a wire timestamp mutation.
                # When the response was based on stale list state, vK immediately refreshes.
                if stale and self.on_refresh_requested is not None:
                    try:
                        refresh = self.on_refresh_requested()
                        if inspect.isawaitable(refresh):
                            await refresh
                    except Exception:
                        # The share already succeeded; the official follow-up refresh is a
                        # best-effort reconciliation and does not turn it into a failed share.
                        pass
        return response

    async def send_list_email(
        self, list_id: str, email: str, *, decimal_separator: str = "."
    ) -> JSONMapping:
        raw = await self.transport.request(
            "POST",
            "/data/shopping-lists/send-as-email",
            fields={"email": email, "list_id": list_id, "decimal_separator": decimal_separator},
        )
        return cast(JSONMapping, json.loads(raw or b"{}"))

    async def send_recipe_email(
        self,
        recipe_id: str,
        email: str,
        *,
        event_id: str | None = None,
        event_type: int | None = None,
    ) -> JSONMapping:
        fields = {"email": email, "recipe_id": recipe_id}
        if event_id is not None:
            fields["event_id"] = event_id
        if event_type is not None:
            fields["event_type"] = str(event_type)
        return cast(
            JSONMapping,
            json.loads(
                await self.transport.request("POST", "/data/recipes/send-as-email", fields=fields)
                or b"{}"
            ),
        )

    async def send_meal_plan_email(self, email: str, markup: str) -> JSONMapping:
        return cast(
            JSONMapping,
            json.loads(
                await self.transport.request(
                    "POST",
                    "/data/meal-planning-calendar/send-as-email",
                    fields={"email": email, "markup": markup},
                )
                or b"{}"
            ),
        )


class AlexaService:
    def __init__(self, transport: AnyListTransport):
        self.transport = transport

    async def link_list(
        self, *, alexa_list_id: str | None = None, anylist_list_id: str | None = None
    ) -> JSONMapping:
        fields = {}
        if alexa_list_id:
            fields["alexa_list_id"] = alexa_list_id
        if anylist_list_id:
            fields["anylist_list_id"] = anylist_list_id
        return cast(
            JSONMapping,
            json.loads(
                await self.transport.request("POST", "/data/alexa/link-list", fields=fields)
                or b"{}"
            ),
        )

    async def unlink_list(self, alexa_list_id: str) -> JSONMapping:
        return cast(
            JSONMapping,
            json.loads(
                await self.transport.request(
                    "POST", "/data/alexa/unlink-list", fields={"alexa_list_id": alexa_list_id}
                )
                or b"{}"
            ),
        )

    async def unlink_anylist_list(self, anylist_list_id: str) -> JSONMapping:
        return cast(
            JSONMapping,
            json.loads(
                await self.transport.request(
                    "POST",
                    "/data/alexa/unlink-anylist-list",
                    fields={"anylist_list_id": anylist_list_id},
                )
                or b"{}"
            ),
        )

    async def set_enabled_lists(self, enabled: list[str], disabled: list[str]) -> JSONMapping:
        a = PB.PBValue()
        a.stringValue.extend(enabled)
        b = PB.PBValue()
        b.stringValue.extend(disabled)
        raw = await self.transport.request(
            "POST",
            "/data/alexa/set-is-enabled-for-alexa-for-list-ids",
            fields={"enabled_list_ids": encode(a), "disabled_list_ids": encode(b)},
        )
        return cast(JSONMapping, json.loads(raw or b"{}"))


class WebStateService:
    """Small authenticated GETs used by AnyList Web to persist browser prompt cookies."""

    def __init__(self, transport: AnyListTransport):
        self.transport = transport

    async def mark_mac_app_download_prompt_seen(self) -> bytes:
        return await self.transport.request(
            "GET", "/data/web/set-mac-app-download-prompt-cookie", fields=None
        )

    async def mark_welcome_screen_seen(self) -> bytes:
        return await self.transport.request(
            "GET", "/data/web/set-welcome-screen-cookie", fields=None
        )


class RawAPI:
    """Official-protocol escape hatch: no invented endpoint semantics, just the same multipart/protobuf transport."""

    def __init__(self, transport: AnyListTransport):
        self.transport = transport

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        fields: Mapping[str, bytes | str | int | float] | None = None,
        authenticated: bool = True,
    ) -> bytes:
        return await self.transport.request(
            method, endpoint, fields=fields, authenticated=authenticated
        )

    async def post_proto(
        self,
        endpoint: str,
        *,
        fields: Mapping[str, Message | bytes | str | int | float],
        response_type: str | None = None,
    ) -> Message | bytes | None:
        return await self.transport.post_proto(endpoint, fields=fields, response_type=response_type)
