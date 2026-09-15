# Free-account entitlement behavior

Results from a disposable free account tested on 2026-09-13. Server behavior can change independently of this project.

## Test account state

All probes used a newly created disposable account. Fresh authentication reported `is_premium_user = false` before and after the test run.

`GET /data/account/info` also reported `isPremiumUser = false`, with no premium subscription metadata. `icalendarId` was absent after cleanup.

## Server-enforcement result

**No server-side premium gate was found in the tested SDK data paths.** Every tested Complete-labelled operation was accepted, and persistent mutations survived a fresh sync.

Official apps still apply client-side product restrictions, and this test did not cover Web/Mac app access, watch availability, support tiers, or other non-data entitlements.

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

Repeated recipe imports succeeded as `freeRecipeImportsRemainingCount` passed through zero and became negative. The lowest observed value was `-11`; imports and saves still returned success.

In these tests, the counter behaved as client-facing entitlement/accounting metadata rather than a server permission check for the import/save paths.

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

These checks describe Android UI gating. The server behavior is listed separately in the right column.

## Android features that were not premium-gated

No `uc.c.c()` entitlement check was found around shopping-list icon selection in this Android build. A tested list icon also persisted on the free account.

The template-icon picker and iCalendar settings row were also not guarded by that premium predicate. UPC lookup was not server-gated in the tested SDK path.

## `customTheme` versus `customDarkTheme`

`PBListSettings` stores two custom `PBListTheme` messages:

- `customTheme`: the user-defined normal/light appearance for a list.
- `customDarkTheme`: the user-defined dark-mode companion appearance for that same custom theme.

They are not independent theme selections. Android treats them as paired variants of the same list theme. When copying/migrating settings, it rewrites both to the same generated custom-theme identifier and current user ID, and preserves `listThemeId` so that identifier continues to select the pair.

If `customDarkTheme` is absent, Android derives a dark presentation from the normal theme. The native implementation transforms known light textures and colors—for example `classic_paper` to `noisy_net`, `wood` to `dark_wood`, and adjusts table/text/selection colors for dark rendering. Supplying `customDarkTheme` overrides that automatic derivation with an explicit user-designed dark variant.

The native save operations are symmetric:

- `customTheme` → handler `save-custom-theme`
- `customDarkTheme` → handler `save-custom-dark-theme`

The second handler appears in Android source (`a2/e.java`) but not in the web-derived `official_surface.json`, so the Android implementation supplies the handler name.

## SDK policy

The SDK exposes subscription state as account metadata and does not block operations solely because `is_premium_user` is false. Server rejections are surfaced normally.

If AnyList later returns a permission or subscription rejection, the transport will surface that server response.

## Remaining unknowns

- The client-side policy audit covered Android 3.0.3 build 278. Equivalent iOS UI gates were not fully audited.
- A reserved conformance-list ID from another account caused some old fixture-based store tests to fail their safety guard. Self-created disposable-list probes covered the same server operations successfully, so that fixture mismatch is not premium evidence.
- Future AnyList server/client releases may change these behaviors.
