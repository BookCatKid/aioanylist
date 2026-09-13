# anylist-sdk

Async-first, typed, pure-Python client for AnyList, reconstructed from the **official AnyList web application**, its embedded protobuf schema, and AnyList-owned runtime/server behavior.

This project does not use unofficial AnyList clients as protocol authority and is not affiliated with or endorsed by AnyList.

## Highlights

- Typed async API built on `aiohttp`.
- Official protobuf wire format and operation queues.
- Access-token refresh with rotating refresh-token support.
- Incremental synchronization plus WebSocket invalidation/reconnect handling.
- Durable operation journals for replay after interrupted sessions.
- Shopping lists, Favorites/Recents, folders, stores, categories, recipes, meal planning, photos, sharing, account data, and auxiliary endpoints.
- Client-side AnyList behavior including autocomplete, grocery categorization, quantity/package parsing, recipe parsing, normalization, stemming, derived totals, and deterministic identifiers.
- PEP 561 typing with schema-generated protobuf stubs.
- Full-featured Textual example application, kept outside the installable SDK package.

The default repository test suite currently passes **485/485** tests. Detailed source/live verification evidence is tracked in [`docs/conformance.md`](docs/conformance.md).

## Requirements

- Python 3.11+
- An AnyList account for authenticated API use

Install the SDK from the repository:

```bash
python -m pip install -e .
```

For development/testing:

```bash
python -m pip install -e '.[test]'
```

To run the Textual example:

```bash
python -m pip install -e '.[tui]'
```

## Quick start

```python
import asyncio

from anylist_sdk import AnyListClient


async def main() -> None:
    async with AnyListClient() as client:
        await client.sign_in("you@example.com", "password")
        await client.load(realtime=True)

        assert client.lists is not None
        for shopping_list in client.lists.all():
            print(shopping_list.name)


asyncio.run(main())
```

`load()` performs the initial synchronized state load. Passing `realtime=True` also starts the WebSocket invalidation client so remote changes can trigger catch-up refreshes.

The service attributes become available after authentication, for example:

```python
assert client.lists is not None
assert client.recipes is not None
assert client.meal_plan is not None

groceries = client.lists.get("list-id")
recipes = client.recipes.all()
events = client.meal_plan.events()
```

The low-level protobuf namespace is available as `anylist_sdk.proto.PB`. Operation-backed services also expose `operation(...)` as a protocol escape hatch for already-proven official handlers that do not need a dedicated convenience method.

## Authentication and session reuse

Sign-in returns an `AuthTokens` object containing the user ID, access token, refresh token, and account metadata. Normal API calls use the access token as a bearer token. If AnyList rejects it with an authentication response, the transport refreshes it once using the refresh token and retries the original request.

AnyList's refresh response rotates **both** the access token and refresh token, so applications that persist sessions should always save the newest `AuthTokens` value rather than assuming the original refresh token remains valid indefinitely.

The SDK never needs to retain the user's password after sign-in. `logout()` performs AnyList's official native token-session sign-out and then clears local credentials; `clear_session()` is available when an application deliberately wants local-only credential removal. Live verification on both AnyList hosts shows that sign-out revokes the refresh token immediately but does not invalidate the already-issued access token, which remains usable until its normal expiry.

See [`docs/architecture.md`](docs/architecture.md) for the transport, sync, operation-queue, and realtime model.

## Example terminal client

[`examples/anylist_tui.py`](examples/anylist_tui.py) is a substantial downstream example built on the SDK. It intentionally stays outside `src/anylist_sdk`, so installing the library for Home Assistant, automation, or another application does not also install an end-user app.

```bash
python -m pip install -e '.[tui]'
python examples/anylist_tui.py
```

The first run prompts for the AnyList email and password before the TUI starts. The password is never stored. The client caches only the account email plus the current access/refresh token pair under `~/.config/anylist-sdk/`, with the token file written as mode `0600` where supported.

The TUI includes:

- shopping-list creation/editing, autocomplete, categories, stores, quantities, photos, Favorites/Recents, folders, and list behavior;
- recipe collections, full recipe viewing/editing, ingredients, directions, timing, nutrition, ratings, sources, and photos;
- a week-based meal planner, Queue/Favorites workspace, labels, recipe scheduling, per-entry items, and editable multi-day templates.

Use `python examples/anylist_tui.py --login` to ignore a cached session and sign in again, or `python examples/anylist_tui.py --logout` to remove the cached local session.

See [`docs/tui.md`](docs/tui.md) for navigation, shortcuts, session behavior, and troubleshooting.

## Typing

The normal client/service/state surface is fully annotated and the package ships a `py.typed` marker.

The protobuf classes are built dynamically at runtime from AnyList's embedded official schema, while `anylist_sdk.proto` ships a generated `.pyi` from that same schema. Editors and type checkers therefore see concrete message fields such as `ShoppingList.items`, `PBRecipe.ingredients`, and `PBCalendarEvent.eventListItems` rather than generic protobuf `Message` values.

The checked-in stub is deterministic. `tools/generate_proto_stubs.py --check` fails if it drifts from the embedded schema, and the default suite runs strict mypy checks against both the SDK and an external consumer fixture.

## Project layout

```text
src/anylist_sdk/        installable SDK, protocol runtime, and services
tests/                  offline/local regression suite
live_tests/             explicitly opt-in real-service conformance tests
docs/                   architecture, TUI guide, and conformance evidence
tools/                  schema/surface extraction and generated-stub tooling
examples/               downstream example applications
```

## Conformance and safety

The official executable web-client behavior is the primary specification for this project. Captured requests or server acceptance alone are not treated as permission to invent semantics.

The full verification matrix, known official-source contradictions, deliberate evidence-backed divergence, and live-test safety boundaries are documented in [`docs/conformance.md`](docs/conformance.md).

The default test suite is fully offline/local:

```bash
python -m pytest -q
```

Real-service tests live in `live_tests/` and require explicit environment opt-in. They are not collected by the default pytest configuration.

## Development checks

```bash
python -m pytest -q
python -m mypy --strict --disable-error-code attr-defined src/anylist_sdk
python tools/generate_proto_stubs.py --check
python -m ruff format --check .
```

`src/anylist_sdk/proto/__init__.pyi` is deterministic generated output and is intentionally excluded from independent Ruff reformatting; the generator check is its source-of-truth validation.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — authentication, transport, state, sync, queues, realtime, and typing model.
- [`docs/usage.md`](docs/usage.md) — practical SDK patterns for integrations and applications.
- [`docs/tui.md`](docs/tui.md) — terminal-client setup and day-to-day usage.
- [`docs/conformance.md`](docs/conformance.md) — exhaustive verified public surface and live/offline evidence.

## Scope

The SDK exposes source-backed external/account operations where they have been reconstructed, but the terminal client intentionally avoids workflows with external or hard-to-reverse effects such as sharing/email, Alexa linking, recipe web import, and account-name changes.

Because this is a reverse-engineered client for a service that can change independently, future AnyList web/protocol updates may require corresponding SDK updates.
