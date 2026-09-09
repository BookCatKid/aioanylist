from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from google.protobuf.message import Message

from anylist_sdk.proto import PB, message_class


@dataclass
class FakeTransport:
    responses: list[Any] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any], str | None]] = field(default_factory=list)

    async def post_proto(self, endpoint: str, *, fields: dict[str, Any], response_type: str | None = None):
        self.calls.append((endpoint, fields, response_type))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        if response_type == "PBEditOperationResponse":
            response = PB.PBEditOperationResponse()
            operation_list = next(
                (v for v in fields.values() if isinstance(v, Message) and "operations" in v.DESCRIPTOR.fields_by_name),
                None,
            )
            if operation_list is not None:
                response.processedOperations.extend(
                    op.metadata.operationId for op in operation_list.operations
                )
            return response
        if response_type:
            return message_class(response_type)()
        return b""

    async def request(self, method: str, endpoint: str, *, fields=None, authenticated: bool = True):
        self.calls.append((f"{method} {endpoint}", fields or {}, None))
        if self.responses:
            return self.responses.pop(0)
        return b""


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()
