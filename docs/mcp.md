# MCP integration

MCP support is optional. The core SDK has no MCP dependency or MCP-specific public API.

For users who want MCP, the repository includes [`examples/anylist_mcp.py`](../examples/anylist_mcp.py).
It uses the current v2 line of the official MCP Python SDK and holds one authenticated,
synchronized `AnyListClient` open for the server lifespan. Every tool call reuses that
session.

Install the optional dependency from a source checkout:

```console
python -m pip install -e '.[mcp]'
```

Set `ANYLIST_EMAIL` and `ANYLIST_PASSWORD`, then open the example in the MCP
development inspector:

```console
mcp dev examples/anylist_mcp.py
```

Or run it directly through the MCP CLI using the transport appropriate for your
client. The official MCP Python SDK supports stdio and Streamable HTTP, so the
AnyList-specific part of the integration can remain the same across local and
network deployments.

The example exposes a small set of tools:

- refresh synchronized state;
- list/get shopping lists;
- add/check/remove shopping items;
- list/get recipes.

Additional SDK services can be exposed with the same pattern without bypassing the high-level API.

## Existing AnyList MCP servers

MCP support for AnyList already exists in the community. Projects identified in
the September 2026 ecosystem review include:

- [`bobby060/anylist-mcp`](https://github.com/bobby060/anylist-mcp) — the most
  extensive deployment-oriented implementation found in the audit, with stdio and
  HTTP modes, OAuth support, Home Assistant documentation, and grouped shopping,
  recipe, meal-plan, and collection tools;
- [`asachs01/anylist-mcp`](https://github.com/asachs01/anylist-mcp) — another
  grouped-tool AnyList MCP server;
- [`avanrossum/mcp-anylist`](https://github.com/avanrossum/mcp-anylist) — a local
  npm-distributed server focused on shopping and meal planning.

`aioanylist` can also be used as the AnyList backend for an existing MCP server while leaving that server's tool schemas, deployment model, and authentication flow unchanged.
