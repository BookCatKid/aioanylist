# aioanylist

Async-first, typed Python client for AnyList, reverse-engineered from AnyList's web and native clients and embedded protobuf schema.

Unofficial and not affiliated with AnyList.

> **Built to be the last AnyList client you need.**

As of the September 2026 audit, no other reviewed public general-purpose AnyList client matched its combined protocol and client-behavior coverage. See the [ecosystem comparison](docs/ecosystem-comparison.md) for the dated feature matrix and benchmarks.

## Highlights

- Typed async API built on `aiohttp`.
- Official protobuf wire format and operation queues.
- Access-token refresh with rotating refresh-token support.
- Incremental synchronization plus WebSocket invalidation/reconnect handling.
- Durable operation journals for replay after interrupted sessions.
- Shopping lists, Favorites/Recents, folders, stores, categories, recipes, meal planning, photos, sharing, account data, native search/lookup, remote config, and auxiliary endpoints.
- Client-side AnyList behavior including autocomplete, grocery categorization, quantity/package parsing, recipe parsing, normalization, stemming, derived totals, deterministic identifiers, and official visual/theme resolution.
- Current AnyList icon metadata/catalog access and canonical asset URLs without bundling or redistributing AnyList artwork.
- PEP 561 typing with schema-generated protobuf stubs.
- Textual example application kept outside the installable SDK package.

The default test suite passes **530/530** tests. Live and offline verification status is tracked in [`docs/conformance.md`](docs/conformance.md).

## Requirements

- Python 3.11+
- An AnyList account for authenticated API use

Install the SDK from PyPI:

```console
python -m pip install aioanylist
```

For the Textual example dependencies:

```console
python -m pip install 'aioanylist[tui]'
```

For the optional Model Context Protocol example dependencies:

```console
python -m pip install 'aioanylist[mcp]'
```

For development/testing from a source checkout:

```console
python -m pip install -e '.[test,tui]'
```

## Quick start

```python
import asyncio

from aioanylist import AnyListClient


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

Low-level protobuf classes are available under `aioanylist.proto.PB`. Operation-backed services also expose `operation(...)` for known handlers without a dedicated convenience method.

## Authentication and session reuse

Sign-in returns an `AuthTokens` object containing the user ID, access token, refresh token, and account metadata. Normal API calls use the access token as a bearer token. If AnyList rejects it with an authentication response, the transport refreshes it once using the refresh token and retries the original request.

AnyList rotates both tokens during refresh. Applications that persist sessions should save the newest `AuthTokens` value after every refresh.

The SDK does not retain the password after sign-in. `logout()` signs out the token session and clears local credentials. `clear_session()` only clears local credentials. Live tests on both AnyList hosts found that sign-out revokes the refresh token immediately while the current access token remains valid until expiry.

See [`docs/architecture.md`](docs/architecture.md) for the transport, sync, operation-queue, and realtime model.

## Example terminal client

[`examples/anylist_tui.py`](examples/anylist_tui.py) is a downstream example built on the SDK. It lives outside `src/aioanylist`, so installing the library does not also install an end-user application.

```console
git clone https://github.com/BookCatKid/aioanylist.git
cd aioanylist
python -m pip install -e '.[tui]'
python examples/anylist_tui.py
```

On first run, the TUI asks for the AnyList email and password. It stores the email and current token pair under `~/.config/aioanylist/`; the password is not stored. The token file uses mode `0600` where supported.

The TUI includes:

- shopping-list creation/editing, autocomplete, categories, stores, quantities, photos, Favorites/Recents, folders, and list behavior;
- recipe collections, full recipe viewing/editing, ingredients, directions, timing, nutrition, ratings, sources, and photos;
- a week-based meal planner, Queue/Favorites workspace, labels, recipe scheduling, per-entry items, and editable multi-day templates.

Use `python examples/anylist_tui.py --login` to ignore a cached session and sign in again, or `python examples/anylist_tui.py --logout` to remove the cached local session.

See [`docs/tui.md`](docs/tui.md) for navigation, shortcuts, session behavior, and troubleshooting.

## Model Context Protocol

The core package has no MCP dependency. [`examples/anylist_mcp.py`](examples/anylist_mcp.py) shows how to expose the SDK through the official MCP Python SDK while keeping one synchronized `AnyListClient` alive for the server lifespan.

See [`examples/anylist_mcp.py`](examples/anylist_mcp.py) and [`docs/mcp.md`](docs/mcp.md).

## Typing

The normal client/service/state surface is fully annotated and the package ships a `py.typed` marker.

The protobuf classes are built dynamically at runtime from AnyList's embedded official schema, while `aioanylist.proto` ships a generated `.pyi` from that same schema. Editors and type checkers therefore see concrete message fields such as `ShoppingList.items`, `PBRecipe.ingredients`, and `PBCalendarEvent.eventListItems` rather than generic protobuf `Message` values.

The checked-in stub is deterministic. `tools/generate_proto_stubs.py --check` fails if it drifts from the embedded schema, and the default suite runs strict mypy checks against both the SDK and an external consumer fixture.

## Project layout

```text
src/aioanylist/        installable SDK, protocol runtime, and services
tests/                  offline/local regression suite
live_tests/             explicitly opt-in real-service conformance tests
research/               official-client reverse-engineering evidence and inventories
docs/                   architecture, TUI guide, and conformance evidence
tools/                  schema/surface extraction and generated-stub tooling
examples/               downstream example applications
```

## Conformance and safety

Shared/web behavior is matched against AnyList Web. Native-only behavior comes from official Android source and iOS captures. Unofficial clients are not used as protocol references.

[`docs/conformance.md`](docs/conformance.md) records verification status, source contradictions, known divergences, and live-test boundaries.

The default test suite is fully offline/local:

```console
python -m pytest -q
```

Real-service tests live in `live_tests/` and require explicit environment opt-in. They are not collected by the default pytest configuration.

## Development checks

```console
python -m pytest -q
python -m mypy --strict --disable-error-code attr-defined src/aioanylist
python tools/generate_proto_stubs.py --check
python -m ruff format --check .
```

`src/aioanylist/proto/__init__.pyi` is generated deterministically and excluded from standalone Ruff formatting. `tools/generate_proto_stubs.py --check` validates it.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — authentication, transport, state, sync, queues, realtime, and typing model.
- [`docs/usage.md`](docs/usage.md) — practical SDK patterns for integrations and applications.
- [`docs/tui.md`](docs/tui.md) — terminal-client setup and day-to-day usage.
- [`docs/conformance.md`](docs/conformance.md) — public API verification status and live/offline evidence.
- [`docs/protocol-coverage.md`](docs/protocol-coverage.md) — generated merged Web/Android route and operation-handler coverage audit.
- [`docs/visual-assets.md`](docs/visual-assets.md) — official icon catalogs, asset URLs, themes, palettes, and effective visual fallbacks.
- [`docs/usability-audit.md`](docs/usability-audit.md) — protocol-complete surfaces that still need higher-level domain ergonomics.
- [`docs/ecosystem-comparison.md`](docs/ecosystem-comparison.md) — comparison with other public AnyList clients.
- [`docs/mcp.md`](docs/mcp.md) — optional MCP adapter example and related projects.

## Scope

The SDK includes reconstructed external/account operations where available. The terminal example leaves out workflows with external or hard-to-reverse effects, including sharing/email, Alexa linking, recipe web import, and account-name changes.

Because this is a reverse-engineered client for a service that can change independently, future AnyList web/protocol updates may require corresponding SDK updates.
