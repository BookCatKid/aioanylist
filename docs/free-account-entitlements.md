# Free-account entitlement behavior

This note records the 2026-09-13 disposable-account investigation into AnyList Complete feature enforcement. It describes observed protocol behavior, not a promise that AnyList will keep the same policy in future server versions.

## Test account state

All server-verification probes ran against a newly created disposable account. Before the sweep, a fresh authentication reported `is_premium_user = false`. After all probes—including photo upload, meal-plan iCalendar enable/disable, and recipe imports below the advertised free quota—a second fresh authentication still reported `is_premium_user = false`.

`GET /data/account/info` independently agreed: `isPremiumUser = false`; no premium subscription type, management system, expiration, cancellation, or pending-downgrade fields were present; and `icalendarId` was absent after iCalendar was disabled during cleanup. The successful Complete-labelled operations therefore were not accidentally running under a premium trial or subscription.

## Server-enforcement result

**No server-enforced premium gate was found in the exercised SDK data surface.** Every mapped Complete-labelled data operation tested on the free account was accepted by the server and, where persistence applies, survived a fresh-client sync/readback.

This does not mean every AnyList product entitlement is free. The official applications may restrict access before issuing the request, and non-data product features such as official Web/Mac app access, watch availability, or support level are outside this investigation.

## Free account: server verified

The following were exercised while the account remained non-premium:

| Surface | Observed free-account server behavior |
| --- | --- |
| Folders | Create/update/delete round-trip persisted. |
| Meal-plan events, labels and event-list items | Mutations persisted on fresh sync. |
| Meal-plan templates, template groups and template events | Round-trip persisted, including template/event icons used by the tested paths. |
| Meal-plan email | `/data/meal-planning-calendar/send-as-email` delivered a real weekly meal-plan email. |
| Meal-plan iCalendar | Enable returned `statusCode = 0` and populated `icalendarId`; disable returned `statusCode = 0` and cleared it. |
| Binary photo upload | A real JPEG uploaded through `/data/photos/upload`; the resulting photo URL fetched with HTTP 200. |
| URL photo import | `upload_url()` succeeded and the resulting remote photo fetched with HTTP 200. |
| Web image search | Returned live results on the free account. |
| Shopping-item photos | Uploaded photo IDs persisted in `photoIds` across fresh sync. |
| Recipe photos | Uploaded binary and remote photo IDs persisted in recipe `photoIds`. |
| Recipe scaling | `scaleFactor = 2.5` persisted. |
| Recipe web import | Imports continued after the advertised free allowance reached zero; imported recipes could still be saved and freshly read back. |
| UPC product lookup | Returned a product match while non-premium. |
| Stores and store filters | CRUD, ordering, selected `storeFilterId`, and item/store assignment persisted. |
| Item prices | Price/store metadata and the exercised price-related item fields persisted. |
| Store/price display settings | `shouldHideStoreNames`, `shouldHidePrices`, `shouldHideRunningTotals`, `leftRunningTotalType`, and `rightRunningTotalType` persisted. |
| Passcode | First-time list `password` creation persisted. |
| Location reminders | `PBNotificationLocation` plus `locationNotificationsEnabled` persisted. |
| Built-in premium theme | Android premium theme ID `80c0524de86c43349a82e841560e517d` persisted. |
| Custom light theme | `customTheme` persisted. |
| Custom dark theme | `customDarkTheme` persisted when sent with the Android-proven `save-custom-dark-theme` handler. |
| List icon | Tested list icon (`produce`) persisted; the Android audit found no premium gate around shopping-list icons. |
| Badge mode | The exercised `set-badge-mode` list-settings mutation was accepted. |

The earlier HTTP 500 seen while testing `/data/photos/upload` was not a premium rejection. That probe supplied PNG bytes while the helper generated a `.jpg` server filename. Retesting with a real JPEG succeeded and the uploaded object was retrievable.

## Recipe import quota

The server maintains `freeRecipeImportsRemainingCount`, but the tested endpoint did not enforce it as authorization.

The disposable account successfully parsed and saved repeated imports while the response counter decreased through zero. Further imports still returned recipe-import `statusCode = 0`, returned a recipe, and continued decrementing the counter into negative values; the investigation ultimately observed `freeRecipeImportsRemainingCount = -11` while recipe saves still succeeded.

This is strong evidence that the counter is entitlement/accounting metadata consumed by official-client UI rather than a server permission check on the tested import/save paths.

## Android 3.0.3 client-side Complete gates

Android 3.0.3 build 278 stores the account entitlement as `ALIsPremiumUserKey`, sourced from `PBAccountInfoResponse.isPremiumUser`. Its `uc.c.c()` checks gate the following user-facing features before or around the corresponding data operations. False positives that only affected telemetry or unrelated presentation were excluded from this list.

| Feature | Android evidence | Server result from free account |
| --- | --- | --- |
| Folders | `cd/c3.java` premium checks around create/edit flows | Accepted and persisted. |
| Themes, including custom themes | `rc/b3.java`, `cd/g4.java`, `a2/e.java` | Premium built-in theme, custom light theme, and custom dark theme persisted. |
| Meal planning generally | `dd/a3.java`, `dd/i1.java`, related meal-plan UI | Events/labels/templates/groups persisted. |
| Meal-plan labels and event-icon UI | `dd/j2.java` | Persisted when sent through data operations. |
| Item photos | `sd/m.java`, `cd/n.java`; Android also strips/blocks photo behavior for non-premium UI flows | Real binary upload and item attachment persisted. |
| Stores and filters | `ad/q1.java`, `cd/w4.java`, `cd/w1.java` | CRUD/ordering/selection persisted. |
| Prices | `cd/w1.java`, `cd/g2.java` | Price metadata persisted. |
| Web image search | `ed/p1.java` | Search returned live results. |
| Recipe import upsell/quota display | `ed/i.java`, `jd/u0.java` | Imports continued after zero remaining. |
| Recipe photos | `ed/o.java` | Uploaded photo IDs persisted. |
| Recipe scaling | `ed/r0.java`, `dd/j2.java` | `scaleFactor` persisted. |
| Passcode lock | `cd/m4.java` | First-time password persisted. |
| Location reminders | `cd/w1.java`, `rc/i3.java` | Location plus enable setting persisted. |

These checks are evidence about **official Android UI policy**, not wire-level permission requirements.

## Android features that were not premium-gated

The audit did not find a `uc.c.c()` entitlement check around shopping-list icon selection. There is therefore no evidence for a separate class of premium-only shopping-list icons in this Android build. A tested list icon also persisted on the free account.

The template-icon picker and iCalendar settings row likewise were not themselves guarded by the same premium predicate, even though they belong to broader meal-planning UI. UPC lookup itself was also not premium-gated at the network layer exercised by the SDK.

## `customTheme` versus `customDarkTheme`

`PBListSettings` stores two custom `PBListTheme` messages:

- `customTheme`: the user-defined normal/light appearance for a list.
- `customDarkTheme`: the user-defined dark-mode companion appearance for that same custom theme.

They are not independent theme selections. Android treats them as paired variants of the same list theme. When copying/migrating settings, it rewrites both to the same generated custom-theme identifier and current user ID, and preserves `listThemeId` so that identifier continues to select the pair.

If `customDarkTheme` is absent, Android derives a dark presentation from the normal theme. The native implementation transforms known light textures and colors—for example `classic_paper` to `noisy_net`, `wood` to `dark_wood`, and adjusts table/text/selection colors for dark rendering. Supplying `customDarkTheme` overrides that automatic derivation with an explicit user-designed dark variant.

The native save operations are symmetric:

- `customTheme` → handler `save-custom-theme`
- `customDarkTheme` → handler `save-custom-dark-theme`

The second handler is proven by Android source (`a2/e.java`) but is not present in the web-derived `official_surface.json`. The SDK therefore treats it as native-client authority, not as a guessed handler.

## SDK policy

The SDK exposes subscription state as account metadata, but **does not use `is_premium_user` to reject data operations that the server itself accepts**. Adding client-side Complete checks would make the SDK less faithful to the observed server protocol.

If AnyList later begins returning an actual permission/subscription rejection, the normal transport error path should surface that server response rather than relying on hard-coded entitlement policy in the client library.

## Remaining unknowns

- The client-side policy audit covered Android 3.0.3 build 278. Equivalent iOS UI gates were not exhaustively audited.
- A reserved conformance-list ID from another account caused some old fixture-based store tests to fail their safety guard. Self-created disposable-list probes covered the same server operations successfully, so that fixture mismatch is not premium evidence.
- Future AnyList server/client releases may change these behaviors.
