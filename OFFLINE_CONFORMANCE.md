# Offline Conformance Checkpoint

This document records the point at which the SDK's static/offline reconstruction has been
exhausted against the official AnyList web application source available to this project.

Code checkpoint: `2e67352 Handle timestamp read 304 responses`

## Authority

Protocol and behavior decisions in this SDK are derived from:

- the official AnyList `app.js` bundle;
- the protobuf schema embedded in that bundle; and
- AnyList-owned runtime resources such as `tag_data*.json`.

Unofficial clients are not used as protocol authority.

## Offline verification status

- Original offline checkpoint suite: `456 passed`.
- Current offline suite after subsequent live-conformance fixes: `473 passed` (see `CONFORMANCE_MATRIX.md` for the current live status).
- Official operation-handler inventory: `185` handler IDs.
  - All `185` are explicitly represented by tests.
  - `184` have a normal serializable SDK path.
  - `set-web-selected-meal-plan-event-id` is deliberately blocked because the official JS
    invokes `webSelectedMealPlanEventId` accessors while the embedded `PBMobileAppSettings`
    schema contains no such field.
- Official endpoint inventory: `48` endpoint strings.
  - All source-backed token/data endpoint implementations used by the SDK are covered by the
    offline suite.
  - `/auth/logout` is intentionally not called by the SDK token transport: the official token
    client uses `/auth/token` and `/auth/token/refresh`, while the only `/auth/logout` usage in
    `app.js` is a browser-session HTML form carrying `_xsrf` and `next`.
- Dynamic official protobuf schema loads successfully from the packaged wheel.
- Static type gate passes with only `attr-defined` disabled. That error class is not meaningful
  for this project because protobuf message classes and fields are generated dynamically from
  the embedded schema at runtime.
- Correctness-focused Ruff gate passes (`F` rules plus the project's targeted runtime-quality
  checks used during development).
- `compileall` passes.
- The isolated wheel build succeeds as `anylist_sdk-0.1.0-py3-none-any.whl`.
- The wheel contains `proto/schema.json`, `official_surface.json`, and
  `data/recipe_source_aliases.json` and contains no `__pycache__` or `.pyc` files.
- The built wheel installs and imports successfully from outside the source tree.
- Timestamped reads that return HTTP `304 Not Modified` now follow the official managers'
  no-op path rather than attempting to decode an empty protobuf response.

This checkpoint is not a claim of production readiness or live server parity.

## Deliberately unresolved official-source contradictions

### `set-web-selected-meal-plan-event-id`

`app.js` reads `webSelectedMealPlanEventId`, calls `setWebSelectedMealPlanEventId(...)`, and
queues handler `set-web-selected-meal-plan-event-id`. The embedded official
`PBMobileAppSettings` schema has no `webSelectedMealPlanEventId` field. The SDK therefore does
not invent a protobuf field or wire number and raises before attempting to serialize it.

### Browser logout versus token logout

The official token flow has source-backed sign-in and refresh endpoints, but no bearer-token
logout call. The web application's `/auth/logout` occurrence is an HTML form for a browser
session, including `_xsrf` and `next`. `AnyListTransport.logout()` therefore discards local
tokens only instead of sending an unsupported bearer request.

### `delete-events-for-recipe-id` template-event quirk

The official bundle obtains the matching template-event array and then maps the normal-event
array a second time. That is a concrete source-level upstream typo: the fetched template-event
array is otherwise unused. The SDK intentionally diverges here and sends the actual matching
template-event IDs after the normal-event IDs. This is not inferred from a network trace: the
source difference is explicit, the protobuf schema supports both ID sets, an offline regression
locks the divergence, and a live disposable recipe test proved that the server removes both the
normal event and template event. Treat this as an evidence-backed correctness divergence, not
as exact `app.js` parity.

## Pure-Python portability limitation

The web client uses browser `Intl.Collator` behavior for some locale-sensitive ordering and
comparisons. The SDK intentionally remains pure Python, so the small number of those paths use
a deterministic Unicode/case-fold/numeric approximation rather than depending on ICU/PyICU.
The protocol and synchronized state do not depend on this approximation, but live conformance
should exercise user-visible ordering in multiple locales.

## Live conformance plan

Live testing should use a disposable or explicitly approved AnyList account and should proceed
from read-only checks to mutations:

1. Authenticate through `/auth/token`, verify token refresh serialization, and perform an
   initial aggregate `/data/user-data/get` sync.
2. Verify each direct domain refresh endpoint and compare timestamps/logical timestamps before
   and after no-op refreshes.
3. Exercise representative mutations for every operation-backed domain and verify server
   acknowledgements, returned timestamps, optimistic-state reconciliation, and follow-up
   refresh behavior.
4. Exercise partial, zero, and delayed acknowledgement behavior where it can be induced safely,
   plus operation-journal replay after a controlled client restart.
5. Verify WebSocket invalidation, heartbeat, reconnect, token-refresh-on-4010, and catch-up sync
   after a forced disconnect.
6. Verify auxiliary endpoints: sharing, photo URL/byte upload, recipe import, list/recipe/meal
   plan email, iCalendar enablement, Alexa linking controls, and browser-state endpoints that
   are applicable to a token client.
7. Compare locale-sensitive sort/equality behavior for at least English and German data.
8. Specifically observe the two source contradictions above before deciding whether any
   compatibility shim is justified.

`app.js` is the primary behavioral authority. The default is exact reconstruction, and no
protocol behavior may be invented merely from a captured request or because a live server will
accept it. A deliberate divergence is allowed only when all of the following are true: the
official executable path is identified precisely; the defect/limitation is concrete rather than
speculative; the alternative is supported by the official schema/runtime model; an offline
regression records the difference; and a live disposable test proves the alternative behaves as
intended. Such cases must be documented explicitly as intentional divergences rather than
described as parity. Genuine source/schema contradictions remain unresolved until there is enough
evidence to avoid inventing missing wire information.

### Opt-in live harness

The repository includes `live_tests/`, which is intentionally outside the default pytest
`testpaths`. It never runs as part of `pytest` unless invoked explicitly.

Read-only/auth/realtime checks require:

```text
ANYLIST_LIVE=1
ANYLIST_EMAIL=...
ANYLIST_PASSWORD=...
```

Run them with:

```text
python -m pytest -q live_tests/test_live_readonly.py
```

Mutation checks require an additional explicit opt-in and an already-approved disposable
shopping list:

```text
ANYLIST_LIVE_MUTATIONS=1
ANYLIST_LIVE_LIST_ID=<shopping-list-id>
```

The mutation harness has since expanded beyond the original one-item pilot. It is hard-guarded to the approved disposable shopping-list ID and exact name, verifies mutations through fresh server reads, suppresses Recent Items side effects where required, and cleans up temporary list-local resources. Current live results and the precise tested/untested boundary are tracked in `CONFORMANCE_MATRIX.md`. It still never deletes an account, sends email, changes sharing/Alexa/iCalendar state, uploads photos, or restores archived operations without additional authorization.
