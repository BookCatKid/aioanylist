# SDK usage

This guide covers the normal application/integration path. For transport and synchronization internals, see [architecture.md](architecture.md). For exhaustive protocol evidence, see [conformance.md](conformance.md).

## Client lifecycle

Create one `AnyListClient` per signed-in account and keep it alive for as long as the integration is active:

```python
from pathlib import Path

from anylist_sdk import AnyListClient


client = AnyListClient(cache_dir=Path("~/.cache/my-integration").expanduser())
tokens = await client.sign_in(email, password)
await client.load(realtime=True)

# ...use client.lists, client.recipes, client.meal_plan, etc...

await client.close()
```

`cache_dir` enables durable operation-journal replay and tag-data caching. `load()` performs the initial synchronized account load; `realtime=True` also starts the WebSocket invalidation client.

## Reusing a signed-in session

The password is only needed to obtain the first `AuthTokens` bundle. Persist the newest token bundle in your application's credential store and pass it back on the next launch:

```python
from anylist_sdk import AnyListClient, AuthTokens


def save_tokens(tokens: AuthTokens | None) -> None:
    if tokens is None:
        credential_store.delete()
    else:
        credential_store.save(tokens)


client = AnyListClient(
    tokens=credential_store.load(),
    user_email=email,
    cache_dir=cache_dir,
    token_callback=save_tokens,
)
await client.load(realtime=True)
```

AnyList rotates both the access token and refresh token. `token_callback` runs whenever the SDK publishes a new pair, including automatic refresh, so integrations do not need to reach into `client.transport` to keep persisted credentials current. `await client.logout()` uses AnyList's official native token-session sign-out endpoint and publishes `None` after success so the application can clear its persisted token record too. Use `await client.clear_session()` when the application intentionally wants to forget local credentials without revoking that server session.

Native clients can also unregister their push registration while signing out:

```python
await client.logout(push_token=apns_token, push_token_type="apns")
```

Ordinary integrations should omit those arguments.

When constructing a client from existing tokens, pass `user_email` if the application may create shopping lists; AnyList's official new-list flow requires the authenticated account email.

## Shopping lists

After `load()`, `client.lists` exposes the synchronized shopping-list service:

```python
assert client.lists is not None

for shopping_list in client.lists.all():
    print(shopping_list.identifier, shopping_list.name)

groceries = await client.lists.create("Groceries")
milk = await client.lists.add_item(
    groceries.identifier,
    "Milk",
    details="2%",
)

await client.lists.set_checked(groceries.identifier, milk.identifier, True)
```

Fresh grocery items are enriched client-side using the same categorization path reconstructed from AnyList Web. For UI-style autocomplete, use `client.autocomplete` together with current/Favorite/Recent items, or use the higher-level prepared-item helpers on `ShoppingListsService` when an explicitly selected Favorite/Recent suggestion should carry saved metadata.

## Deferring and batching writes

Mutation methods normally flush immediately. Pass `flush=False` to stage several optimistic changes and flush them together:

```python
assert client.lists is not None

await client.lists.add_item(list_id, "Apples", flush=False)
await client.lists.add_item(list_id, "Bananas", flush=False)
await client.flush()
```

The synchronized local state is updated optimistically before acknowledgement, matching the official client model. With a `cache_dir`, pending operation queues are journaled so interrupted writes can be restored on the next `load()`.

## Recipes

The recipe service exposes both CRUD and AnyList's client-derived smart/sorted views:

```python
from anylist_sdk.parsing import parse_ingredient_lines, parse_recipe_steps

assert client.recipes is not None

recipe = await client.recipes.create(
    "Pancakes",
    ingredients=parse_ingredient_lines("1 cup flour\n1 egg\n1 cup milk"),
    preparation_steps=parse_recipe_steps("Mix ingredients\nCook on a griddle"),
    servings="4",
)

collection = await client.recipes.create_collection("Breakfast")
await client.recipes.add_to_collection(collection.identifier, [recipe.identifier])

for recipe in client.recipes.sorted(collection_id=collection.identifier):
    print(recipe.name)
```

`source_collections()` and `not_in_collection()` expose the smart collections derived locally by AnyList Web.

## Meal planning

Meal-plan events are protobuf models because the official surface carries a rich event shape:

```python
from anylist_sdk.proto import PB

assert client.meal_plan is not None

event = PB.PBCalendarEvent(
    title="Tacos",
    date="2026-09-14",
    eventType=PB.PBCalendarEventType.MealPlanCalendarEvent,
)
saved = await client.meal_plan.save_event(event)

# Move it back to the queue later.
await client.meal_plan.set_event_date([saved.identifier], None)
```

The same service exposes labels, Favorites/Queue events, templates, template groups, and event-list items.

## Photos

Photo upload is an auxiliary HTTP service:

```python
assert client.photos is not None

photo_id = await client.photos.upload_bytes(
    image_bytes,
    content_type="image/jpeg",
    filename="photo.jpg",
)
print(client.photos.url(photo_id))
```

`upload_url()` asks AnyList to import an HTTP(S) image URL. The service enforces the content types and 10 MiB limit used by AnyList Web for byte uploads.

## Manual refresh and realtime

`await client.refresh()` performs timestamp-based catch-up synchronization. With realtime enabled, WebSocket messages act as invalidations and the SDK refreshes synchronized domains rather than treating socket messages as an independent source of truth.

For long-running integrations such as Home Assistant, the usual lifecycle is:

1. restore the newest saved `AuthTokens`;
2. construct one client with `token_callback` and a persistent `cache_dir`;
3. `await client.load(realtime=True)` during setup;
4. read synchronized state and use service methods for writes;
5. `await client.close()` during unload/reload.

## Errors

All public SDK failures derive from `AnyListError`:

```python
from anylist_sdk import AuthenticationError, TransportError

try:
    await client.refresh()
except AuthenticationError:
    # The stored AnyList session is no longer usable.
    ...
except TransportError:
    # Network/server failure; do not treat this as a bad password.
    ...
```

`AuthenticationError`, `PermissionDeniedError`, `ProtocolError`, `TransportError`, `SyncError`, and `TagDataError` allow applications to distinguish auth, network, protocol, synchronization, and tag-resource failures without parsing exception strings.

## Raw protocol escape hatch

High-level services cover the reconstructed public behavior. `client.raw` and each operation-backed service's `operation(...)` method exist for source-backed protocol work that does not need a dedicated convenience wrapper. Prefer the typed service surface for ordinary application code.
