from __future__ import annotations


from ..operations import OperationJournal, QueueSpec
from ..state import AnyListState
from ..transport import AnyListTransport
from .base import OperationService


class GenericDomainService(OperationService):
    """Typed protobuf operation surface for less-common AnyList domains."""

    def __init__(
        self,
        transport: AnyListTransport,
        state: AnyListState,
        *,
        user_id: str,
        queue_id: str,
        endpoint: str,
        operation_type: str,
        operation_list_type: str,
        journal: OperationJournal | None = None,
    ) -> None:
        super().__init__(
            transport,
            state,
            user_id=user_id,
            spec=QueueSpec(queue_id, endpoint, operation_type, operation_list_type),
            journal=journal,
        )
