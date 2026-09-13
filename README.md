# anylist-sdk

Async-first, standalone, pure-Python AnyList SDK reconstructed from the **official AnyList web application** (`/static/webapp/js/app.js`) and AnyList's own runtime resources/server behavior.

This project deliberately does **not** use unofficial AnyList clients as protocol authority.

## Goals

- Full protobuf wire compatibility with the official web client.
- Async `aiohttp` transport with token refresh and WebSocket invalidation handling.
- Incremental aggregate synchronization and durable operation queues.
- Shopping lists, starter/favorite/recent lists, folders, categories, settings, recipes, meal planning, photos, sharing, account data, and official auxiliary endpoints.
- Client-side behavior used by AnyList Web: normalization, stemming, quantity/package parsing, categorization/tag data, autocomplete, recipe parsing, recipe-to-list provenance, derived totals, and deterministic identifiers.
- PEP 561 typed public API, including schema-generated stubs for the dynamic protobuf models.
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
    assert client.lists is not None
    for shopping_list in client.lists.all():
        print(shopping_list.name)
```

The low-level protobuf namespace is available as `anylist_sdk.proto.PB`, and each operation-backed service exposes `operation(...)` for official handlers whose high-level convenience wrapper is not needed by an application.

## Typing

The normal client/service/state surface is fully annotated and the distribution includes a
`py.typed` marker. The protobuf implementation remains generated dynamically at runtime from
AnyList's embedded official schema, while `anylist_sdk.proto` ships a `.pyi` generated from the
same schema so editors and type checkers still see concrete models and fields. For example,
`ShoppingList.items` is typed as a repeated collection of `ListItem`, and recipe ingredients
are `PBIngredient` values rather than generic protobuf `Message` objects.

The checked-in protobuf stub is reproducible with `tools/generate_proto_stubs.py`; `--check`
fails when the schema and stub drift. The test suite also runs a strict mypy consumer fixture.

## Live conformance

Opt-in real-service tests live under `live_tests/` and are excluded from the default test
suite. See [`OFFLINE_CONFORMANCE.md`](OFFLINE_CONFORMANCE.md) for the required environment
variables and mutation-safety rules.

## Example TUI client

A substantial Textual-based terminal client lives at [`examples/anylist_tui.py`](examples/anylist_tui.py).
It is intentionally an example downstream application rather than part of the SDK API. Its primary
navigation is user-facing — **Lists**, **Recipes**, and **Meal Plan** — instead of exposing the SDK's
service boundaries as separate pages. Create/edit actions open focused forms with the relevant
controls; less-common list features such as stores/categories, saved items, and folders live behind
the contextual **List Settings…** screen. Item creation defaults to AnyList-style automatic
categorization and exposes the same current-list/Favorite/Recent/generic autocomplete branches while
still allowing manual overrides. Item and recipe photos can be added from a local image or URL.
Recipes are browsable by custom and smart collections, with a full detail reader and editors for
ingredients, directions, timing, nutrition, rating, source data, and photos. Meal Plan provides a
week planner, Queue/Favorites workspace, recipe-first scheduling, labels, and editable multi-day
templates. List settings expose stores/categories, saved items, folder moves/deletion, ordering, and
default-category controls. External-account actions such as sharing/email, Alexa, recipe web import,
and account-name changes are intentionally omitted.

```bash
python -m pip install -e '.[tui]'
python examples/anylist_tui.py
```

The first run prompts for the AnyList email/password before the TUI starts. The password is never
stored; only the access/refresh tokens and email are cached under `~/.config/anylist-sdk/` with the
token file set to mode `0600` where supported. Use `python examples/anylist_tui.py --logout` to remove
the cached session.
