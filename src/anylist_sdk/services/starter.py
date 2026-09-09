from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID
from google.protobuf.message import Message

from ..identifiers import uuid4_hex, uuid5_hex
from ..operations import OperationQueue, QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message

_FAVORITE_NAMESPACE = UUID(hex="839503408980408581879d73a33ec4c1")
_RECENT_NAMESPACE = UUID(hex="f915bc60200c4c61b4b0a17bd9978960")
_AGGREGATE_FAVORITES_ID = "dbd9354fd9e847b398882e823a8fd388"


def favorite_list_id(list_id: str) -> str:
    return uuid5_hex(list_id, _FAVORITE_NAMESPACE)


def recent_list_id(list_id: str) -> str:
    return uuid5_hex(list_id, _RECENT_NAMESPACE)


def aggregate_favorites_id() -> str:
    return _AGGREGATE_FAVORITES_ID


class StarterListsService(OperationService):
    def __init__(self,transport:AnyListTransport,state:AnyListState,*,user_id:str,journal=None):
        super().__init__(transport,state,user_id=user_id,
            spec=QueueSpec(f"{user_id}:starter-lists","/data/starter-lists/update",
                           "PBStarterListOperation","PBStarterListOperationList"),journal=journal)
        self.order_queue=OperationQueue(transport,QueueSpec(f"{user_id}:starter-list-order",
            "/data/starter-lists/update-ordered-ids","PBOrderedStarterListIDsOperation",
            "PBOrderedStarterListIDsOperationList"),user_id=user_id,journal=journal)
        self.user_id=user_id
        self.queue.on_response=self._on_response
        self.order_queue.on_response=self._on_order_response
    async def _on_response(self,response:Message)->None:
        all_lists={**self.state.starter_lists,**self.state.recent_item_lists,**self.state.favorite_item_lists}
        mismatch=False
        for index,original in enumerate(response.originalTimestamps):
            current=all_lists.get(str(original.identifier))
            if current is not None:
                if float(current.timestamp)==float(original.timestamp):
                    if index < len(response.newTimestamps):current.timestamp=response.newTimestamps[index].timestamp
                else:mismatch=True
        if mismatch:await self.refresh()
    async def _on_order_response(self,response:Message)->None:
        if not response.originalTimestamps or not response.newTimestamps:return
        original=float(response.originalTimestamps[0].timestamp)
        if original==float(self.state.ordered_starter_list_ids_timestamp):
            self.state.ordered_starter_list_ids_timestamp=float(response.newTimestamps[0].timestamp)
        else:await self.refresh_order()
    def all(self): return list(self.state.starter_lists.values())
    def recent(self): return list(self.state.recent_item_lists.values())
    def favorites(self): return list(self.state.favorite_item_lists.values())
    def get(self,list_id:str):
        return self.state.starter_lists.get(list_id) or self.state.recent_item_lists.get(list_id) or self.state.favorite_item_lists.get(list_id)

    async def refresh(self) -> Message:
        response = await self.transport.post_proto(
            "/data/starter-lists/all-v2",
            fields={
                "user_lists_timestamps": self.state._starter_timestamps(self.state.starter_lists),
                "recent_item_lists_timestamps": self.state._starter_timestamps(self.state.recent_item_lists),
                "favorite_item_lists_timestamps": self.state._starter_timestamps(self.state.favorite_item_lists),
            },
            response_type="StarterListsResponseV2",
        )
        assert isinstance(response, Message)
        self.state.apply_starter_lists(response)
        return response

    async def refresh_order(self) -> Message:
        timestamp = PB.PBTimestamp(
            identifier=self.user_id, timestamp=self.state.ordered_starter_list_ids_timestamp
        )
        response = await self.transport.post_proto(
            "/data/starter-lists/ordered-ids",
            fields={"timestamp": timestamp},
            response_type="PBIdentifierList",
        )
        assert isinstance(response, Message)
        self.state.apply_ordered_starter_ids(response)
        return response

    async def create(self,name:str,*,list_id:str|None=None,user_list_id:str|None=None,starter_type:int|None=None,flush:bool=True)->Message:
        lst=PB.StarterList(identifier=list_id or uuid4_hex(),name=name,userId=self.user_id)
        if user_list_id: lst.listId=user_list_id
        if starter_type is not None: lst.starterListType=starter_type
        self.state.starter_lists[lst.identifier]=clone(lst)
        if lst.identifier not in self.state.ordered_starter_list_ids:self.state.ordered_starter_list_ids.append(lst.identifier)
        await self.operation("new-starter-list",listId=lst.identifier,list=lst,flush=flush);return self.state.starter_lists[lst.identifier]
    async def remove(self,list_id:str,*,flush:bool=True)->None:
        self.state.starter_lists.pop(list_id,None);self.state.recent_item_lists.pop(list_id,None);self.state.favorite_item_lists.pop(list_id,None)
        if list_id in self.state.ordered_starter_list_ids:self.state.ordered_starter_list_ids.remove(list_id)
        await self.operation("remove-starter-list",listId=list_id,flush=flush)
    async def add_item(self,list_id:str,item:Message,*,flush:bool=True)->Message:
        lst=self._require(list_id); x=clone_message(item)
        if not x.identifier:x.identifier=uuid4_hex()
        x.listId=list_id; lst.items.add().CopyFrom(x)
        await self.operation("add-item",listId=list_id,listItemId=x.identifier,listItem=x,flush=flush);return lst.items[-1]
    async def remove_item(self,list_id:str,item_id:str,*,flush:bool=True)->None:
        lst=self._require(list_id);original=None
        for i,x in enumerate(lst.items):
            if x.identifier==item_id: original=clone_message(x);del lst.items[i];break
        if original is None:raise KeyError(item_id)
        await self.operation("remove-item",listId=list_id,listItemId=item_id,listItem=original,flush=flush)
    async def clear(self,list_id:str,*,flush:bool=True)->None:
        lst=self._require(list_id); del lst.items[:]
        await self.operation("clear-starter-list",listId=list_id,flush=flush)
    async def rename(self,list_id:str,name:str,*,flush:bool=True)->None:
        lst=self._require(list_id);lst.name=name
        await self.operation("rename-list",listId=list_id,updatedValue=name,flush=flush)
    async def reorder_lists(self,ids:Sequence[str],*,flush:bool=True)->str:
        self.state.ordered_starter_list_ids=list(ids)
        op=self.order_queue.new_operation("set-ordered-list-ids",orderedListIds=list(ids))
        return await self.order_queue.enqueue(op,flush=flush)
    async def flush(self):
        a=await super().flush();b=await self.order_queue.flush();return b or a
    async def restore(self)->int:return await super().restore()+await self.order_queue.restore()
    def _require(self,lid):
        x=self.get(lid)
        if x is None:raise KeyError(lid)
        return x
