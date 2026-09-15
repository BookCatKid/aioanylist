# Architecture

`aioanylist` is structured around the same broad responsibilities visible in AnyList Web: token auth, protobuf HTTP transport, synchronized local state, operation queues, and realtime invalidation.

## Authentication

`AnyListTransport.sign_in()` posts the email/password to `/auth/token` and returns `AuthTokens` containing:

- `user_id`
- `access_token`
- `refresh_token`
- optional premium/account locale metadata

Authenticated requests send the access token as a bearer token. On an authentication failure, the transport serializes refresh through a lock, posts the current refresh token to `/auth/token/refresh`, replaces both returned tokens, and retries the original request once.

Refresh tokens rotate. Applications that persist sessions should write the latest token pair whenever it changes.

`logout()` uses `/data/auth/sign-out` with bearer authentication and the current refresh token. iOS sends multipart data; Android sends the same fields as URL-encoded form data. The server accepts both, and the SDK uses the multipart form verified by live tests. Optional push-token metadata can be supplied by native integrations.

Live tests against `www.anylist.com` and `production.anylist.com` found that sign-out revokes the refresh token immediately while the current access token stays valid until expiry. `clear_session()` discards local credentials without contacting AnyList.

## Transport and protobufs

Normal data/edit endpoints use the multipart/protobuf format emitted by AnyList Web. Binary protobuf values are ordinary multipart fields rather than file uploads. The normal request path can allow specific HTTP statuses when the endpoint defines them as application data; UPC lookup, for example, treats 404 as a normal miss.

The default host is `https://www.anylist.com`. Android uses `production.anylist.com`; callers can override `base_url` when needed.

The package embeds the official protobuf schema and builds runtime message classes dynamically. Static typing is provided by `src/aioanylist/proto/__init__.pyi`, generated deterministically from the same schema.

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

WebSocket messages invalidate synchronized domains; they are not applied as standalone state updates. Reconnects trigger a catch-up refresh.

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
- AnyList-owned visual catalogs, asset URL resolution, built-in themes, and effective visual fallbacks.

Verification status for each public callable is tracked in [`conformance.md`](conformance.md).
[`usability-audit.md`](usability-audit.md) tracks protocol surfaces that still need higher-level client semantics.

## Protocol sources

Protocol behavior comes from AnyList Web, official native clients for native-only features, the embedded protobuf schema, and observed server behavior. Unofficial clients are not used as protocol references. Android source supplies native-only features such as search, lookup, and remote configuration; UI plumbing, lifecycle code, telemetry, and obsolete integrations stay in the research notes.

When the official JavaScript and embedded schema disagree, the SDK leaves the ambiguity unresolved unless another official source establishes the wire behavior. Known divergences are listed in the conformance matrix.
