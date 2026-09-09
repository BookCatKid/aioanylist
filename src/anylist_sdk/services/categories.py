from __future__ import annotations

from collections.abc import Sequence
import hashlib
from google.protobuf.message import Message

from ..identifiers import uuid4_hex
from ..normalization import canonical_category_match_id
from ..operations import QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message


# Exact return value of the official user-category manager's MA() helper.  Note that the
# legacy "deli" system category exists in other built-in mappings but is intentionally not
# in this list; categorized-item wire payloads therefore encode it as category="other".
_SYSTEM_CATEGORY_MATCH_IDS = frozenset(
    {
        "baby",
        "bakery",
        "beverages",
        "breakfast-and-cereal",
        "condiments-oils-and-salad-dressings",
        "cooking-and-baking",
        "dairy",
        "frozen-foods",
        "grains-pasta-and-side-dishes",
        "health-and-personal-care",
        "household-and-cleaning",
        "meat",
        "pet-supplies",
        "produce",
        "seafood",
        "snacks-cookies-and-candy",
        "soups-and-canned-goods",
        "wine-beer-spirits",
        "other",
    }
)


def _wire_category(category_match_id: str) -> str:
    return category_match_id if category_match_id in _SYSTEM_CATEGORY_MATCH_IDS else "other"


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
        self.user_id = user_id
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
    @staticmethod
    def _category_id(item: Message) -> str:
        # Official ListItem.categoryID(): categoryMatchId -> category -> "other".
        return str(getattr(item, "categoryMatchId", "") or getattr(item, "category", "") or "other")

    def memory_id(self, name: str, list_id: str = "") -> str:
        # ALCategorizedListItemsManager: md5(lowercaseName + "-" + listId + "-" + userId).
        raw = f"{name.lower()}-{list_id}-{self.user_id}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def lookup(self, name: str, list_id: str = "") -> Message | None:
        """Return AnyList's learned categorization, preferring list-specific over global."""
        item = self.state.categorized_items.get(self.memory_id(name, list_id))
        if item is None and list_id:
            item = self.state.categorized_items.get(self.memory_id(name, ""))
        return item

    async def categorize(
        self, item: Message, *, global_scope: bool = False, flush: bool = True
    ) -> str:
        """Remember an item's category using AnyList's list/global categorization keys."""
        source_list_id = str(getattr(item, "listId", "") or "")
        learned = clone_message(item)
        learned.userId = self.user_id
        if global_scope:
            learned.listId = ""
        learned.name = (learned.name or "").lower()
        learned.identifier = self.memory_id(learned.name, learned.listId)

        existing = self.state.categorized_items.get(learned.identifier)
        changed = existing is None or self._category_id(existing) != self._category_id(learned)
        if existing is None:
            self.state.categorized_items[learned.identifier] = clone_message(learned)
        elif changed:
            # AA updates only categoryMatchId on an existing learned item.  Its legacy
            # ``category`` field is deliberately left untouched.
            existing.categoryMatchId = self._category_id(learned)

        operation_id = ""
        if changed:
            operation_id = await self.operation(
                "categorize-item", listItem=learned, flush=flush
            )

        # Global learning replaces a previously remembered value for this concrete list.
        if global_scope and source_list_id:
            local_id = self.memory_id(learned.name, source_list_id)
            local = self.state.categorized_items.pop(local_id, None)
            if local is not None:
                removal = PB.ListItem(
                    identifier=local_id,
                    name=(local.name or "").lower(),
                    userId=self.user_id,
                    listId=source_list_id,
                    categoryMatchId=self._category_id(local),
                    category=_wire_category(self._category_id(local)),
                )
                await self.operation(
                    "remove-categorized-item", listItem=removal, flush=flush
                )
        return operation_id

    async def remove(
        self, item: Message, *, global_scope: bool = False, flush: bool = True
    ) -> str:
        list_id = "" if global_scope else str(getattr(item, "listId", "") or "")
        identifier = self.memory_id(str(getattr(item, "name", "") or ""), list_id)
        existing = self.state.categorized_items.pop(identifier, None)
        source = existing or item
        removal = PB.ListItem(
            identifier=identifier,
            name=(getattr(source, "name", "") or "").lower(),
            userId=self.user_id,
            listId=list_id,
            categoryMatchId=self._category_id(source),
            category=_wire_category(self._category_id(source)),
        )
        return await self.operation(
            "remove-categorized-item", listItem=removal, flush=flush
        )

    async def migrate_category(
        self,
        old_category_match_id: str,
        new_category_match_id: str,
        *,
        flush: bool = True,
    ) -> int:
        """Rewrite learned-category memories using the official UA migration algorithm.

        The web client pauses this queue, mutates each matching learned item optimistically,
        and emits a deliberately small partial ListItem.  Its counter is tested with ``>20``
        after incrementing, so a temporary resume/flush occurs every *21* operations, not 20.
        """
        self.pause()
        changed = 0
        chunk_count = 0
        try:
            for learned in self.state.categorized_items.values():
                if self._category_id(learned) != old_category_match_id:
                    continue
                learned.categoryMatchId = new_category_match_id
                partial = PB.ListItem(
                    identifier=str(learned.identifier),
                    userId=self.user_id,
                    listId=str(learned.listId or ""),
                    name=str(learned.name or ""),
                    categoryMatchId=new_category_match_id,
                    category=_wire_category(new_category_match_id),
                )
                await self.operation("categorize-item", listItem=partial, flush=False)
                changed += 1
                chunk_count += 1
                if chunk_count > 20:
                    # Tl(false) immediately schedules the pending queue in the official
                    # implementation, then Tl(true) pauses it again for the next chunk.
                    await self.resume(flush=True)
                    self.pause()
                    chunk_count = 0
        finally:
            # Always balance the initial pause, including cancellation/error paths.  The
            # public ``flush=False`` extension keeps operations queued for caller batching;
            # the normal/default path mirrors Tl(false)'s immediate send behavior.
            await self.resume(flush=flush)
        return changed
