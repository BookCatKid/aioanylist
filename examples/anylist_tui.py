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
import mimetypes
import os
import sys
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
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
from anylist_sdk.derived import effective_recipe_scale_factor, recipe_servings_after_scaling
from anylist_sdk.normalization import canonical_category_match_id
from anylist_sdk.parsing.ingredient import parse_ingredient_lines, parse_recipe_steps
from anylist_sdk.parsing.quantity import parse_quantity_and_package_size
from anylist_sdk.proto import (
    PB,
    ListItem,
    PBCalendarEvent,
    PBMealPlanTemplate,
    PBMealPlanTemplateGroup,
    PBRecipe,
    StarterList,
)
from anylist_sdk.types import AuthTokens, AutocompleteSuggestion

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
    rendered_rows = tuple(tuple(str(cell) for cell in row) for row in rows)
    if table.row_count == len(rendered_rows):
        current_rows = tuple(
            tuple(str(cell) for cell in table.get_row_at(index)) for index in range(table.row_count)
        )
        if current_rows == rendered_rows:
            # Periodic UI refreshes should be visually inert when synchronized state has
            # not changed. Clearing/re-adding identical rows resets DataTable's scroll
            # offset, which made long lists jump back every two seconds.
            return
    table.clear()
    for row in rendered_rows:
        table.add_row(*row)
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


def _folder_descendants(client: AnyListClient, folder_id: str) -> set[str]:
    descendants: set[str] = set()
    pending = [folder_id]
    while pending:
        current = pending.pop()
        folder = client.state.list_folders.get(current)
        if folder is None:
            continue
        for item in folder.items:
            if int(item.itemType) != 1:
                continue
            child_id = str(item.identifier)
            if child_id in descendants:
                continue
            descendants.add(child_id)
            pending.append(child_id)
    return descendants


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


def _ingredient_line(value: object) -> str:
    if bool(getattr(value, "isHeading", False)):
        return f"# {getattr(value, 'name', '')}".rstrip()
    raw = str(getattr(value, "rawIngredient", "") or "")
    if raw:
        return raw
    quantity = str(getattr(value, "quantity", "") or "").strip()
    name = str(getattr(value, "name", "") or "").strip()
    note = str(getattr(value, "note", "") or "").strip()
    result = " ".join(part for part in (quantity, name) if part)
    if note:
        result = f"{result}, {note}" if result else note
    return result


def _recipe_ingredients_text(recipe: object) -> str:
    return "\n".join(_ingredient_line(value) for value in getattr(recipe, "ingredients", ()))


def _recipe_steps_text(recipe: object) -> str:
    return "\n\n".join(str(value) for value in getattr(recipe, "preparationSteps", ()))


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
    FormModal #form-autocomplete { height: 8; border: round $panel; margin-top: 0; }
    FormModal #autocomplete-choice { height: auto; color: $text-muted; margin-bottom: 1; }
    FormModal .form-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    FormModal .form-buttons Button { margin-left: 1; }
    """

    def __init__(
        self,
        title: str,
        fields: Sequence[FormField],
        *,
        submit_label: str = "Save",
        autocomplete: Callable[[str], Awaitable[list[AutocompleteSuggestion]]] | None = None,
    ) -> None:
        super().__init__()
        self.form_title = title
        self.fields = tuple(fields)
        self.submit_label = submit_label
        self.autocomplete = autocomplete
        self.autocomplete_suggestions: list[AutocompleteSuggestion] = []
        self.selected_autocomplete: AutocompleteSuggestion | None = None
        self._autocomplete_generation = 0

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
                        if field.key == "name" and self.autocomplete is not None:
                            yield DataTable(
                                id="form-autocomplete",
                                cursor_type="row",
                                zebra_stripes=True,
                            )
                            yield Static("", id="autocomplete-choice")
            with Horizontal(classes="form-buttons"):
                yield Button("Cancel", id="form-cancel")
                yield Button(self.submit_label, id="form-submit", variant="primary")

    def on_mount(self) -> None:
        if self.autocomplete is None:
            return
        table = self.query_one("#form-autocomplete", DataTable)
        table.add_columns("Suggestion", "Source")
        table.display = False

    @staticmethod
    def _autocomplete_source_label(source: str) -> str:
        return {
            "add": "Add new item",
            "current-list": "Already on this list",
            "favorite": "Favorite",
            "recent": "Recent",
            "generic": "Suggested",
        }.get(source, source.replace("-", " ").title())

    async def on_input_changed(self, event: Input.Changed) -> None:
        if self.autocomplete is None or event.input.id != "field-name":
            return
        query = event.value.strip()
        table = self.query_one("#form-autocomplete", DataTable)
        choice = self.query_one("#autocomplete-choice", Static)
        if self.selected_autocomplete is not None and query != self.selected_autocomplete.text:
            self.selected_autocomplete = None
            choice.update("")
        if not query:
            self._autocomplete_generation += 1
            self.autocomplete_suggestions = []
            table.clear()
            table.display = False
            return
        if self.selected_autocomplete is not None and query == self.selected_autocomplete.text:
            table.display = False
            return

        self._autocomplete_generation += 1
        generation = self._autocomplete_generation
        try:
            suggestions = await self.autocomplete(query)
        except Exception:  # noqa: BLE001 - typing should remain usable if suggestions fail
            if generation == self._autocomplete_generation:
                self.autocomplete_suggestions = []
                table.clear()
                table.display = False
            return
        if generation != self._autocomplete_generation:
            return
        self.autocomplete_suggestions = suggestions
        table.clear()
        for suggestion in suggestions:
            table.add_row(
                suggestion.text,
                self._autocomplete_source_label(suggestion.source),
            )
        table.display = bool(suggestions)
        if suggestions:
            table.move_cursor(row=0)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "form-autocomplete":
            return
        row = event.data_table.cursor_row
        if row is None or row < 0 or row >= len(self.autocomplete_suggestions):
            return
        suggestion = self.autocomplete_suggestions[row]
        self.selected_autocomplete = suggestion
        self.query_one("#field-name", Input).value = suggestion.text
        self.query_one("#autocomplete-choice", Static).update(
            f"Selected from {self._autocomplete_source_label(suggestion.source)}"
        )
        event.data_table.display = False
        self.query_one("#field-name", Input).focus()

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
        if self.autocomplete is not None:
            name = str(result.get("name") or "")
            result["_autocomplete_suggestion"] = (
                self.selected_autocomplete
                if self.selected_autocomplete is not None
                and self.selected_autocomplete.text == name
                else None
            )
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
        elif key == "down" and self.autocomplete is not None:
            name = self.query_one("#field-name", Input)
            table = self.query_one("#form-autocomplete", DataTable)
            if self.focused is name and table.display and self.autocomplete_suggestions:
                table.focus()


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

    async def upload_photo_reference(self, value: str) -> str:
        photos = self.client.photos
        if photos is None:
            raise RuntimeError("Photo service is unavailable")
        reference = value.strip()
        if reference.startswith(("http://", "https://")):
            return await photos.upload_url(reference)
        path = Path(reference).expanduser()
        if not path.is_file():
            raise ValueError("Photo must be an existing local file or an http(s) URL")
        content_type = mimetypes.guess_type(path.name)[0] or ""
        if content_type not in photos.ACCEPTED_CONTENT_TYPES:
            raise ValueError(f"Unsupported photo type for {path.name}")
        return await photos.upload_bytes(
            path.read_bytes(),
            content_type=content_type,
            filename=path.name,
        )

    def form(
        self,
        title: str,
        fields: Sequence[FormField],
        handler: FormHandler,
        *,
        submit_label: str = "Save",
        autocomplete: Callable[[str], Awaitable[list[AutocompleteSuggestion]]] | None = None,
    ) -> None:
        async def finished(result: FormResult | None) -> None:
            if result is None:
                return
            try:
                await handler(result)
            except Exception as exc:  # noqa: BLE001 - UI boundary reports service errors
                self.error(exc)

        self.app.push_screen(
            FormModal(title, fields, submit_label=submit_label, autocomplete=autocomplete),
            finished,
        )

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
            "Item", "✓", "Quantity", "Category", "Stores", "Photo", "Details"
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
                    str(len(item.photoIds)) if getattr(item, "photoIds", ()) else "",
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

    def _item_form_fields(self, *, item: ListItem | None = None) -> list[FormField]:
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
        current_photo = ""
        if item is not None and item.photoIds and self.client.photos is not None:
            current_photo = self.client.photos.url(str(item.photoIds[0]))
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
                placeholder="Optional notes about this item",
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
            FormField(
                "photo",
                "Photo",
                value=current_photo,
                placeholder=(
                    "Local file or image URL"
                    if item is None
                    else "Keep this URL, replace with file/URL, or type - to remove"
                ),
            ),
        ]
        if item is None:
            fields.insert(
                2,
                FormField(
                    "quantity",
                    "Quantity / package size (optional override)",
                    placeholder="e.g. 2, 1 lb, or 2 cans (14 oz)",
                ),
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

    def _effective_list_setting_bool(self, list_id: str, field: str) -> bool:
        for settings_id in (list_id, ""):
            settings = self.client.state.list_settings.get(settings_id)
            if settings is None or field not in settings.DESCRIPTOR.fields_by_name:
                continue
            try:
                if settings.HasField(field):
                    return bool(getattr(settings, field))
            except ValueError:
                return bool(getattr(settings, field))
        return False

    async def _item_autocomplete(self, query: str) -> list[AutocompleteSuggestion]:
        list_id = self.current_list_id
        service = self.client.lists
        if service is None or not list_id:
            return []
        current = service.get(list_id)
        if current is None:
            return []

        favorites: Sequence[ListItem] = ()
        recents: Sequence[ListItem] = ()
        starter = self.client.starter_lists
        if starter is not None:
            favorite_list = starter.favorite_for_shopping_list(list_id)
            recent_list = starter.recent_for_shopping_list(list_id)
            if favorite_list is not None:
                favorites = starter.autocomplete_items(str(favorite_list.identifier))
            if recent_list is not None:
                recents = starter.autocomplete_items(str(recent_list.identifier))

        return await self.client.autocomplete.suggestions(
            query,
            current_items=current.items,
            favorites=favorites,
            recents=recents,
            include_favorites=self._effective_list_setting_bool(
                list_id, "favoritesAutocompleteEnabled"
            ),
            include_recents=self._effective_list_setting_bool(
                list_id, "recentItemsAutocompleteEnabled"
            ),
            include_generic=self._effective_list_setting_bool(
                list_id, "genericGroceryAutocompleteEnabled"
            ),
        )

    @staticmethod
    def _category_match_id(category: object) -> str:
        system = str(getattr(category, "systemCategory", "") or "")
        return system or canonical_category_match_id(str(getattr(category, "name", "") or ""))

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
        suggestion = cast(AutocompleteSuggestion | None, result.get("_autocomplete_suggestion"))
        source_item = (
            suggestion.payload
            if suggestion is not None
            and suggestion.source in {"current-list", "favorite", "recent"}
            and isinstance(suggestion.payload, ListItem)
            else None
        )

        if (
            suggestion is not None
            and suggestion.source == "current-list"
            and source_item is not None
        ):
            revived = await service.revive_matching_item(list_id, source_item)
            if revived is not None:
                await self.refresh_view()
                return

        if (
            suggestion is not None
            and suggestion.source in {"favorite", "recent"}
            and source_item is not None
        ):
            draft = await service.prepare_autocomplete_item_for_add(list_id, source_item)
        else:
            draft = await service.prepare_item_for_add(list_id, name)

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
        if category_id != AUTO_CATEGORY:
            category = self.client.state.list_categories.get(list_id, {}).get(category_id or "")
            if category is not None:
                service.apply_category_to_prepared_item(list_id, draft, category)

        photo_reference = str(result.get("photo") or "").strip()
        if photo_reference and photo_reference != "-":
            photo_id = await self.upload_photo_reference(photo_reference)
            del draft.photoIds[:]
            draft.photoIds.append(photo_id)
        elif photo_reference == "-":
            del draft.photoIds[:]

        await service.add_prepared_item(list_id, draft)
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
        photo_reference = str(result.get("photo") or "").strip()
        current_photo = (
            self.client.photos.url(str(item.photoIds[0]))
            if item.photoIds and self.client.photos is not None
            else ""
        )
        if photo_reference == "-":
            if item.photoIds:
                await service.set_photo(list_id, item_id, None, flush=False)
                changed = True
        elif photo_reference and photo_reference != current_photo:
            photo_id = await self.upload_photo_reference(photo_reference)
            await service.set_photo(list_id, item_id, photo_id, flush=False)
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
            self.form(
                "Add item",
                self._item_form_fields(),
                self._new_item,
                submit_label="Add",
                autocomplete=self._item_autocomplete,
            )
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
            yield Button("Store ↑", id="store-up")
            yield Button("Store ↓", id="store-down")
            yield Button("Delete store", id="store-delete", variant="error")
            yield Button("New category", id="category-new", variant="success")
            yield Button("Edit category", id="category-edit")
            yield Button("Category ↑", id="category-up")
            yield Button("Category ↓", id="category-down")
            yield Button("Make default", id="category-default")
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
        elif button_id in {"store-up", "store-down"}:
            if not store_id:
                return self.error("Select a store")
            ordered = [
                str(value.identifier)
                for value in sorted(stores.values(), key=lambda value: int(value.sortIndex))
            ]
            index = ordered.index(store_id)
            new_index = index - 1 if button_id == "store-up" else index + 1
            if new_index < 0 or new_index >= len(ordered):
                return
            ordered[index], ordered[new_index] = ordered[new_index], ordered[index]
            await service.set_sorted_store_ids(self.list_id, ordered)
            await self.refresh_view()
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
            category_group = group
            if str(group.defaultCategoryId) == category_id:
                return self.error("The default category cannot be deleted")

            async def delete_category() -> None:
                await service.remove_category_ids(category_group, [category])
                await self.refresh_view()

            self.confirm("Delete category", f"Delete “{category.name}”?", delete_category)
        elif button_id in {"category-up", "category-down", "category-default"}:
            if not category_id:
                return self.error("Select a category")
            category = categories[category_id]
            group = groups.get(str(category.categoryGroupId))
            if group is None:
                return self.error("Category group is missing")
            if button_id == "category-default":
                await service.set_default_category(group, category_id)
                await self.refresh_view()
                return
            ordered = [
                str(value.identifier)
                for value in sorted(
                    (
                        value
                        for value in categories.values()
                        if str(value.categoryGroupId) == str(group.identifier)
                    ),
                    key=lambda value: int(value.sortIndex),
                )
            ]
            index = ordered.index(category_id)
            new_index = index - 1 if button_id == "category-up" else index + 1
            if new_index < 0 or new_index >= len(ordered):
                return
            ordered[index], ordered[new_index] = ordered[new_index], ordered[index]
            await service.set_sorted_category_ids(group, ordered)
            await self.refresh_view()


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
            yield Button("Delete folder", id="folder-delete", variant="error")
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
            current_folder = current
            settings = current_folder.folderSettings
            current_parent = _folder_parent_map(self.client).get(folder_id)
            excluded = {folder_id, *_folder_descendants(self.client, folder_id)}
            parent_options = tuple(
                (label, value)
                for label, value in _folder_options(self.client)
                if value not in excluded
            )

            async def edit(result: FormResult) -> None:
                name = str(result["name"]).strip()
                color = _hex_color(str(result.get("color") or "").strip())
                icon = str(result.get("icon") or "").strip()
                parent_id = cast(str | None, result.get("parent"))
                if not name:
                    return self.error("Folder name is required")
                if color is None:
                    return self.error("Color must be a six-digit hex value")
                changed = False
                if name != current_folder.name:
                    await service.rename(folder_id, name, flush=False)
                    changed = True
                if color and color != settings.folderHexColor:
                    await service.set_hex_color(folder_id, color, flush=False)
                    changed = True
                if icon and icon != settings.icon.iconName:
                    await service.set_icon(folder_id, icon, flush=False)
                    changed = True
                if parent_id and current_parent and parent_id != current_parent:
                    await service.move(
                        [PB.PBListFolderItem(identifier=folder_id, itemType=1)],
                        current_parent,
                        parent_id,
                        flush=False,
                    )
                    changed = True
                if changed:
                    await service.flush()
                await self.refresh_view()

            self.form(
                "Edit folder",
                [
                    FormField("name", "Folder name", value=current.name),
                    FormField(
                        "parent",
                        "Parent folder",
                        kind="select",
                        value=current_parent,
                        options=parent_options,
                        allow_blank=False,
                    ),
                    FormField("color", "Color", value=settings.folderHexColor),
                    FormField("icon", "Icon name", value=settings.icon.iconName),
                ],
                edit,
            )
        elif button_id == "folder-delete":
            if not folder_id:
                return self.error("Select a folder")
            current = service.get(folder_id)
            parent_id = _folder_parent_map(self.client).get(folder_id)
            if current is None or not parent_id:
                return self.error("That folder cannot be deleted")

            descendants = _folder_descendants(self.client, folder_id)
            nested_lists = 0
            for candidate_id in {folder_id, *descendants}:
                candidate = service.get(candidate_id)
                if candidate is not None:
                    nested_lists += sum(1 for item in candidate.items if int(item.itemType) == 0)

            async def delete_folder() -> None:
                await service.delete_folder(folder_id, parent_id)
                await self.refresh_view()

            message = f"Delete “{current.name}”"
            if descendants or nested_lists:
                message += (
                    f" and everything inside it ({len(descendants)} nested folder"
                    f"{'s' if len(descendants) != 1 else ''}, {nested_lists} list"
                    f"{'s' if nested_lists != 1 else ''})?"
                )
            else:
                message += "?"
            self.confirm("Delete folder", message, delete_folder)


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
        self.query_one("#saved-items", DataTable).add_columns(
            "Item", "Quantity", "Photo", "Stores", "Details"
        )

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
        stores = self.client.state.list_stores.get(self.shopping_list_id, {})

        def row(value: ListItem) -> tuple[object, ...]:
            quantity = str(getattr(getattr(value, "quantityPb", None), "rawQuantity", "") or "")
            package = str(
                getattr(getattr(value, "packageSizePb", None), "rawPackageSize", "") or ""
            )
            quantity_text = " ".join(part for part in (quantity, package) if part)
            store_names = ", ".join(
                str(stores[store_id].name) for store_id in value.storeIds if store_id in stores
            )
            return (
                value.name,
                quantity_text,
                len(value.photoIds) or "",
                store_names,
                value.details,
            )

        _replace_rows(
            table,
            (row(value) for value in items),
            self.item_ids,
            keep_id=old,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "saved-lists":
            self.current_list_id = _selected_id(event.data_table, self.list_ids)
            self._refresh_items()

    def _item_fields(self, item: ListItem | None = None) -> list[FormField]:
        current_photo = ""
        if item is not None and item.photoIds and self.client.photos is not None:
            current_photo = self.client.photos.url(str(item.photoIds[0]))
        stores = sorted(
            self.client.state.list_stores.get(self.shopping_list_id, {}).values(),
            key=lambda value: (int(value.sortIndex), str(value.name).casefold()),
        )
        return [
            FormField("name", "Name", value=getattr(item, "name", "")),
            FormField("details", "Details", kind="textarea", value=getattr(item, "details", "")),
            FormField(
                "quantity",
                "Quantity / package size",
                placeholder="e.g. 2, 1 lb, or 2 cans (14 oz)",
            ),
            FormField(
                "stores",
                "Stores",
                kind="multiselect",
                options=tuple((str(value.name), str(value.identifier)) for value in stores),
                selected=tuple(str(value) for value in getattr(item, "storeIds", ())),
            ),
            FormField(
                "photo",
                "Photo",
                value=current_photo,
                placeholder=(
                    "Local file or image URL"
                    if item is None
                    else "Keep this URL, replace with file/URL, or type - to remove"
                ),
            ),
        ]

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
                item = PB.ListItem(name=name, details=str(result.get("details") or ""))
                quantity_text = str(result.get("quantity") or "").strip()
                parsed = parse_quantity_and_package_size(quantity_text) if quantity_text else None
                if quantity_text and parsed is None:
                    return self.error("Could not understand that quantity/package size")
                if parsed is not None and parsed.HasField("quantityPb"):
                    item.quantityPb.CopyFrom(parsed.quantityPb)
                if parsed is not None and parsed.HasField("packageSizePb"):
                    item.packageSizePb.CopyFrom(parsed.packageSizePb)
                item.storeIds.extend(cast(list[str], result.get("stores") or []))
                photo_reference = str(result.get("photo") or "").strip()
                if photo_reference and photo_reference != "-":
                    item.photoIds.append(await self.upload_photo_reference(photo_reference))
                await service.add_item(list_id, item)
                await self.refresh_view()

            self.form(
                "Add saved item",
                self._item_fields(),
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
                quantity_text = str(result.get("quantity") or "").strip()
                if quantity_text:
                    parsed = parse_quantity_and_package_size(quantity_text)
                    if parsed is None:
                        return self.error("Could not understand that quantity/package size")
                    if parsed.HasField("quantityPb"):
                        await service.set_quantity(list_id, item_id, parsed.quantityPb, flush=False)
                        changed = True
                    if parsed.HasField("packageSizePb"):
                        await service.set_package_size(
                            list_id, item_id, parsed.packageSizePb, flush=False
                        )
                        changed = True
                requested_stores = set(cast(list[str], result.get("stores") or []))
                current_stores = {str(value) for value in item_snapshot.storeIds}
                added_stores = sorted(requested_stores - current_stores)
                removed_stores = sorted(current_stores - requested_stores)
                if added_stores:
                    await service.add_store_ids_to_items(
                        list_id, [item_id], added_stores, flush=False
                    )
                    changed = True
                if removed_stores:
                    await service.remove_store_ids_from_items(
                        list_id, [item_id], removed_stores, flush=False
                    )
                    changed = True
                current_photo = (
                    self.client.photos.url(str(item_snapshot.photoIds[0]))
                    if item_snapshot.photoIds and self.client.photos is not None
                    else ""
                )
                photo_reference = str(result.get("photo") or "").strip()
                if photo_reference == "-":
                    if item_snapshot.photoIds:
                        await service.set_photo(list_id, item_id, None, flush=False)
                        changed = True
                elif photo_reference and photo_reference != current_photo:
                    await service.set_photo(
                        list_id,
                        item_id,
                        await self.upload_photo_reference(photo_reference),
                        flush=False,
                    )
                    changed = True
                if changed:
                    await service.flush()
                await self.refresh_view()

            self.form(
                "Edit saved item",
                self._item_fields(item_snapshot),
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


class ListBehaviorPanel(SDKPanel):
    """User-facing list behavior toggles backed by official PBListSettings mutations."""

    DEFAULT_CSS = """
    ListBehaviorPanel { padding: 1; }
    ListBehaviorPanel Checkbox { margin-bottom: 1; }
    ListBehaviorPanel #behavior-save { margin-top: 1; }
    """

    _FIELDS: ClassVar[tuple[tuple[str, str, str, bool, bool], ...]] = (
        (
            "behavior-generic",
            "Grocery autocomplete suggestions",
            "genericGroceryAutocompleteEnabled",
            False,
            False,
        ),
        (
            "behavior-favorites",
            "Favorite-item autocomplete suggestions",
            "favoritesAutocompleteEnabled",
            True,
            False,
        ),
        (
            "behavior-recents",
            "Recent-item autocomplete suggestions",
            "recentItemsAutocompleteEnabled",
            True,
            False,
        ),
        (
            "behavior-remember-categories",
            "Remember item categories",
            "shouldRememberItemCategories",
            True,
            False,
        ),
        (
            "behavior-show-categories",
            "Show categories",
            "shouldHideCategories",
            False,
            True,
        ),
        (
            "behavior-show-completed",
            "Show completed items",
            "shouldHideCompletedItems",
            False,
            True,
        ),
        (
            "behavior-show-store-names",
            "Show store names",
            "shouldHideStoreNames",
            False,
            True,
        ),
        (
            "behavior-show-prices",
            "Show item prices",
            "shouldHidePrices",
            False,
            True,
        ),
        (
            "behavior-show-running-totals",
            "Show running totals",
            "shouldHideRunningTotals",
            False,
            True,
        ),
    )

    def __init__(self, list_id: str) -> None:
        super().__init__()
        self.list_id = list_id

    def compose(self) -> ComposeResult:
        yield Label("List behavior")
        yield Static(
            "These are the per-list switches AnyList uses for suggestions and list presentation."
        )
        for widget_id, label, _field, _default, _inverted in self._FIELDS:
            yield Checkbox(label, id=widget_id)
        yield Button("Save behavior", id="behavior-save", variant="primary")

    def _effective_bool(self, field: str, default: bool) -> bool:
        for settings_id in (self.list_id, ""):
            settings = self.client.state.list_settings.get(settings_id)
            if settings is None:
                continue
            try:
                if settings.HasField(field):
                    return bool(getattr(settings, field))
            except ValueError:
                return bool(getattr(settings, field))
        return default

    async def refresh_view(self) -> None:
        for widget_id, _label, field, default, inverted in self._FIELDS:
            raw = self._effective_bool(field, default)
            self.query_one(f"#{widget_id}", Checkbox).value = not raw if inverted else raw

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "behavior-save":
            return
        service = self.client.list_settings
        if service is None:
            return
        changed = False
        for widget_id, _label, field, default, inverted in self._FIELDS:
            shown = bool(self.query_one(f"#{widget_id}", Checkbox).value)
            requested = not shown if inverted else shown
            if requested == self._effective_bool(field, default):
                continue
            await service.set(self.list_id, field, requested, flush=False)
            changed = True
        if changed:
            await service.flush()
            self.tui.notify("List behavior saved")
        await self.refresh_view()


class ListSettingsScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ListSettingsScreen { align: center middle; background: $background 45%; }
    ListSettingsScreen #tools-dialog {
        width: 94%; height: 90%; padding: 1;
        border: round $accent; background: $surface;
    }
    ListSettingsScreen #tools-header { height: 3; }
    ListSettingsScreen #tools-title { width: 1fr; height: auto; }
    ListSettingsScreen #tools-close { dock: right; }
    ListSettingsScreen TabbedContent { height: 1fr; }
    ListSettingsScreen TabPane { height: 1fr; padding: 1; }
    ListSettingsScreen StoresCategoriesPanel,
    ListSettingsScreen SavedItemsPanel,
    ListSettingsScreen FoldersPanel,
    ListSettingsScreen ListBehaviorPanel { width: 1fr; height: 1fr; }
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
                with Vertical(id="tools-title"):
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
                with TabPane("Behavior", id="list-behavior"):
                    yield ListBehaviorPanel(self.list_id)

    async def on_mount(self) -> None:
        for panel in self.query(SDKPanel):
            await panel.refresh_view()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tools-close":
            self.dismiss(None)


class RecipeDetailScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    RecipeDetailScreen { align: center middle; background: $background 55%; }
    RecipeDetailScreen #recipe-detail-dialog {
        width: 92%; height: 90%; padding: 1 2;
        border: round $accent; background: $surface;
    }
    RecipeDetailScreen #recipe-detail-header { height: 3; }
    RecipeDetailScreen #recipe-detail-title { width: 1fr; text-style: bold; }
    RecipeDetailScreen #recipe-detail-close { dock: right; }
    RecipeDetailScreen #recipe-detail-body { height: 1fr; }
    """

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.recipe_title = title
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="recipe-detail-dialog"):
            with Horizontal(id="recipe-detail-header"):
                yield Label(self.recipe_title, id="recipe-detail-title")
                yield Button("Close", id="recipe-detail-close")
            with VerticalScroll(id="recipe-detail-body"):
                yield Static(self.body, markup=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "recipe-detail-close":
            self.dismiss(None)

    def on_key(self, event: object) -> None:
        if getattr(event, "key", "") == "escape":
            self.dismiss(None)


class RecipesPanel(SDKPanel):
    ALL_RECIPES = "__all_recipes__"

    DEFAULT_CSS = """
    RecipesPanel .toolbar { height: auto; }
    RecipesPanel #recipe-browser { height: 1fr; }
    RecipesPanel #recipe-collections-pane { width: 27%; }
    RecipesPanel #recipe-list-pane { width: 34%; }
    RecipesPanel #recipe-detail-pane {
        width: 1fr; border: round $primary; padding: 0 1;
    }
    RecipesPanel #collection-list, RecipesPanel #recipe-list { height: 1fr; }
    RecipesPanel #recipe-preview-scroll { height: 1fr; }
    RecipesPanel #recipe-preview { height: auto; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.recipe_ids: list[str] = []
        self.collection_ids: list[str] = []
        self.collection_values: dict[str, object] = {}
        self.active_collection_id = self.ALL_RECIPES

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("View recipe", id="recipe-view", variant="primary")
            yield Button("New recipe", id="recipe-new", variant="success")
            yield Button("Edit recipe", id="recipe-edit")
            yield Button("Delete recipe", id="recipe-delete", variant="error")
            yield Button("New collection", id="collection-new", variant="success")
            yield Button("Edit collection", id="collection-edit")
            yield Button("Delete collection", id="collection-delete", variant="error")
            yield Button("Add to collection", id="collection-add")
            yield Button("Remove from collection", id="collection-remove")
        with Horizontal(id="recipe-browser"):
            with Vertical(id="recipe-collections-pane"):
                yield Label("Collections")
                yield DataTable(id="collection-list", cursor_type="row", zebra_stripes=True)
            with Vertical(id="recipe-list-pane"):
                yield Label("Recipes")
                yield DataTable(id="recipe-list", cursor_type="row", zebra_stripes=True)
            with Vertical(id="recipe-detail-pane"):
                yield Label("Recipe details")
                with VerticalScroll(id="recipe-preview-scroll"):
                    yield Static("Select a recipe", id="recipe-preview", markup=False)

    def on_mount(self) -> None:
        self.query_one("#collection-list", DataTable).add_columns("Collection", "Recipes")
        self.query_one("#recipe-list", DataTable).add_columns(
            "Recipe", "Rating", "Servings", "Photo"
        )

    def _browse_collections(self) -> list[object]:
        service = self.client.recipes
        if service is None:
            return []
        state = self.client.state
        ordered: list[object] = []
        seen: set[str] = set()
        for collection_id in state.recipe_collection_ids:
            value = state.recipe_collections.get(collection_id)
            if value is not None:
                ordered.append(value)
                seen.add(collection_id)
        ordered.extend(
            value
            for collection_id, value in state.recipe_collections.items()
            if collection_id not in seen
        )
        ordered.append(service.not_in_collection())
        ordered.extend(service.source_collections())
        return ordered

    def _active_collection(self) -> object | None:
        return self.collection_values.get(self.active_collection_id)

    def _recipes_for_active_collection(self) -> list[PBRecipe]:
        service = self.client.recipes
        if service is None:
            return []
        if self.active_collection_id == self.ALL_RECIPES:
            return list(service.sorted())
        collection = self._active_collection()
        if collection is None:
            return list(service.sorted())
        recipes = [
            self.client.state.recipes[recipe_id]
            for recipe_id in getattr(collection, "recipeIds", ())
            if recipe_id in self.client.state.recipes
        ]
        settings = (
            collection.collectionSettings
            if getattr(collection, "HasField", lambda _name: False)("collectionSettings")
            else None
        )
        return list(service.sorted(recipes, settings=settings))

    async def refresh_view(self) -> None:
        service = self.client.recipes
        if service is None:
            return
        collection_table = self.query_one("#collection-list", DataTable)
        previous_collection = self.active_collection_id
        collections = self._browse_collections()
        self.collection_values = {
            str(getattr(value, "identifier", "")): value for value in collections
        }
        self.collection_ids = [self.ALL_RECIPES, *self.collection_values]
        if previous_collection not in self.collection_ids:
            self.active_collection_id = self.ALL_RECIPES
        _replace_rows(
            collection_table,
            (
                [("All Recipes", len(service.all()))]
                + [
                    (
                        getattr(value, "name", "Unnamed collection"),
                        len(getattr(value, "recipeIds", ())),
                    )
                    for value in collections
                ]
            ),
            self.collection_ids,
            keep_id=self.active_collection_id,
        )
        self.active_collection_id = (
            _selected_id(collection_table, self.collection_ids) or self.ALL_RECIPES
        )
        self._refresh_recipes()

    def _refresh_recipes(self) -> None:
        table = self.query_one("#recipe-list", DataTable)
        old_recipe = _selected_id(table, self.recipe_ids)
        recipes = self._recipes_for_active_collection()
        self.recipe_ids = [str(getattr(value, "identifier", "")) for value in recipes]
        _replace_rows(
            table,
            (
                (
                    getattr(value, "name", ""),
                    getattr(value, "rating", "") or "",
                    getattr(value, "servings", ""),
                    len(getattr(value, "photoIds", ())) + len(getattr(value, "photoUrls", ()))
                    or "",
                )
                for value in recipes
            ),
            self.recipe_ids,
            keep_id=old_recipe,
        )
        self._refresh_recipe_preview()

    def _selected_recipe(self) -> PBRecipe | None:
        service = self.client.recipes
        if service is None:
            return None
        recipe_id = _selected_id(self.query_one("#recipe-list", DataTable), self.recipe_ids)
        return service.get(recipe_id) if recipe_id else None

    def _recipe_collection_names(self, recipe_id: str) -> list[str]:
        return [
            str(value.name)
            for value in self.client.state.recipe_collections.values()
            if recipe_id in value.recipeIds
        ]

    def _recipe_detail_text(self, recipe: PBRecipe) -> str:
        lines: list[str] = [str(recipe.name)]
        servings = str(getattr(recipe, "servings", "") or "")
        rating = int(getattr(recipe, "rating", 0) or 0)
        prep_time = int(getattr(recipe, "prepTime", 0) or 0)
        cook_time = int(getattr(recipe, "cookTime", 0) or 0)
        metadata = []
        if servings:
            metadata.append(f"Servings: {servings}")
        if rating:
            metadata.append(f"Rating: {rating}/5")
        if prep_time:
            metadata.append(f"Prep: {prep_time} min")
        if cook_time:
            metadata.append(f"Cook: {cook_time} min")
        if metadata:
            lines.append(" • ".join(metadata))

        collection_names = self._recipe_collection_names(str(getattr(recipe, "identifier", "")))
        if collection_names:
            lines.append(f"Collections: {', '.join(collection_names)}")

        photos: list[str] = [str(value) for value in getattr(recipe, "photoUrls", ()) if value]
        if self.client.photos is not None:
            photos.extend(
                self.client.photos.url(str(value))
                for value in getattr(recipe, "photoIds", ())
                if value
            )
        if photos:
            lines.extend(["", "Photos:", *(f"  {value}" for value in photos)])

        note = str(getattr(recipe, "note", "") or "")
        if note:
            lines.extend(["", "Notes:", note])

        ingredients = list(getattr(recipe, "ingredients", ()))
        if ingredients:
            lines.extend(["", "Ingredients:"])
            for ingredient in ingredients:
                rendered = _ingredient_line(ingredient)
                lines.append(rendered if rendered.startswith("# ") else f"  • {rendered}")

        steps = list(getattr(recipe, "preparationSteps", ()))
        if steps:
            lines.extend(["", "Directions:"])
            step_number = 1
            for step in steps:
                text = str(step)
                if text.startswith("# "):
                    lines.append(text)
                else:
                    lines.append(f"  {step_number}. {text}")
                    step_number += 1

        nutritional = str(getattr(recipe, "nutritionalInfo", "") or "")
        if nutritional:
            lines.extend(["", "Nutrition:", nutritional])

        source_name = str(getattr(recipe, "sourceName", "") or "")
        source_url = str(getattr(recipe, "sourceUrl", "") or "")
        if source_name or source_url:
            lines.extend(["", "Source:"])
            if source_name:
                lines.append(f"  {source_name}")
            if source_url:
                lines.append(f"  {source_url}")
        return "\n".join(lines) or "No additional recipe details."

    def _refresh_recipe_preview(self) -> None:
        preview = self.query_one("#recipe-preview", Static)
        recipe = self._selected_recipe()
        preview.update(
            self._recipe_detail_text(recipe) if recipe is not None else "Select a recipe"
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "collection-list":
            selected = _selected_id(event.data_table, self.collection_ids)
            if selected and selected != self.active_collection_id:
                self.active_collection_id = selected
                self._refresh_recipes()
        elif event.data_table.id == "recipe-list":
            self._refresh_recipe_preview()

    def _recipe_fields(self, recipe: PBRecipe | None = None) -> list[FormField]:
        return [
            FormField("name", "Recipe name", value=getattr(recipe, "name", "")),
            FormField("note", "Notes", kind="textarea", value=getattr(recipe, "note", "")),
            FormField(
                "ingredients",
                "Ingredients — one per line; use '# Heading' for sections",
                kind="textarea",
                value=_recipe_ingredients_text(recipe) if recipe is not None else "",
            ),
            FormField(
                "steps",
                "Directions — one step per line (numbered pasted directions are supported)",
                kind="textarea",
                value=_recipe_steps_text(recipe) if recipe is not None else "",
            ),
            FormField(
                "servings",
                "Servings",
                value=getattr(recipe, "servings", ""),
                placeholder="e.g. 4 servings",
            ),
            FormField(
                "rating",
                "Rating",
                kind="select",
                value=int(getattr(recipe, "rating", 0) or 0),
                options=(
                    ("Unrated", 0),
                    ("1 star", 1),
                    ("2 stars", 2),
                    ("3 stars", 3),
                    ("4 stars", 4),
                    ("5 stars", 5),
                ),
                allow_blank=False,
            ),
            FormField(
                "prep_time", "Prep time (minutes)", value=getattr(recipe, "prepTime", "") or ""
            ),
            FormField(
                "cook_time", "Cook time (minutes)", value=getattr(recipe, "cookTime", "") or ""
            ),
            FormField(
                "nutrition",
                "Nutritional information",
                kind="textarea",
                value=getattr(recipe, "nutritionalInfo", ""),
            ),
            FormField("source_name", "Source name", value=getattr(recipe, "sourceName", "")),
            FormField("source_url", "Source URL", value=getattr(recipe, "sourceUrl", "")),
            FormField(
                "photo",
                "Add photo",
                placeholder="Local file or image URL; type - to remove existing photos",
            ),
        ]

    async def _recipe_from_form(
        self, result: FormResult, recipe: PBRecipe | None = None
    ) -> PBRecipe:
        name = str(result["name"]).strip()
        if not name:
            raise ValueError("Recipe name is required")
        updated = PB.PBRecipe()
        if recipe is not None:
            updated.CopyFrom(recipe)
        updated.name = name

        for key, field in (
            ("note", "note"),
            ("servings", "servings"),
            ("nutrition", "nutritionalInfo"),
            ("source_name", "sourceName"),
            ("source_url", "sourceUrl"),
        ):
            value = str(result.get(key) or "").strip()
            if value:
                setattr(updated, field, value)
            elif recipe is not None:
                updated.ClearField(field)

        for key, field in (("prep_time", "prepTime"), ("cook_time", "cookTime")):
            raw = str(result.get(key) or "").strip()
            if raw:
                try:
                    numeric_value = int(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"{key.replace('_', ' ').title()} must be a whole number"
                    ) from exc
                if numeric_value < 0:
                    raise ValueError(f"{key.replace('_', ' ').title()} cannot be negative")
                setattr(updated, field, numeric_value)
            elif recipe is not None:
                updated.ClearField(field)
        rating_value = result.get("rating")
        updated.rating = int(rating_value) if isinstance(rating_value, (str, int)) else 0

        ingredients_text = str(result.get("ingredients") or "")
        if recipe is None or ingredients_text != _recipe_ingredients_text(recipe):
            del updated.ingredients[:]
            for ingredient in parse_ingredient_lines(ingredients_text):
                updated.ingredients.add().CopyFrom(ingredient)

        steps_text = str(result.get("steps") or "")
        if recipe is None or steps_text != _recipe_steps_text(recipe):
            del updated.preparationSteps[:]
            updated.preparationSteps.extend(parse_recipe_steps(steps_text))

        photo_reference = str(result.get("photo") or "").strip()
        if photo_reference == "-":
            del updated.photoIds[:]
            del updated.photoUrls[:]
        elif photo_reference:
            photo_id = await self.upload_photo_reference(photo_reference)
            updated.photoIds.append(photo_id)
        return updated

    def _custom_collection_id(self) -> str | None:
        return (
            self.active_collection_id
            if self.active_collection_id in self.client.state.recipe_collections
            else None
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        service = self.client.recipes
        if service is None:
            return
        button_id = event.button.id or ""
        recipe = self._selected_recipe()
        recipe_id = str(getattr(recipe, "identifier", "")) if recipe is not None else None
        collection_id = self._custom_collection_id()

        if button_id == "recipe-view":
            if recipe is None:
                return self.error("Select a recipe")
            self.app.push_screen(
                RecipeDetailScreen(str(recipe.name), self._recipe_detail_text(recipe))
            )
        elif button_id == "recipe-new":

            async def create(result: FormResult) -> None:
                created_recipe = await self._recipe_from_form(result)
                saved = await service.save(created_recipe)
                if collection_id:
                    await service.add_to_collection(collection_id, [str(saved.identifier)])
                await self.refresh_view()

            self.form("New recipe", self._recipe_fields(), create, submit_label="Create")
        elif button_id == "recipe-edit":
            if recipe is None:
                return self.error("Select a recipe")
            recipe_snapshot = PB.PBRecipe()
            recipe_snapshot.CopyFrom(recipe)

            async def edit(result: FormResult) -> None:
                updated = await self._recipe_from_form(result, recipe_snapshot)
                await service.save(updated)
                await self.refresh_view()

            self.form("Edit recipe", self._recipe_fields(recipe_snapshot), edit)
        elif button_id == "recipe-delete":
            if recipe is None or not recipe_id:
                return self.error("Select a recipe")

            async def delete() -> None:
                await service.remove(recipe_id)
                await self.refresh_view()

            self.confirm("Delete recipe", f"Delete “{recipe.name}”?", delete)
        elif button_id == "collection-new":

            async def create_collection(result: FormResult) -> None:
                name = str(result["name"]).strip()
                if not name:
                    return self.error("Collection name is required")
                created = await service.create_collection(name)
                self.active_collection_id = str(created.identifier)
                await self.refresh_view()

            self.form(
                "New collection",
                [FormField("name", "Collection name")],
                create_collection,
                submit_label="Create",
            )
        elif button_id == "collection-edit":
            if not collection_id:
                return self.error("Select a custom collection")
            current_collection = self.client.state.recipe_collections[collection_id]

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
                return self.error("Select a custom collection")
            current_collection = self.client.state.recipe_collections[collection_id]

            async def delete_collection() -> None:
                self.active_collection_id = self.ALL_RECIPES
                await service.remove_collection(collection_id)
                await self.refresh_view()

            self.confirm(
                "Delete collection",
                f"Delete “{current_collection.name}”? Recipes are kept.",
                delete_collection,
            )
        elif button_id == "collection-add":
            if not collection_id:
                return self.error("Select a custom collection first")
            current_collection = self.client.state.recipe_collections[collection_id]
            candidates = [
                value
                for value in service.sorted()
                if str(value.identifier) not in current_collection.recipeIds
            ]
            if not candidates:
                return self.tui.notify("Every recipe is already in this collection")

            async def add_recipes(result: FormResult) -> None:
                recipe_ids = cast(list[str], result.get("recipes") or [])
                if not recipe_ids:
                    return self.error("Select at least one recipe")
                await service.add_to_collection(collection_id, recipe_ids)
                await self.refresh_view()
                self.tui.notify(
                    f"Added {len(recipe_ids)} recipe{'s' if len(recipe_ids) != 1 else ''}"
                )

            self.form(
                f"Add recipes to {current_collection.name}",
                [
                    FormField(
                        "recipes",
                        "Recipes",
                        kind="multiselect",
                        options=tuple(
                            (str(value.name), str(value.identifier)) for value in candidates
                        ),
                    )
                ],
                add_recipes,
                submit_label="Add",
            )
        elif button_id == "collection-remove":
            if not collection_id:
                return self.error("Select a custom collection first")
            if not recipe_id:
                return self.error("Select a recipe")
            await service.remove_from_collection(collection_id, [recipe_id])
            await self.refresh_view()
            self.tui.notify("Removed recipe from collection")


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
            yield Button("Move ↑", id="label-up")
            yield Button("Move ↓", id="label-down")
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
        elif button_id in {"label-up", "label-down"}:
            if not label_id:
                return self.error("Select a label")
            ordered = [
                str(value.identifier)
                for value in sorted(service.labels(), key=lambda value: int(value.sortIndex))
            ]
            index = ordered.index(label_id)
            new_index = index - 1 if button_id == "label-up" else index + 1
            if new_index < 0 or new_index >= len(ordered):
                return
            ordered[index], ordered[new_index] = ordered[new_index], ordered[index]
            await service.reorder_labels(ordered)
            await self.refresh_view()


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
    MealPlanPanel #meal-tabs { height: 1fr; }
    MealPlanPanel #planner-browser,
    MealPlanPanel #ideas-browser,
    MealPlanPanel #template-top,
    MealPlanPanel #template-bottom { height: 1fr; }
    MealPlanPanel #planner-days-pane { width: 24%; }
    MealPlanPanel #planner-events-pane { width: 36%; }
    MealPlanPanel #planner-detail-pane { width: 1fr; border: round $primary; padding: 0 1; }
    MealPlanPanel #ideas-list-pane { width: 46%; }
    MealPlanPanel #ideas-detail-pane { width: 1fr; border: round $primary; padding: 0 1; }
    MealPlanPanel #template-groups-pane { width: 34%; }
    MealPlanPanel #templates-pane { width: 1fr; }
    MealPlanPanel #template-days-pane { width: 34%; }
    MealPlanPanel #template-events-pane { width: 1fr; }
    MealPlanPanel #template-detail-pane { width: 1fr; border: round $primary; padding: 0 1; }
    MealPlanPanel #planner-detail-scroll,
    MealPlanPanel #ideas-detail-scroll,
    MealPlanPanel #template-detail-scroll { height: 1fr; }
    MealPlanPanel #planner-items,
    MealPlanPanel #ideas-items,
    MealPlanPanel #template-items { height: 12; }
    MealPlanPanel #template-events { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        today = datetime.now().astimezone().date()
        self.week_start = today - timedelta(days=today.weekday())
        self.selected_date = today.isoformat()
        self.day_ids: list[str] = []
        self.event_ids: list[str] = []
        self.current_event_id: str | None = None
        self.item_ids: list[str] = []
        self.idea_ids: list[str] = []
        self.current_idea_id: str | None = None
        self.idea_item_ids: list[str] = []
        self.template_group_ids: list[str] = []
        self.current_template_group_id: str | None = None
        self.template_ids: list[str] = []
        self.current_template_id: str | None = None
        self.template_day_ids: list[str] = []
        self.current_template_day_id: str | None = None
        self.template_event_ids: list[str] = []
        self.current_template_event_id: str | None = None
        self.template_item_ids: list[str] = []

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="meal-planner-tab", id="meal-tabs"):
            with TabPane("Planner", id="meal-planner-tab"):
                with Horizontal(classes="toolbar"):
                    yield Button("← Week", id="meal-week-prev")
                    yield Button("Today", id="meal-week-today", variant="primary")
                    yield Button("Week →", id="meal-week-next")
                    yield Button("Add recipe", id="meal-add-recipe", variant="success")
                    yield Button("Add note", id="meal-add-note")
                    yield Button("Edit", id="meal-edit")
                    yield Button("Delete", id="meal-delete", variant="error")
                    yield Button("Move to queue", id="meal-to-queue")
                    yield Button("Save favorite", id="meal-save-favorite")
                    yield Button("Labels…", id="meal-labels")
                with Horizontal(id="planner-browser"):
                    with Vertical(id="planner-days-pane"):
                        yield Label("Week", id="meal-week-title")
                        yield DataTable(id="meal-days", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="planner-events-pane"):
                        yield Label("Meals", id="meal-selected-day-title")
                        yield DataTable(id="meal-events", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="template-detail-pane"):
                        yield Label("Entry details")
                        with VerticalScroll(id="planner-detail-scroll"):
                            yield Static("Select an entry", id="meal-event-detail", markup=False)
                        yield Label("Items")
                        yield DataTable(id="planner-items", cursor_type="row", zebra_stripes=True)
                        with Horizontal(classes="toolbar"):
                            yield Button("Add item", id="meal-item-new", variant="success")
                            yield Button("Edit item", id="meal-item-edit")
                            yield Button("Item ↑", id="meal-item-up")
                            yield Button("Item ↓", id="meal-item-down")
                            yield Button("Remove item", id="meal-item-delete", variant="error")

            with TabPane("Queue & Favorites", id="meal-ideas-tab"):
                with Horizontal(classes="toolbar"):
                    yield Button("Add recipe", id="idea-add-recipe", variant="success")
                    yield Button("Add note", id="idea-add-note")
                    yield Button("Edit", id="idea-edit")
                    yield Button("Delete", id="idea-delete", variant="error")
                    yield Button("Schedule", id="idea-schedule", variant="primary")
                    yield Button("Save favorite", id="idea-save-favorite")
                with Horizontal(id="ideas-browser"):
                    with Vertical(id="ideas-list-pane"):
                        yield Label("Queue & Favorites")
                        yield DataTable(id="meal-ideas", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="ideas-detail-pane"):
                        yield Label("Entry details")
                        with VerticalScroll(id="ideas-detail-scroll"):
                            yield Static("Select an entry", id="idea-detail", markup=False)
                        yield Label("Items")
                        yield DataTable(id="ideas-items", cursor_type="row", zebra_stripes=True)
                        with Horizontal(classes="toolbar"):
                            yield Button("Add item", id="idea-item-new", variant="success")
                            yield Button("Edit item", id="idea-item-edit")
                            yield Button("Item ↑", id="idea-item-up")
                            yield Button("Item ↓", id="idea-item-down")
                            yield Button("Remove item", id="idea-item-delete", variant="error")

            with TabPane("Templates", id="meal-templates-tab"):
                with Horizontal(classes="toolbar"):
                    yield Button("New group", id="template-group-new", variant="success")
                    yield Button("Delete group", id="template-group-delete", variant="error")
                    yield Button("New template", id="template-new", variant="success")
                    yield Button("Edit template", id="template-edit")
                    yield Button("Delete template", id="template-delete", variant="error")
                    yield Button("Use template", id="template-use", variant="primary")
                with Horizontal(id="template-top"):
                    with Vertical(id="template-groups-pane"):
                        yield Label("Template groups")
                        yield DataTable(id="template-groups", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="templates-pane"):
                        yield Label("Templates")
                        yield DataTable(id="templates", cursor_type="row", zebra_stripes=True)
                with Horizontal(classes="toolbar"):
                    yield Button("Add day", id="template-day-new", variant="success")
                    yield Button("Remove day", id="template-day-delete", variant="error")
                    yield Button("Add recipe", id="template-event-recipe", variant="success")
                    yield Button("Add note", id="template-event-note")
                    yield Button("Edit entry", id="template-event-edit")
                    yield Button("Delete entry", id="template-event-delete", variant="error")
                with Horizontal(id="template-bottom"):
                    with Vertical(id="template-days-pane"):
                        yield Label("Days")
                        yield DataTable(id="template-days", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="template-events-pane"):
                        yield Label("Entries")
                        yield DataTable(id="template-events", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="planner-detail-pane"):
                        yield Label("Template entry details")
                        with VerticalScroll(id="template-detail-scroll"):
                            yield Static(
                                "Select an entry", id="template-event-detail", markup=False
                            )
                        yield Label("Items")
                        yield DataTable(id="template-items", cursor_type="row", zebra_stripes=True)
                        with Horizontal(classes="toolbar"):
                            yield Button("Add item", id="template-item-new", variant="success")
                            yield Button("Edit item", id="template-item-edit")
                            yield Button("Item ↑", id="template-item-up")
                            yield Button("Item ↓", id="template-item-down")
                            yield Button("Remove item", id="template-item-delete", variant="error")

    def on_mount(self) -> None:
        self.query_one("#meal-days", DataTable).add_columns("Day", "Date", "Meals")
        self.query_one("#meal-events", DataTable).add_columns("Meal", "Label", "Recipe", "Items")
        self.query_one("#planner-items", DataTable).add_columns("Item", "Quantity", "Details")
        self.query_one("#meal-ideas", DataTable).add_columns(
            "Type", "Meal", "Label", "Recipe", "Items"
        )
        self.query_one("#ideas-items", DataTable).add_columns("Item", "Quantity", "Details")
        self.query_one("#template-groups", DataTable).add_columns("Group", "Templates", "Groups")
        self.query_one("#templates", DataTable).add_columns("Template", "Days")
        self.query_one("#template-days", DataTable).add_columns("Day", "Entries")
        self.query_one("#template-events", DataTable).add_columns("Meal", "Recipe", "Label")
        self.query_one("#template-items", DataTable).add_columns("Item", "Quantity", "Details")

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

    def _event_title(self, event: PBCalendarEvent) -> str:
        recipe_id = str(event.recipeId or "")
        recipe = self.client.state.recipes.get(recipe_id) if recipe_id else None
        return str(event.title or (recipe.name if recipe is not None else "") or "Untitled entry")

    def _event_recipe_name(self, event: PBCalendarEvent) -> str:
        recipe_id = str(event.recipeId or "")
        recipe = self.client.state.recipes.get(recipe_id) if recipe_id else None
        return str(recipe.name) if recipe is not None else ""

    def _event_label_name(self, event: PBCalendarEvent) -> str:
        label = self.client.state.meal_plan_labels.get(str(event.labelId or ""))
        return str(label.name) if label is not None else ""

    @staticmethod
    def _event_item_quantity(item: object) -> str:
        quantity = str(getattr(getattr(item, "quantityPb", None), "rawQuantity", "") or "")
        package = str(getattr(getattr(item, "packageSizePb", None), "rawPackageSize", "") or "")
        return " ".join(part for part in (quantity, package) if part)

    def _event_detail_text(self, event: PBCalendarEvent | None) -> str:
        if event is None:
            return "Select an entry"
        lines = [self._event_title(event)]
        metadata: list[str] = [self._event_type_name(event)]
        if event.date:
            metadata.append(str(event.date))
        label = self._event_label_name(event)
        if label:
            metadata.append(label)
        if bool(event.isLeftover):
            metadata.append("Leftovers")
        lines.append(" • ".join(metadata))
        recipe_id = str(event.recipeId or "")
        recipe = self.client.state.recipes.get(recipe_id) if recipe_id else None
        if recipe is not None:
            factor = float(event.recipeScaleFactor or 1.0)
            lines.extend(["", f"Recipe: {recipe.name}"])
            servings = recipe_servings_after_scaling(recipe, event)
            if servings:
                lines.append(f"Servings: {servings}")
            if factor != 1.0:
                lines.append(f"Scale: {factor:g}×")
            if recipe.sourceUrl:
                lines.append(f"Source: {recipe.sourceUrl}")
        if event.details:
            lines.extend(["", "Details:", str(event.details)])
        if event.eventListItems:
            lines.extend(["", "Items:"])
            for item in event.eventListItems:
                quantity = self._event_item_quantity(item)
                suffix = f" — {item.details}" if item.details else ""
                lines.append(f"  • {quantity + ' ' if quantity else ''}{item.name}{suffix}")
        return "\n".join(lines)

    def _calendar_events_for_date(self, value: str) -> list[PBCalendarEvent]:
        events = [
            event
            for event in self.client.state.meal_plan_events.values()
            if int(event.eventType) == int(PB.PBCalendarEventType.MealPlanCalendarEvent)
            and str(event.date or "") == value
        ]
        return sorted(
            events,
            key=lambda event: (
                int(event.labelSortIndex),
                int(event.orderAddedSortIndex),
                self._event_title(event).casefold(),
            ),
        )

    async def refresh_view(self) -> None:
        if self.client.meal_plan is None:
            return
        self._refresh_planner()
        self._refresh_ideas()
        self._refresh_templates()

    def _refresh_planner(self) -> None:
        day_table = self.query_one("#meal-days", DataTable)
        days = [self.week_start + timedelta(days=index) for index in range(7)]
        self.day_ids = [value.isoformat() for value in days]
        if self.selected_date not in self.day_ids:
            self.selected_date = self.day_ids[0]
        _replace_rows(
            day_table,
            (
                (
                    value.strftime("%a"),
                    value.strftime("%b %-d"),
                    len(self._calendar_events_for_date(value.isoformat())),
                )
                for value in days
            ),
            self.day_ids,
            keep_id=self.selected_date,
        )
        self.query_one("#meal-week-title", Label).update(
            f"Week of {self.week_start.strftime('%b %-d, %Y')}"
        )
        self._refresh_planner_events()

    def _refresh_planner_events(self) -> None:
        table = self.query_one("#meal-events", DataTable)
        old = self.current_event_id or _selected_id(table, self.event_ids)
        events = self._calendar_events_for_date(self.selected_date)
        self.event_ids = [str(event.identifier) for event in events]
        _replace_rows(
            table,
            (
                (
                    self._event_title(event),
                    self._event_label_name(event),
                    self._event_recipe_name(event),
                    len(event.eventListItems),
                )
                for event in events
            ),
            self.event_ids,
            keep_id=old,
        )
        self.current_event_id = _selected_id(table, self.event_ids)
        try:
            pretty = date.fromisoformat(self.selected_date).strftime("%A, %b %-d")
        except ValueError:
            pretty = self.selected_date
        self.query_one("#meal-selected-day-title", Label).update(pretty)
        self._refresh_planner_detail()

    def _planner_event(self) -> PBCalendarEvent | None:
        return (
            self.client.state.meal_plan_events.get(self.current_event_id)
            if self.current_event_id
            else None
        )

    def _refresh_planner_detail(self) -> None:
        event = self._planner_event()
        self.query_one("#meal-event-detail", Static).update(self._event_detail_text(event))
        table = self.query_one("#planner-items", DataTable)
        if event is None:
            self.item_ids = []
            table.clear()
            return
        old = _selected_id(table, self.item_ids)
        items = list(event.eventListItems)
        self.item_ids = [str(item.identifier) for item in items]
        _replace_rows(
            table,
            ((item.name, self._event_item_quantity(item), item.details) for item in items),
            self.item_ids,
            keep_id=old,
        )

    def _idea_events(self) -> list[PBCalendarEvent]:
        allowed = {
            int(PB.PBCalendarEventType.MealPlanQueueEvent),
            int(PB.PBCalendarEventType.MealPlanFavoriteEvent),
        }
        return sorted(
            (
                event
                for event in self.client.state.meal_plan_events.values()
                if int(event.eventType) in allowed
            ),
            key=lambda event: (
                int(event.eventType),
                int(event.labelSortIndex),
                int(event.orderAddedSortIndex),
                self._event_title(event).casefold(),
            ),
        )

    def _refresh_ideas(self) -> None:
        table = self.query_one("#meal-ideas", DataTable)
        old = self.current_idea_id or _selected_id(table, self.idea_ids)
        events = self._idea_events()
        self.idea_ids = [str(event.identifier) for event in events]
        _replace_rows(
            table,
            (
                (
                    self._event_type_name(event),
                    self._event_title(event),
                    self._event_label_name(event),
                    self._event_recipe_name(event),
                    len(event.eventListItems),
                )
                for event in events
            ),
            self.idea_ids,
            keep_id=old,
        )
        self.current_idea_id = _selected_id(table, self.idea_ids)
        self._refresh_idea_detail()

    def _idea_event(self) -> PBCalendarEvent | None:
        return (
            self.client.state.meal_plan_events.get(self.current_idea_id)
            if self.current_idea_id
            else None
        )

    def _refresh_idea_detail(self) -> None:
        event = self._idea_event()
        self.query_one("#idea-detail", Static).update(self._event_detail_text(event))
        table = self.query_one("#ideas-items", DataTable)
        if event is None:
            self.idea_item_ids = []
            table.clear()
            return
        old = _selected_id(table, self.idea_item_ids)
        items = list(event.eventListItems)
        self.idea_item_ids = [str(item.identifier) for item in items]
        _replace_rows(
            table,
            ((item.name, self._event_item_quantity(item), item.details) for item in items),
            self.idea_item_ids,
            keep_id=old,
        )

    def _template_parent_map(self) -> dict[str, str]:
        parent: dict[str, str] = {}
        for group_id, group in self.client.state.meal_plan_template_groups.items():
            for item in group.items:
                if int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Group):
                    parent[str(item.identifier)] = group_id
        return parent

    def _template_group_path(self, group_id: str) -> str:
        parents = self._template_parent_map()
        parts: list[str] = []
        current = group_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            group = self.client.state.meal_plan_template_groups.get(current)
            if group is None:
                break
            if group.name:
                parts.append(str(group.name))
            current = parents.get(current, "")
        return " / ".join(reversed(parts)) or "Templates"

    def _sorted_template_groups(self) -> list[PBMealPlanTemplateGroup]:
        groups = list(self.client.state.meal_plan_template_groups.values())
        return sorted(
            groups, key=lambda group: self._template_group_path(str(group.identifier)).casefold()
        )

    def _refresh_templates(self) -> None:
        group_table = self.query_one("#template-groups", DataTable)
        groups = self._sorted_template_groups()
        old_group = self.current_template_group_id or _selected_id(
            group_table, self.template_group_ids
        )
        self.template_group_ids = [str(group.identifier) for group in groups]
        _replace_rows(
            group_table,
            (
                (
                    self._template_group_path(str(group.identifier)),
                    len(
                        [
                            item
                            for item in group.items
                            if int(item.itemType)
                            == int(PB.PBMealPlanTemplateGroupItem.Type.Template)
                        ]
                    ),
                    len(
                        [
                            item
                            for item in group.items
                            if int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Group)
                        ]
                    ),
                )
                for group in groups
            ),
            self.template_group_ids,
            keep_id=old_group,
        )
        self.current_template_group_id = _selected_id(group_table, self.template_group_ids)
        self._refresh_template_list()

    def _current_template_group(self) -> PBMealPlanTemplateGroup | None:
        return (
            self.client.state.meal_plan_template_groups.get(self.current_template_group_id)
            if self.current_template_group_id
            else None
        )

    def _refresh_template_list(self) -> None:
        table = self.query_one("#templates", DataTable)
        old = self.current_template_id or _selected_id(table, self.template_ids)
        group = self._current_template_group()
        ids = []
        if group is not None:
            ids = [
                str(item.identifier)
                for item in group.items
                if int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Template)
                and str(item.identifier) in self.client.state.meal_plan_templates
            ]
        templates = [self.client.state.meal_plan_templates[value] for value in ids]
        templates.sort(key=lambda value: (int(value.sortIndex), str(value.name).casefold()))
        self.template_ids = [str(value.identifier) for value in templates]
        _replace_rows(
            table,
            ((value.name, len(value.dayIds)) for value in templates),
            self.template_ids,
            keep_id=old,
        )
        self.current_template_id = _selected_id(table, self.template_ids)
        self._refresh_template_days()

    def _current_template(self) -> PBMealPlanTemplate | None:
        return (
            self.client.state.meal_plan_templates.get(self.current_template_id)
            if self.current_template_id
            else None
        )

    def _refresh_template_days(self) -> None:
        table = self.query_one("#template-days", DataTable)
        old = self.current_template_day_id or _selected_id(table, self.template_day_ids)
        template = self._current_template()
        self.template_day_ids = list(template.dayIds) if template is not None else []
        event_counts = {
            day_id: sum(
                1
                for event in self.client.state.meal_plan_template_events.values()
                if str(event.templateId) == str(self.current_template_id or "")
                and str(event.templateDayId) == day_id
            )
            for day_id in self.template_day_ids
        }
        _replace_rows(
            table,
            (
                (f"Day {index + 1}", event_counts.get(day_id, 0))
                for index, day_id in enumerate(self.template_day_ids)
            ),
            self.template_day_ids,
            keep_id=old,
        )
        self.current_template_day_id = _selected_id(table, self.template_day_ids)
        self._refresh_template_events()

    def _refresh_template_events(self) -> None:
        table = self.query_one("#template-events", DataTable)
        old = self.current_template_event_id or _selected_id(table, self.template_event_ids)
        events = sorted(
            (
                event
                for event in self.client.state.meal_plan_template_events.values()
                if str(event.templateId) == str(self.current_template_id or "")
                and str(event.templateDayId) == str(self.current_template_day_id or "")
            ),
            key=lambda event: (int(event.orderAddedSortIndex), self._event_title(event).casefold()),
        )
        self.template_event_ids = [str(event.identifier) for event in events]
        _replace_rows(
            table,
            (
                (
                    self._event_title(event),
                    self._event_recipe_name(event),
                    self._event_label_name(event),
                )
                for event in events
            ),
            self.template_event_ids,
            keep_id=old,
        )
        self.current_template_event_id = _selected_id(table, self.template_event_ids)
        self._refresh_template_event_detail()

    def _refresh_template_event_detail(self) -> None:
        event = (
            self.client.state.meal_plan_template_events.get(self.current_template_event_id)
            if self.current_template_event_id
            else None
        )
        self.query_one("#template-event-detail", Static).update(self._event_detail_text(event))
        table = self.query_one("#template-items", DataTable)
        if event is None:
            self.template_item_ids = []
            table.clear()
            return
        old = _selected_id(table, self.template_item_ids)
        items = list(event.eventListItems)
        self.template_item_ids = [str(item.identifier) for item in items]
        _replace_rows(
            table,
            ((item.name, self._event_item_quantity(item), item.details) for item in items),
            self.template_item_ids,
            keep_id=old,
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table_id = event.data_table.id or ""
        if table_id == "meal-days":
            selected = _selected_id(event.data_table, self.day_ids)
            if selected and selected != self.selected_date:
                self.selected_date = selected
                self.current_event_id = None
                self._refresh_planner_events()
        elif table_id == "meal-events":
            self.current_event_id = _selected_id(event.data_table, self.event_ids)
            self._refresh_planner_detail()
        elif table_id == "meal-ideas":
            self.current_idea_id = _selected_id(event.data_table, self.idea_ids)
            self._refresh_idea_detail()
        elif table_id == "template-groups":
            selected = _selected_id(event.data_table, self.template_group_ids)
            if selected != self.current_template_group_id:
                self.current_template_group_id = selected
                self.current_template_id = None
                self._refresh_template_list()
        elif table_id == "templates":
            selected = _selected_id(event.data_table, self.template_ids)
            if selected != self.current_template_id:
                self.current_template_id = selected
                self.current_template_day_id = None
                self._refresh_template_days()
        elif table_id == "template-days":
            selected = _selected_id(event.data_table, self.template_day_ids)
            if selected != self.current_template_day_id:
                self.current_template_day_id = selected
                self.current_template_event_id = None
                self._refresh_template_events()
        elif table_id == "template-events":
            self.current_template_event_id = _selected_id(event.data_table, self.template_event_ids)
            self._refresh_template_event_detail()

    def _label_options(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (str(value.name), str(value.identifier))
            for value in sorted(
                self.client.state.meal_plan_labels.values(), key=lambda value: int(value.sortIndex)
            )
        )

    def _note_fields(
        self, event: PBCalendarEvent | None = None, *, include_date: bool = True
    ) -> list[FormField]:
        fields = [
            FormField("title", "Title", value=event.title if event is not None else ""),
            FormField(
                "details",
                "Details",
                kind="textarea",
                value=event.details if event is not None else "",
            ),
            FormField(
                "label",
                "Label",
                kind="select",
                value=str(event.labelId) if event is not None and event.labelId else None,
                options=self._label_options(),
            ),
        ]
        if include_date:
            fields.insert(
                2,
                FormField(
                    "date",
                    "Date",
                    value=event.date if event is not None and event.date else self.selected_date,
                    placeholder="YYYY-MM-DD; blank keeps it in the queue",
                ),
            )
        return fields

    def _recipe_fields(self, *, include_date: bool, include_kind: bool = False) -> list[FormField]:
        recipes = self.client.recipes.sorted() if self.client.recipes is not None else []
        fields = [
            FormField(
                "recipe",
                "Recipe",
                kind="select",
                options=tuple((str(value.name), str(value.identifier)) for value in recipes),
                value=str(recipes[0].identifier) if recipes else None,
                allow_blank=False,
            ),
            FormField("label", "Label", kind="select", options=self._label_options(), value=None),
            FormField(
                "scale",
                "Scale factor",
                value="",
                placeholder="Optional, e.g. 0.5, 1, 2",
            ),
        ]
        if include_date:
            fields.insert(
                1, FormField("date", "Date", value=self.selected_date, placeholder="YYYY-MM-DD")
            )
        if include_kind:
            fields.insert(
                1,
                FormField(
                    "kind",
                    "Save to",
                    kind="select",
                    value="queue",
                    options=(("Queue", "queue"), ("Favorites", "favorite")),
                    allow_blank=False,
                ),
            )
        return fields

    def _validate_date(self, raw: str, *, allow_blank: bool = False) -> str:
        value = raw.strip()
        if not value and allow_blank:
            return ""
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("Date must use YYYY-MM-DD") from exc

    def _new_recipe_event(
        self,
        recipe_id: str,
        *,
        event_type: int,
        date_value: str = "",
        label_id: str = "",
        scale: float | None = None,
        template_id: str = "",
        template_day_id: str = "",
    ) -> PBCalendarEvent:
        recipe = self.client.state.recipes.get(recipe_id)
        if recipe is None:
            raise ValueError("Recipe is no longer available")
        event = PB.PBCalendarEvent(eventType=event_type, recipeId=recipe_id)
        effective_scale = scale if scale is not None else effective_recipe_scale_factor(recipe)
        if effective_scale:
            event.recipeScaleFactor = effective_scale
        if date_value:
            event.date = date_value
        if label_id:
            event.labelId = label_id
        if template_id:
            event.templateId = template_id
        if template_day_id:
            event.templateDayId = template_day_id
        return event

    @staticmethod
    def _copy_event(
        source: PBCalendarEvent, *, event_type: int, date_value: str = ""
    ) -> PBCalendarEvent:
        event = PB.PBCalendarEvent(eventType=event_type)
        if source.recipeId:
            event.recipeId = source.recipeId
        if float(source.recipeScaleFactor or 1.0) != 1.0:
            event.recipeScaleFactor = source.recipeScaleFactor
        if source.isLeftover:
            event.isLeftover = True
        if source.title:
            event.title = source.title
        if source.details:
            event.details = source.details
        if source.HasField("icon"):
            event.icon.CopyFrom(source.icon)
        if source.labelId:
            event.labelId = source.labelId
        if date_value:
            event.date = date_value
        for item in source.eventListItems:
            copied = event.eventListItems.add(identifier=uuid4().hex)
            copied.name = item.name
            copied.details = item.details
            if item.HasField("quantityPb"):
                copied.quantityPb.CopyFrom(item.quantityPb)
            if item.HasField("packageSizePb"):
                copied.packageSizePb.CopyFrom(item.packageSizePb)
        return event

    async def _create_recipe_event(self, result: FormResult, *, mode: str) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        recipe_id = cast(str | None, result.get("recipe"))
        if not recipe_id:
            return self.error("Select a recipe")
        label_id = cast(str | None, result.get("label")) or ""
        scale_text = str(result.get("scale") or "").strip()
        scale = None
        if scale_text:
            try:
                scale = float(scale_text)
            except ValueError as exc:
                raise ValueError("Scale factor must be a number") from exc
            if scale <= 0:
                raise ValueError("Scale factor must be greater than zero")
        if mode == "planner":
            date_value = self._validate_date(str(result.get("date") or ""))
            event_type = PB.PBCalendarEventType.MealPlanCalendarEvent
        elif mode == "template":
            date_value = ""
            event_type = PB.PBCalendarEventType.MealPlanTemplateEvent
        else:
            date_value = ""
            kind = cast(str | None, result.get("kind")) or "queue"
            event_type = (
                PB.PBCalendarEventType.MealPlanFavoriteEvent
                if kind == "favorite"
                else PB.PBCalendarEventType.MealPlanQueueEvent
            )
        event = self._new_recipe_event(
            recipe_id,
            event_type=event_type,
            date_value=date_value,
            label_id=label_id,
            scale=scale,
            template_id=str(self.current_template_id or "") if mode == "template" else "",
            template_day_id=str(self.current_template_day_id or "") if mode == "template" else "",
        )
        created = await service.save_event(event)
        if mode == "planner":
            self.current_event_id = str(created.identifier)
        elif mode == "template":
            self.current_template_event_id = str(created.identifier)
        else:
            self.current_idea_id = str(created.identifier)
        await self.refresh_view()

    async def _create_note_event(
        self, result: FormResult, *, mode: str, event_type: int | None = None
    ) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        title = str(result.get("title") or "").strip()
        if not title:
            return self.error("Title is required")
        label_id = cast(str | None, result.get("label")) or ""
        if mode == "planner":
            date_value = self._validate_date(str(result.get("date") or ""))
            resolved_type = PB.PBCalendarEventType.MealPlanCalendarEvent
        elif mode == "template":
            date_value = ""
            resolved_type = PB.PBCalendarEventType.MealPlanTemplateEvent
        else:
            date_value = ""
            resolved_type = event_type or PB.PBCalendarEventType.MealPlanQueueEvent
        event = PB.PBCalendarEvent(eventType=resolved_type, title=title)
        details = str(result.get("details") or "")
        if details:
            event.details = details
        if label_id:
            event.labelId = label_id
        if date_value:
            event.date = date_value
        if mode == "template":
            event.templateId = str(self.current_template_id or "")
            event.templateDayId = str(self.current_template_day_id or "")
        created = await service.save_event(event)
        if mode == "planner":
            self.current_event_id = str(created.identifier)
        elif mode == "template":
            self.current_template_event_id = str(created.identifier)
        else:
            self.current_idea_id = str(created.identifier)
        await self.refresh_view()

    def _edit_event_form(
        self,
        event: PBCalendarEvent,
        *,
        include_date: bool,
        callback: Callable[[FormResult], Awaitable[None]],
    ) -> None:
        self.form(
            "Edit meal-plan entry", self._note_fields(event, include_date=include_date), callback
        )

    async def _apply_event_edit(
        self, event: PBCalendarEvent, result: FormResult, *, include_date: bool
    ) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        event_id = str(event.identifier)
        changed = False
        title = str(result.get("title") or "").strip()
        if title != str(event.title or ""):
            await service.set_event_title(event_id, title, flush=False)
            changed = True
        details = str(result.get("details") or "")
        if details != str(event.details or ""):
            await service.set_event_details(event_id, details, flush=False)
            changed = True
        label_id = cast(str | None, result.get("label")) or ""
        if label_id != str(event.labelId or ""):
            await service.set_event_label(event_id, label_id, flush=False)
            changed = True
        if include_date:
            date_value = self._validate_date(str(result.get("date") or ""), allow_blank=True)
            if date_value != str(event.date or ""):
                await service.set_event_date([event_id], date_value or None, flush=False)
                changed = True
        if changed:
            await service.flush()
        await self.refresh_view()

    def _event_item_fields(self, item: object | None = None) -> list[FormField]:
        current_quantity = self._event_item_quantity(item) if item is not None else ""
        return [
            FormField("name", "Name", value=getattr(item, "name", "")),
            FormField("details", "Details", kind="textarea", value=getattr(item, "details", "")),
            FormField(
                "quantity",
                "Quantity / package size",
                placeholder=(
                    f"Current: {current_quantity} — leave blank to keep"
                    if current_quantity
                    else "e.g. 2, 1 lb, or 2 cans (14 oz)"
                ),
            ),
        ]

    async def _edit_event_item(
        self, event_id: str, item_id: str, item: object, result: FormResult
    ) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        name = str(result.get("name") or "").strip()
        if not name:
            return self.error("Item name is required")
        changed = False
        if name != str(getattr(item, "name", "")):
            await service.set_event_list_item_name(event_id, item_id, name, flush=False)
            changed = True
        details = str(result.get("details") or "")
        if details != str(getattr(item, "details", "")):
            await service.set_event_list_item_details(event_id, item_id, details, flush=False)
            changed = True
        quantity_text = str(result.get("quantity") or "").strip()
        if quantity_text:
            parsed = parse_quantity_and_package_size(quantity_text)
            if parsed is None:
                return self.error("Could not understand that quantity/package size")
            if parsed.HasField("quantityPb"):
                await service.set_event_list_item_quantity(
                    event_id, item_id, parsed.quantityPb, flush=False
                )
                changed = True
            if parsed.HasField("packageSizePb"):
                await service.set_event_list_item_package_size(
                    event_id, item_id, parsed.packageSizePb, flush=False
                )
                changed = True
        if changed:
            await service.flush()
        await self.refresh_view()

    async def _handle_event_item_action(
        self,
        button_id: str,
        event: PBCalendarEvent,
        *,
        prefix: str,
        table_selector: str,
        item_ids: Sequence[str],
    ) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        event_id = str(event.identifier)
        item_id = _selected_id(self.query_one(table_selector, DataTable), item_ids)
        current_item = next(
            (item for item in event.eventListItems if str(item.identifier) == str(item_id or "")),
            None,
        )

        if button_id == f"{prefix}-new":

            async def create_item(result: FormResult) -> None:
                name = str(result.get("name") or "").strip()
                if not name:
                    return self.error("Item name is required")
                item = PB.PBCalendarEventListItem(
                    name=name, details=str(result.get("details") or "")
                )
                quantity_text = str(result.get("quantity") or "").strip()
                parsed = parse_quantity_and_package_size(quantity_text) if quantity_text else None
                if quantity_text and parsed is None:
                    return self.error("Could not understand that quantity/package size")
                if parsed is not None and parsed.HasField("quantityPb"):
                    item.quantityPb.CopyFrom(parsed.quantityPb)
                if parsed is not None and parsed.HasField("packageSizePb"):
                    item.packageSizePb.CopyFrom(parsed.packageSizePb)
                await service.add_event_list_item(event_id, item)
                await self.refresh_view()

            self.form(
                "Add meal-plan item",
                self._event_item_fields(),
                create_item,
                submit_label="Add",
            )
            return

        if current_item is None:
            self.error("Select an item")
            return

        selected_item = current_item
        if button_id == f"{prefix}-edit":

            async def edit_selected_item(result: FormResult) -> None:
                await self._edit_event_item(
                    event_id, str(selected_item.identifier), selected_item, result
                )

            self.form(
                "Edit meal-plan item",
                self._event_item_fields(selected_item),
                edit_selected_item,
            )
        elif button_id == f"{prefix}-delete":

            async def delete_item() -> None:
                await service.remove_event_list_item(event_id, str(selected_item.identifier))
                await self.refresh_view()

            self.confirm(
                "Remove meal-plan item",
                f"Remove “{selected_item.name}”?",
                delete_item,
                confirm_label="Remove",
            )
        elif button_id in {f"{prefix}-up", f"{prefix}-down"}:
            ordered = [str(item.identifier) for item in event.eventListItems]
            index = ordered.index(str(selected_item.identifier))
            new_index = index - 1 if button_id == f"{prefix}-up" else index + 1
            if 0 <= new_index < len(ordered):
                ordered[index], ordered[new_index] = ordered[new_index], ordered[index]
                await service.reorder_event_list_items(event_id, ordered)
                await self.refresh_view()

    async def _ensure_template_root(self) -> str:
        service = self.client.meal_plan
        if service is None:
            raise RuntimeError("Meal plan service is unavailable")
        groups = self.client.state.meal_plan_template_groups
        if groups:
            parents = self._template_parent_map()
            roots = [group_id for group_id in groups if group_id not in parents]
            if roots:
                return roots[0]
        created = await service.create_root_template_group()
        return str(created.identifier)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        service = self.client.meal_plan
        if service is None:
            return
        button_id = event.button.id or ""

        if button_id == "meal-week-prev":
            self.week_start -= timedelta(days=7)
            self.selected_date = self.week_start.isoformat()
            self.current_event_id = None
            self._refresh_planner()
        elif button_id == "meal-week-next":
            self.week_start += timedelta(days=7)
            self.selected_date = self.week_start.isoformat()
            self.current_event_id = None
            self._refresh_planner()
        elif button_id == "meal-week-today":
            today = datetime.now().astimezone().date()
            self.week_start = today - timedelta(days=today.weekday())
            self.selected_date = today.isoformat()
            self.current_event_id = None
            self._refresh_planner()
        elif button_id == "meal-add-recipe":
            if not self.client.state.recipes:
                return self.error("No recipes are available")
            self.form(
                "Add recipe to meal plan",
                self._recipe_fields(include_date=True),
                lambda result: self._create_recipe_event(result, mode="planner"),
                submit_label="Add",
            )
        elif button_id == "meal-add-note":
            self.form(
                "Add meal-plan note",
                self._note_fields(None, include_date=True),
                lambda result: self._create_note_event(result, mode="planner"),
                submit_label="Add",
            )
        elif button_id == "meal-edit":
            selected = self._planner_event()
            if selected is None:
                return self.error("Select an entry")
            selected_event = selected

            async def edit_selected(result: FormResult) -> None:
                await self._apply_event_edit(selected_event, result, include_date=True)

            self._edit_event_form(
                selected_event,
                include_date=True,
                callback=edit_selected,
            )
        elif button_id == "meal-delete":
            selected = self._planner_event()
            if selected is None:
                return self.error("Select an entry")
            selected_event = selected

            async def delete() -> None:
                await service.delete_event(str(selected_event.identifier))
                self.current_event_id = None
                await self.refresh_view()

            self.confirm(
                "Delete meal-plan entry", f"Delete “{self._event_title(selected_event)}”?", delete
            )
        elif button_id == "meal-to-queue":
            selected = self._planner_event()
            if selected is None:
                return self.error("Select an entry")
            await service.set_event_date([str(selected.identifier)], None)
            self.current_event_id = None
            await self.refresh_view()
        elif button_id == "meal-save-favorite":
            selected = self._planner_event()
            if selected is None:
                return self.error("Select an entry")
            favorite = self._copy_event(
                selected, event_type=PB.PBCalendarEventType.MealPlanFavoriteEvent
            )
            await service.save_event(favorite)
            await self.refresh_view()
            self.tui.notify("Saved to Favorites")
        elif button_id == "meal-labels":
            self.app.push_screen(LabelsScreen())
        elif button_id in {
            "meal-item-new",
            "meal-item-edit",
            "meal-item-delete",
            "meal-item-up",
            "meal-item-down",
        }:
            selected = self._planner_event()
            if selected is None:
                return self.error("Select an entry")
            await self._handle_event_item_action(
                button_id,
                selected,
                prefix="meal-item",
                table_selector="#planner-items",
                item_ids=self.item_ids,
            )

        elif button_id == "idea-add-recipe":
            if not self.client.state.recipes:
                return self.error("No recipes are available")
            self.form(
                "Save recipe for later",
                self._recipe_fields(include_date=False, include_kind=True),
                lambda result: self._create_recipe_event(result, mode="idea"),
                submit_label="Save",
            )
        elif button_id == "idea-add-note":
            fields = self._note_fields(None, include_date=False)
            fields.append(
                FormField(
                    "kind",
                    "Save to",
                    kind="select",
                    value="queue",
                    options=(("Queue", "queue"), ("Favorites", "favorite")),
                    allow_blank=False,
                )
            )

            async def create_idea_note(result: FormResult) -> None:
                kind = cast(str | None, result.get("kind")) or "queue"
                event_type = (
                    PB.PBCalendarEventType.MealPlanFavoriteEvent
                    if kind == "favorite"
                    else PB.PBCalendarEventType.MealPlanQueueEvent
                )
                await self._create_note_event(result, mode="idea", event_type=event_type)

            self.form("Save meal idea", fields, create_idea_note, submit_label="Save")
        elif button_id == "idea-edit":
            selected = self._idea_event()
            if selected is None:
                return self.error("Select an entry")
            selected_event = selected

            async def edit_idea(result: FormResult) -> None:
                await self._apply_event_edit(selected_event, result, include_date=False)

            self._edit_event_form(
                selected_event,
                include_date=False,
                callback=edit_idea,
            )
        elif button_id == "idea-delete":
            selected = self._idea_event()
            if selected is None:
                return self.error("Select an entry")
            selected_event = selected

            async def delete_idea() -> None:
                await service.delete_event(str(selected_event.identifier))
                self.current_idea_id = None
                await self.refresh_view()

            self.confirm(
                "Delete saved meal", f"Delete “{self._event_title(selected_event)}”?", delete_idea
            )
        elif button_id == "idea-schedule":
            selected = self._idea_event()
            if selected is None:
                return self.error("Select an entry")
            if int(selected.eventType) == int(PB.PBCalendarEventType.MealPlanQueueEvent):
                await service.set_event_date([str(selected.identifier)], self.selected_date)
            else:
                scheduled = self._copy_event(
                    selected,
                    event_type=PB.PBCalendarEventType.MealPlanCalendarEvent,
                    date_value=self.selected_date,
                )
                await service.save_event(scheduled)
            await self.refresh_view()
            self.tui.notify(f"Scheduled for {self.selected_date}")
        elif button_id == "idea-save-favorite":
            selected = self._idea_event()
            if selected is None:
                return self.error("Select an entry")
            if int(selected.eventType) == int(PB.PBCalendarEventType.MealPlanFavoriteEvent):
                return self.tui.notify("This entry is already a Favorite")
            favorite = self._copy_event(
                selected, event_type=PB.PBCalendarEventType.MealPlanFavoriteEvent
            )
            await service.save_event(favorite)
            await self.refresh_view()
            self.tui.notify("Saved to Favorites")
        elif button_id in {
            "idea-item-new",
            "idea-item-edit",
            "idea-item-delete",
            "idea-item-up",
            "idea-item-down",
        }:
            selected = self._idea_event()
            if selected is None:
                return self.error("Select an entry")
            await self._handle_event_item_action(
                button_id,
                selected,
                prefix="idea-item",
                table_selector="#ideas-items",
                item_ids=self.idea_item_ids,
            )

        elif button_id == "template-group-new":
            parent_id = self.current_template_group_id or await self._ensure_template_root()

            async def create_group(result: FormResult) -> None:
                name = str(result.get("name") or "").strip()
                if not name:
                    return self.error("Group name is required")
                created = await service.create_template_group(
                    name, parent_id, icon=str(result.get("icon") or "") or None
                )
                self.current_template_group_id = str(created.identifier)
                await self.refresh_view()

            self.form(
                "New template group",
                [FormField("name", "Group name"), FormField("icon", "Icon name")],
                create_group,
                submit_label="Create",
            )
        elif button_id == "template-group-delete":
            group_id = self.current_template_group_id
            if not group_id:
                return self.error("Select a template group")
            parent_group_id = self._template_parent_map().get(group_id)
            if not parent_group_id:
                return self.error("The root template group cannot be deleted")
            group = self.client.state.meal_plan_template_groups[group_id]

            async def delete_group() -> None:
                await service.delete_template_group(group_id, parent_group_id)
                self.current_template_group_id = parent_group_id
                await self.refresh_view()

            self.confirm(
                "Delete template group",
                f"Delete “{group.name}” and its nested templates/groups?",
                delete_group,
            )
        elif button_id == "template-new":
            parent_id = self.current_template_group_id or await self._ensure_template_root()

            async def create_template(result: FormResult) -> None:
                name = str(result.get("name") or "").strip()
                if not name:
                    return self.error("Template name is required")
                template = PB.PBMealPlanTemplate(name=name)
                icon = str(result.get("icon") or "").strip()
                if icon:
                    template.icon.iconName = icon
                created = await service.save_template(template, parent_group_id=parent_id)
                self.current_template_group_id = parent_id
                self.current_template_id = str(created.identifier)
                await self.refresh_view()

            self.form(
                "New meal-plan template",
                [FormField("name", "Template name"), FormField("icon", "Icon name")],
                create_template,
                submit_label="Create",
            )
        elif button_id == "template-edit":
            template = self._current_template()
            if template is None:
                return self.error("Select a template")
            selected_template = template

            async def edit_template(result: FormResult) -> None:
                name = str(result.get("name") or "").strip()
                icon = str(result.get("icon") or "").strip()
                if not name:
                    return self.error("Template name is required")
                if name != selected_template.name:
                    await service.set_template_name(
                        str(selected_template.identifier), name, flush=False
                    )
                current_icon = (
                    str(selected_template.icon.iconName or "")
                    if selected_template.HasField("icon")
                    else ""
                )
                if icon and icon != current_icon:
                    await service.set_template_icon(
                        str(selected_template.identifier), icon, flush=False
                    )
                await service.flush()
                await self.refresh_view()

            self.form(
                "Edit meal-plan template",
                [
                    FormField("name", "Template name", value=selected_template.name),
                    FormField(
                        "icon",
                        "Icon name",
                        value=(
                            selected_template.icon.iconName
                            if selected_template.HasField("icon")
                            else ""
                        ),
                    ),
                ],
                edit_template,
            )
        elif button_id == "template-delete":
            template = self._current_template()
            if template is None:
                return self.error("Select a template")
            template_id = str(template.identifier)

            async def delete_template() -> None:
                await service.delete_template(template_id)
                self.current_template_id = None
                await self.refresh_view()

            self.confirm("Delete template", f"Delete “{template.name}”?", delete_template)
        elif button_id == "template-use":
            template = self._current_template()
            if template is None:
                return self.error("Select a template")
            selected_template = template

            async def use_template(result: FormResult) -> None:
                start = date.fromisoformat(self._validate_date(str(result.get("date") or "")))
                events: list[PBCalendarEvent] = []
                for index, day_id in enumerate(selected_template.dayIds):
                    target_date = (start + timedelta(days=index)).isoformat()
                    for source in self.client.state.meal_plan_template_events.values():
                        if str(source.templateId) != str(selected_template.identifier) or str(
                            source.templateDayId
                        ) != str(day_id):
                            continue
                        events.append(
                            self._copy_event(
                                source,
                                event_type=PB.PBCalendarEventType.MealPlanCalendarEvent,
                                date_value=target_date,
                            )
                        )
                if not events:
                    return self.error("This template has no entries")
                await service.save_events(events)
                self.week_start = start - timedelta(days=start.weekday())
                self.selected_date = start.isoformat()
                await self.refresh_view()
                self.tui.notify(f"Added {len(events)} template entries")

            self.form(
                f"Use {selected_template.name}",
                [
                    FormField(
                        "date", "Start date", value=self.selected_date, placeholder="YYYY-MM-DD"
                    )
                ],
                use_template,
                submit_label="Add to plan",
            )
        elif button_id == "template-day-new":
            template = self._current_template()
            if template is None:
                return self.error("Select a template")
            day_id = uuid4().hex
            await service.add_template_day_ids(str(template.identifier), [day_id])
            self.current_template_day_id = day_id
            await self.refresh_view()
        elif button_id == "template-day-delete":
            template = self._current_template()
            selected_day_id = self.current_template_day_id
            if template is None or not selected_day_id:
                return self.error("Select a template day")
            selected_template = template
            day_number = list(selected_template.dayIds).index(selected_day_id) + 1

            async def delete_day() -> None:
                await service.remove_template_day_ids(
                    str(selected_template.identifier), [selected_day_id]
                )
                self.current_template_day_id = None
                await self.refresh_view()

            self.confirm(
                "Remove template day",
                f"Remove Day {day_number} and all entries on that day?",
                delete_day,
                confirm_label="Remove",
            )
        elif button_id == "template-event-recipe":
            if not self.current_template_id or not self.current_template_day_id:
                return self.error("Select a template day")
            if not self.client.state.recipes:
                return self.error("No recipes are available")
            self.form(
                "Add recipe to template",
                self._recipe_fields(include_date=False),
                lambda result: self._create_recipe_event(result, mode="template"),
                submit_label="Add",
            )
        elif button_id == "template-event-note":
            if not self.current_template_id or not self.current_template_day_id:
                return self.error("Select a template day")
            self.form(
                "Add note to template",
                self._note_fields(None, include_date=False),
                lambda result: self._create_note_event(result, mode="template"),
                submit_label="Add",
            )
        elif button_id == "template-event-edit":
            selected = (
                self.client.state.meal_plan_template_events.get(self.current_template_event_id)
                if self.current_template_event_id
                else None
            )
            if selected is None:
                return self.error("Select a template entry")
            selected_event = selected

            async def edit_template_event(result: FormResult) -> None:
                await self._apply_event_edit(selected_event, result, include_date=False)

            self._edit_event_form(
                selected_event,
                include_date=False,
                callback=edit_template_event,
            )
        elif button_id == "template-event-delete":
            selected = (
                self.client.state.meal_plan_template_events.get(self.current_template_event_id)
                if self.current_template_event_id
                else None
            )
            if selected is None:
                return self.error("Select a template entry")
            selected_event = selected

            async def delete_template_event() -> None:
                await service.delete_event(str(selected_event.identifier))
                self.current_template_event_id = None
                await self.refresh_view()

            self.confirm(
                "Delete template entry",
                f"Delete “{self._event_title(selected_event)}”?",
                delete_template_event,
            )
        elif button_id in {
            "template-item-new",
            "template-item-edit",
            "template-item-delete",
            "template-item-up",
            "template-item-down",
        }:
            selected = (
                self.client.state.meal_plan_template_events.get(self.current_template_event_id)
                if self.current_template_event_id
                else None
            )
            if selected is None:
                return self.error("Select a template entry")
            await self._handle_event_item_action(
                button_id,
                selected,
                prefix="template-item",
                table_selector="#template-items",
                item_ids=self.template_item_ids,
            )


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
            "External-account actions such as sharing/email, Alexa, recipe web import, and",
            "account changes are intentionally left out of this example client.",
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
