# anylist-sdk

Async-first, standalone, pure-Python AnyList SDK reconstructed from the **official AnyList web application** (`/static/webapp/js/app.js`) and AnyList's own runtime resources/server behavior.

This project deliberately does **not** use unofficial AnyList clients as protocol authority.

## Goals

- Full protobuf wire compatibility with the official web client.
- Async `aiohttp` transport with token refresh and WebSocket invalidation handling.
- Incremental aggregate synchronization and durable operation queues.
- Shopping lists, starter/favorite/recent lists, folders, categories, settings, recipes, meal planning, photos, sharing, account data, and official auxiliary endpoints.
- Client-side behavior used by AnyList Web: normalization, stemming, quantity/package parsing, categorization/tag data, autocomplete, recipe parsing, recipe-to-list provenance, derived totals, and deterministic identifiers.
- No Home Assistant assumptions.

## Status

The SDK is under reconstruction and is intended to be statically testable without credentials. Live credentials are only needed for final server conformance testing.

```python
from anylist_sdk import AnyListClient

async with AnyListClient() as client:
    await client.sign_in("you@example.com", "password")
    await client.load(realtime=True)
    for shopping_list in client.lists.all():
        print(shopping_list.name)
```

The low-level protobuf namespace is available as `anylist_sdk.proto.PB`, and each operation-backed service exposes `operation(...)` for official handlers whose high-level convenience wrapper is not needed by an application.
