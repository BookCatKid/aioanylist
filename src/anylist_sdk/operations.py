from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from google.protobuf.message import Message

from .identifiers import uuid4_hex
from .proto import PB, decode, encode, message_class
from .transport import AnyListTransport
from .types import OperationAck

logger = logging.getLogger(__name__)


class OperationJournal(Protocol):
    async def save(self, queue_id: str, payload: bytes) -> None: ...
    async def load(self, queue_id: str) -> bytes | None: ...
    async def clear(self, queue_id: str) -> None: ...


class FileOperationJournal:
    """Optional durable pending-operation journal analogous to ALArchivedOperations."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def _path(self, queue_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in queue_id)
        return self.directory / f"{safe}.json"

    async def save(self, queue_id: str, payload: bytes) -> None:
        path = self._path(queue_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"operations": base64.b64encode(payload).decode("ascii")})
        await asyncio.to_thread(path.write_text, data, "utf-8")

    async def load(self, queue_id: str) -> bytes | None:
        path = self._path(queue_id)
        if not path.exists():
            return None
        data = json.loads(await asyncio.to_thread(path.read_text, "utf-8"))
        return base64.b64decode(data["operations"])

    async def clear(self, queue_id: str) -> None:
        path = self._path(queue_id)
        if path.exists():
            await asyncio.to_thread(path.unlink)


@dataclass(slots=True, frozen=True)
class QueueSpec:
    queue_id: str
    endpoint: str
    operation_type: str
    operation_list_type: str
    form_field: str = "operations"
    max_batch_size: int = 200


class OperationQueue:
    """Official-style ordered operation queue with server acknowledgements by operation ID."""

    def __init__(
        self,
        transport: AnyListTransport,
        spec: QueueSpec,
        *,
        user_id: str,
        journal: OperationJournal | None = None,
        on_response: Callable[[Message], Awaitable[None] | None] | None = None,
    ) -> None:
        self.transport = transport
        self.spec = spec
        self.user_id = user_id
        self.journal = journal
        self.on_response = on_response
        self._pending: list[Message] = []
        self._lock = asyncio.Lock()
        self._pause_count = 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def paused(self) -> bool:
        return self._pause_count > 0

    def pause(self) -> None:
        self._pause_count += 1

    async def resume(self, *, flush: bool = True) -> OperationAck | None:
        if self._pause_count:
            self._pause_count -= 1
        if flush and not self.paused:
            return await self.flush()
        return None

    def new_operation(
        self,
        handler_id: str,
        *,
        operation_class: int | None = None,
        operation_version: int | None = None,
        **fields: Any,
    ) -> Message:
        op = message_class(self.spec.operation_type)()
        metadata = PB.PBOperationMetadata(
            operationId=uuid4_hex(), handlerId=handler_id, userId=self.user_id
        )
        if operation_class is not None:
            metadata.operationClass = operation_class
        if operation_version is not None:
            metadata.operationVersion = operation_version
        op.metadata.CopyFrom(metadata)
        for name, value in fields.items():
            field = op.DESCRIPTOR.fields_by_name.get(name)
            if field is None:
                raise TypeError(f"{self.spec.operation_type} has no field {name!r}")
            target = getattr(op, name)
            if field.is_repeated:
                if field.message_type:
                    for item in value:
                        target.add().CopyFrom(item)
                else:
                    target.extend(value)
            elif field.message_type:
                target.CopyFrom(value)
            else:
                setattr(op, name, value)
        return op

    async def enqueue(self, operation: Message, *, flush: bool = True) -> str:
        if operation.DESCRIPTOR.name != self.spec.operation_type:
            raise TypeError(
                f"Expected {self.spec.operation_type}, got {operation.DESCRIPTOR.name}"
            )
        # Serialize pending-list mutations with flush. Without this, a producer could append
        # while flush is replacing _pending after acknowledgements and lose a newly queued op.
        async with self._lock:
            self._pending.append(operation)
            await self._persist()
        operation_id = operation.metadata.operationId
        if flush and not self.paused:
            await self.flush()
        return operation_id

    async def add(self, handler_id: str, *, flush: bool = True, **fields: Any) -> str:
        return await self.enqueue(self.new_operation(handler_id, **fields), flush=flush)

    def _as_list_message(self, operations: list[Message] | None = None) -> Message:
        result = message_class(self.spec.operation_list_type)()
        for operation in operations if operations is not None else self._pending:
            result.operations.add().CopyFrom(operation)
        return result

    async def _persist(self) -> None:
        if self.journal is None:
            return
        if not self._pending:
            await self.journal.clear(self.spec.queue_id)
            return
        await self.journal.save(self.spec.queue_id, encode(self._as_list_message()))

    async def restore(self) -> int:
        if self.journal is None:
            return 0
        raw = await self.journal.load(self.spec.queue_id)
        if not raw:
            return 0
        restored = decode(self.spec.operation_list_type, raw)
        async with self._lock:
            self._pending = []
            for op in restored.operations:
                clone = message_class(self.spec.operation_type)()
                clone.CopyFrom(op)
                self._pending.append(clone)
        return len(self._pending)

    async def flush(self) -> OperationAck | None:
        if self.paused or not self._pending:
            return None
        async with self._lock:
            if self.paused or not self._pending:
                return None
            all_processed: list[str] = []
            last_response: Message | None = None
            while self._pending and not self.paused:
                batch = self._pending[: self.spec.max_batch_size]
                request = self._as_list_message(batch)
                response = await self.transport.post_proto(
                    self.spec.endpoint,
                    fields={self.spec.form_field: request},
                    response_type="PBEditOperationResponse",
                )
                assert isinstance(response, Message)
                last_response = response
                if self.on_response is not None:
                    callback_result = self.on_response(response)
                    if asyncio.iscoroutine(callback_result):
                        await callback_result
                processed = list(response.processedOperations)
                removed = 0
                # AnyList Web walks processed IDs in order and only shifts when each ID
                # matches the *current* queue head. A mismatch is reported but does not
                # discard local work. Mirror that behavior instead of treating the response
                # as an unordered acknowledgement set.
                for operation_id in processed:
                    if self._pending and self._pending[0].metadata.operationId == operation_id:
                        del self._pending[0]
                        removed += 1
                    else:
                        local_id = (
                            self._pending[0].metadata.operationId if self._pending else None
                        )
                        logger.error(
                            "AnyList operation acknowledgement mismatch for %s: "
                            "processed=%s local_head=%s",
                            self.spec.endpoint,
                            operation_id,
                            local_id,
                        )
                all_processed.extend(processed)
                await self._persist()

                # KO schedules another request only when at least one operation was
                # acknowledged and the remaining queue has fallen to <= the 200-op batch
                # limit. In particular, a 401-op queue becomes 201 after the first request
                # and stops until a later queue trigger. A partial ack from a small queue is
                # retried immediately. Zero-ack responses retain the queue without spinning.
                if not self._pending or not processed:
                    break
                if len(self._pending) > self.spec.max_batch_size:
                    break
                # A malformed response containing only mismatched IDs would make the web
                # client reschedule indefinitely. Avoid a synchronous hot loop while keeping
                # the same retained-queue semantics.
                if removed == 0:
                    break
            if last_response is None:
                return None
            return OperationAck(tuple(all_processed), last_response)
