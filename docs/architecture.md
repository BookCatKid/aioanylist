# Architecture

`anylist-sdk` is structured around the same broad responsibilities visible in AnyList Web: token auth, protobuf HTTP transport, synchronized local state, operation queues, and realtime invalidation.

## Authentication

`AnyListTransport.sign_in()` posts the email/password to `/auth/token` and returns `AuthTokens` containing:

- `user_id`
- `access_token`
- `refresh_token`
- optional premium/account locale metadata

Authenticated requests send the access token as a bearer token. On an authentication failure, the transport serializes refresh through a lock, posts the current refresh token to `/auth/token/refresh`, replaces both returned tokens, and retries the original request once.

Refresh tokens rotate. Applications that persist sessions should write the latest token pair whenever it changes.

`logout()` uses the official native `/data/auth/sign-out` token-session endpoint. iOS traffic established the endpoint and its server semantics. Android sends the same fields as URL-encoded form data, while iOS uses multipart; the server accepts both, and the SDK keeps the already-live-verified multipart path. The request carries bearer authentication and the current refresh token; optional push-token metadata can be supplied by a native integration. Live tests against both `www.anylist.com` and `production.anylist.com` confirmed that sign-out immediately revokes the refresh token but leaves the current access token valid until its normal expiry. `clear_session()` is the explicit local-only operation for discarding credentials without contacting AnyList.

## Transport and protobufs

Normal data/edit endpoints use the multipart/protobuf format emitted by AnyList Web. Binary protobuf values are ordinary multipart fields rather than file uploads. The normal request path can allow specific HTTP statuses when the endpoint defines them as application data; UPC lookup, for example, treats 404 as a normal miss.

The SDK default remains `https://www.anylist.com`. Android hardcodes `production.anylist.com`, but the host is transport configuration rather than protocol semantics; callers can override `base_url` when reproducing a native environment.

The package embeds the official protobuf schema and builds runtime message classes dynamically. Static typing is provided by `src/anylist_sdk/proto/__init__.pyi`, generated deterministically from the same schema.

## State and synchronization

`AnyListState` is the local mirror of synchronized account data. `SyncCoordinator` applies aggregate or domain responses into that state and tracks timestamp-based incremental refreshes.

The initial `AnyListClient.load()` performs a full aggregate refresh. Later `refresh()` calls use the synchronized timestamp state and apply only returned changes.

Services such as `client.lists`, `client.recipes`, and `client.meal_plan` are views/mutation APIs over the same state rather than independent caches.

## Optimistic operations

Writes use operation queues that mirror AnyList Web's edit model:

1. update local state optimistically;
2. enqueue the exact official operation message;
3. batch/flush to the matching update endpoint;
4. process acknowledgement/timestamps;
5. refresh when conflict semantics require it.

Queues can be paused, resumed, explicitly flushed, and restored from an `OperationJournal`. With a cache directory, the default client uses a file-backed journal so acknowledged writes can survive an interrupted process and be replayed on the next load.

## Realtime behavior

The WebSocket client is an invalidation/catch-up channel, not a second authoritative state store. Realtime events identify domains that should refresh. Reconnect callbacks trigger catch-up synchronization after an interrupted socket.

Pass `realtime=True` to `client.load()` to start it automatically.

## Client-side AnyList behavior

Several visible AnyList features are computed locally rather than by the server. The SDK therefore includes source-derived implementations for:

- grocery tag data and categorization;
- current-list/Favorite/Recent/generic autocomplete;
- quantity and package-size parsing;
- recipe ingredient/direction parsing;
- normalization, stemming, and search helpers;
- deterministic IDs used by official data structures;
- recipe/meal-plan/pricing derived values.

The exhaustive evidence level for each public callable is tracked in [`conformance.md`](conformance.md).

## Source authority

Protocol behavior is derived from the official AnyList web application, official AnyList native clients for native-only functionality, the embedded protobuf schema, and AnyList-owned runtime resources/server behavior. Unofficial clients are not used as behavioral authority. Web behavior remains primary where the surfaces overlap; Android source is used to reconstruct useful native-only data features such as search/lookup and remote configuration, while app UI plumbing, mobile lifecycle behavior, telemetry, and obsolete integrations remain documented without becoming public SDK surface.

When official JavaScript and the embedded schema contradict each other, the SDK does not guess missing wire information. Evidence-backed deliberate divergences are documented explicitly in the conformance matrix.
