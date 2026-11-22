from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from aioanylist import AnyListClient
from aioanylist.proto import PB
from aioanylist.types import AuthTokens

widgets = pytest.importorskip("textual.widgets")
spec = importlib.util.spec_from_file_location(
    "anylist_tui_layout_test", Path(__file__).parents[1] / "examples" / "anylist_tui.py"
)
assert spec is not None and spec.loader is not None
tui = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tui
spec.loader.exec_module(tui)


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [100, 180])
@pytest.mark.parametrize("populated", [False, True])
async def test_split_tables_fill_their_proportioned_panes(width, populated, monkeypatch):
    client = AnyListClient(tokens=AuthTokens("user", "access", "refresh"))
    if populated:
        client.state.shopping_lists["list"] = PB.ShoppingList(identifier="list", name="Groceries")
        client.state.list_stores["list"] = {"store": PB.PBStore(identifier="store", name="Market")}
    monkeypatch.setattr(tui, "_save_token_cache", lambda *args: None)
    app = tui.AnyListTUI(client, "test", Path("unused-token-cache"))

    def check_split(container, left_table, right_table, left_fraction):
        left = app.screen.query_one(f"DataTable{left_table}")
        right = app.screen.query_one(f"DataTable{right_table}")
        available = app.screen.query_one(container).content_size.width
        assert left.region.width == left.parent.content_size.width
        assert right.region.width == right.parent.content_size.width
        assert abs(left.region.width - available * left_fraction) <= 1
        assert abs(left.region.width + right.region.width - available) <= 1

    try:
        async with app.run_test(size=(width, 45)) as pilot:
            check_split("#lists-tables", "#lists-table", "#items-table", 0.38)
            await app.push_screen(tui.ListSettingsScreen("list"))
            await pilot.pause()
            check_split("#metadata-tables", "#stores-table", "#categories-table", 0.44)
            app.screen.query_one(widgets.TabbedContent).active = "saved-items"
            await pilot.pause()
            check_split("#saved-tables", "#saved-lists", "#saved-items", 0.42)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["shopping", "saved", "meal"])
@pytest.mark.parametrize("quantity", ["2", "", "nonsense"])
async def test_item_edit_validates_quantity_before_mutating(kind, quantity, monkeypatch):
    client = AnyListClient(tokens=AuthTokens("user", "access", "refresh"))
    client.state.shopping_lists["list"] = PB.ShoppingList(
        identifier="list", name="Groceries", items=[PB.ListItem(identifier="item", name="Original")]
    )
    client.state.starter_lists["saved"] = PB.StarterList(
        identifier="saved", name="Custom", items=[PB.ListItem(identifier="item", name="Original")]
    )
    client.state.meal_plan_events["event"] = PB.PBCalendarEvent(
        identifier="event",
        eventListItems=[PB.PBCalendarEventListItem(identifier="item", name="Original")],
    )
    monkeypatch.setattr(tui, "_save_token_cache", lambda *args: None)
    app = tui.AnyListTUI(client, "test", Path("unused-token-cache"))
    result = {"name": "Edited", "details": "New details", "quantity": quantity}
    try:
        async with app.run_test() as pilot:
            if kind == "shopping":
                panel = app.query_one(tui.ListsPanel)
                service = client.lists
                item = client.state.shopping_lists["list"].items[0]
                edit = lambda: panel._edit_item("item", result)
            elif kind == "meal":
                panel = app.query_one(tui.MealPlanPanel)
                service = client.meal_plan
                item = client.state.meal_plan_events["event"].eventListItems[0]
                edit = lambda: panel._edit_event_item("event", "item", item, result)
            else:
                await app.push_screen(tui.ListSettingsScreen("list"))
                await pilot.pause()
                panel = app.screen.query_one(tui.SavedItemsPanel)
                service = client.starter_lists
                item = client.state.starter_lists["saved"].items[0]
                form = Mock()
                monkeypatch.setattr(panel, "form", form)
                await panel.on_button_pressed(
                    widgets.Button.Pressed(widgets.Button(id="saved-edit-item"))
                )
                handler = form.call_args.args[2]
                edit = lambda: handler(result)
            monkeypatch.setattr(service, "flush", AsyncMock())
            monkeypatch.setattr(panel, "refresh_view", AsyncMock())
            error = Mock()
            monkeypatch.setattr(panel, "error", error)
            before = item.SerializeToString()
            await edit()
            queues = [service.queue]
            if kind == "shopping":
                queues.append(service.legacy_queue)
            if quantity == "nonsense":
                error.assert_called_once_with("Could not understand that quantity/package size")
                assert item.SerializeToString() == before
                assert all(queue.pending_count == 0 for queue in queues)
                service.flush.assert_not_awaited()
                panel.refresh_view.assert_not_awaited()
            else:
                error.assert_not_called()
                assert item.name == "Edited"
                assert item.details == "New details"
                assert bool(item.HasField("quantityPb")) == bool(quantity)
                service.flush.assert_awaited_once()
                panel.refresh_view.assert_awaited_once()
    finally:
        await client.close()
