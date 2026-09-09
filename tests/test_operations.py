from __future__ import annotations

import asyncio

import pytest

from anylist_sdk.operations import FileOperationJournal, OperationQueue, QueueSpec
from anylist_sdk.proto import PB


@pytest.mark.asyncio
async def test_operation_metadata_and_200_batching(fake_transport) -> None:
    queue = OperationQueue(
        fake_transport,
        QueueSpec("q", "/update", "PBListOperation", "PBListOperationList"),
        user_id="user",
    )
    for i in range(201):
        await queue.add("set-list-item-name", flush=False, listId="l", listItemId=str(i))
    ack = await queue.flush()
    assert ack is not None
    assert len(ack.processed_ids) == 201
    assert len(fake_transport.calls) == 2
    first = fake_transport.calls[0][1]["operations"]
    second = fake_transport.calls[1][1]["operations"]
    assert len(first.operations) == 200
    assert len(second.operations) == 1
    assert first.operations[0].metadata.userId == "user"
    assert first.operations[0].metadata.handlerId == "set-list-item-name"
    assert queue.pending_count == 0


@pytest.mark.asyncio
async def test_partial_ack_immediately_retries_when_remaining_queue_is_small(fake_transport) -> None:
    queue = OperationQueue(
        fake_transport,
        QueueSpec("q", "/update", "PBListOperation", "PBListOperationList"),
        user_id="u",
    )
    a = queue.new_operation("a")
    b = queue.new_operation("b")
    await queue.enqueue(a, flush=False)
    await queue.enqueue(b, flush=False)
    fake_transport.responses.append(PB.PBEditOperationResponse(processedOperations=[a.metadata.operationId]))
    ack = await queue.flush()
    assert ack is not None
    assert queue.pending_count == 0
    assert len(fake_transport.calls) == 2
    assert ack.processed_ids == (a.metadata.operationId, b.metadata.operationId)


@pytest.mark.asyncio
async def test_file_journal_round_trip(tmp_path, fake_transport) -> None:
    journal = FileOperationJournal(tmp_path)
    spec = QueueSpec("q", "/update", "PBListOperation", "PBListOperationList")
    q1 = OperationQueue(fake_transport, spec, user_id="u", journal=journal)
    await q1.add("rename-list", flush=False, listId="l", updatedValue="Name")
    q2 = OperationQueue(fake_transport, spec, user_id="u", journal=journal)
    assert await q2.restore() == 1
    assert q2._pending[0].metadata.handlerId == "rename-list"


@pytest.mark.asyncio
async def test_operation_queue_retains_head_on_out_of_order_acknowledgement(fake_transport) -> None:
    spec = QueueSpec("q", "/update", "PBListOperation", "PBListOperationList")
    queue = OperationQueue(fake_transport, spec, user_id="user")
    first = queue.new_operation("set-list-name", listId="list", updatedValue="A")
    second = queue.new_operation("set-list-name", listId="list", updatedValue="B")
    await queue.enqueue(first, flush=False)
    await queue.enqueue(second, flush=False)
    response = PB.PBEditOperationResponse(processedOperations=[second.metadata.operationId])
    fake_transport.responses.append(response)

    ack = await queue.flush()

    assert ack is not None
    assert ack.processed_ids == (second.metadata.operationId,)
    assert queue.pending_count == 2
    assert queue._pending[0].metadata.operationId == first.metadata.operationId


@pytest.mark.asyncio
async def test_zero_ack_retains_queue_without_retry(fake_transport) -> None:
    spec = QueueSpec("q", "/update", "PBListOperation", "PBListOperationList")
    queue = OperationQueue(fake_transport, spec, user_id="user")
    op = queue.new_operation("set-list-name", listId="list", updatedValue="A")
    await queue.enqueue(op, flush=False)
    fake_transport.responses.append(PB.PBEditOperationResponse())

    ack = await queue.flush()

    assert ack is not None
    assert ack.processed_ids == ()
    assert queue.pending_count == 1
    assert len(fake_transport.calls) == 1


@pytest.mark.asyncio
async def test_queue_stops_when_first_200_ack_leaves_more_than_200(fake_transport) -> None:
    spec = QueueSpec("q", "/update", "PBListOperation", "PBListOperationList")
    queue = OperationQueue(fake_transport, spec, user_id="user")
    for i in range(401):
        await queue.add("set-list-item-name", flush=False, listId="l", listItemId=str(i))

    ack = await queue.flush()

    assert ack is not None
    assert len(ack.processed_ids) == 200
    assert queue.pending_count == 201
    assert len(fake_transport.calls) == 1


@pytest.mark.asyncio
async def test_response_delegate_observes_queue_after_acknowledged_operations_are_shifted(fake_transport) -> None:
    queue = OperationQueue(
        fake_transport,
        QueueSpec("q", "/update", "PBListOperation", "PBListOperationList"),
        user_id="user",
    )
    op = queue.new_operation("rename-list", listId="list", updatedValue="Name")
    await queue.enqueue(op, flush=False)
    seen_pending: list[int] = []

    async def on_response(_response):
        seen_pending.append(queue.pending_count)

    queue.on_response = on_response

    await queue.flush()

    assert seen_pending == [0]


@pytest.mark.asyncio
async def test_response_delegate_failure_is_isolated_after_server_ack(fake_transport) -> None:
    queue = OperationQueue(
        fake_transport,
        QueueSpec("q", "/update", "PBListOperation", "PBListOperationList"),
        user_id="user",
    )
    op = queue.new_operation("rename-list", listId="list", updatedValue="Name")
    await queue.enqueue(op, flush=False)

    async def broken_delegate(_response):
        raise RuntimeError("consumer bug")

    queue.on_response = broken_delegate

    ack = await queue.flush()

    assert ack is not None
    assert ack.processed_ids == (op.metadata.operationId,)
    assert queue.pending_count == 0

@pytest.mark.asyncio
async def test_enqueue_during_inflight_flush_is_not_blocked_and_is_sent_next() -> None:
    class BlockingTransport:
        def __init__(self):
            self.calls = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def post_proto(self, endpoint, *, fields, response_type=None):
            request = fields["operations"]
            self.calls.append(request)
            if len(self.calls) == 1:
                self.started.set()
                await self.release.wait()
            response = PB.PBEditOperationResponse()
            response.processedOperations.extend(
                op.metadata.operationId for op in request.operations
            )
            return response

    transport = BlockingTransport()
    queue = OperationQueue(
        transport,
        QueueSpec("q", "/update", "PBListOperation", "PBListOperationList"),
        user_id="user",
    )
    first = queue.new_operation("first")
    second = queue.new_operation("second")
    await queue.enqueue(first, flush=False)

    flushing = asyncio.create_task(queue.flush())
    await transport.started.wait()
    # zT/QO may append while KO is active; this must not wait for the HTTP response.
    await asyncio.wait_for(queue.enqueue(second, flush=False), timeout=0.1)
    assert queue.pending_count == 2

    transport.release.set()
    ack = await flushing

    assert ack is not None
    assert ack.processed_ids == (first.metadata.operationId, second.metadata.operationId)
    assert len(transport.calls) == 2
    assert [op.metadata.handlerId for op in transport.calls[0].operations] == ["first"]
    assert [op.metadata.handlerId for op in transport.calls[1].operations] == ["second"]
    assert queue.pending_count == 0


@pytest.mark.asyncio
async def test_response_delegate_can_enqueue_without_recursive_flush_deadlock(fake_transport) -> None:
    queue = OperationQueue(
        fake_transport,
        QueueSpec("q", "/update", "PBListOperation", "PBListOperationList"),
        user_id="user",
    )
    first = queue.new_operation("first")
    await queue.enqueue(first, flush=False)
    added = False

    async def on_response(_response):
        nonlocal added
        if not added:
            added = True
            await queue.add("second", flush=True)

    queue.on_response = on_response
    ack = await asyncio.wait_for(queue.flush(), timeout=0.5)

    assert ack is not None
    assert queue.pending_count == 0
    assert len(fake_transport.calls) == 2


@pytest.mark.asyncio
async def test_restore_rejects_operations_archived_for_another_user(tmp_path, fake_transport) -> None:
    journal = FileOperationJournal(tmp_path)
    spec = QueueSpec("shared", "/update", "PBListOperation", "PBListOperationList")
    old = OperationQueue(fake_transport, spec, user_id="old-user", journal=journal)
    await old.add("rename-list", flush=False, listId="l", updatedValue="Name")

    current = OperationQueue(fake_transport, spec, user_id="new-user", journal=journal)
    assert await current.restore() == 0
    assert current.pending_count == 0
    assert await journal.load(spec.queue_id) is None


@pytest.mark.asyncio
async def test_restore_ignores_corrupt_archived_operation_payload(tmp_path, fake_transport) -> None:
    journal = FileOperationJournal(tmp_path)
    spec = QueueSpec("q", "/update", "PBListOperation", "PBListOperationList")
    await journal.save(spec.queue_id, b"not a protobuf")
    queue = OperationQueue(fake_transport, spec, user_id="user", journal=journal)

    assert await queue.restore() == 0
    assert queue.pending_count == 0

@pytest.mark.asyncio
async def test_transport_failure_retains_pending_operations_for_retry(fake_transport) -> None:
    from anylist_sdk.exceptions import TransportError

    queue = OperationQueue(
        fake_transport,
        QueueSpec("q", "/update", "PBListOperation", "PBListOperationList"),
        user_id="user",
    )
    op = queue.new_operation("rename-list", listId="list", updatedValue="Name")
    await queue.enqueue(op, flush=False)
    fake_transport.responses.append(TransportError("offline"))

    with pytest.raises(TransportError):
        await queue.flush()

    assert queue.pending_count == 1
    assert queue._pending[0].metadata.operationId == op.metadata.operationId


@pytest.mark.asyncio
async def test_pause_during_inflight_request_processes_ack_but_suppresses_next_send() -> None:
    class BlockingTransport:
        def __init__(self):
            self.calls = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def post_proto(self, endpoint, *, fields, response_type=None):
            request = fields["operations"]
            self.calls.append(request)
            self.started.set()
            await self.release.wait()
            response = PB.PBEditOperationResponse()
            response.processedOperations.extend(
                op.metadata.operationId for op in request.operations
            )
            return response

    transport = BlockingTransport()
    queue = OperationQueue(
        transport,
        QueueSpec("q", "/update", "PBListOperation", "PBListOperationList", max_batch_size=1),
        user_id="user",
    )
    first = queue.new_operation("first")
    second = queue.new_operation("second")
    await queue.enqueue(first, flush=False)
    await queue.enqueue(second, flush=False)

    flushing = asyncio.create_task(queue.flush())
    await transport.started.wait()
    queue.pause()
    transport.release.set()
    ack = await flushing

    assert ack is not None
    assert ack.processed_ids == (first.metadata.operationId,)
    assert queue.pending_count == 1
    assert len(transport.calls) == 1

    # Tl(false) schedules the retained queue immediately in the official client.
    await queue.resume(flush=True)
    assert queue.pending_count == 0
    assert len(transport.calls) == 2
