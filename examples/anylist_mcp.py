"""Small MCP server example backed by one long-lived AnyList SDK session.

Run from a source checkout with:

    python -m pip install -e '.[mcp]'
    mcp dev examples/anylist_mcp.py

Set ANYLIST_EMAIL and ANYLIST_PASSWORD in the environment first. The example is
deliberately small: it demonstrates the integration pattern without making MCP a
dependency of the core SDK or attempting to prescribe one universal tool taxonomy.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from anylist_sdk import AnyListClient


@dataclass
class AppContext:
    client: AnyListClient


@asynccontextmanager
async def lifespan(_: MCPServer) -> AsyncIterator[AppContext]:
    email = os.environ["ANYLIST_EMAIL"]
    password = os.environ["ANYLIST_PASSWORD"]
    client = AnyListClient()
    try:
        await client.sign_in(email, password)
        # MCP tools generally need synchronized account data, not the static grocery-tag
        # resources used by interactive autocomplete/categorization UIs.
        await client.load(load_tag_data=False)
        yield AppContext(client=client)
    finally:
        await client.close()


mcp = MCPServer(
    "AnyList",
    description="Shopping-list and recipe tools backed by anylist-sdk",
    lifespan=lifespan,
)


def _client(ctx: Context[AppContext]) -> AnyListClient:
    return ctx.request_context.lifespan_context.client


def _message_dict(message: Any) -> dict[str, Any]:
    return MessageToDict(message, preserving_proto_field_name=True)


@mcp.tool()
async def refresh(ctx: Context[AppContext]) -> dict[str, int]:
    """Catch up synchronized AnyList state and report high-level object counts."""
    client = _client(ctx)
    await client.refresh()
    assert client.lists is not None
    assert client.recipes is not None
    return {
        "shopping_lists": len(client.lists.all()),
        "recipes": len(client.recipes.all()),
    }


@mcp.tool()
async def list_shopping_lists(ctx: Context[AppContext]) -> list[dict[str, Any]]:
    """List synchronized shopping lists with IDs, names, and item counts."""
    client = _client(ctx)
    assert client.lists is not None
    return [
        {
            "id": str(shopping_list.identifier),
            "name": str(shopping_list.name),
            "item_count": len(shopping_list.items),
        }
        for shopping_list in client.lists.all()
    ]


@mcp.tool()
async def get_shopping_list(list_id: str, ctx: Context[AppContext]) -> dict[str, Any]:
    """Return one shopping list, including its current synchronized items."""
    client = _client(ctx)
    assert client.lists is not None
    shopping_list = client.lists.get(list_id)
    if shopping_list is None:
        raise KeyError(f"unknown shopping list: {list_id}")
    return _message_dict(shopping_list)


@mcp.tool()
async def add_shopping_item(
    list_id: str,
    name: str,
    ctx: Context[AppContext],
    details: str | None = None,
) -> dict[str, Any]:
    """Add an item to a shopping list and return the synchronized item."""
    client = _client(ctx)
    assert client.lists is not None
    item = await client.lists.add_item(list_id, name, details=details)
    return _message_dict(item)


@mcp.tool()
async def set_shopping_item_checked(
    list_id: str,
    item_id: str,
    checked: bool,
    ctx: Context[AppContext],
) -> None:
    """Check or uncheck a shopping-list item."""
    client = _client(ctx)
    assert client.lists is not None
    await client.lists.set_checked(list_id, item_id, checked)


@mcp.tool()
async def remove_shopping_item(
    list_id: str,
    item_id: str,
    ctx: Context[AppContext],
) -> None:
    """Remove an item from a shopping list."""
    client = _client(ctx)
    assert client.lists is not None
    await client.lists.remove_item(list_id, item_id)


@mcp.tool()
async def list_recipes(
    ctx: Context[AppContext],
    query: str = "",
) -> list[dict[str, Any]]:
    """List recipe summaries, optionally filtering by a name substring."""
    client = _client(ctx)
    assert client.recipes is not None
    needle = query.casefold()
    return [
        {
            "id": str(recipe.identifier),
            "name": str(recipe.name),
            "rating": int(recipe.rating) if recipe.HasField("rating") else None,
        }
        for recipe in client.recipes.all()
        if not needle or needle in str(recipe.name).casefold()
    ]


@mcp.tool()
async def get_recipe(recipe_id: str, ctx: Context[AppContext]) -> dict[str, Any]:
    """Return the complete synchronized protobuf-backed recipe as JSON-safe data."""
    client = _client(ctx)
    assert client.recipes is not None
    recipe = client.recipes.get(recipe_id)
    if recipe is None:
        raise KeyError(f"unknown recipe: {recipe_id}")
    return _message_dict(recipe)
