from __future__ import annotations

from collections.abc import Sequence
from google.protobuf.message import Message

from ..identifiers import uuid4_hex
from ..normalization import canonical_category_match_id
from ..operations import QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message


class UserCategoriesService(OperationService):
    def __init__(self, transport: AnyListTransport, state: AnyListState, *, user_id: str, journal=None):
        super().__init__(transport,state,user_id=user_id,
            spec=QueueSpec(f"{user_id}:user-categories", "/data/user-categories/update",
                           "PBUserCategoryOperation","PBUserCategoryOperationList"),journal=journal)
        self.user_id=user_id
        self.queue.on_response = self._on_response
    async def _on_response(self,response:Message)->None:
        if not response.originalTimestamps or not response.newTimestamps:return
        original=float(response.originalTimestamps[0].timestamp)
        if original==float(self.state.user_categories_timestamp):
            self.state.user_categories_timestamp=float(response.newTimestamps[0].timestamp)
        else:
            await self.refresh()
    def all(self): return list(self.state.user_categories.values())
    def groupings(self): return list(self.state.category_groupings.values())

    async def refresh(self) -> Message:
        fields: dict[str, Message] = {}
        if self.state.user_category_data_id:
            fields["timestamp"] = PB.PBTimestamp(timestamp=self.state.user_categories_timestamp)
        response = await self.transport.post_proto(
            "/data/user-categories/all", fields=fields, response_type="PBUserCategoryData"
        )
        assert isinstance(response, Message)
        self.state.apply_user_categories(response)
        return response

    async def add_category(self,name:str,icon:str="",*,flush:bool=True)->Message:
        c=PB.PBUserCategory(identifier=uuid4_hex(),userId=self.user_id,name=name,
                            categoryMatchId=canonical_category_match_id(name),icon=icon)
        self.state.user_categories[c.identifier]=clone(c)
        await self.operation("add-category",category=c,flush=flush); return self.state.user_categories[c.identifier]
    async def remove_category(self,cid:str,*,flush:bool=True)->None:
        c=self.state.user_categories.pop(cid,None)
        if c is None: raise KeyError(cid)
        await self.operation("remove-category",category=c,flush=flush)
    async def rename_category(self,cid:str,name:str,*,flush:bool=True)->Message:
        c=self._cat(cid); c.name=name
        if not getattr(c,"systemCategory",""): c.categoryMatchId=canonical_category_match_id(name)
        await self.operation("set-category-name",category=clone_message(c),flush=flush); return c
    async def set_category_icon(self,cid:str,icon:str,*,flush:bool=True)->Message:
        c=self._cat(cid); c.icon=icon
        await self.operation("set-category-icon",category=clone_message(c),flush=flush); return c
    async def add_grouping(self,name:str,category_ids:Sequence[str]=(),*,flush:bool=True)->Message:
        g=PB.PBCategoryGrouping(identifier=uuid4_hex(),userId=self.user_id,name=name,sharingId=uuid4_hex())
        g.categoryIds.extend(category_ids); self.state.category_groupings[g.identifier]=clone(g)
        await self.operation("add-grouping",grouping=g,flush=flush); return self.state.category_groupings[g.identifier]
    async def remove_grouping(self,gid:str,*,flush:bool=True)->None:
        g=self.state.category_groupings.pop(gid,None)
        if g is None: raise KeyError(gid)
        p=clone_message(g); del p.categoryIds[:]
        await self.operation("remove-grouping",grouping=p,flush=flush)
    async def set_grouping_categories(self,gid:str,category_ids:Sequence[str],*,ordering_only:bool=False,flush:bool=True)->Message:
        g=self._group(gid); del g.categoryIds[:]; g.categoryIds.extend(category_ids)
        await self.operation("set-grouping-category-order" if ordering_only else "set-grouping-categories",
                             grouping=clone_message(g),flush=flush); return g
    async def rename_grouping(self,gid:str,name:str,*,flush:bool=True)->Message:
        g=self._group(gid);g.name=name;p=clone_message(g);del p.categoryIds[:]
        await self.operation("set-grouping-name",grouping=p,flush=flush);return g
    async def hide_grouping_from_browse(self,gid:str,*,flush:bool=True)->Message:
        g=self._group(gid)
        if g.shouldHideFromBrowseListCategoryGroupsScreen:
            return g
        g.shouldHideFromBrowseListCategoryGroupsScreen=True
        p=clone_message(g);del p.categoryIds[:]
        await self.operation(
            "set-should-hide-category-group-from-browse-list-category-groups-screen",
            grouping=p,flush=flush
        )
        return g
    def _cat(self,cid):
        c=self.state.user_categories.get(cid)
        if c is None: raise KeyError(cid)
        return c
    def _group(self,gid):
        g=self.state.category_groupings.get(gid)
        if g is None: raise KeyError(gid)
        return g


class CategorizedItemsService(OperationService):
    def __init__(self,transport:AnyListTransport,state:AnyListState,*,user_id:str,journal=None):
        super().__init__(transport,state,user_id=user_id,
            spec=QueueSpec(f"{user_id}:categorized-items","/data/categorized-items/update",
                           "PBCategorizeItemOperation","PBCategorizeItemOperationList"),journal=journal)
        self.queue.on_response = self._on_response
    async def _on_response(self,response:Message)->None:
        if not response.originalTimestamps or not response.newTimestamps:return
        original=float(response.originalTimestamps[0].timestamp)
        if original==float(self.state.categorized_items_timestamp):
            self.state.categorized_items_timestamp=float(response.newTimestamps[0].timestamp)
        else:
            await self.refresh()
    async def refresh(self) -> Message:
        timestamp = PB.PBTimestamp(
            identifier="last-categorized-item-timestamp",
            timestamp=self.state.categorized_items_timestamp,
        )
        response = await self.transport.post_proto(
            "/data/categorized-items/all",
            fields={"timestamp": timestamp},
            response_type="PBCategorizedItemsList",
        )
        assert isinstance(response, Message)
        self.state.apply_categorized_items(response)
        return response
    async def categorize(self,item:Message,*,flush:bool=True)->str:
        c=clone_message(item); c.name=c.name.lower()
        self.state.categorized_items[c.identifier]=c
        return await self.operation("categorize-item",listItem=c,flush=flush)
    async def remove(self,item:Message,*,flush:bool=True)->str:
        self.state.categorized_items.pop(item.identifier,None)
        return await self.operation("remove-categorized-item",listItem=clone_message(item),flush=flush)
