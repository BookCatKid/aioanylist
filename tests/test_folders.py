from __future__ import annotations

import pytest

from anylist_sdk.proto import PB
from anylist_sdk.services.folders import FoldersService
from anylist_sdk.state import AnyListState


def _folder_item(identifier: str, item_type: int) -> object:
    return PB.PBListFolderItem(identifier=identifier, itemType=item_type)


@pytest.mark.asyncio
async def test_new_folder_inherits_parent_settings(fake_transport) -> None:
    state = AnyListState(user_id="user", root_folder_id="root", list_data_id="data")
    root = PB.PBListFolder(identifier="root")
    root.folderSettings.listsSortOrder = 2
    root.folderSettings.folderSortPosition = 1
    root.folderSettings.folderHexColor = "AAAAAA"
    state.list_folders["root"] = root
    service = FoldersService(fake_transport, state, user_id="user")

    child = await service.create("Child", hex_color="BBBBBB")

    assert child.folderSettings.listsSortOrder == 2
    assert child.folderSettings.folderSortPosition == 1
    assert child.folderSettings.folderHexColor == "BBBBBB"
    assert any(item.identifier == child.identifier for item in state.list_folders["root"].items)


@pytest.mark.asyncio
async def test_move_updates_both_parent_mirrors_and_exact_wire_fields(fake_transport) -> None:
    state = AnyListState(user_id="user", list_data_id="data")
    source = PB.PBListFolder(identifier="source")
    target = PB.PBListFolder(identifier="target")
    item = _folder_item("list", 0)
    source.items.add().CopyFrom(item)
    state.list_folders.update(source=source, target=target)
    service = FoldersService(fake_transport, state, user_id="user")

    await service.move([item], "source", "target")

    assert list(state.list_folders["source"].items) == []
    assert [x.identifier for x in state.list_folders["target"].items] == ["list"]
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "move-folder-items"
    assert operation.originalParentFolderId == "source"
    assert operation.updatedParentFolderId == "target"
    assert [x.identifier for x in operation.folderItems] == ["list"]


@pytest.mark.asyncio
async def test_delete_folder_item_updates_parent_and_folder_index(fake_transport) -> None:
    state = AnyListState(user_id="user", list_data_id="data")
    parent = PB.PBListFolder(identifier="parent")
    item = _folder_item("child", 1)
    parent.items.add().CopyFrom(item)
    state.list_folders["parent"] = parent
    state.list_folders["child"] = PB.PBListFolder(identifier="child")
    service = FoldersService(fake_transport, state, user_id="user")

    await service.delete_items([item], "parent")

    assert list(state.list_folders["parent"].items) == []
    assert "child" not in state.list_folders
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "delete-folder-items"
    assert operation.originalParentFolderId == "parent"
    assert [x.identifier for x in operation.folderItems] == ["child"]


def test_full_folder_response_clears_stale_entries() -> None:
    state = AnyListState(user_id="user")
    state.list_folders["stale"] = PB.PBListFolder(identifier="stale")
    response = PB.PBListFoldersResponse(includesAllFolders=True, rootFolderId="root")
    response.listFolders.add(identifier="root", name="Root")

    state.apply_list_folders(response)

    assert set(state.list_folders) == {"root"}
    assert state.root_folder_id == "root"
