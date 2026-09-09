from __future__ import annotations

from typing import Any, TypeVar

from google.protobuf.message import Message

from ..operations import OperationJournal, OperationQueue, QueueSpec
from ..proto import message_class
from ..state import AnyListState
from ..transport import AnyListTransport
from ..types import OperationAck


class OperationService:
    """Base for an AnyList domain backed by the official PB*Operation queue."""

    def __init__(
        self,
        transport: AnyListTransport,
        state: AnyListState,
        *,
        user_id: str,
        spec: QueueSpec,
        journal: OperationJournal | None = None,
    ) -> None:
        self.transport = transport
        self.state = state
        self.queue = OperationQueue(
            transport, spec, user_id=user_id, journal=journal
        )

    async def operation(
        self,
        handler_id: str,
        *,
        flush: bool = True,
        operation_class: int | None = None,
        operation_version: int | None = None,
        **fields: Any,
    ) -> str:
        op = self.queue.new_operation(
            handler_id,
            operation_class=operation_class,
            operation_version=operation_version,
            **fields,
        )
        return await self.queue.enqueue(op, flush=flush)

    async def flush(self) -> OperationAck | None:
        return await self.queue.flush()

    def pause(self) -> None:
        self.queue.pause()

    async def resume(self, *, flush: bool = True) -> OperationAck | None:
        return await self.queue.resume(flush=flush)

    async def restore(self) -> int:
        return await self.queue.restore()


def partial_message(type_name: str, **fields: Any) -> Message:
    """Build a partial proto using official field names."""
    msg = message_class(type_name)()
    for name, value in fields.items():
        if value is None:
            continue
        field = msg.DESCRIPTOR.fields_by_name.get(name)
        if field is None:
            raise TypeError(f"{type_name} has no field {name!r}")
        target = getattr(msg, name)
        if field.is_repeated:
            if field.message_type:
                for item in value:
                    target.add().CopyFrom(item)
            else:
                target.extend(value)
        elif field.message_type:
            target.CopyFrom(value)
        else:
            setattr(msg, name, value)
    return msg


_MessageT = TypeVar("_MessageT", bound=Message)


def clone_message(msg: _MessageT) -> _MessageT:
    out = msg.__class__()
    out.CopyFrom(msg)
    return out
