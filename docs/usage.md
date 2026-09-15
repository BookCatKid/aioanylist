# SDK usage

Common `aioanylist` usage patterns. See [architecture.md](architecture.md) for internals and [conformance.md](conformance.md) for verification status.

## Client lifecycle

Create one `AnyListClient` per signed-in account and keep it alive for as long as the integration is active:

```python
from pathlib import Path

from aioanylist import AnyListClient


client = AnyListClient(cache_dir=Path("~/.cache/my-integration").expanduser())
tokens = await client.sign_in(email, password)
await client.load(realtime=True)

# ...use client.lists, client.recipes, client.meal_plan, etc...

await client.close()
```

`cache_dir` enables durable operation-journal replay and tag-data caching. `load()` performs the initial synchronized account load; `realtime=True` also starts the WebSocket invalidation client.

`client.visuals` works before authentication because AnyList serves the icon metadata and image resources publicly. With `cache_dir`, JSON catalogs are cached alongside tag data. Image binaries are not stored in the package.

## Icons, images, colors, and themes

Use `client.visuals` rather than hard-coding AnyList asset paths or theme IDs:

```python
from aioanylist import IconContext

catalog = await client.visuals.icon_catalog(IconContext.LIST)
paint = next(icon for icon in catalog.unique_entries() if icon.icon_name == "emoji/1f3a8")

print(client.visuals.icon_url(paint))
print(client.visuals.category_icon_url("produce"))

assert client.list_settings is not None
settings = client.list_settings.get(list_id)
theme = client.visuals.resolve_list_theme(settings, dark=False)
dark_theme = client.visuals.resolve_list_theme(settings, dark=True)
icon = client.visuals.resolve_list_icon(settings)
style = client.visuals.theme_style(theme)

print(style.control_hex_color, style.table_texture_url, style.font_family)
```

The icon index comes from the JSON catalogs used by AnyList Web. Context-specific catalogs cover lists, folders, recipes, recipe collections, meal-plan notes, and meal-plan templates. See [`visual-assets.md`](visual-assets.md) for asset paths, theme palettes, dark-mode behavior, and fallback rules.

## Reusing a signed-in session

The password is only needed to obtain the first `AuthTokens` bundle. Persist the newest token bundle in your application's credential store and pass it back on the next launch:

```python
from aioanylist import AnyListClient, AuthTokens


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

AnyList rotates both tokens. `token_callback` runs whenever the SDK receives a new pair, including automatic refresh, so applications can persist tokens without reaching into `client.transport`.

`await client.logout()` signs out the token session and publishes `None` after success. Live tests found that the refresh token is revoked immediately while the current access token remains valid until expiry. `await client.clear_session()` only removes local credentials.

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

New grocery items use the reconstructed AnyList categorization path. For autocomplete, use `client.autocomplete` with current/Favorite/Recent items. `ShoppingListsService` also has prepared-item helpers for cases where selecting a Favorite or Recent should reuse its saved metadata.

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
from aioanylist.parsing import parse_ingredient_lines, parse_recipe_steps

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
from aioanylist.proto import PB

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

AnyList's Android client also exposes its native image-search endpoint through the same service:

```python
results = await client.photos.image_search("pancakes")
for result in results:
    print(result.media_url, result.thumbnail_url)
```

## Native search, lookup, and configuration

Some endpoints only appear in the native clients. The SDK uses `www.anylist.com` by default and sends the Android request shapes for these routes.

UPC lookup returns `PBProductLookupResponse`; a 404 or empty response means no match. Android puts this feature behind a premium UI gate:

```python
assert client.products is not None

product = await client.products.lookup("012345678905")
if product is not None:
    print(product.listItem.name, product.productThumbnailUrl)
```

Place search mirrors the Android location-notification picker. Android computes `radius` as half the visible map diagonal; the SDK accepts the already-computed radius in meters:

```python
assert client.maps is not None

places = await client.maps.place_search(
    "grocery store",
    latitude=32.7,
    longitude=-117.1,
    radius_meters=2_000,
)
```

`client.config.get()` exposes Android's `/data/version-check` endpoint, which is actually a remote-configuration response containing assistant visibility/status messages and retail-promotion data. It can be called before or after authentication.

`client.alexa.set_default_list_id(...)` exposes the Android Alexa default-list selector alongside the SDK's existing Alexa integration methods.

Android App Notices, client metrics, rating-prompt feedback, FCM push-token registration, and legacy Google Assistant link/unlink routes are documented in the research inventory but not exposed as SDK services. They are app UI, telemetry, lifecycle, or obsolete integration paths.

The Android client also exposes account/signup/password/subuser/delete/purchase endpoints. They remain in the research and conformance notes instead of the high-level SDK because they are account-management or store-specific operations with broad side effects.

## Manual refresh and realtime

`await client.refresh()` performs timestamp-based catch-up synchronization. With realtime enabled, WebSocket messages mark domains stale and trigger the corresponding refreshes.

For long-running integrations such as Home Assistant, the usual lifecycle is:

1. restore the newest saved `AuthTokens`;
2. construct one client with `token_callback` and a persistent `cache_dir`;
3. `await client.load(realtime=True)` during setup;
4. read synchronized state and use service methods for writes;
5. `await client.close()` during unload/reload.

## Errors

All public SDK failures derive from `AnyListError`:

```python
from aioanylist import AuthenticationError, TransportError

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

## Low-level protocol access

`client.raw` and each operation-backed service's `operation(...)` method provide access to known protocol operations without dedicated convenience wrappers. Most application code should use the typed service methods.
