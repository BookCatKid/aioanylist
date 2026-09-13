#!/usr/bin/env python3
# ruff: noqa: BLE001, S112
"""A feature-rich Textual example client for ``anylist-sdk``.

This is intentionally an example application, not a second public SDK surface.  It exercises
normal high-level service methods and avoids actions with external side effects such as sharing,
email delivery, Alexa linking, photo upload, account-name changes, and recipe web import.

Install the optional UI dependency with::

    python -m pip install -e '.[tui]'

Then run::

    python examples/anylist_tui.py

The first launch prompts for email/password before entering the TUI.  Only access/refresh tokens
and the account email are cached; the password is never written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from getpass import getpass
from pathlib import Path
from typing import ClassVar, cast
from uuid import uuid4

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import (
        Button,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        Static,
        TabbedContent,
        TabPane,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - friendly runtime error for optional extra
    raise SystemExit("Textual is required for this example. Install with: pip install -e '.[tui]'") from exc

from anylist_sdk import AnyListClient
from anylist_sdk.proto import PB, PBCalendarEvent, StarterList
from anylist_sdk.types import AuthTokens

APP_DIR = Path.home() / ".config" / "anylist-sdk"
TOKEN_CACHE = APP_DIR / "tui-tokens.json"
SDK_CACHE = APP_DIR / "cache"


def _load_token_cache(path: Path) -> tuple[str, AuthTokens] | None:
    try:
        raw = json.loads(path.read_text("utf-8"))
        email = str(raw["email"])
        tokens = AuthTokens(
            user_id=str(raw["user_id"]),
            access_token=str(raw["access_token"]),
            refresh_token=str(raw["refresh_token"]),
            is_premium_user=raw.get("is_premium_user"),
            user_locale=raw.get("user_locale"),
        )
        return email, tokens
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_token_cache(path: Path, email: str, tokens: AuthTokens) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"email": email, **asdict(tokens)}
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _message_name(value: object, default: str = "") -> str:
    return str(getattr(value, "name", default) or default)


def _selected_id(table: DataTable[object], ids: Sequence[str]) -> str | None:
    if not ids:
        return None
    row = table.cursor_row
    if row is None or row < 0 or row >= len(ids):
        return ids[0]
    return ids[row]


def _replace_rows(
    table: DataTable[object],
    rows: Iterable[Sequence[object]],
    ids: Sequence[str],
    *,
    keep_id: str | None = None,
) -> None:
    table.clear()
    for row in rows:
        table.add_row(*(str(cell) for cell in row))
    if not ids:
        return
    target = keep_id if keep_id in ids else ids[0]
    table.move_cursor(row=ids.index(target))


class SDKPanel(Vertical):
    """Shared helpers for panes backed by the same AnyList client."""

    @property
    def tui(self) -> AnyListTUI:
        return cast(AnyListTUI, self.app)

    @property
    def client(self) -> AnyListClient:
        return self.tui.client

    def entry(self) -> str:
        widget = self.query_one(".entry", Input)
        return widget.value.strip()

    def clear_entry(self) -> None:
        self.query_one(".entry", Input).value = ""

    def say(self, message: str) -> None:
        self.tui.notify(message)

    def error(self, exc: BaseException | str) -> None:
        self.tui.notify(str(exc), severity="error", timeout=6)

    async def refresh_view(self) -> None:
        """Refresh from already-synchronized local state."""


class ShoppingPanel(SDKPanel):
    DEFAULT_CSS = """
    ShoppingPanel .toolbar { height: auto; }
    ShoppingPanel #shopping-tables { height: 1fr; }
    ShoppingPanel #shopping-lists { width: 38%; }
    ShoppingPanel #shopping-items { width: 62%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.list_ids: list[str] = []
        self.item_ids: list[str] = []
        self.current_list_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Name / item / detail value", classes="entry")
        with Horizontal(classes="toolbar"):
            yield Button("New list", id="shop-new-list")
            yield Button("Rename list", id="shop-rename-list")
            yield Button("Add item", id="shop-add-item", variant="success")
            yield Button("Toggle checked", id="shop-toggle")
            yield Button("Rename item", id="shop-rename-item")
            yield Button("Set details", id="shop-details")
            yield Button("Remove item", id="shop-remove-item", variant="error")
            yield Button("Remove checked", id="shop-remove-checked")
            yield Button("Uncheck all", id="shop-uncheck")
        with Horizontal(id="shopping-tables"):
            with Vertical():
                yield Label("Shopping lists")
                yield DataTable(id="shopping-lists", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Label("Items")
                yield DataTable(id="shopping-items", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#shopping-lists", DataTable).add_columns("List", "Items", "Checked")
        self.query_one("#shopping-items", DataTable).add_columns(
            "Item", "✓", "Quantity", "Category", "Stores", "Details"
        )

    async def refresh_view(self) -> None:
        service = self.client.lists
        if service is None:
            return
        list_table = self.query_one("#shopping-lists", DataTable)
        old_list = self.current_list_id or _selected_id(list_table, self.list_ids)
        lists = sorted(service.all(), key=lambda value: _message_name(value).casefold())
        self.list_ids = [str(value.identifier) for value in lists]
        _replace_rows(
            list_table,
            (
                (
                    value.name,
                    len(value.items),
                    sum(1 for item in value.items if bool(item.checked)),
                )
                for value in lists
            ),
            self.list_ids,
            keep_id=old_list,
        )
        self.current_list_id = _selected_id(list_table, self.list_ids)
        self._refresh_items()

    def _refresh_items(self) -> None:
        service = self.client.lists
        table = self.query_one("#shopping-items", DataTable)
        if service is None or self.current_list_id is None:
            self.item_ids = []
            table.clear()
            return
        current = service.get(self.current_list_id)
        if current is None:
            self.item_ids = []
            table.clear()
            return
        old_item = _selected_id(table, self.item_ids)
        items = list(current.items)
        self.item_ids = [str(item.identifier) for item in items]
        _replace_rows(
            table,
            (
                (
                    item.name,
                    "yes" if item.checked else "",
                    getattr(getattr(item, "quantityPb", None), "rawQuantity", "") or "",
                    getattr(item, "category", "") or "",
                    len(item.storeIds),
                    item.details,
                )
                for item in items
            ),
            self.item_ids,
            keep_id=old_item,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "shopping-lists":
            self.current_list_id = _selected_id(event.data_table, self.list_ids)
            self._refresh_items()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("shop-"):
            return
        service = self.client.lists
        if service is None:
            return
        value = self.entry()
        list_id = self.current_list_id
        item_id = _selected_id(self.query_one("#shopping-items", DataTable), self.item_ids)
        try:
            if button_id == "shop-new-list":
                if not value:
                    return self.error("Enter a list name first")
                await service.create(value)
                self.clear_entry()
            elif button_id == "shop-rename-list":
                if not list_id or not value:
                    return self.error("Select a list and enter its new name")
                await service.rename(list_id, value)
                self.clear_entry()
            elif button_id == "shop-add-item":
                if not list_id or not value:
                    return self.error("Select a list and enter an item name")
                await service.add_item(list_id, value)
                self.clear_entry()
            elif button_id == "shop-toggle":
                if not list_id or not item_id:
                    return self.error("Select an item")
                item = service.item(list_id, item_id)
                if item is None:
                    return
                await service.set_checked(list_id, item_id, not bool(item.checked))
            elif button_id == "shop-rename-item":
                if not list_id or not item_id or not value:
                    return self.error("Select an item and enter a new name")
                await service.rename_item(list_id, item_id, value)
                self.clear_entry()
            elif button_id == "shop-details":
                if not list_id or not item_id:
                    return self.error("Select an item; entry text becomes its details")
                await service.set_details(list_id, item_id, value)
                self.clear_entry()
            elif button_id == "shop-remove-item":
                if not list_id or not item_id:
                    return self.error("Select an item")
                await service.remove_item(list_id, item_id)
            elif button_id == "shop-remove-checked":
                if not list_id:
                    return self.error("Select a list")
                await service.remove_checked(list_id)
            elif button_id == "shop-uncheck":
                if not list_id:
                    return self.error("Select a list")
                await service.uncheck_all(list_id)
            await self.refresh_view()
        except Exception as exc:
            self.error(exc)


class ListDataPanel(SDKPanel):
    DEFAULT_CSS = """
    ListDataPanel .toolbar { height: auto; }
    ListDataPanel #list-data-tables { height: 1fr; }
    ListDataPanel .third { width: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.list_ids: list[str] = []
        self.store_ids: list[str] = []
        self.category_ids: list[str] = []
        self.current_list_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Store/category name or category icon", classes="entry")
        with Horizontal(classes="toolbar"):
            yield Button("New store", id="meta-new-store")
            yield Button("Rename store", id="meta-rename-store")
            yield Button("Delete store", id="meta-delete-store", variant="error")
            yield Button("New category", id="meta-new-category")
            yield Button("Rename category", id="meta-rename-category")
            yield Button("Set category icon", id="meta-icon-category")
            yield Button("Delete category", id="meta-delete-category", variant="error")
        with Horizontal(id="list-data-tables"):
            with Vertical(classes="third"):
                yield Label("Lists")
                yield DataTable(id="meta-lists", cursor_type="row", zebra_stripes=True)
            with Vertical(classes="third"):
                yield Label("Stores")
                yield DataTable(id="meta-stores", cursor_type="row", zebra_stripes=True)
            with Vertical(classes="third"):
                yield Label("Categories")
                yield DataTable(id="meta-categories", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#meta-lists", DataTable).add_columns("List")
        self.query_one("#meta-stores", DataTable).add_columns("Store", "Order")
        self.query_one("#meta-categories", DataTable).add_columns("Category", "Icon", "Order")

    async def refresh_view(self) -> None:
        service = self.client.lists
        if service is None:
            return
        table = self.query_one("#meta-lists", DataTable)
        old = self.current_list_id or _selected_id(table, self.list_ids)
        lists = sorted(service.all(), key=lambda value: _message_name(value).casefold())
        self.list_ids = [str(value.identifier) for value in lists]
        _replace_rows(table, ((value.name,) for value in lists), self.list_ids, keep_id=old)
        self.current_list_id = _selected_id(table, self.list_ids)
        self._refresh_metadata()

    def _refresh_metadata(self) -> None:
        list_id = self.current_list_id
        stores_table = self.query_one("#meta-stores", DataTable)
        categories_table = self.query_one("#meta-categories", DataTable)
        if not list_id:
            self.store_ids = []
            self.category_ids = []
            stores_table.clear()
            categories_table.clear()
            return
        stores = sorted(
            self.client.state.list_stores.get(list_id, {}).values(), key=lambda value: int(value.sortIndex)
        )
        categories = sorted(
            self.client.state.list_categories.get(list_id, {}).values(),
            key=lambda value: (int(value.sortIndex), str(value.name).casefold()),
        )
        old_store = _selected_id(stores_table, self.store_ids)
        old_category = _selected_id(categories_table, self.category_ids)
        self.store_ids = [str(value.identifier) for value in stores]
        self.category_ids = [str(value.identifier) for value in categories]
        _replace_rows(
            stores_table,
            ((value.name, value.sortIndex) for value in stores),
            self.store_ids,
            keep_id=old_store,
        )
        _replace_rows(
            categories_table,
            ((value.name, value.icon, value.sortIndex) for value in categories),
            self.category_ids,
            keep_id=old_category,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "meta-lists":
            self.current_list_id = _selected_id(event.data_table, self.list_ids)
            self._refresh_metadata()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("meta-"):
            return
        service = self.client.lists
        list_id = self.current_list_id
        if service is None or not list_id:
            return self.error("Select a list")
        value = self.entry()
        store_id = _selected_id(self.query_one("#meta-stores", DataTable), self.store_ids)
        category_id = _selected_id(
            self.query_one("#meta-categories", DataTable), self.category_ids
        )
        stores = self.client.state.list_stores.get(list_id, {})
        categories = self.client.state.list_categories.get(list_id, {})
        groups = self.client.state.list_category_groups.get(list_id, {})
        try:
            if button_id == "meta-new-store":
                if not value:
                    return self.error("Enter a store name")
                await service.save_store(
                    list_id,
                    PB.PBStore(identifier=uuid4().hex, listId=list_id, name=value),
                    is_new=True,
                )
                self.clear_entry()
            elif button_id == "meta-rename-store":
                if not store_id or not value:
                    return self.error("Select a store and enter a new name")
                store = PB.PBStore()
                store.CopyFrom(stores[store_id])
                store.name = value
                await service.save_store(list_id, store)
                self.clear_entry()
            elif button_id == "meta-delete-store":
                if not store_id:
                    return self.error("Select a store")
                await service.delete_store(list_id, stores[store_id])
            elif button_id == "meta-new-category":
                if not value:
                    return self.error("Enter a category name")
                if not groups:
                    return self.error("This list has no category group")
                group = min(groups.values(), key=lambda g: str(g.identifier))
                members = [c for c in categories.values() if c.categoryGroupId == group.identifier]
                await service.save_list_category(
                    PB.PBListCategory(
                        identifier=uuid4().hex,
                        listId=list_id,
                        categoryGroupId=str(group.identifier),
                        name=value,
                        icon="other",
                        sortIndex=max((int(c.sortIndex) for c in members), default=-1) + 1,
                    )
                )
                self.clear_entry()
            elif button_id == "meta-rename-category":
                if not category_id or not value:
                    return self.error("Select a category and enter a new name")
                await service.rename_list_category(categories[category_id], value)
                self.clear_entry()
            elif button_id == "meta-icon-category":
                if not category_id or not value:
                    return self.error("Select a category and enter an icon name")
                await service.set_list_category_icon(categories[category_id], value)
                self.clear_entry()
            elif button_id == "meta-delete-category":
                if not category_id:
                    return self.error("Select a category")
                category = categories[category_id]
                selected_group = groups.get(str(category.categoryGroupId))
                if selected_group is None:
                    return self.error("Category group is missing")
                if str(selected_group.defaultCategoryId) == category_id:
                    return self.error("Refusing to remove the group's default category")
                await service.remove_category_ids(selected_group, [category])
            self._refresh_metadata()
        except Exception as exc:
            self.error(exc)


class FoldersPanel(SDKPanel):
    """Small, intentionally conservative folder-management surface.

    Recursive folder deletion and moving arbitrary nested trees are valid SDK operations, but
    are intentionally left out of this example because a mistaken click can remove shopping
    lists along with a folder.  Creating and editing folder presentation is enough to exercise
    the normal folder service without making the example unnecessarily dangerous.
    """

    DEFAULT_CSS = """
    FoldersPanel .toolbar { height: auto; }
    FoldersPanel #folder-table { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.folder_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Folder name, #RRGGBB color, or icon name", classes="entry")
        with Horizontal(classes="toolbar"):
            yield Button("New root folder", id="folder-new", variant="success")
            yield Button("Rename", id="folder-rename")
            yield Button("Set color", id="folder-color")
            yield Button("Set icon", id="folder-icon")
        yield Label("Folders (destructive recursive delete/move intentionally omitted)")
        yield DataTable(id="folder-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#folder-table", DataTable).add_columns(
            "Folder", "Children", "Lists", "Color", "Icon", "Root"
        )

    async def refresh_view(self) -> None:
        service = self.client.folders
        if service is None:
            return
        table = self.query_one("#folder-table", DataTable)
        old = _selected_id(table, self.folder_ids)
        folders = sorted(service.all(), key=lambda value: _message_name(value).casefold())
        root_id = self.client.state.root_folder_id or ""
        self.folder_ids = [str(value.identifier) for value in folders]

        def folder_row(folder: object) -> tuple[object, ...]:
            items = list(getattr(folder, "items", ()))
            child_count = sum(1 for item in items if int(item.itemType) == 1)
            list_count = sum(1 for item in items if int(item.itemType) == 0)
            settings = getattr(folder, "folderSettings", None)
            color = getattr(settings, "folderHexColor", "") if settings is not None else ""
            icon = ""
            if settings is not None and getattr(settings, "icon", None) is not None:
                icon = getattr(settings.icon, "iconName", "") or ""
            identifier = str(getattr(folder, "identifier", ""))
            return (
                _message_name(folder, "(unnamed root)"),
                child_count,
                list_count,
                color or "",
                icon,
                "yes" if identifier == root_id else "",
            )

        _replace_rows(
            table,
            (folder_row(folder) for folder in folders),
            self.folder_ids,
            keep_id=old,
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("folder-"):
            return
        service = self.client.folders
        if service is None:
            return
        table = self.query_one("#folder-table", DataTable)
        folder_id = _selected_id(table, self.folder_ids)
        value = self.entry()
        try:
            if button_id == "folder-new":
                if not value:
                    return self.error("Enter a folder name")
                await service.create(value)
                self.clear_entry()
            elif button_id == "folder-rename":
                if not folder_id or not value:
                    return self.error("Select a folder and enter a new name")
                if folder_id == self.client.state.root_folder_id:
                    return self.error("The root folder is not renamed by this example")
                await service.rename(folder_id, value)
                self.clear_entry()
            elif button_id == "folder-color":
                if not folder_id or not value:
                    return self.error("Select a folder and enter a #RRGGBB color")
                color = value if value.startswith("#") else f"#{value}"
                if len(color) != 7 or any(ch not in "0123456789abcdefABCDEF" for ch in color[1:]):
                    return self.error("Folder color must be a six-digit hex color, e.g. #4A90E2")
                await service.set_hex_color(folder_id, color)
                self.clear_entry()
            elif button_id == "folder-icon":
                if not folder_id or not value:
                    return self.error("Select a folder and enter an icon name")
                await service.set_icon(folder_id, value)
                self.clear_entry()
            await self.refresh_view()
        except Exception as exc:
            self.error(exc)


class StarterPanel(SDKPanel):
    DEFAULT_CSS = """
    StarterPanel .toolbar { height: auto; }
    StarterPanel #starter-tables { height: 1fr; }
    StarterPanel #starter-lists { width: 42%; }
    StarterPanel #starter-items { width: 58%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.list_ids: list[str] = []
        self.item_ids: list[str] = []
        self.current_list_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Starter-list name / item name", classes="entry")
        with Horizontal(classes="toolbar"):
            yield Button("New custom list", id="starter-new-list")
            yield Button("Rename custom list", id="starter-rename-list")
            yield Button("Delete custom list", id="starter-delete-list", variant="error")
            yield Button("Add item", id="starter-add-item", variant="success")
            yield Button("Rename item", id="starter-rename-item")
            yield Button("Set details", id="starter-details")
            yield Button("Remove item", id="starter-remove-item", variant="error")
        with Horizontal(id="starter-tables"):
            with Vertical():
                yield Label("Favorites / Recents / custom starter lists")
                yield DataTable(id="starter-lists", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Label("Items")
                yield DataTable(id="starter-items", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#starter-lists", DataTable).add_columns("Name", "Type", "Items")
        self.query_one("#starter-items", DataTable).add_columns("Item", "Details", "Stores")

    def _all_lists(self) -> list[StarterList]:
        state = self.client.state
        combined = list(state.starter_lists.values())
        combined += list(state.favorite_item_lists.values())
        combined += list(state.recent_item_lists.values())
        seen: set[str] = set()
        result: list[StarterList] = []
        for value in combined:
            identifier = str(value.identifier)
            if identifier in seen:
                continue
            seen.add(identifier)
            result.append(value)
        return sorted(result, key=lambda value: _message_name(value).casefold())

    def _kind(self, identifier: str) -> str:
        state = self.client.state
        if identifier in state.favorite_item_lists:
            return "Favorite"
        if identifier in state.recent_item_lists:
            return "Recent"
        return "Custom"

    async def refresh_view(self) -> None:
        service = self.client.starter_lists
        if service is None:
            return
        table = self.query_one("#starter-lists", DataTable)
        old = self.current_list_id or _selected_id(table, self.list_ids)
        lists = self._all_lists()
        self.list_ids = [str(value.identifier) for value in lists]
        _replace_rows(
            table,
            ((value.name, self._kind(str(value.identifier)), len(value.items)) for value in lists),
            self.list_ids,
            keep_id=old,
        )
        self.current_list_id = _selected_id(table, self.list_ids)
        self._refresh_items()

    def _refresh_items(self) -> None:
        table = self.query_one("#starter-items", DataTable)
        service = self.client.starter_lists
        if service is None or not self.current_list_id:
            table.clear()
            self.item_ids = []
            return
        starter = service.get(self.current_list_id)
        if starter is None:
            table.clear()
            self.item_ids = []
            return
        old = _selected_id(table, self.item_ids)
        items = list(starter.items)
        self.item_ids = [str(item.identifier) for item in items]
        _replace_rows(
            table,
            ((item.name, item.details, len(item.storeIds)) for item in items),
            self.item_ids,
            keep_id=old,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "starter-lists":
            self.current_list_id = _selected_id(event.data_table, self.list_ids)
            self._refresh_items()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("starter-"):
            return
        service = self.client.starter_lists
        if service is None:
            return
        list_id = self.current_list_id
        item_id = _selected_id(self.query_one("#starter-items", DataTable), self.item_ids)
        value = self.entry()
        try:
            if button_id == "starter-new-list":
                if not value:
                    return self.error("Enter a custom starter-list name")
                await service.create(value)
                self.clear_entry()
            elif button_id == "starter-rename-list":
                if not list_id or self._kind(list_id) != "Custom" or not value:
                    return self.error("Select a custom starter list and enter a new name")
                await service.rename(list_id, value)
                self.clear_entry()
            elif button_id == "starter-delete-list":
                if not list_id or self._kind(list_id) != "Custom":
                    return self.error("Only custom starter lists may be deleted here")
                await service.remove(list_id)
                self.current_list_id = None
            elif button_id == "starter-add-item":
                if not list_id or not value:
                    return self.error("Select a list and enter an item name")
                if self._kind(list_id) == "Recent":
                    return self.error("Recent Items is managed by shopping-list actions")
                await service.add_item(list_id, PB.ListItem(name=value))
                self.clear_entry()
            elif button_id == "starter-rename-item":
                if not list_id or not item_id or not value:
                    return self.error("Select an item and enter its new name")
                if self._kind(list_id) == "Recent":
                    return self.error("Recent Items is managed by shopping-list actions")
                await service.set_item_name(list_id, item_id, value)
                self.clear_entry()
            elif button_id == "starter-details":
                if not list_id or not item_id:
                    return self.error("Select an item; entry text becomes its details")
                if self._kind(list_id) == "Recent":
                    return self.error("Recent Items is managed by shopping-list actions")
                await service.set_item_details(list_id, item_id, value)
                self.clear_entry()
            elif button_id == "starter-remove-item":
                if not list_id or not item_id:
                    return self.error("Select an item")
                await service.remove_item(list_id, item_id)
            await self.refresh_view()
        except Exception as exc:
            self.error(exc)


class RecipesPanel(SDKPanel):
    DEFAULT_CSS = """
    RecipesPanel .toolbar { height: auto; }
    RecipesPanel #recipe-tables { height: 1fr; }
    RecipesPanel #recipe-list { width: 58%; }
    RecipesPanel #collection-list { width: 42%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.recipe_ids: list[str] = []
        self.collection_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Recipe/collection name, note, or servings", classes="entry")
        with Horizontal(classes="toolbar"):
            yield Button("New recipe", id="recipe-new", variant="success")
            yield Button("Rename recipe", id="recipe-rename")
            yield Button("Set note", id="recipe-note")
            yield Button("Set servings", id="recipe-servings")
            yield Button("Delete recipe", id="recipe-delete", variant="error")
            yield Button("New collection", id="collection-new")
            yield Button("Rename collection", id="collection-rename")
            yield Button("Delete collection", id="collection-delete", variant="error")
            yield Button("Add recipe → collection", id="collection-add")
            yield Button("Remove recipe ← collection", id="collection-remove")
        with Horizontal(id="recipe-tables"):
            with Vertical():
                yield Label("Recipes")
                yield DataTable(id="recipe-list", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Label("Collections")
                yield DataTable(id="collection-list", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#recipe-list", DataTable).add_columns(
            "Recipe", "Rating", "Servings", "Ingredients", "Source"
        )
        self.query_one("#collection-list", DataTable).add_columns("Collection", "Recipes")

    async def refresh_view(self) -> None:
        service = self.client.recipes
        if service is None:
            return
        recipe_table = self.query_one("#recipe-list", DataTable)
        collection_table = self.query_one("#collection-list", DataTable)
        old_recipe = _selected_id(recipe_table, self.recipe_ids)
        old_collection = _selected_id(collection_table, self.collection_ids)
        recipes = service.sorted()
        collections = sorted(service.collections(), key=lambda value: _message_name(value).casefold())
        self.recipe_ids = [str(value.identifier) for value in recipes]
        self.collection_ids = [str(value.identifier) for value in collections]
        _replace_rows(
            recipe_table,
            (
                (
                    value.name,
                    value.rating,
                    value.servings,
                    len(value.ingredients),
                    value.sourceName,
                )
                for value in recipes
            ),
            self.recipe_ids,
            keep_id=old_recipe,
        )
        _replace_rows(
            collection_table,
            ((value.name, len(value.recipeIds)) for value in collections),
            self.collection_ids,
            keep_id=old_collection,
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith(("recipe-", "collection-")):
            return
        service = self.client.recipes
        if service is None:
            return
        recipe_id = _selected_id(self.query_one("#recipe-list", DataTable), self.recipe_ids)
        collection_id = _selected_id(
            self.query_one("#collection-list", DataTable), self.collection_ids
        )
        value = self.entry()
        try:
            if button_id == "recipe-new":
                if not value:
                    return self.error("Enter a recipe name")
                await service.create(value)
                self.clear_entry()
            elif button_id in {"recipe-rename", "recipe-note", "recipe-servings"}:
                if not recipe_id:
                    return self.error("Select a recipe")
                current = service.get(recipe_id)
                if current is None:
                    return
                updated = PB.PBRecipe()
                updated.CopyFrom(current)
                if button_id == "recipe-rename":
                    if not value:
                        return self.error("Enter a new recipe name")
                    updated.name = value
                elif button_id == "recipe-note":
                    updated.note = value
                else:
                    updated.servings = value
                await service.save(updated)
                self.clear_entry()
            elif button_id == "recipe-delete":
                if not recipe_id:
                    return self.error("Select a recipe")
                await service.remove(recipe_id)
            elif button_id == "collection-new":
                if not value:
                    return self.error("Enter a collection name")
                await service.create_collection(value)
                self.clear_entry()
            elif button_id == "collection-rename":
                if not collection_id or not value:
                    return self.error("Select a collection and enter a new name")
                await service.rename_collection(collection_id, value)
                self.clear_entry()
            elif button_id == "collection-delete":
                if not collection_id:
                    return self.error("Select a collection")
                await service.remove_collection(collection_id)
            elif button_id == "collection-add":
                if not collection_id or not recipe_id:
                    return self.error("Select both a recipe and a collection")
                await service.add_to_collection(collection_id, [recipe_id])
            elif button_id == "collection-remove":
                if not collection_id or not recipe_id:
                    return self.error("Select both a recipe and a collection")
                await service.remove_from_collection(collection_id, [recipe_id])
            await self.refresh_view()
        except Exception as exc:
            self.error(exc)


class MealPlanPanel(SDKPanel):
    DEFAULT_CSS = """
    MealPlanPanel .toolbar { height: auto; }
    MealPlanPanel #meal-events { height: 46%; }
    MealPlanPanel #meal-bottom { height: 1fr; }
    MealPlanPanel #meal-items { width: 60%; }
    MealPlanPanel #meal-labels { width: 40%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.event_ids: list[str] = []
        self.item_ids: list[str] = []
        self.label_ids: list[str] = []
        self.current_event_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Event title/details/date/item/label name", classes="entry")
        with Horizontal(classes="toolbar"):
            yield Button("New queue event", id="meal-new-event", variant="success")
            yield Button("Rename event", id="meal-rename-event")
            yield Button("Set details", id="meal-details")
            yield Button("Set date YYYY-MM-DD", id="meal-date")
            yield Button("Move to queue", id="meal-clear-date")
            yield Button("Delete event", id="meal-delete-event", variant="error")
            yield Button("Add event item", id="meal-add-item")
            yield Button("Rename event item", id="meal-rename-item")
            yield Button("Set item details", id="meal-item-details")
            yield Button("Remove event item", id="meal-remove-item")
            yield Button("New label", id="meal-new-label")
            yield Button("Rename label", id="meal-rename-label")
            yield Button("Assign label", id="meal-assign-label")
            yield Button("Clear label", id="meal-clear-label")
            yield Button("Delete label", id="meal-delete-label", variant="error")
        yield DataTable(id="meal-events", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="meal-bottom"):
            with Vertical():
                yield Label("Event items")
                yield DataTable(id="meal-items", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Label("Labels")
                yield DataTable(id="meal-labels", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#meal-events", DataTable).add_columns(
            "Event", "Type", "Date", "Label", "Items"
        )
        self.query_one("#meal-items", DataTable).add_columns("Item", "Details")
        self.query_one("#meal-labels", DataTable).add_columns("Label", "Color")

    async def refresh_view(self) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        event_table = self.query_one("#meal-events", DataTable)
        label_table = self.query_one("#meal-labels", DataTable)
        old_event = self.current_event_id or _selected_id(event_table, self.event_ids)
        old_label = _selected_id(label_table, self.label_ids)
        events = sorted(service.events(), key=lambda value: (str(value.date or ""), str(value.title).casefold()))
        labels = sorted(service.labels(), key=lambda value: int(value.sortIndex))
        self.event_ids = [str(value.identifier) for value in events]
        self.label_ids = [str(value.identifier) for value in labels]
        label_names = {str(value.identifier): str(value.name) for value in labels}
        _replace_rows(
            event_table,
            (
                (
                    event.title,
                    int(event.eventType),
                    event.date or "queue",
                    label_names.get(str(event.labelId), ""),
                    len(event.eventListItems),
                )
                for event in events
            ),
            self.event_ids,
            keep_id=old_event,
        )
        _replace_rows(
            label_table,
            ((label.name, label.hexColor) for label in labels),
            self.label_ids,
            keep_id=old_label,
        )
        self.current_event_id = _selected_id(event_table, self.event_ids)
        self._refresh_items()

    def _event(self) -> PBCalendarEvent | None:
        if not self.current_event_id:
            return None
        return self.client.state.meal_plan_events.get(self.current_event_id)

    def _refresh_items(self) -> None:
        table = self.query_one("#meal-items", DataTable)
        event = self._event()
        if event is None:
            self.item_ids = []
            table.clear()
            return
        old = _selected_id(table, self.item_ids)
        items = list(event.eventListItems)
        self.item_ids = [str(item.identifier) for item in items]
        _replace_rows(
            table,
            ((item.name, item.details) for item in items),
            self.item_ids,
            keep_id=old,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "meal-events":
            self.current_event_id = _selected_id(event.data_table, self.event_ids)
            self._refresh_items()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("meal-"):
            return
        service = self.client.meal_plan
        if service is None:
            return
        event_id = self.current_event_id
        item_id = _selected_id(self.query_one("#meal-items", DataTable), self.item_ids)
        label_id = _selected_id(self.query_one("#meal-labels", DataTable), self.label_ids)
        value = self.entry()
        try:
            if button_id == "meal-new-event":
                if not value:
                    return self.error("Enter an event title")
                await service.save_event(
                    PB.PBCalendarEvent(
                        eventType=PB.PBCalendarEventType.MealPlanQueueEvent,
                        title=value,
                    )
                )
                self.clear_entry()
            elif button_id == "meal-rename-event":
                if not event_id or not value:
                    return self.error("Select an event and enter a new title")
                await service.set_event_title(event_id, value)
                self.clear_entry()
            elif button_id == "meal-details":
                if not event_id:
                    return self.error("Select an event")
                await service.set_event_details(event_id, value)
                self.clear_entry()
            elif button_id == "meal-date":
                if not event_id or not value:
                    return self.error("Select an event and enter YYYY-MM-DD")
                await service.set_event_date([event_id], value)
                self.clear_entry()
            elif button_id == "meal-clear-date":
                if not event_id:
                    return self.error("Select an event")
                await service.set_event_date([event_id], None)
            elif button_id == "meal-delete-event":
                if not event_id:
                    return self.error("Select an event")
                await service.delete_event(event_id)
                self.current_event_id = None
            elif button_id == "meal-add-item":
                if not event_id or not value:
                    return self.error("Select an event and enter an item")
                await service.add_event_list_item(event_id, PB.PBCalendarEventListItem(name=value))
                self.clear_entry()
            elif button_id == "meal-rename-item":
                if not event_id or not item_id or not value:
                    return self.error("Select an event item and enter its new name")
                await service.set_event_list_item_name(event_id, item_id, value)
                self.clear_entry()
            elif button_id == "meal-item-details":
                if not event_id or not item_id:
                    return self.error("Select an event item; entry text becomes its details")
                await service.set_event_list_item_details(event_id, item_id, value)
                self.clear_entry()
            elif button_id == "meal-remove-item":
                if not event_id or not item_id:
                    return self.error("Select an event item")
                await service.remove_event_list_item(event_id, item_id)
            elif button_id == "meal-new-label":
                if not value:
                    return self.error("Enter a label name")
                await service.save_label(PB.PBCalendarLabel(name=value, hexColor="#808080"))
                self.clear_entry()
            elif button_id == "meal-rename-label":
                if not label_id or not value:
                    return self.error("Select a label and enter a new name")
                current = self.client.state.meal_plan_labels[label_id]
                updated = PB.PBCalendarLabel()
                updated.CopyFrom(current)
                updated.name = value
                await service.save_label(updated, is_new=False)
                self.clear_entry()
            elif button_id == "meal-assign-label":
                if not event_id or not label_id:
                    return self.error("Select both an event and a label")
                await service.set_event_label(event_id, label_id)
            elif button_id == "meal-clear-label":
                if not event_id:
                    return self.error("Select an event")
                await service.set_event_label(event_id, "")
            elif button_id == "meal-delete-label":
                if not label_id:
                    return self.error("Select a label")
                await service.delete_label(label_id)
            await self.refresh_view()
        except Exception as exc:
            self.error(exc)


class CategoriesPanel(SDKPanel):
    DEFAULT_CSS = """
    CategoriesPanel .toolbar { height: auto; }
    CategoriesPanel #category-tables { height: 1fr; }
    CategoriesPanel #global-categories { width: 50%; }
    CategoriesPanel #global-groups { width: 50%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.category_ids: list[str] = []
        self.group_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Category/group name or category icon", classes="entry")
        with Horizontal(classes="toolbar"):
            yield Button("New category", id="cat-new")
            yield Button("Rename category", id="cat-rename")
            yield Button("Set icon", id="cat-icon")
            yield Button("Delete category", id="cat-delete", variant="error")
            yield Button("New group", id="group-new")
            yield Button("Rename group", id="group-rename")
            yield Button("Add category → group", id="group-add")
            yield Button("Remove category ← group", id="group-remove-category")
            yield Button("Hide group", id="group-hide")
            yield Button("Delete group", id="group-delete", variant="error")
        with Horizontal(id="category-tables"):
            with Vertical():
                yield Label("Global user categories")
                yield DataTable(id="global-categories", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Label("Category groupings")
                yield DataTable(id="global-groups", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#global-categories", DataTable).add_columns("Category", "Icon", "Match ID")
        self.query_one("#global-groups", DataTable).add_columns("Group", "Members", "Hidden")

    async def refresh_view(self) -> None:
        service = self.client.categories
        if service is None:
            return
        category_table = self.query_one("#global-categories", DataTable)
        group_table = self.query_one("#global-groups", DataTable)
        old_category = _selected_id(category_table, self.category_ids)
        old_group = _selected_id(group_table, self.group_ids)
        categories = sorted(service.all(), key=lambda value: _message_name(value).casefold())
        groups = sorted(service.groupings(), key=lambda value: _message_name(value).casefold())
        self.category_ids = [str(value.identifier) for value in categories]
        self.group_ids = [str(value.identifier) for value in groups]
        _replace_rows(
            category_table,
            ((value.name, value.icon, value.categoryMatchId) for value in categories),
            self.category_ids,
            keep_id=old_category,
        )
        _replace_rows(
            group_table,
            (
                (
                    value.name,
                    len(value.categoryIds),
                    "yes" if value.shouldHideFromBrowseListCategoryGroupsScreen else "",
                )
                for value in groups
            ),
            self.group_ids,
            keep_id=old_group,
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith(("cat-", "group-")):
            return
        service = self.client.categories
        if service is None:
            return
        category_id = _selected_id(
            self.query_one("#global-categories", DataTable), self.category_ids
        )
        group_id = _selected_id(self.query_one("#global-groups", DataTable), self.group_ids)
        value = self.entry()
        categories = self.client.state.user_categories
        groups = self.client.state.category_groupings
        try:
            if button_id == "cat-new":
                if not value:
                    return self.error("Enter a category name")
                await service.add_category(value)
                self.clear_entry()
            elif button_id == "cat-rename":
                if not category_id or not value:
                    return self.error("Select a category and enter a new name")
                await service.rename_category(category_id, value)
                self.clear_entry()
            elif button_id == "cat-icon":
                if not category_id or not value:
                    return self.error("Select a category and enter an icon")
                await service.set_category_icon(category_id, value)
                self.clear_entry()
            elif button_id == "cat-delete":
                if not category_id:
                    return self.error("Select a category")
                await service.remove_category(category_id)
            elif button_id == "group-new":
                if not value:
                    return self.error("Enter a grouping name")
                await service.add_grouping(value)
                self.clear_entry()
            elif button_id == "group-rename":
                if not group_id or not value:
                    return self.error("Select a grouping and enter a new name")
                await service.rename_grouping(group_id, value)
                self.clear_entry()
            elif button_id == "group-add":
                if not group_id or not category_id:
                    return self.error("Select both a category and grouping")
                group = groups[group_id]
                ids = list(group.categoryIds)
                if category_id not in ids:
                    ids.append(category_id)
                    await service.set_grouping_categories(group_id, ids)
            elif button_id == "group-remove-category":
                if not group_id or not category_id:
                    return self.error("Select both a category and grouping")
                group = groups[group_id]
                await service.set_grouping_categories(
                    group_id, [identifier for identifier in group.categoryIds if identifier != category_id]
                )
            elif button_id == "group-hide":
                if not group_id:
                    return self.error("Select a grouping")
                await service.hide_grouping_from_browse(group_id)
            elif button_id == "group-delete":
                if not group_id:
                    return self.error("Select a grouping")
                await service.remove_grouping(group_id)
            # Keep these mappings touched so stale selections fail obviously during development.
            _ = categories
            await self.refresh_view()
        except Exception as exc:
            self.error(exc)


class StatusPanel(SDKPanel):
    def compose(self) -> ComposeResult:
        yield Static(id="status-summary")

    async def refresh_view(self) -> None:
        client = self.client
        account = client.state.account_info
        lines = [
            "[b]anylist-sdk TUI example[/b]",
            "",
            f"User ID: {client.user_id or 'unknown'}",
            f"Realtime: {'connected' if client.realtime.connected.is_set() else 'disconnected'}",
            f"Shopping lists: {len(client.state.shopping_lists)}",
            f"Folders: {len(client.state.list_folders)}",
            f"Starter lists: {len(client.state.starter_lists) + len(client.state.favorite_item_lists) + len(client.state.recent_item_lists)}",
            f"Recipes: {len(client.state.recipes)}",
            f"Recipe collections: {len(client.state.recipe_collections)}",
            f"Meal-plan events: {len(client.state.meal_plan_events)}",
            f"Meal-plan templates: {len(client.state.meal_plan_templates)}",
            f"Global categories: {len(client.state.user_categories)}",
            "",
            "Keys: [b]r[/b] refresh from server · [b]q[/b] quit · [b]1–8[/b] switch tabs",
            "",
            "This example intentionally omits external-side-effect actions such as email/sharing,",
            "Alexa linking, photo upload, recipe web import, and account-name changes.",
        ]
        if account is not None:
            lines.insert(4, "Account info loaded: yes")
        self.query_one("#status-summary", Static).update("\n".join(lines))


class AnyListTUI(App[None]):
    TITLE = "AnyList SDK TUI"
    SUB_TITLE = "Example client"
    CSS = """
    Screen { layout: vertical; }
    Header { dock: top; }
    Footer { dock: bottom; }
    .toolbar { layout: horizontal; height: auto; overflow-x: auto; }
    .toolbar Button { margin-right: 1; min-width: 14; }
    .entry { margin-bottom: 1; }
    DataTable { height: 1fr; border: round $primary; }
    TabPane { padding: 1; }
    #status-summary { padding: 2; }
    """
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Server refresh"),
        Binding("1", "tab('shopping')", "Shopping", show=False),
        Binding("2", "tab('list-data')", "List data", show=False),
        Binding("3", "tab('folders')", "Folders", show=False),
        Binding("4", "tab('starters')", "Favorites", show=False),
        Binding("5", "tab('recipes')", "Recipes", show=False),
        Binding("6", "tab('meal-plan')", "Meal plan", show=False),
        Binding("7", "tab('categories')", "Categories", show=False),
        Binding("8", "tab('status')", "Status", show=False),
    ]

    def __init__(self, client: AnyListClient, email: str, cache_path: Path) -> None:
        super().__init__()
        self.client = client
        self.email = email
        self.cache_path = cache_path

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="shopping", id="tabs"):
            with TabPane("Shopping", id="shopping"):
                yield ShoppingPanel()
            with TabPane("Stores & categories", id="list-data"):
                yield ListDataPanel()
            with TabPane("Folders", id="folders"):
                yield FoldersPanel()
            with TabPane("Favorites / Recents", id="starters"):
                yield StarterPanel()
            with TabPane("Recipes", id="recipes"):
                yield RecipesPanel()
            with TabPane("Meal plan", id="meal-plan"):
                yield MealPlanPanel()
            with TabPane("Global categories", id="categories"):
                yield CategoriesPanel()
            with TabPane("Status", id="status"):
                yield StatusPanel()
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_views()
        self.set_interval(2.0, self.refresh_views)
        self.notify("Connected to AnyList")

    async def refresh_views(self) -> None:
        for panel in self.query(SDKPanel):
            try:
                await panel.refresh_view()
            except Exception:
                # Periodic local redraw should never crash the whole application.
                continue

    async def action_refresh(self) -> None:
        try:
            await self.client.refresh()
            await self.refresh_views()
            self.notify("Refreshed from AnyList")
        except Exception as exc:
            self.notify(str(exc), severity="error", timeout=6)

    def action_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id

    async def on_unmount(self) -> None:
        tokens = self.client.tokens
        if tokens is not None:
            _save_token_cache(self.cache_path, self.email, tokens)


async def _authenticated_client(cache_path: Path, *, force_login: bool = False) -> tuple[AnyListClient, str]:
    cached = None if force_login else _load_token_cache(cache_path)
    if cached is not None:
        email, tokens = cached
        client = AnyListClient(tokens=tokens, user_email=email, cache_dir=SDK_CACHE)
        try:
            await client.load(realtime=True, load_tag_data=False, restore_pending=True)
            if client.tokens is not None:
                _save_token_cache(cache_path, email, client.tokens)
            return client, email
        except Exception as exc:
            print(f"Cached AnyList session failed ({exc}); signing in again.", file=sys.stderr)
            await client.close()

    email = input("AnyList email: ").strip()
    password = getpass("AnyList password: ")
    if not email or not password:
        raise SystemExit("Email and password are required")
    client = AnyListClient(user_email=email, cache_dir=SDK_CACHE)
    try:
        tokens = await client.sign_in(email, password)
        _save_token_cache(cache_path, email, tokens)
        await client.load(realtime=True, load_tag_data=False, restore_pending=True)
        if client.tokens is not None:
            _save_token_cache(cache_path, email, client.tokens)
        return client, email
    except Exception:
        await client.close()
        raise


async def _async_main(args: argparse.Namespace) -> None:
    cache_path = Path(args.token_cache).expanduser()
    if args.logout:
        try:
            cache_path.unlink()
        except FileNotFoundError:
            pass
        print(f"Removed cached AnyList TUI session: {cache_path}")
        return

    client, email = await _authenticated_client(cache_path, force_login=args.login)
    app = AnyListTUI(client, email, cache_path)
    try:
        await app.run_async()
    finally:
        tokens = client.tokens
        if tokens is not None:
            _save_token_cache(cache_path, email, tokens)
        await client.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feature-rich Textual example for anylist-sdk")
    parser.add_argument(
        "--token-cache",
        default=os.fspath(TOKEN_CACHE),
        help=f"cached token file (default: {TOKEN_CACHE})",
    )
    parser.add_argument("--login", action="store_true", help="ignore cached tokens and sign in again")
    parser.add_argument("--logout", action="store_true", help="remove cached tokens and exit")
    return parser


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
