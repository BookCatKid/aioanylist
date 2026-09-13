#!/usr/bin/env python3
"""A user-facing Textual example client for ``anylist-sdk``.

The SDK exposes many protocol-oriented services.  This example intentionally does not mirror
those service boundaries in its navigation.  Its primary UI follows the three concepts a normal
AnyList user expects: Lists, Recipes, and Meal Plan.  Less-common list settings live behind a
contextual screen, and editing uses focused forms instead of one overloaded text box.

Install and run with::

    python -m pip install -e '.[tui]'
    python examples/anylist_tui.py

The password is never stored.  Only access/refresh tokens and the account email are cached.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from getpass import getpass
from pathlib import Path
from typing import ClassVar, Literal, cast
from uuid import uuid4

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button,
        Checkbox,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        Select,
        SelectionList,
        Static,
        TabbedContent,
        TabPane,
        TextArea,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - friendly optional-extra error
    raise SystemExit(
        "Textual is required for this example. Install with: pip install -e '.[tui]'"
    ) from exc

from anylist_sdk import AnyListClient
from anylist_sdk.item_semantics import apply_properties_from_item
from anylist_sdk.normalization import canonical_category_match_id
from anylist_sdk.parsing.quantity import parse_quantity_and_package_size
from anylist_sdk.proto import PB, ListItem, PBCalendarEvent, StarterList
from anylist_sdk.types import AuthTokens

APP_DIR = Path.home() / ".config" / "anylist-sdk"
TOKEN_CACHE = APP_DIR / "tui-tokens.json"
SDK_CACHE = APP_DIR / "cache"
AUTO_CATEGORY = "__auto__"


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


def _folder_parent_map(client: AnyListClient) -> dict[str, str]:
    parents: dict[str, str] = {}
    for parent_id, folder in client.state.list_folders.items():
        for item in folder.items:
            if int(item.itemType) == 1:
                parents[str(item.identifier)] = parent_id
    return parents


def _folder_label(client: AnyListClient, folder_id: str) -> str:
    root_id = client.state.root_folder_id or ""
    if folder_id == root_id:
        return "Top level"
    parents = _folder_parent_map(client)
    parts: list[str] = []
    seen: set[str] = set()
    current = folder_id
    while current and current != root_id and current not in seen:
        seen.add(current)
        folder = client.state.list_folders.get(current)
        if folder is None:
            break
        parts.append(_message_name(folder, "Unnamed folder"))
        current = parents.get(current, "")
    return " / ".join(reversed(parts)) or "Top level"


def _folder_options(client: AnyListClient) -> tuple[tuple[str, str], ...]:
    values = list(client.state.list_folders)
    values.sort(key=lambda folder_id: _folder_label(client, folder_id).casefold())
    return tuple((_folder_label(client, folder_id), folder_id) for folder_id in values)


def _list_folder_id(client: AnyListClient, list_id: str) -> str | None:
    for folder_id, folder in client.state.list_folders.items():
        if any(
            int(item.itemType) == 0 and str(item.identifier) == list_id for item in folder.items
        ):
            return folder_id
    return None


def _hex_color(value: str) -> str | None:
    if not value:
        return ""
    color = value if value.startswith("#") else f"#{value}"
    if len(color) != 7 or any(ch not in "0123456789abcdefABCDEF" for ch in color[1:]):
        return None
    return color.upper()


FieldKind = Literal["input", "textarea", "select", "multiselect", "checkbox"]


@dataclass(frozen=True)
class FormField:
    key: str
    label: str
    kind: FieldKind = "input"
    value: object = ""
    placeholder: str = ""
    options: tuple[tuple[str, object], ...] = ()
    selected: tuple[object, ...] = ()
    allow_blank: bool = True


FormResult = dict[str, object]
FormHandler = Callable[[FormResult], Awaitable[None]]


class FormModal(ModalScreen[FormResult | None]):
    """Small reusable form screen used by all create/edit flows."""

    DEFAULT_CSS = """
    FormModal { align: center middle; background: $background 65%; }
    FormModal #form-dialog {
        width: 76; max-width: 92%; height: auto; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    FormModal #form-title { text-style: bold; margin-bottom: 1; }
    FormModal .field-label { margin-top: 1; }
    FormModal .form-area { height: 6; }
    FormModal SelectionList { height: 7; border: round $panel; }
    FormModal .form-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    FormModal .form-buttons Button { margin-left: 1; }
    """

    def __init__(
        self,
        title: str,
        fields: Sequence[FormField],
        *,
        submit_label: str = "Save",
    ) -> None:
        super().__init__()
        self.form_title = title
        self.fields = tuple(fields)
        self.submit_label = submit_label

    def compose(self) -> ComposeResult:
        with Vertical(id="form-dialog"):
            yield Label(self.form_title, id="form-title")
            with VerticalScroll():
                for field in self.fields:
                    widget_id = f"field-{field.key}"
                    if field.kind == "checkbox":
                        yield Checkbox(field.label, value=bool(field.value), id=widget_id)
                        continue
                    yield Label(field.label, classes="field-label")
                    if field.kind == "textarea":
                        yield TextArea(
                            str(field.value or ""),
                            id=widget_id,
                            classes="form-area",
                            placeholder=field.placeholder,
                        )
                    elif field.kind == "select":
                        value = Select.NULL if field.value is None else field.value
                        yield Select(
                            field.options,
                            value=value,
                            allow_blank=field.allow_blank,
                            id=widget_id,
                        )
                    elif field.kind == "multiselect":
                        chosen = set(field.selected)
                        yield SelectionList(
                            *((label, value, value in chosen) for label, value in field.options),
                            id=widget_id,
                        )
                    else:
                        yield Input(
                            value=str(field.value or ""),
                            placeholder=field.placeholder,
                            id=widget_id,
                        )
            with Horizontal(classes="form-buttons"):
                yield Button("Cancel", id="form-cancel")
                yield Button(self.submit_label, id="form-submit", variant="primary")

    def _values(self) -> FormResult:
        result: FormResult = {}
        for field in self.fields:
            widget_id = f"#field-{field.key}"
            if field.kind == "textarea":
                result[field.key] = self.query_one(widget_id, TextArea).text
            elif field.kind == "select":
                value = self.query_one(widget_id, Select).value
                result[field.key] = None if value is Select.NULL else value
            elif field.kind == "multiselect":
                result[field.key] = list(self.query_one(widget_id, SelectionList).selected)
            elif field.kind == "checkbox":
                result[field.key] = self.query_one(widget_id, Checkbox).value
            else:
                result[field.key] = self.query_one(widget_id, Input).value.strip()
        return result

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-cancel":
            self.dismiss(None)
        elif event.button.id == "form-submit":
            self.dismiss(self._values())

    def on_key(self, event: object) -> None:
        key = getattr(event, "key", "")
        if key == "escape":
            self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmModal { align: center middle; background: $background 65%; }
    ConfirmModal #confirm-dialog {
        width: 64; height: auto; padding: 1 2;
        border: round $error; background: $surface;
    }
    ConfirmModal #confirm-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    ConfirmModal #confirm-buttons Button { margin-left: 1; }
    """

    def __init__(self, title: str, message: str, *, confirm_label: str = "Delete") -> None:
        super().__init__()
        self.confirm_title = title
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(f"[b]{self.confirm_title}[/b]")
            yield Static(self.message)
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="confirm-cancel")
                yield Button(self.confirm_label, id="confirm-ok", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-ok")


class SDKPanel(Vertical):
    """Shared UI helpers for panels backed by the same AnyList client."""

    @property
    def tui(self) -> AnyListTUI:
        return cast(AnyListTUI, self.app)

    @property
    def client(self) -> AnyListClient:
        return self.tui.client

    def error(self, exc: BaseException | str) -> None:
        self.tui.notify(str(exc), severity="error", timeout=6)

    def form(
        self,
        title: str,
        fields: Sequence[FormField],
        handler: FormHandler,
        *,
        submit_label: str = "Save",
    ) -> None:
        async def finished(result: FormResult | None) -> None:
            if result is None:
                return
            try:
                await handler(result)
            except Exception as exc:  # noqa: BLE001 - UI boundary reports service errors
                self.error(exc)

        self.app.push_screen(FormModal(title, fields, submit_label=submit_label), finished)

    def confirm(
        self,
        title: str,
        message: str,
        handler: Callable[[], Awaitable[None]],
        *,
        confirm_label: str = "Delete",
    ) -> None:
        async def finished(confirmed: bool | None) -> None:
            if not confirmed:
                return
            try:
                await handler()
            except Exception as exc:  # noqa: BLE001 - UI boundary reports service errors
                self.error(exc)

        self.app.push_screen(ConfirmModal(title, message, confirm_label=confirm_label), finished)

    async def refresh_view(self) -> None:
        """Refresh from already-synchronized local state."""


class ListsPanel(SDKPanel):
    DEFAULT_CSS = """
    ListsPanel .toolbar { height: auto; }
    ListsPanel #lists-tables { height: 1fr; }
    ListsPanel #lists-table { width: 38%; }
    ListsPanel #items-table { width: 62%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.list_ids: list[str] = []
        self.item_ids: list[str] = []
        self.current_list_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("New list", id="lists-new", variant="success")
            yield Button("Edit list", id="lists-edit")
            yield Button("Add item", id="items-new", variant="success")
            yield Button("Edit item", id="items-edit")
            yield Button("Toggle done", id="items-toggle")
            yield Button("Remove item", id="items-remove", variant="error")
            yield Button("List Settings…", id="lists-settings")
        with Horizontal(id="lists-tables"):
            with Vertical():
                yield Label("Lists")
                yield DataTable(id="lists-table", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Label("Items")
                yield DataTable(id="items-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#lists-table", DataTable).add_columns("List", "Items", "Done", "Folder")
        self.query_one("#items-table", DataTable).add_columns(
            "Item", "✓", "Quantity", "Category", "Stores", "Details"
        )

    async def refresh_view(self) -> None:
        service = self.client.lists
        if service is None:
            return
        list_table = self.query_one("#lists-table", DataTable)
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
                    _folder_label(
                        self.client, _list_folder_id(self.client, str(value.identifier)) or ""
                    ),
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
        table = self.query_one("#items-table", DataTable)
        if service is None or self.current_list_id is None:
            self.item_ids = []
            table.clear()
            return
        current = service.get(self.current_list_id)
        if current is None:
            self.item_ids = []
            table.clear()
            return
        stores = self.client.state.list_stores.get(self.current_list_id, {})
        categories = self.client.state.list_categories.get(self.current_list_id, {})
        old_item = _selected_id(table, self.item_ids)
        items = list(current.items)
        self.item_ids = [str(item.identifier) for item in items]

        def category_name(item: object) -> str:
            for assignment in getattr(item, "categoryAssignments", ()):
                category = categories.get(str(assignment.categoryId))
                if category is not None:
                    return str(category.name)
            raw = str(getattr(item, "categoryMatchId", "") or getattr(item, "category", "") or "")
            return raw.replace("-", " ").title() if raw else ""

        def store_names(item: object) -> str:
            names = [
                str(stores[store_id].name)
                for store_id in getattr(item, "storeIds", ())
                if store_id in stores
            ]
            if len(names) > 2:
                return f"{', '.join(names[:2])} +{len(names) - 2}"
            return ", ".join(names)

        _replace_rows(
            table,
            (
                (
                    item.name,
                    "yes" if item.checked else "",
                    getattr(getattr(item, "quantityPb", None), "rawQuantity", "") or "",
                    category_name(item),
                    store_names(item),
                    item.details,
                )
                for item in items
            ),
            self.item_ids,
            keep_id=old_item,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "lists-table":
            self.current_list_id = _selected_id(event.data_table, self.list_ids)
            self._refresh_items()

    def _list_type_fields(self) -> tuple[tuple[str, object], ...]:
        return (
            ("Grocery — automatic grocery categories", 0),
            ("Categorized — manual categories", 1),
            ("Basic — no categories", 2),
        )

    def _item_form_fields(self, *, item: object | None = None) -> list[FormField]:
        list_id = self.current_list_id or ""
        stores = sorted(
            self.client.state.list_stores.get(list_id, {}).values(),
            key=lambda value: (int(value.sortIndex), str(value.name).casefold()),
        )
        categories = sorted(
            self.client.state.list_categories.get(list_id, {}).values(),
            key=lambda value: (int(value.sortIndex), str(value.name).casefold()),
        )
        category_id: str | None = None
        if item is not None:
            for assignment in getattr(item, "categoryAssignments", ()):
                if str(assignment.categoryId) in {
                    str(category.identifier) for category in categories
                }:
                    category_id = str(assignment.categoryId)
                    break
        selected_stores = tuple(str(value) for value in getattr(item, "storeIds", ()))
        category_options = tuple((str(value.name), str(value.identifier)) for value in categories)
        if item is None:
            category_options = (("Automatic (recommended)", AUTO_CATEGORY),) + category_options
            category_id = AUTO_CATEGORY

        fields = [
            FormField("name", "Name", value=getattr(item, "name", "")),
            FormField(
                "details",
                "Details" if item is not None else "Details (optional override)",
                kind="textarea",
                value=getattr(item, "details", ""),
                placeholder=(
                    "Optional notes about this item"
                    if item is not None
                    else "Leave blank to reuse saved details when available"
                ),
            ),
            FormField(
                "category",
                "Category",
                kind="select",
                value=category_id,
                options=category_options,
                allow_blank=item is not None,
            ),
            FormField(
                "stores",
                "Stores" if item is not None else "Stores (optional override)",
                kind="multiselect",
                options=tuple((str(value.name), str(value.identifier)) for value in stores),
                selected=selected_stores,
            ),
        ]
        if item is None:
            fields.insert(
                2,
                FormField(
                    "quantity",
                    "Quantity / package size (optional override)",
                    placeholder="Leave blank to reuse saved quantity; e.g. 2, 1 lb, 2 cans (14 oz)",
                ),
            )
            fields.append(
                FormField(
                    "reuse_saved",
                    "Reuse info from Favorites / Recent Items when available",
                    kind="checkbox",
                    value=True,
                )
            )
        else:
            fields.insert(
                2,
                FormField(
                    "quantity",
                    "Change quantity / package size",
                    placeholder="Leave blank to keep the current quantity",
                ),
            )
            fields.append(
                FormField(
                    "checked",
                    "Completed",
                    kind="checkbox",
                    value=bool(getattr(item, "checked", False)),
                )
            )
        return fields

    def _saved_item_for_name(self, list_id: str, name: str) -> ListItem | None:
        """Return the same kind of list-specific saved item AnyList uses for autocomplete."""
        service = self.client.starter_lists
        if service is None:
            return None
        wanted = name.casefold()
        favorite = service.favorite_for_shopping_list(list_id)
        recent = service.recent_for_shopping_list(list_id)
        for starter in (favorite, recent):
            if starter is None:
                continue
            values = service.autocomplete_items(str(starter.identifier))
            for candidate in values:
                if str(candidate.name).casefold() == wanted:
                    return candidate
        return None

    @staticmethod
    def _category_match_id(category: object) -> str:
        system = str(getattr(category, "systemCategory", "") or "")
        return system or canonical_category_match_id(str(getattr(category, "name", "") or ""))

    async def _automatic_category(
        self, list_id: str, name: str, saved: ListItem | None
    ) -> object | None:
        """Resolve the category a normal AnyList add-item flow would prefer."""
        categories = self.client.state.list_categories.get(list_id, {})
        if not categories:
            return None

        # Per-list categorization rules are the strongest local signal.
        for rule in self.client.state.list_categorization_rules.get(list_id, {}).values():
            if str(rule.itemName).casefold() == name.casefold():
                category = categories.get(str(rule.categoryId))
                if category is not None:
                    return category

        # AnyList also keeps learned list-specific/global category memory.
        learned_match = ""
        learned_service = self.client.categorized_items
        if learned_service is not None:
            learned = learned_service.lookup(name, list_id)
            if learned is not None:
                learned_match = str(learned.categoryMatchId or learned.category or "")

        # A saved Favorite/Recent item is a useful fallback when learned memory is absent.
        if not learned_match and saved is not None:
            learned_match = str(
                getattr(saved, "categoryMatchId", "") or getattr(saved, "category", "") or ""
            )

        if learned_match:
            for category in categories.values():
                if self._category_match_id(category) == learned_match:
                    return category

        # Finally use AnyList's grocery tag data for a new, never-seen item.
        try:
            tag = await self.client.categorizer.classify(name)
            if tag:
                data = await self.client.tag_data.get()
                root = str(data.tags.get(tag, {}).get("rootCategory") or tag)
                for category in categories.values():
                    if str(category.systemCategory or "") in {tag, root}:
                        return category
        except Exception:  # noqa: BLE001 - categorization is best-effort UI enrichment
            tag = None

        return next(
            (
                category
                for category in categories.values()
                if str(category.systemCategory) == "other"
            ),
            None,
        )

    async def _new_list(self, result: FormResult) -> None:
        service = self.client.lists
        if service is None:
            return
        name = str(result["name"]).strip()
        if not name:
            return self.error("List name is required")
        folder_id = cast(str | None, result.get("folder")) or self.client.state.root_folder_id
        list_type = int(cast(int, result.get("type", 0)))
        created = await service.create(name, folder_id=folder_id, list_type=list_type)
        self.current_list_id = str(created.identifier)
        await self.refresh_view()

    async def _edit_list(self, result: FormResult) -> None:
        list_id = self.current_list_id
        service = self.client.lists
        folders = self.client.folders
        if service is None or folders is None or not list_id:
            return
        current = service.get(list_id)
        if current is None:
            return
        name = str(result["name"]).strip()
        if not name:
            return self.error("List name is required")
        old_folder = _list_folder_id(self.client, list_id)
        new_folder = cast(str | None, result.get("folder"))
        changed = False
        if name != current.name:
            await service.rename(list_id, name, flush=False)
            changed = True
        if new_folder and old_folder and new_folder != old_folder:
            item = PB.PBListFolderItem(identifier=list_id, itemType=0)
            await folders.move([item], old_folder, new_folder, flush=False)
            changed = True
        if changed:
            await self.client.flush()
        await self.refresh_view()

    async def _new_item(self, result: FormResult) -> None:
        list_id = self.current_list_id
        service = self.client.lists
        if service is None or not list_id:
            return
        name = str(result["name"]).strip()
        if not name:
            return self.error("Item name is required")
        saved = (
            self._saved_item_for_name(list_id, name) if bool(result.get("reuse_saved")) else None
        )
        draft = PB.ListItem(
            identifier=uuid4().hex,
            listId=list_id,
            userId=self.client.user_id or "",
            name=name,
            checked=False,
        )
        if saved is not None:
            apply_properties_from_item(draft, saved)

        details = str(result.get("details") or "")
        if details:
            draft.details = details

        quantity_text = str(result.get("quantity") or "").strip()
        parsed = parse_quantity_and_package_size(quantity_text) if quantity_text else None
        if quantity_text and parsed is None:
            return self.error("Could not understand that quantity/package size")
        if parsed is not None and parsed.HasField("quantityPb"):
            draft.quantityPb.CopyFrom(parsed.quantityPb)
        if parsed is not None and parsed.HasField("packageSizePb"):
            draft.packageSizePb.CopyFrom(parsed.packageSizePb)

        requested_stores = cast(list[str], result.get("stores") or [])
        if requested_stores:
            del draft.storeIds[:]
            draft.storeIds.extend(requested_stores)

        category_id = cast(str | None, result.get("category"))
        if category_id == AUTO_CATEGORY:
            category = await self._automatic_category(list_id, name, saved)
        else:
            category = self.client.state.list_categories.get(list_id, {}).get(category_id or "")

        added = await service.add_items(list_id, [draft], flush=False)
        if not added:
            return self.error("AnyList did not add the item")
        item = added[0]
        if category is not None:
            await service.assign_category(
                list_id,
                str(item.identifier),
                PB.PBListItemCategoryAssignment(
                    categoryGroupId=str(category.categoryGroupId),
                    categoryId=str(category.identifier),
                ),
                flush=False,
            )
            await service.set_category_match_id(
                list_id,
                str(item.identifier),
                self._category_match_id(category),
                flush=False,
            )
        await service.flush()
        await self.refresh_view()

    async def _edit_item(self, item_id: str, result: FormResult) -> None:
        list_id = self.current_list_id
        service = self.client.lists
        if service is None or not list_id:
            return
        item = service.item(list_id, item_id)
        if item is None:
            return
        name = str(result["name"]).strip()
        if not name:
            return self.error("Item name is required")
        changed = False
        if name != item.name:
            await service.rename_item(list_id, item_id, name, flush=False)
            changed = True
        details = str(result.get("details") or "")
        if details != item.details:
            await service.set_details(list_id, item_id, details, flush=False)
            changed = True
        checked = bool(result.get("checked"))
        if checked != bool(item.checked):
            await service.set_checked(list_id, item_id, checked, flush=False)
            changed = True
        requested_stores = set(cast(list[str], result.get("stores") or []))
        current_stores = {str(value) for value in item.storeIds}
        add_stores = sorted(requested_stores - current_stores)
        remove_stores = sorted(current_stores - requested_stores)
        if add_stores:
            await service.add_store_ids_to_items(list_id, [item_id], add_stores, flush=False)
            changed = True
        if remove_stores:
            await service.remove_store_ids_from_items(
                list_id, [item_id], remove_stores, flush=False
            )
            changed = True
        category_id = cast(str | None, result.get("category"))
        if category_id:
            category = self.client.state.list_categories.get(list_id, {}).get(category_id)
            if category is not None:
                current_category_ids = {str(value.categoryId) for value in item.categoryAssignments}
                if category_id not in current_category_ids:
                    await service.assign_category(
                        list_id,
                        item_id,
                        PB.PBListItemCategoryAssignment(
                            categoryGroupId=str(category.categoryGroupId),
                            categoryId=category_id,
                        ),
                        flush=False,
                    )
                    match_id = str(category.systemCategory or "") or canonical_category_match_id(
                        str(category.name)
                    )
                    await service.set_category_match_id(list_id, item_id, match_id, flush=False)
                    changed = True
        quantity_text = str(result.get("quantity") or "").strip()
        if quantity_text:
            parsed = parse_quantity_and_package_size(quantity_text)
            if parsed is None:
                return self.error("Could not understand that quantity/package size")
            if parsed.HasField("quantityPb"):
                await service.set_quantity(list_id, item_id, parsed.quantityPb, flush=False)
                changed = True
            if parsed.HasField("packageSizePb"):
                await service.set_package_size(list_id, item_id, parsed.packageSizePb, flush=False)
                changed = True
        if changed:
            await service.flush()
        await self.refresh_view()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        service = self.client.lists
        if service is None:
            return
        list_id = self.current_list_id
        item_id = _selected_id(self.query_one("#items-table", DataTable), self.item_ids)

        if button_id == "lists-new":
            folder_options = _folder_options(self.client)
            self.form(
                "New list",
                [
                    FormField("name", "List name", placeholder="Groceries"),
                    FormField(
                        "type",
                        "List type",
                        kind="select",
                        value=0,
                        options=self._list_type_fields(),
                        allow_blank=False,
                    ),
                    FormField(
                        "folder",
                        "Folder",
                        kind="select",
                        value=self.client.state.root_folder_id,
                        options=folder_options,
                        allow_blank=False,
                    ),
                ],
                self._new_list,
                submit_label="Create",
            )
        elif button_id == "lists-edit":
            if not list_id:
                return self.error("Select a list")
            current = service.get(list_id)
            if current is None:
                return
            self.form(
                "Edit list",
                [
                    FormField("name", "List name", value=current.name),
                    FormField(
                        "folder",
                        "Folder",
                        kind="select",
                        value=_list_folder_id(self.client, list_id),
                        options=_folder_options(self.client),
                        allow_blank=False,
                    ),
                ],
                self._edit_list,
            )
        elif button_id == "items-new":
            if not list_id:
                return self.error("Select a list")
            self.form("Add item", self._item_form_fields(), self._new_item, submit_label="Add")
        elif button_id == "items-edit":
            if not list_id or not item_id:
                return self.error("Select an item")
            item = service.item(list_id, item_id)
            if item is None:
                return

            async def save_item(result: FormResult) -> None:
                await self._edit_item(item_id, result)

            self.form("Edit item", self._item_form_fields(item=item), save_item)
        elif button_id == "items-toggle":
            if not list_id or not item_id:
                return self.error("Select an item")
            item = service.item(list_id, item_id)
            if item is not None:
                try:
                    await service.set_checked(list_id, item_id, not bool(item.checked))
                    await self.refresh_view()
                except Exception as exc:  # noqa: BLE001 - UI boundary reports service errors
                    self.error(exc)
        elif button_id == "items-remove":
            if not list_id or not item_id:
                return self.error("Select an item")
            item = service.item(list_id, item_id)
            if item is None:
                return

            async def remove_item() -> None:
                await service.remove_item(list_id, item_id)
                await self.refresh_view()

            self.confirm(
                "Remove item",
                f"Remove “{item.name}” from this list?",
                remove_item,
                confirm_label="Remove",
            )
        elif button_id == "lists-settings":
            if not list_id:
                return self.error("Select a list")
            self.app.push_screen(ListSettingsScreen(list_id))


class StoresCategoriesPanel(SDKPanel):
    DEFAULT_CSS = """
    StoresCategoriesPanel .toolbar { height: auto; }
    StoresCategoriesPanel #metadata-tables { height: 1fr; }
    StoresCategoriesPanel #stores-table { width: 44%; }
    StoresCategoriesPanel #categories-table { width: 56%; }
    """

    def __init__(self, list_id: str) -> None:
        super().__init__()
        self.list_id = list_id
        self.store_ids: list[str] = []
        self.category_ids: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("New store", id="store-new", variant="success")
            yield Button("Edit store", id="store-edit")
            yield Button("Delete store", id="store-delete", variant="error")
            yield Button("New category", id="category-new", variant="success")
            yield Button("Edit category", id="category-edit")
            yield Button("Delete category", id="category-delete", variant="error")
        with Horizontal(id="metadata-tables"):
            with Vertical():
                yield Label("Stores")
                yield DataTable(id="stores-table", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Label("Categories")
                yield DataTable(id="categories-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#stores-table", DataTable).add_columns("Store", "Order")
        self.query_one("#categories-table", DataTable).add_columns("Category", "Icon", "Order")

    async def refresh_view(self) -> None:
        stores_table = self.query_one("#stores-table", DataTable)
        categories_table = self.query_one("#categories-table", DataTable)
        old_store = _selected_id(stores_table, self.store_ids)
        old_category = _selected_id(categories_table, self.category_ids)
        stores = sorted(
            self.client.state.list_stores.get(self.list_id, {}).values(),
            key=lambda value: int(value.sortIndex),
        )
        categories = sorted(
            self.client.state.list_categories.get(self.list_id, {}).values(),
            key=lambda value: (int(value.sortIndex), str(value.name).casefold()),
        )
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

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        service = self.client.lists
        if service is None:
            return
        button_id = event.button.id or ""
        stores = self.client.state.list_stores.get(self.list_id, {})
        categories = self.client.state.list_categories.get(self.list_id, {})
        groups = self.client.state.list_category_groups.get(self.list_id, {})
        store_id = _selected_id(self.query_one("#stores-table", DataTable), self.store_ids)
        category_id = _selected_id(
            self.query_one("#categories-table", DataTable), self.category_ids
        )

        if button_id == "store-new":

            async def create(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Store name is required")
                await service.save_store(
                    self.list_id,
                    PB.PBStore(identifier=uuid4().hex, listId=self.list_id, name=name),
                    is_new=True,
                )
                await self.refresh_view()

            self.form("New store", [FormField("name", "Store name")], create, submit_label="Create")
        elif button_id == "store-edit":
            if not store_id:
                return self.error("Select a store")
            current = stores[store_id]

            async def edit(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Store name is required")
                updated = PB.PBStore()
                updated.CopyFrom(current)
                updated.name = name
                await service.save_store(self.list_id, updated)
                await self.refresh_view()

            self.form("Edit store", [FormField("name", "Store name", value=current.name)], edit)
        elif button_id == "store-delete":
            if not store_id:
                return self.error("Select a store")
            current = stores[store_id]

            async def delete() -> None:
                await service.delete_store(self.list_id, current)
                await self.refresh_view()

            self.confirm("Delete store", f"Delete “{current.name}”?", delete)
        elif button_id == "category-new":
            if not groups:
                return self.error("This list has no category group")
            group_options = tuple(
                (
                    str(group.name or "Default categories"),
                    str(group.identifier),
                )
                for group in sorted(groups.values(), key=lambda value: str(value.name).casefold())
            )

            async def create_category(result: FormResult) -> None:
                name = str(result["name"]).strip()
                group_id = cast(str | None, result.get("group"))
                if not name or not group_id:
                    return self.error("Category name and group are required")
                members = [
                    value for value in categories.values() if str(value.categoryGroupId) == group_id
                ]
                await service.save_list_category(
                    PB.PBListCategory(
                        identifier=uuid4().hex,
                        listId=self.list_id,
                        categoryGroupId=group_id,
                        name=name,
                        icon=str(result.get("icon") or "other"),
                        sortIndex=max((int(value.sortIndex) for value in members), default=-1) + 1,
                    )
                )
                await self.refresh_view()

            self.form(
                "New category",
                [
                    FormField("name", "Category name"),
                    FormField("icon", "Icon name", value="other"),
                    FormField(
                        "group",
                        "Category group",
                        kind="select",
                        value=group_options[0][1],
                        options=group_options,
                        allow_blank=False,
                    ),
                ],
                create_category,
                submit_label="Create",
            )
        elif button_id == "category-edit":
            if not category_id:
                return self.error("Select a category")
            current_category = categories[category_id]

            async def edit_category(result: FormResult) -> None:
                name = str(result["name"]).strip()
                icon = str(result["icon"]).strip()
                if not name:
                    return self.error("Category name is required")
                changed = False
                if name != current_category.name:
                    await service.rename_list_category(current_category, name, flush=False)
                    changed = True
                if icon != current_category.icon:
                    # Re-read the optimistic category in case rename changed the indexed clone.
                    latest = self.client.state.list_categories[self.list_id].get(
                        category_id, current_category
                    )
                    await service.set_list_category_icon(latest, icon, flush=False)
                    changed = True
                if changed:
                    await service.flush()
                await self.refresh_view()

            self.form(
                "Edit category",
                [
                    FormField("name", "Category name", value=current_category.name),
                    FormField("icon", "Icon name", value=current_category.icon),
                ],
                edit_category,
            )
        elif button_id == "category-delete":
            if not category_id:
                return self.error("Select a category")
            category = categories[category_id]
            group = groups.get(str(category.categoryGroupId))
            if group is None:
                return self.error("Category group is missing")
            if str(group.defaultCategoryId) == category_id:
                return self.error("The default category cannot be deleted")

            async def delete_category() -> None:
                await service.remove_category_ids(group, [category])
                await self.refresh_view()

            self.confirm("Delete category", f"Delete “{category.name}”?", delete_category)


class FoldersPanel(SDKPanel):
    DEFAULT_CSS = """
    FoldersPanel .toolbar { height: auto; }
    FoldersPanel #folders-table { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.folder_ids: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("New folder", id="folder-new", variant="success")
            yield Button("Edit folder", id="folder-edit")
        yield Label("Folders — destructive recursive deletion is intentionally omitted")
        yield DataTable(id="folders-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#folders-table", DataTable).add_columns(
            "Folder", "Child folders", "Lists", "Color", "Icon"
        )

    async def refresh_view(self) -> None:
        table = self.query_one("#folders-table", DataTable)
        root_id = self.client.state.root_folder_id or ""
        folders = [
            value
            for value in self.client.state.list_folders.values()
            if str(value.identifier) != root_id
        ]
        folders.sort(key=lambda value: _folder_label(self.client, str(value.identifier)).casefold())
        old = _selected_id(table, self.folder_ids)
        self.folder_ids = [str(value.identifier) for value in folders]

        def row(folder: object) -> tuple[object, ...]:
            items = list(getattr(folder, "items", ()))
            settings = getattr(folder, "folderSettings", None)
            return (
                _folder_label(self.client, str(getattr(folder, "identifier", ""))),
                sum(1 for item in items if int(item.itemType) == 1),
                sum(1 for item in items if int(item.itemType) == 0),
                getattr(settings, "folderHexColor", "") if settings is not None else "",
                getattr(getattr(settings, "icon", None), "iconName", "")
                if settings is not None
                else "",
            )

        _replace_rows(table, (row(value) for value in folders), self.folder_ids, keep_id=old)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        service = self.client.folders
        if service is None:
            return
        button_id = event.button.id or ""
        folder_id = _selected_id(self.query_one("#folders-table", DataTable), self.folder_ids)
        if button_id == "folder-new":
            options = _folder_options(self.client)

            async def create(result: FormResult) -> None:
                name = str(result["name"]).strip()
                parent_id = cast(str | None, result.get("parent"))
                color = _hex_color(str(result.get("color") or "").strip())
                if not name:
                    return self.error("Folder name is required")
                if color is None:
                    return self.error("Color must be a six-digit hex value")
                await service.create(name, parent_id=parent_id, hex_color=color or None)
                await self.refresh_view()

            self.form(
                "New folder",
                [
                    FormField("name", "Folder name"),
                    FormField(
                        "parent",
                        "Parent folder",
                        kind="select",
                        value=self.client.state.root_folder_id,
                        options=options,
                        allow_blank=False,
                    ),
                    FormField("color", "Color", placeholder="#4A90E2 (optional)"),
                ],
                create,
                submit_label="Create",
            )
        elif button_id == "folder-edit":
            if not folder_id:
                return self.error("Select a folder")
            current = service.get(folder_id)
            if current is None:
                return
            settings = current.folderSettings

            async def edit(result: FormResult) -> None:
                name = str(result["name"]).strip()
                color = _hex_color(str(result.get("color") or "").strip())
                icon = str(result.get("icon") or "").strip()
                if not name:
                    return self.error("Folder name is required")
                if color is None:
                    return self.error("Color must be a six-digit hex value")
                changed = False
                if name != current.name:
                    await service.rename(folder_id, name, flush=False)
                    changed = True
                if color and color != settings.folderHexColor:
                    await service.set_hex_color(folder_id, color, flush=False)
                    changed = True
                if icon and icon != settings.icon.iconName:
                    await service.set_icon(folder_id, icon, flush=False)
                    changed = True
                if changed:
                    await service.flush()
                await self.refresh_view()

            self.form(
                "Edit folder",
                [
                    FormField("name", "Folder name", value=current.name),
                    FormField("color", "Color", value=settings.folderHexColor),
                    FormField("icon", "Icon name", value=settings.icon.iconName),
                ],
                edit,
            )


class SavedItemsPanel(SDKPanel):
    DEFAULT_CSS = """
    SavedItemsPanel .toolbar { height: auto; }
    SavedItemsPanel #saved-tables { height: 1fr; }
    SavedItemsPanel #saved-lists { width: 42%; }
    SavedItemsPanel #saved-items { width: 58%; }
    """

    def __init__(self, shopping_list_id: str) -> None:
        super().__init__()
        self.shopping_list_id = shopping_list_id
        self.list_ids: list[str] = []
        self.item_ids: list[str] = []
        self.current_list_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("New custom list", id="saved-new-list", variant="success")
            yield Button("Edit custom list", id="saved-edit-list")
            yield Button("Delete custom list", id="saved-delete-list", variant="error")
            yield Button("Add item", id="saved-add-item", variant="success")
            yield Button("Edit item", id="saved-edit-item")
            yield Button("Remove item", id="saved-remove-item", variant="error")
        with Horizontal(id="saved-tables"):
            with Vertical():
                yield Label("Saved item lists")
                yield DataTable(id="saved-lists", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Label("Items")
                yield DataTable(id="saved-items", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#saved-lists", DataTable).add_columns("Name", "Type", "Items")
        self.query_one("#saved-items", DataTable).add_columns("Item", "Details")

    def _kind(self, identifier: str) -> str:
        state = self.client.state
        if identifier in state.favorite_item_lists:
            return "Favorites"
        if identifier in state.recent_item_lists:
            return "Recents"
        return "Custom"

    def _lists(self) -> list[StarterList]:
        state = self.client.state
        values = list(state.starter_lists.values())
        values += [
            value
            for value in state.favorite_item_lists.values()
            if str(value.listId or "") == self.shopping_list_id
        ]
        values += [
            value
            for value in state.recent_item_lists.values()
            if str(value.listId or "") == self.shopping_list_id
        ]
        return sorted(
            values,
            key=lambda value: (self._kind(str(value.identifier)), str(value.name).casefold()),
        )

    async def refresh_view(self) -> None:
        if self.client.starter_lists is None:
            return
        table = self.query_one("#saved-lists", DataTable)
        old = self.current_list_id or _selected_id(table, self.list_ids)
        values = self._lists()
        self.list_ids = [str(value.identifier) for value in values]
        _replace_rows(
            table,
            ((value.name, self._kind(str(value.identifier)), len(value.items)) for value in values),
            self.list_ids,
            keep_id=old,
        )
        self.current_list_id = _selected_id(table, self.list_ids)
        self._refresh_items()

    def _refresh_items(self) -> None:
        table = self.query_one("#saved-items", DataTable)
        service = self.client.starter_lists
        if service is None or not self.current_list_id:
            table.clear()
            self.item_ids = []
            return
        current = service.get(self.current_list_id)
        if current is None:
            table.clear()
            self.item_ids = []
            return
        old = _selected_id(table, self.item_ids)
        items = list(current.items)
        self.item_ids = [str(value.identifier) for value in items]
        _replace_rows(
            table,
            ((value.name, value.details) for value in items),
            self.item_ids,
            keep_id=old,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "saved-lists":
            self.current_list_id = _selected_id(event.data_table, self.list_ids)
            self._refresh_items()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        service = self.client.starter_lists
        if service is None:
            return
        button_id = event.button.id or ""
        list_id = self.current_list_id
        item_id = _selected_id(self.query_one("#saved-items", DataTable), self.item_ids)
        kind = self._kind(list_id) if list_id else ""

        if button_id == "saved-new-list":

            async def create(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("List name is required")
                await service.create(name)
                await self.refresh_view()

            self.form(
                "New saved-items list",
                [FormField("name", "List name")],
                create,
                submit_label="Create",
            )
        elif button_id == "saved-edit-list":
            if not list_id or kind != "Custom":
                return self.error("Select a custom saved-items list")
            current = service.get(list_id)
            if current is None:
                return

            async def edit(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("List name is required")
                await service.rename(list_id, name)
                await self.refresh_view()

            self.form(
                "Edit saved-items list", [FormField("name", "List name", value=current.name)], edit
            )
        elif button_id == "saved-delete-list":
            if not list_id or kind != "Custom":
                return self.error("Only custom saved-items lists can be deleted here")
            current = service.get(list_id)
            if current is None:
                return

            async def delete() -> None:
                await service.remove(list_id)
                self.current_list_id = None
                await self.refresh_view()

            self.confirm("Delete saved-items list", f"Delete “{current.name}”?", delete)
        elif button_id == "saved-add-item":
            if not list_id:
                return self.error("Select a saved-items list")
            if kind == "Recents":
                return self.error("Recents are managed automatically from shopping-list actions")

            async def add_item(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Item name is required")
                await service.add_item(
                    list_id,
                    PB.ListItem(name=name, details=str(result.get("details") or "")),
                )
                await self.refresh_view()

            self.form(
                "Add saved item",
                [FormField("name", "Name"), FormField("details", "Details", kind="textarea")],
                add_item,
                submit_label="Add",
            )
        elif button_id == "saved-edit-item":
            if not list_id or not item_id:
                return self.error("Select an item")
            if kind == "Recents":
                return self.error("Recents are managed automatically from shopping-list actions")
            current_list = service.get(list_id)
            current_item = (
                next((value for value in current_list.items if value.identifier == item_id), None)
                if current_list
                else None
            )
            if current_item is None:
                return
            item_snapshot = current_item

            async def edit_item(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Item name is required")
                changed = False
                if name != item_snapshot.name:
                    await service.set_item_name(list_id, item_id, name, flush=False)
                    changed = True
                details = str(result.get("details") or "")
                if details != item_snapshot.details:
                    await service.set_item_details(list_id, item_id, details, flush=False)
                    changed = True
                if changed:
                    await service.flush()
                await self.refresh_view()

            self.form(
                "Edit saved item",
                [
                    FormField("name", "Name", value=item_snapshot.name),
                    FormField("details", "Details", kind="textarea", value=item_snapshot.details),
                ],
                edit_item,
            )
        elif button_id == "saved-remove-item":
            if not list_id or not item_id:
                return self.error("Select an item")
            current_list = service.get(list_id)
            current_item = (
                next((value for value in current_list.items if value.identifier == item_id), None)
                if current_list
                else None
            )
            if current_item is None:
                return

            async def remove_item() -> None:
                await service.remove_item(list_id, item_id)
                await self.refresh_view()

            self.confirm(
                "Remove saved item",
                f"Remove “{current_item.name}”?",
                remove_item,
                confirm_label="Remove",
            )


class ListSettingsScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ListSettingsScreen { align: center middle; background: $background 45%; }
    ListSettingsScreen #tools-dialog {
        width: 94%; height: 90%; padding: 1;
        border: round $accent; background: $surface;
    }
    ListSettingsScreen #tools-header { height: auto; }
    ListSettingsScreen #tools-close { dock: right; }
    """

    def __init__(self, list_id: str) -> None:
        super().__init__()
        self.list_id = list_id

    def compose(self) -> ComposeResult:
        lists = self.app.client.lists if isinstance(self.app, AnyListTUI) else None
        current = lists.get(self.list_id) if lists is not None else None
        title = current.name if current is not None else "Selected list"
        with Vertical(id="tools-dialog"):
            with Horizontal(id="tools-header"):
                with Vertical():
                    yield Label("[b]List Settings[/b]")
                    yield Static(title)
                yield Button("Close", id="tools-close")
            with TabbedContent(initial="stores-categories"):
                with TabPane("Stores & Categories", id="stores-categories"):
                    yield StoresCategoriesPanel(self.list_id)
                with TabPane("Favorites & Recent", id="saved-items"):
                    yield SavedItemsPanel(self.list_id)
                with TabPane("Folders", id="folders"):
                    yield FoldersPanel()

    async def on_mount(self) -> None:
        for panel in self.query(SDKPanel):
            await panel.refresh_view()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tools-close":
            self.dismiss(None)


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
        with Horizontal(classes="toolbar"):
            yield Button("New recipe", id="recipe-new", variant="success")
            yield Button("Edit recipe", id="recipe-edit")
            yield Button("Delete recipe", id="recipe-delete", variant="error")
            yield Button("New collection", id="collection-new", variant="success")
            yield Button("Edit collection", id="collection-edit")
            yield Button("Delete collection", id="collection-delete", variant="error")
            yield Button("Add to collection", id="collection-add")
            yield Button("Remove from collection", id="collection-remove")
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
        collections = sorted(
            service.collections(), key=lambda value: _message_name(value).casefold()
        )
        self.recipe_ids = [str(value.identifier) for value in recipes]
        self.collection_ids = [str(value.identifier) for value in collections]
        _replace_rows(
            recipe_table,
            (
                (value.name, value.rating, value.servings, len(value.ingredients), value.sourceName)
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

    def _recipe_fields(self, recipe: object | None = None) -> list[FormField]:
        return [
            FormField("name", "Recipe name", value=getattr(recipe, "name", "")),
            FormField("note", "Notes", kind="textarea", value=getattr(recipe, "note", "")),
            FormField(
                "servings",
                "Servings",
                value=getattr(recipe, "servings", ""),
                placeholder="e.g. 4 servings",
            ),
            FormField("source_name", "Source name", value=getattr(recipe, "sourceName", "")),
            FormField("source_url", "Source URL", value=getattr(recipe, "sourceUrl", "")),
        ]

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        service = self.client.recipes
        if service is None:
            return
        button_id = event.button.id or ""
        recipe_id = _selected_id(self.query_one("#recipe-list", DataTable), self.recipe_ids)
        collection_id = _selected_id(
            self.query_one("#collection-list", DataTable), self.collection_ids
        )

        if button_id == "recipe-new":

            async def create(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Recipe name is required")
                recipe = PB.PBRecipe(name=name)
                for key, field in (
                    ("note", "note"),
                    ("servings", "servings"),
                    ("source_name", "sourceName"),
                    ("source_url", "sourceUrl"),
                ):
                    value = str(result.get(key) or "").strip()
                    if value:
                        setattr(recipe, field, value)
                await service.save(recipe)
                await self.refresh_view()

            self.form("New recipe", self._recipe_fields(), create, submit_label="Create")
        elif button_id == "recipe-edit":
            if not recipe_id:
                return self.error("Select a recipe")
            current_recipe = service.get(recipe_id)
            if current_recipe is None:
                return
            recipe_snapshot = current_recipe

            async def edit(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Recipe name is required")
                updated = PB.PBRecipe()
                updated.CopyFrom(recipe_snapshot)
                updated.name = name
                for key, field in (
                    ("note", "note"),
                    ("servings", "servings"),
                    ("source_name", "sourceName"),
                    ("source_url", "sourceUrl"),
                ):
                    value = str(result.get(key) or "").strip()
                    if value:
                        setattr(updated, field, value)
                    else:
                        updated.ClearField(field)
                await service.save(updated)
                await self.refresh_view()

            self.form("Edit recipe", self._recipe_fields(recipe_snapshot), edit)
        elif button_id == "recipe-delete":
            if not recipe_id:
                return self.error("Select a recipe")
            current_recipe = service.get(recipe_id)
            if current_recipe is None:
                return

            async def delete() -> None:
                await service.remove(recipe_id)
                await self.refresh_view()

            self.confirm("Delete recipe", f"Delete “{current_recipe.name}”?", delete)
        elif button_id == "collection-new":

            async def create_collection(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Collection name is required")
                await service.create_collection(name)
                await self.refresh_view()

            self.form(
                "New collection",
                [FormField("name", "Collection name")],
                create_collection,
                submit_label="Create",
            )
        elif button_id == "collection-edit":
            if not collection_id:
                return self.error("Select a collection")
            current_collection = self.client.state.recipe_collections.get(collection_id)
            if current_collection is None:
                return

            async def edit_collection(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Collection name is required")
                await service.rename_collection(collection_id, name)
                await self.refresh_view()

            self.form(
                "Edit collection",
                [FormField("name", "Collection name", value=current_collection.name)],
                edit_collection,
            )
        elif button_id == "collection-delete":
            if not collection_id:
                return self.error("Select a collection")
            current_collection = self.client.state.recipe_collections.get(collection_id)
            if current_collection is None:
                return

            async def delete_collection() -> None:
                await service.remove_collection(collection_id)
                await self.refresh_view()

            self.confirm(
                "Delete collection",
                f"Delete “{current_collection.name}”? Recipes are kept.",
                delete_collection,
            )
        elif button_id == "collection-add":
            if not collection_id or not recipe_id:
                return self.error("Select both a recipe and a collection")
            try:
                await service.add_to_collection(collection_id, [recipe_id])
                await self.refresh_view()
                self.tui.notify("Added recipe to collection")
            except Exception as exc:  # noqa: BLE001 - UI boundary reports service errors
                self.error(exc)
        elif button_id == "collection-remove":
            if not collection_id or not recipe_id:
                return self.error("Select both a recipe and a collection")
            try:
                await service.remove_from_collection(collection_id, [recipe_id])
                await self.refresh_view()
                self.tui.notify("Removed recipe from collection")
            except Exception as exc:  # noqa: BLE001 - UI boundary reports service errors
                self.error(exc)


class LabelsPanel(SDKPanel):
    DEFAULT_CSS = """
    LabelsPanel .toolbar { height: auto; }
    LabelsPanel #labels-table { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.label_ids: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("New label", id="label-new", variant="success")
            yield Button("Edit label", id="label-edit")
            yield Button("Delete label", id="label-delete", variant="error")
        yield DataTable(id="labels-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#labels-table", DataTable).add_columns("Label", "Color", "Order")

    async def refresh_view(self) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        table = self.query_one("#labels-table", DataTable)
        old = _selected_id(table, self.label_ids)
        labels = sorted(service.labels(), key=lambda value: int(value.sortIndex))
        self.label_ids = [str(value.identifier) for value in labels]
        _replace_rows(
            table,
            ((value.name, value.hexColor, value.sortIndex) for value in labels),
            self.label_ids,
            keep_id=old,
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        button_id = event.button.id or ""
        label_id = _selected_id(self.query_one("#labels-table", DataTable), self.label_ids)
        if button_id == "label-new":

            async def create(result: FormResult) -> None:
                name = str(result["name"]).strip()
                color = _hex_color(str(result.get("color") or "#808080"))
                if not name:
                    return self.error("Label name is required")
                if color is None:
                    return self.error("Color must be a six-digit hex value")
                await service.save_label(PB.PBCalendarLabel(name=name, hexColor=color or "#808080"))
                await self.refresh_view()

            self.form(
                "New meal-plan label",
                [FormField("name", "Label name"), FormField("color", "Color", value="#808080")],
                create,
                submit_label="Create",
            )
        elif button_id == "label-edit":
            if not label_id:
                return self.error("Select a label")
            current_label = self.client.state.meal_plan_labels.get(label_id)
            if current_label is None:
                return
            label_snapshot = current_label

            async def edit(result: FormResult) -> None:
                name = str(result["name"]).strip()
                color = _hex_color(str(result.get("color") or ""))
                if not name:
                    return self.error("Label name is required")
                if color is None or not color:
                    return self.error("Color must be a six-digit hex value")
                updated = PB.PBCalendarLabel()
                updated.CopyFrom(label_snapshot)
                updated.name = name
                updated.hexColor = color
                await service.save_label(updated, is_new=False)
                await self.refresh_view()

            self.form(
                "Edit meal-plan label",
                [
                    FormField("name", "Label name", value=label_snapshot.name),
                    FormField("color", "Color", value=label_snapshot.hexColor),
                ],
                edit,
            )
        elif button_id == "label-delete":
            if not label_id:
                return self.error("Select a label")
            current_label = self.client.state.meal_plan_labels.get(label_id)
            if current_label is None:
                return

            async def delete() -> None:
                await service.delete_label(label_id)
                await self.refresh_view()

            self.confirm(
                "Delete label",
                f"Delete “{current_label.name}”? It will also be cleared from events that use it.",
                delete,
            )


class LabelsScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    LabelsScreen { align: center middle; background: $background 45%; }
    LabelsScreen #labels-dialog {
        width: 78; max-width: 94%; height: 75%; padding: 1;
        border: round $accent; background: $surface;
    }
    LabelsScreen #labels-header { height: auto; }
    LabelsScreen #labels-close { dock: right; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="labels-dialog"):
            with Horizontal(id="labels-header"):
                yield Label("[b]Meal-plan labels[/b]")
                yield Button("Close", id="labels-close")
            yield LabelsPanel()

    async def on_mount(self) -> None:
        await self.query_one(LabelsPanel).refresh_view()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "labels-close":
            self.dismiss(None)


class MealPlanPanel(SDKPanel):
    DEFAULT_CSS = """
    MealPlanPanel .toolbar { height: auto; }
    MealPlanPanel #meal-events { height: 58%; }
    MealPlanPanel #meal-items { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.event_ids: list[str] = []
        self.item_ids: list[str] = []
        self.current_event_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("New entry", id="meal-new", variant="success")
            yield Button("Edit entry", id="meal-edit")
            yield Button("Delete entry", id="meal-delete", variant="error")
            yield Button("Add item", id="meal-item-new", variant="success")
            yield Button("Edit item", id="meal-item-edit")
            yield Button("Remove item", id="meal-item-delete", variant="error")
            yield Button("Labels…", id="meal-labels")
        yield Label("Meal plan")
        yield DataTable(id="meal-events", cursor_type="row", zebra_stripes=True)
        yield Label("Items in selected entry")
        yield DataTable(id="meal-items", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#meal-events", DataTable).add_columns(
            "Entry", "When", "Label", "Items", "Type"
        )
        self.query_one("#meal-items", DataTable).add_columns("Item", "Details")

    @staticmethod
    def _event_type_name(event: PBCalendarEvent) -> str:
        value = int(event.eventType)
        if value == int(PB.PBCalendarEventType.MealPlanQueueEvent):
            return "Queue"
        if value == int(PB.PBCalendarEventType.MealPlanFavoriteEvent):
            return "Favorite"
        if value == int(PB.PBCalendarEventType.MealPlanTemplateEvent):
            return "Template"
        return "Calendar"

    async def refresh_view(self) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        table = self.query_one("#meal-events", DataTable)
        old = self.current_event_id or _selected_id(table, self.event_ids)
        events = sorted(
            service.events(),
            key=lambda value: (str(value.date or "9999-99-99"), str(value.title).casefold()),
        )
        labels = {str(value.identifier): str(value.name) for value in service.labels()}
        self.event_ids = [str(value.identifier) for value in events]
        _replace_rows(
            table,
            (
                (
                    value.title,
                    value.date or "Queue",
                    labels.get(str(value.labelId), ""),
                    len(value.eventListItems),
                    self._event_type_name(value),
                )
                for value in events
            ),
            self.event_ids,
            keep_id=old,
        )
        self.current_event_id = _selected_id(table, self.event_ids)
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
        self.item_ids = [str(value.identifier) for value in items]
        _replace_rows(
            table,
            ((value.name, value.details) for value in items),
            self.item_ids,
            keep_id=old,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "meal-events":
            self.current_event_id = _selected_id(event.data_table, self.event_ids)
            self._refresh_items()

    def _event_fields(self, event: PBCalendarEvent | None = None) -> list[FormField]:
        labels = sorted(
            self.client.state.meal_plan_labels.values(),
            key=lambda value: int(value.sortIndex),
        )
        return [
            FormField("title", "Title", value=event.title if event is not None else ""),
            FormField(
                "details",
                "Details",
                kind="textarea",
                value=event.details if event is not None else "",
            ),
            FormField(
                "date",
                "Date",
                value=event.date if event is not None else "",
                placeholder="YYYY-MM-DD — leave blank to keep it in the queue",
            ),
            FormField(
                "label",
                "Label",
                kind="select",
                value=str(event.labelId) if event is not None and event.labelId else None,
                options=tuple((str(value.name), str(value.identifier)) for value in labels),
            ),
        ]

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        button_id = event.button.id or ""
        event_id = self.current_event_id
        item_id = _selected_id(self.query_one("#meal-items", DataTable), self.item_ids)

        if button_id == "meal-new":
            fields = self._event_fields()
            fields.append(
                FormField(
                    "items",
                    "Items",
                    kind="textarea",
                    placeholder="Optional — one item per line",
                )
            )

            async def create(result: FormResult) -> None:
                title = str(result["title"]).strip()
                if not title:
                    return self.error("Title is required")
                date = str(result.get("date") or "").strip()
                new_event = PB.PBCalendarEvent(
                    eventType=(
                        PB.PBCalendarEventType.MealPlanCalendarEvent
                        if date
                        else PB.PBCalendarEventType.MealPlanQueueEvent
                    ),
                    title=title,
                )
                details = str(result.get("details") or "")
                if details:
                    new_event.details = details
                if date:
                    new_event.date = date
                label_id = cast(str | None, result.get("label"))
                if label_id:
                    new_event.labelId = label_id
                for line in str(result.get("items") or "").splitlines():
                    name = line.strip()
                    if name:
                        new_event.eventListItems.add(identifier=uuid4().hex, name=name)
                created = await service.save_event(new_event)
                self.current_event_id = str(created.identifier)
                await self.refresh_view()

            self.form("New meal-plan entry", fields, create, submit_label="Create")
        elif button_id == "meal-edit":
            if not event_id:
                return self.error("Select a meal-plan entry")
            current_event = self._event()
            if current_event is None:
                return
            event_snapshot = current_event

            async def edit(result: FormResult) -> None:
                title = str(result["title"]).strip()
                if not title:
                    return self.error("Title is required")
                changed = False
                if title != event_snapshot.title:
                    await service.set_event_title(event_id, title, flush=False)
                    changed = True
                details = str(result.get("details") or "")
                if details != event_snapshot.details:
                    await service.set_event_details(event_id, details, flush=False)
                    changed = True
                date = str(result.get("date") or "").strip()
                current_date = str(event_snapshot.date or "")
                if date != current_date:
                    await service.set_event_date([event_id], date or None, flush=False)
                    changed = True
                label_id = cast(str | None, result.get("label")) or ""
                if label_id != str(event_snapshot.labelId or ""):
                    await service.set_event_label(event_id, label_id, flush=False)
                    changed = True
                if changed:
                    await service.flush()
                await self.refresh_view()

            self.form("Edit meal-plan entry", self._event_fields(event_snapshot), edit)
        elif button_id == "meal-delete":
            if not event_id:
                return self.error("Select a meal-plan entry")
            current_event = self._event()
            if current_event is None:
                return

            async def delete() -> None:
                await service.delete_event(event_id)
                self.current_event_id = None
                await self.refresh_view()

            self.confirm("Delete meal-plan entry", f"Delete “{current_event.title}”?", delete)
        elif button_id == "meal-item-new":
            if not event_id:
                return self.error("Select a meal-plan entry")

            async def create_item(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Item name is required")
                await service.add_event_list_item(
                    event_id,
                    PB.PBCalendarEventListItem(
                        name=name,
                        details=str(result.get("details") or ""),
                    ),
                )
                await self.refresh_view()

            self.form(
                "Add meal-plan item",
                [FormField("name", "Name"), FormField("details", "Details", kind="textarea")],
                create_item,
                submit_label="Add",
            )
        elif button_id == "meal-item-edit":
            if not event_id or not item_id:
                return self.error("Select an item")
            current_event = self._event()
            current_item = (
                next(
                    (
                        value
                        for value in current_event.eventListItems
                        if value.identifier == item_id
                    ),
                    None,
                )
                if current_event
                else None
            )
            if current_item is None:
                return
            item_snapshot = current_item

            async def edit_item(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Item name is required")
                changed = False
                if name != item_snapshot.name:
                    await service.set_event_list_item_name(event_id, item_id, name, flush=False)
                    changed = True
                details = str(result.get("details") or "")
                if details != item_snapshot.details:
                    await service.set_event_list_item_details(
                        event_id, item_id, details, flush=False
                    )
                    changed = True
                if changed:
                    await service.flush()
                await self.refresh_view()

            self.form(
                "Edit meal-plan item",
                [
                    FormField("name", "Name", value=item_snapshot.name),
                    FormField("details", "Details", kind="textarea", value=item_snapshot.details),
                ],
                edit_item,
            )
        elif button_id == "meal-item-delete":
            if not event_id or not item_id:
                return self.error("Select an item")
            current_event = self._event()
            current_item = (
                next(
                    (
                        value
                        for value in current_event.eventListItems
                        if value.identifier == item_id
                    ),
                    None,
                )
                if current_event
                else None
            )
            if current_item is None:
                return

            async def delete_item() -> None:
                await service.remove_event_list_item(event_id, item_id)
                await self.refresh_view()

            self.confirm(
                "Remove meal-plan item",
                f"Remove “{current_item.name}”?",
                delete_item,
                confirm_label="Remove",
            )
        elif button_id == "meal-labels":
            self.app.push_screen(LabelsScreen())


class InfoScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    InfoScreen { align: center middle; background: $background 55%; }
    InfoScreen #info-dialog {
        width: 68; height: auto; padding: 2;
        border: round $accent; background: $surface;
    }
    InfoScreen #info-close { margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        client = cast(AnyListTUI, self.app).client
        lines = [
            "[b]AnyList SDK example[/b]",
            "",
            f"Realtime: {'connected' if client.realtime.connected.is_set() else 'disconnected'}",
            f"Lists: {len(client.state.shopping_lists)}",
            f"Recipes: {len(client.state.recipes)}",
            f"Meal-plan entries: {len(client.state.meal_plan_events)}",
            f"Folders: {len(client.state.list_folders)}",
            "",
            "The TUI intentionally leaves out sharing/email, Alexa, uploads, recipe web import,",
            "account changes, and destructive recursive folder operations.",
        ]
        with Vertical(id="info-dialog"):
            yield Static("\n".join(lines))
            yield Button("Close", id="info-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "info-close":
            self.dismiss(None)


class AnyListTUI(App[None]):
    TITLE = "AnyList"
    SUB_TITLE = "SDK example"
    CSS = """
    Screen { layout: vertical; }
    Header { dock: top; }
    Footer { dock: bottom; }
    .toolbar { layout: horizontal; height: auto; overflow-x: auto; margin-bottom: 1; }
    .toolbar Button { margin-right: 1; min-width: 13; }
    DataTable { height: 1fr; border: round $primary; }
    TabPane { padding: 1; }
    """
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("i", "info", "Info"),
        Binding("1", "tab('lists')", "Lists", show=False),
        Binding("2", "tab('recipes')", "Recipes", show=False),
        Binding("3", "tab('meal-plan')", "Meal Plan", show=False),
    ]

    def __init__(self, client: AnyListClient, email: str, cache_path: Path) -> None:
        super().__init__()
        self.client = client
        self.email = email
        self.cache_path = cache_path

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="lists", id="tabs"):
            with TabPane("Lists", id="lists"):
                yield ListsPanel()
            with TabPane("Recipes", id="recipes"):
                yield RecipesPanel()
            with TabPane("Meal Plan", id="meal-plan"):
                yield MealPlanPanel()
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_views()
        self.set_interval(2.0, self.refresh_views)
        self.notify("Connected to AnyList")

    async def refresh_views(self) -> None:
        panels: tuple[SDKPanel, ...] = (
            self.query_one(ListsPanel),
            self.query_one(RecipesPanel),
            self.query_one(MealPlanPanel),
        )
        for panel in panels:
            try:
                await panel.refresh_view()
            except Exception:  # noqa: BLE001,S112 - periodic redraw must not terminate app
                continue

    async def action_refresh(self) -> None:
        try:
            await self.client.refresh()
            await self.refresh_views()
            self.notify("Refreshed from AnyList")
        except Exception as exc:  # noqa: BLE001 - top-level UI boundary
            self.notify(str(exc), severity="error", timeout=6)

    def action_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id

    def action_info(self) -> None:
        self.push_screen(InfoScreen())

    async def on_unmount(self) -> None:
        tokens = self.client.tokens
        if tokens is not None:
            _save_token_cache(self.cache_path, self.email, tokens)


async def _authenticated_client(
    cache_path: Path, *, force_login: bool = False
) -> tuple[AnyListClient, str]:
    cached = None if force_login else _load_token_cache(cache_path)
    if cached is not None:
        email, tokens = cached
        client = AnyListClient(tokens=tokens, user_email=email, cache_dir=SDK_CACHE)
        try:
            await client.load(realtime=True, load_tag_data=False, restore_pending=True)
            if client.tokens is not None:
                _save_token_cache(cache_path, email, client.tokens)
            return client, email
        except Exception as exc:  # noqa: BLE001 - failed cached session falls back to login
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
    parser = argparse.ArgumentParser(description="User-facing Textual example for anylist-sdk")
    parser.add_argument(
        "--token-cache",
        default=os.fspath(TOKEN_CACHE),
        help=f"cached token file (default: {TOKEN_CACHE})",
    )
    parser.add_argument(
        "--login", action="store_true", help="ignore cached tokens and sign in again"
    )
    parser.add_argument("--logout", action="store_true", help="remove cached tokens and exit")
    return parser


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
