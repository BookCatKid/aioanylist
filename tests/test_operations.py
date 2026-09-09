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
