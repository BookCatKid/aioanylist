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

The static/offline reconstruction has reached a conformance checkpoint. Live credentials are
now only needed for final server conformance testing; this is not yet a production-readiness
claim. See [`OFFLINE_CONFORMANCE.md`](OFFLINE_CONFORMANCE.md) for the exact verified boundary,
known official-source contradictions, and live test plan.

```python
from anylist_sdk import AnyListClient

async with AnyListClient() as client:
    await client.sign_in("you@example.com", "password")
    await client.load(realtime=True)
    for shopping_list in client.lists.all():
        print(shopping_list.name)
```

The low-level protobuf namespace is available as `anylist_sdk.proto.PB`, and each operation-backed service exposes `operation(...)` for official handlers whose high-level convenience wrapper is not needed by an application.

## Live conformance

Opt-in real-service tests live under `live_tests/` and are excluded from the default test
suite. See [`OFFLINE_CONFORMANCE.md`](OFFLINE_CONFORMANCE.md) for the required environment
variables and mutation-safety rules.
