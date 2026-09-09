from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from google.protobuf.message import Message

from ..identifiers import uuid4_hex
from ..operations import QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message


class FoldersService(OperationService):
    def __init__(self, transport: AnyListTransport, state: AnyListState, *, user_id: str, journal=None):
        super().__init__(transport, state, user_id=user_id,
            spec=QueueSpec(f"{user_id}:list-folders", "/data/list-folders/update",
                           "PBListFolderOperation", "PBListFolderOperationList"), journal=journal)
        self.queue.on_response = self._on_response
        self.on_list_removed: Callable[[str, bool], Awaitable[None]] | None = None
        self.on_shopping_refresh_requested: Callable[[], Awaitable[object]] | None = None
        self._refresh_shopping_after_queue = False

    async def _on_response(self, response: Message) -> None:
        mismatch = False
        new_by_id = {str(x.identifier): x for x in response.newTimestamps}
        for original in response.originalTimestamps:
            current = self.state.list_folders.get(str(original.identifier))
            if current is None:
                if str(original.identifier) in new_by_id:
                    mismatch = True
            elif float(current.timestamp) != float(original.timestamp):
                mismatch = True
        if mismatch:
            await self.refresh()
            return
        for value in response.newTimestamps:
            current = self.state.list_folders.get(str(value.identifier))
            if current is not None:
                current.timestamp = value.timestamp
        if self._refresh_shopping_after_queue:
            self._refresh_shopping_after_queue = False
            if self.on_shopping_refresh_requested is not None:
                await self.on_shopping_refresh_requested()

    async def operation(self, handler_id: str, *, flush: bool = True, **fields):
        if self.state.list_data_id and "listDataId" not in fields:
            fields["listDataId"] = self.state.list_data_id
        return await super().operation(handler_id, flush=flush, **fields)

    def all(self): return list(self.state.list_folders.values())
    def get(self, folder_id: str): return self.state.list_folders.get(folder_id)

    def has_pending_delete_items(self) -> bool:
        return any(
            str(op.metadata.handlerId) == "delete-folder-items"
            for op in self.queue._pending
        )

    async def refresh(self) -> Message | None:
        if self.queue.pending_count:
            return None
        fields: dict[str, Message | str] = {}
        if self.state.list_folders:
            # post_proto accepts Message/bytes/string fields, not repeated containers; build
            # the same PBTimestampList the web manager's GB() helper produces.
            timestamps = PB.PBTimestampList()
            timestamps.timestamps.extend(self.state.list_folder_timestamps().folderTimestamps)
            fields["timestamps"] = timestamps
            if self.state.root_folder_id:
                fields["root_folder_id"] = self.state.root_folder_id
        response = await self.transport.post_proto(
            "/data/list-folders/all", fields=fields, response_type="PBListFoldersResponse"
        )
        if response is None:
            return None
        assert isinstance(response, Message)
        self.state.apply_list_folders(response)
        return response

    async def create(self, name: str, *, parent_id: str | None = None, hex_color: str | None = None,
                     flush: bool = True) -> Message:
        parent_id = parent_id or self.state.root_folder_id
        if not parent_id: raise RuntimeError("No root folder synchronized")
        folder = PB.PBListFolder(identifier=uuid4_hex(), name=name)
        parent = self.get(parent_id)
        # AnyList clones the parent's folder settings into a newly-created folder, then
        # applies an explicitly supplied folder color on top.
        if parent is not None and parent.HasField("folderSettings"):
            folder.folderSettings.CopyFrom(parent.folderSettings)
        if hex_color:
            folder.folderSettings.folderHexColor = hex_color
        self.state.list_folders[folder.identifier] = clone(folder)
        if parent:
            parent.items.add(identifier=folder.identifier, itemType=1)  # official enum FolderType
        await self.operation("create-new-folder", listFolder=folder, updatedParentFolderId=parent_id,
                             flush=flush)
        return self.state.list_folders[folder.identifier]

    async def rename(self, folder_id: str, name: str, *, flush: bool = True) -> None:
        f=self._require(folder_id); f.name=name
        await self.operation("set-folder-name", listFolder=PB.PBListFolder(identifier=folder_id,name=name), flush=flush)

    async def set_hex_color(self, folder_id: str, color: str, *, flush: bool=True) -> None:
        f=self._require(folder_id); f.folderSettings.folderHexColor=color
        p=PB.PBListFolder(identifier=folder_id); p.folderSettings.folderHexColor=color
        await self.operation("set-folder-hex-color", listFolder=p, flush=flush)

    async def set_icon(self, folder_id: str, icon: str | Message, *, flush: bool=True) -> None:
        f=self._require(folder_id)
        value = icon if isinstance(icon, Message) else PB.PBIcon(iconName=icon)
        f.folderSettings.icon.CopyFrom(value)
        p=PB.PBListFolder(identifier=folder_id)
        p.folderSettings.icon.CopyFrom(value)
        await self.operation("set-icon", listFolder=p, flush=flush)

    async def set_lists_sort_order(self, folder_id: str, value: int, *, flush: bool=True) -> None:
        f=self._require(folder_id); f.folderSettings.listsSortOrder=value
        p=PB.PBListFolder(identifier=folder_id); p.folderSettings.listsSortOrder=value
        await self.operation("set-lists-sort-order", listFolder=p, flush=flush)

    async def set_folder_sort_position(self, folder_id: str, value: int, *, flush: bool=True) -> None:
        f=self._require(folder_id); f.folderSettings.folderSortPosition=value
        p=PB.PBListFolder(identifier=folder_id); p.folderSettings.folderSortPosition=value
        await self.operation("set-folder-sort-position", listFolder=p, flush=flush)

    async def reorder(self, folder_id: str, items: Sequence[Message], *, flush: bool=True) -> None:
        f=self._require(folder_id); del f.items[:]
        for x in items: f.items.add().CopyFrom(x)
        await self.operation("set-ordered-folder-items", originalParentFolderId=folder_id,
                             folderItems=[clone_message(x) for x in items], flush=flush)

    async def move(self, items: Sequence[Message], original_parent_id: str, updated_parent_id: str,
                   *, flush: bool=True) -> None:
        original = self._require(original_parent_id)
        updated = self._require(updated_parent_id)
        wire_items = [clone_message(x) for x in items]
        keys = {(int(x.itemType), str(x.identifier)) for x in wire_items}
        kept = [clone_message(x) for x in original.items if (int(x.itemType), str(x.identifier)) not in keys]
        del original.items[:]
        for item in kept:
            original.items.add().CopyFrom(item)
        existing = {(int(x.itemType), str(x.identifier)) for x in updated.items}
        for item in wire_items:
            key = (int(item.itemType), str(item.identifier))
            if key not in existing:
                updated.items.add().CopyFrom(item)
                existing.add(key)
        await self.operation("move-folder-items", folderItems=wire_items,
                             originalParentFolderId=original_parent_id,
                             updatedParentFolderId=updated_parent_id, flush=flush)

    async def delete_items(self, items: Sequence[Message], parent_id: str, *, flush: bool=True) -> None:
        parent = self._require(parent_id)
        wire_items = [clone_message(x) for x in items]
        keys = {(int(x.itemType), str(x.identifier)) for x in wire_items}
        kept = [clone_message(x) for x in parent.items if (int(x.itemType), str(x.identifier)) not in keys]
        del parent.items[:]
        for item in kept:
            parent.items.add().CopyFrom(item)
        # FolderType is enum value 1 in the official schema.  Removing a folder item also
        # removes that folder's local mirror immediately; callers can explicitly pass nested
        # items when reproducing recursive folder deletion.
        for item in wire_items:
            if int(item.itemType) == 1:
                self.state.list_folders.pop(str(item.identifier), None)
        await self.operation("delete-folder-items", folderItems=wire_items,
                             originalParentFolderId=parent_id, flush=flush)


    async def delete_folder(
        self, folder_id: str, parent_id: str, *, flush: bool = True
    ) -> None:
        """Recursively delete a folder exactly like the AnyList Web folder manager.

        Direct shopping lists are removed first, then child folders recursively, and finally
        the folder itself is removed from its parent.  Each removed node is represented by
        the same ``delete-folder-items`` operation shape used by the web client.
        """
        folder = self._require(folder_id)
        self._require(parent_id)

        direct_list_ids = [
            str(item.identifier) for item in list(folder.items) if int(item.itemType) == 0
        ]
        child_folder_ids = [
            str(item.identifier) for item in list(folder.items) if int(item.itemType) == 1
        ]

        # qB(listID, folderID) removes the list from the shopping manager and asks the
        # folder manager to remove the corresponding ListType item.  Preserve the one-op
        # per direct list behavior rather than collapsing the recursive delete into one op.
        for list_id in direct_list_ids:
            if self.on_list_removed is not None:
                await self.on_list_removed(list_id, False)
            item = PB.PBListFolderItem(identifier=list_id, itemType=0)
            await self.delete_items([item], folder_id, flush=False)

        for child_id in child_folder_ids:
            if child_id in self.state.list_folders:
                await self.delete_folder(child_id, folder_id, flush=False)

        # The recursive child calls may already have removed all child entries. Remove this
        # folder from its parent and its indexed mirror last, matching IB().
        folder_item = PB.PBListFolderItem(identifier=folder_id, itemType=1)
        await self.delete_items([folder_item], parent_id, flush=False)

        if flush:
            await self.flush()

    def _require(self, fid: str) -> Message:
        x=self.get(fid)
        if x is None: raise KeyError(fid)
        return x
