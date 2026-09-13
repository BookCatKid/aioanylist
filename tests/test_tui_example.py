from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("textual")

from anylist_sdk import AnyListClient


def _load_tui_module() -> object:
    path = Path(__file__).parents[1] / "examples" / "anylist_tui.py"
    spec = importlib.util.spec_from_file_location("anylist_tui_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_tui_mounts_and_switches_tabs_without_authentication(tmp_path: Path) -> None:
    """The example should be renderable without making any network request."""

    tui = _load_tui_module()
    client = AnyListClient()
    app = tui.AnyListTUI(client, "", tmp_path / "tokens.json")
    try:
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            tabs = app.query_one("#tabs", tui.TabbedContent)
            assert len(app.query(tui.SDKPanel)) == 8
            assert len(app.query(tui.DataTable)) == 15
            assert tabs.active == "shopping"

            await pilot.press("8")
            await pilot.pause()
            assert tabs.active == "status"

            await pilot.press("3")
            await pilot.pause()
            assert tabs.active == "folders"
    finally:
        await client.close()


def test_tui_token_cache_round_trip_is_private(tmp_path: Path) -> None:
    tui = _load_tui_module()
    tokens = tui.AuthTokens(
        user_id="test-user",
        access_token="test-access",
        refresh_token="test-refresh",
        is_premium_user=True,
        user_locale="en-US",
    )
    path = tmp_path / "tokens.json"

    tui._save_token_cache(path, "test@example.invalid", tokens)

    assert path.stat().st_mode & 0o777 == 0o600
    assert tui._load_token_cache(path) == ("test@example.invalid", tokens)
