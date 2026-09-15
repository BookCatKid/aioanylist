# AnyList hidden endpoints — Android 3.0.3 research

Notes from the AnyList Android 3.0.3 endpoint audit. Request shapes, response handling, and source locations are recorded here or in the linked artifacts.

Machine-readable inventory: `research/android/endpoints.json`.
Raw evidence: `research/android/endpoint_call_sites.tsv`,
`research/android/endpoint_context.txt`.

## Summary

- `/data/auth/sign-out` confirmed in Android (`e7/w6.java:46`): **form-encoded**
  (not multipart like iOS), bearer + `refresh_token` + optional
  `push_token`/`push_token_type=fcm`. Wipes local session afterwards.
- Android 3.0.3 exposed 22 AnyList API routes that were missing from the SDK at discovery time. **5 native routes are now implemented**: remote config, Alexa default-list selection, place search, image search, and UPC lookup. The other **17 routes are not exposed** as high-level SDK methods; see §10.
- The APK ships AnyList's own protobuf schema. `model.proto` == the
  SDK's `proto/schema.json` (156/156 messages); `server.proto` adds only
  `PBUserProfileProperty` / `PBUserProfileInfo` (Mixpanel people profile).
  So **every hidden endpoint's message shape is already available**.
- `/data/track-event`, `/data/update-profile`, `/groups/`, `/flags/` are
  **Mixpanel**, not AnyList API. Grocery endpoints are **eMeals/Instacart**.
- Realtime `add-user-listener` is WSS + bearer + exp backoff (SDK matches).

## 1. Method and provenance

- App: AnyList Android, package `com.purplecover.anylist`, version **3.0.3
  (build 278)** — verified latest on APKPure at download time.
- Fetched with `apkeep -a com.purplecover.anylist -d apk-pure` (XAPK with base
  APK + config splits); base APK extracted and decompiled with `jadx 1.5.6`.
- Route inventory built from both jadx Java string literals and raw DEX strings. The AnyList route sets matched.
- Every call site captured with surrounding code; request maps, encodings,
  response parsers, and protobuf classes read from the decompiled sources.
- Reproduction + file guide: `research/android/README.md`.

## 2. Transport model (what every request looks like)

Base URL `https://production.anylist.com:443/` (`xc/b.java:53-56`).
Per-request headers set in the client constructor (`xc/b.java:66-79`):

- `X-AnyLeaf-Android-App-Version: 3.0.3`
- `X-AnyLeaf-API-Version: 3`
- `X-AnyLeaf-Client-Identifier: <DeviceClientID UUID, persisted>`
- `Accept-Language: <device locale>`

Request builders (`xc/b.java`):

| Helper | Verb/encoding | Used for |
|---|---|---|
| `d(url, cb)` | GET | reads, product lookup, version-check |
| `f(url, map, cb)` | POST `application/x-www-form-urlencoded` | token endpoints, metrics, sign-out, push token, purchase unlock, password flows |
| `g(url, map, cb)` | POST multipart; values may be `String`/`Number`/raw `byte[]`/file part; empty map degrades to empty form POST | protobuf sync/data endpoints, most feature endpoints |
| `h(url, map)` | synchronous POST form | `/auth/token/refresh`, `exchange-signed-user-id` |

Auth: `xc.a` injects `Authorization: Bearer <access token>` (omitted when no
token, so the same client works pre-login). `xc.g` is the 401 authenticator:
if the failed request's bearer differs from the current access token, retry
with the current one; otherwise refresh once via `/auth/token/refresh` and
retry; give up after 3 attempts. This is the behavior the SDK's
refresh-and-retry already mirrors.

Clients: `xc.b.f14690f` (API), `xc.b.f14691g` (photos upload path),
`xc.c.f14697a` (unauthenticated, token refresh), `xc.h.f14704h` (realtime).
`b.e(url)` dedupes in-flight requests.

Sync services (`yc/f`, `yc/o`, `yc/p`; root `yc/m0.java`): each domain owns an
update endpoint, a get/by-id endpoint, an operation-queue id, and a protobuf
operation-list class. Confirmed queue ids:

- `shopping-list-operations` + `logical-timestamp-shopping-list-operations`
- `starter-list-operations` + `ordered-starter-list-ids-operations`
- `starter-list-setting-operations`, `list-folder-operations`,
  `list-setting-operations`, `category-operations`,
  `categorized-item-operations`, `meal-plan-operations`, `recipe-operations`,
  `mobile-app-setting-operations`, `app-notice-operations`

## 3. Sign-out confirmation (`/data/auth/sign-out`)

`e7/w6.java:46`: `bVar.f("/data/auth/sign-out", map, …)` where `map` holds the
stored refresh token (`xc.c.b()`), plus `push_token` and
`push_token_type="fcm"` when an FCM token exists in prefs (`ALFCMTokenKey`).
No request is sent when no refresh token exists ("No refresh token present
during user sign out!"). Callback `n5.c(…, 26)` forwards success/failure into
the local-wipe routine `ed/x2.java` case 25: cancel in-flight calls, clear
access + refresh tokens and auth prefs, delete the local DB and user files,
set the completion flag.

Differences vs the iOS capture: Android sends
`application/x-www-form-urlencoded`; iOS sends multipart. The server accepts
both (live SDK tests use multipart). Field names are otherwise identical.

## 4. Endpoint inventory

`hidden` = present in Android, absent from the SDK. `sdk` = already covered.
Full per-endpoint detail (exact fields, response keys, evidence) is in
`research/android/endpoints.json`.

| Endpoint | Req | Response | Status |
|---|---|---|---|
| `POST auth/token` (`email`,`password`) | form | JSON token set | sdk |
| `POST /auth/token/refresh` (`refresh_token`) | form | JSON token set | sdk |
| `POST /auth/token/exchange-signed-user-id` (`signed_user_id`) | form | JSON token set | known / not exposed |
| `POST /data/signup` (`use_token_auth`,`create_default_list`,`create_default_recipes`,`email`,`password?`,`preferred_user_id?`,`locale`) | multipart | JSON token set / `err` | known / not exposed |
| `POST /data/send-password-reset` (`email`) | form | **plain text** `SUCCESS`/`NO_ACCOUNT` | known / not exposed |
| `POST /data/reset-password` (`email`,`password`,`reset_token`,`use_token_auth`) | form | JSON token set / `err` | known / not exposed |
| `GET /data/account/info` | — | `PBAccountInfoResponse` | sdk |
| `POST /data/account/info` (`account_info` proto bytes) | multipart | `PBAccountInfoResponse` | sdk |
| `POST /data/account/change-password` (`current_password`,`new_password`,`refresh_token`) | multipart | `PBAccountChangePasswordResponse` (**rotates both tokens**) | known / not exposed |
| `POST /data/account/add-subuser` (`email`) | multipart | `PBAccountInfoResponse` | known / not exposed |
| `POST /data/account/remove-subuser` (`email`) | multipart | `PBAccountInfoResponse` | known / not exposed |
| `POST /data/account/request-delete` (no fields) | form | JSON `success`,`localized_reason` | known / not exposed |
| `POST /data/account/unlock-google-play-purchase` (`purchaseJson`) | form | JSON `already_processed`,`valid`,`account_info` (base64 proto),`is_subscription_modification` | known / not exposed |
| `POST /data/account/update-locale` (`locale`) | multipart | none parsed | known / not exposed |
| `POST /data/update-push-token` (`push-token`,`type=fcm`) | form | none parsed | known / not exposed |
| `POST /data/auth/sign-out` (`refresh_token`,`push_token?`,`push_token_type?`) | form | none parsed | sdk |
| `POST /data/app-notices/get` (3 proto timestamp fields) | multipart | `PBAppNoticesResponse` | known / not exposed |
| `POST /data/app-notices/update` (op queue `app-notice-operations`) | multipart | `PBEditOperationResponse` | known / not exposed |
| `GET /data/version-check` | — | JSON feature flags (see §5.4) | sdk (Android-derived) |
| `POST /data/increment-metric` (`metric`) | form | none parsed | known / not exposed |
| `POST /data/contact/app-rating-prompt-feedback` (8 fields, §5.5) | multipart | none parsed | known / not exposed |
| `POST /data/alexa/link-list` (`alexa_list_id?`,`anylist_list_id?`) | multipart | JSON | sdk |
| `POST /data/alexa/set-default-list-id` (`list_id`) | multipart | JSON | known / not exposed |
| `POST /data/alexa/set-is-enabled-for-alexa-for-list-ids` (2 `PBValue` byte fields) | multipart | JSON | sdk |
| `POST /data/alexa/unlink-anylist-list` (`anylist_list_id`) | multipart | JSON | sdk |
| `POST /data/alexa/unlink-list` (`alexa_list_id`, optional `should_update_is_enabled_for_alexa_property=n`) | multipart | JSON | sdk |
| `POST /data/gassistant/link-list` (`anylist_list_id`) | multipart | JSON `success`,`should_contact_support`,`network_error`,`server_error_message` | known / not exposed |
| `POST /data/gassistant/unlink-list` (`google_assistant_list_id`) | multipart | JSON `success` | known / not exposed |
| `POST /data/maps/place-search` (`query`,`lat`,`lng`,`radius`) | multipart | JSON Google-Places-shaped `results[]` | sdk (Android-derived) |
| `POST /data/photos/image-search` (`query`) | multipart | JSON `results[].MediaUrl`, `Thumbnail.MediaUrl` | sdk (Android-derived) |
| `GET /data/product-lookup/{upc}` | path | `PBProductLookupResponse`; 404 = miss | sdk (Android-derived) |
| `POST /data/recipes/web-import` (`url`) | multipart | `PBRecipeWebImportResponse` | sdk |
| `POST /data/shopping-lists/share-list` (`operation` = `PBListOperation`, handler `share-shopping-list`) | multipart | `PBShareListOperationResponse` | sdk |
| `POST /data/meal-planning-calendar/set-icalendar-enabled` (`icalendar_request` proto bytes) | multipart | `PBMealPlanSetICalendarEnabledRequestResponse` | sdk |
| `POST /data/user-recipe-data/accept-recipe-link-request` (`link_request_id`,`user_id`) | multipart | `PBRecipeLinkRequestResponse` | sdk |
| `POST /data/user-recipe-data/cancel-recipe-link-request` (`link_request` proto bytes) | multipart | `PBRecipeLinkRequestResponse` | sdk |
| `POST /data/user-recipe-data/request-recipe-link-v2` (`link_request` proto bytes) | multipart | `PBRecipeLinkRequestResponse` | sdk |
| `POST /data/user-recipe-data/unlink-recipes` (`user_id`) | multipart | `PBRecipeLinkRequestResponse` | sdk |
| `POST /data/user-data/get` (`timestamps?`,`client_info`,`is_background_fetch?`) | multipart | `PBUserDataResponse` | sdk |
| `WSS /data/add-user-listener` | upgrade | push invalidations | sdk |
| `POST /data/photos/upload` (`photo` file part `<id>.jpg`) | multipart | none parsed | sdk |
| remaining `*/all`, `*/update`, `*/by-id`, `*/update-v2`, `*/ordered-ids` sync endpoints | proto sync | proto | sdk |

## 5. Hidden-endpoint details

### 5.1 Auth: Google signed-id exchange, signup, password flows

- `POST /auth/token/exchange-signed-user-id` (`xc/c.java:112`): form field
  `signed_user_id` from prefs `ALSignedUserIDKey`; JSON
  `access_token`/`refresh_token`; key cleared after use. Google sign-in path.
- `POST /data/signup` (`e7/u6.java:42`): multipart `use_token_auth=1`,
  `create_default_list=0`, `create_default_recipes=0`, `email`, optional
  `password` (omitted when empty — social signups), optional
  `preferred_user_id` (random UUID in `ALPreferredUseIDKey`), `locale`
  (`en-US` style). Response JSON `user_id`/`access_token`/`refresh_token` or
  `err=EMAIL_ALREADY_REGISTERED` (`s/g.java:891`).
- `POST /data/send-password-reset` (`ad/b.java:85`): form `email`. Body is
  plain text: `SUCCESS` or `NO_ACCOUNT`. HTTP 500 → error state.
- `POST /data/reset-password` (`ad/b.java:174`): form `email`, `password`,
  `reset_token`, `use_token_auth=1`. Response JSON `user_id`,
  `access_token`, `refresh_token`, `is_premium_user`; error shape carries
  `err`. On success the client stores the new pair and resyncs. Deep-link
  entry `https://www.anylist.com/reset-password`.
- `POST auth/token` login response (`s/g.java:1005`): JSON `user_id`,
  `access_token`, `refresh_token`, `is_premium_user`.

### 5.2 Account: password change, subusers, delete, purchase, locale

- `POST /data/account/change-password` (`od/e0.java:154`): multipart
  `current_password`, `new_password`, `refresh_token`.
  `PBAccountChangePasswordResponse { status_code, error_title,
  error_message, refresh_token, access_token }` — **tokens rotate here**.
- `POST /data/account/add-subuser` / `remove-subuser` (`hd/k.java:254/238`):
  multipart `email`; response `PBAccountInfoResponse` with updated
  `master_user`/`subusers` and `status_code`.
- `POST /data/account/request-delete` (`od/a.java:133`): empty form POST;
  JSON `success`, `localized_reason` shown in an alert (`n5/c.java:411`).
- `POST data/account/unlock-google-play-purchase` (`pc/h.java:117`): form
  `purchaseJson` = raw Google Play Billing JSON; also retried from
  `ALUnprocessedPurchaseKey` on foreground. Response JSON:
  `already_processed`, `valid`, `account_info` (**base64-encoded
  `PBAccountInfoResponse`**), `is_subscription_modification`.
  Related schema: `PBGooglePlayPurchase { order_id, purchase_info,
  purchase_token }`, `PBIAPReceipt`, Stripe/Redemption messages.
- `POST /data/account/update-locale` (`MainActivity.java:154`): multipart
  `locale` (`en-US`); sent when logged in and on locale change.
- `PBAccountInfoResponse` core fields: `status_code, first_name, last_name,
  email, is_premium_user, subscription_type, subscription_management_system,
  expiration_timestamp_ms(+_str), master_user, subusers[], icalendar_id,
  subscription_is_canceled, subscription_is_pending_downgrade`.

### 5.3 Push, notices, config, metrics, feedback

- `POST /data/update-push-token` (`e7/w5.java:14`): form `push-token`
  (hyphenated — unlike sign-out's `push_token`), `type=fcm`; FCM token from
  `ALFCMTokenKey`; sent when a user is present.
- `POST /data/app-notices/get` (`yc/b.java:153`): multipart
  `global_app_notice_timestamps` + `user_app_notice_timestamps`
  (`PBTimestampList` bytes) + `app_notice_user_data_timestamp`
  (`PBTimestamp` bytes). Response `PBAppNoticesResponse { new/updated/
  removed global + user notices, user_data }`.
- `POST /data/app-notices/update` (`yc/b.java:32`): queue
  `app-notice-operations`, `PBAppNoticeOperationList`.
- `GET /data/version-check` (`AnyListApp.java:193`, `n7/c0.java:136`): a
  **remote-config** endpoint despite the name. JSON keys:
  `should_show_legacy_alexa_settings`,
  `alexa_skill_disabled_footer_text_android`,
  `should_hide_alexa_linking_ui`, `alexa_skill_status` (object),
  `google_assistant_locales_message`, `google_assistant_shutdown_message`,
  `should_hide_google_assistant_linking_ui`, `shop_online_retailer_promotions`
  (array).
- `POST /data/increment-metric` (single form field `metric`): all 10 observed
  names start `anylist.client.`: `app_notice_notification_permission_denied`,
  `did_show_app_notice_notification`, `did_tap_app_notice_notification`,
  `did_show_whats_new_notification`, `did_tap_whats_new_notification`,
  `did_view_app_notice_from_settings`, `did_view_whats_new_from_settings`,
  `settings_did_scroll_to_app_notice`, `settings_did_scroll_to_whats_new`,
  `whats_new_notification_permission_denied`.
- `POST data/contact/app-rating-prompt-feedback` (`ad/q3.java:252`):
  multipart `body_text`, optional `account_email`, `platform=Android`,
  `app_version` (e.g. `3.0.3 (278)`), `os_version`, `device_model`,
  `is_paid` (`1`/`0`), `star_rating`. Response ignored.

### 5.4 Assistant endpoints

- `POST /data/alexa/set-default-list-id` (`dd/d3.java:469`): multipart
  `list_id` (the only Alexa route the SDK lacks).
- `POST /data/gassistant/link-list` (`cd/q2.java:89`): multipart
  `anylist_list_id`; client pre-rejects duplicate links. Response JSON
  `success`, `should_contact_support`, `network_error`,
  `server_error_message`. Related schema `PBGoogleAssistantList(+Item,
  Operation, Task, User)`.
- `POST /data/gassistant/unlink-list` (`cd/q2.java:119`): multipart
  `google_assistant_list_id`; response JSON `success`.

### 5.5 Search and lookup

- `POST /data/maps/place-search` (`ad/m2.java:187`): multipart `query`
  (string), `lat`/`lng` (doubles), `radius` (int meters = half the visible-map
  diagonal). Response is Google-Places-shaped JSON: `results[].geometry.
  location.{lat,lng}`, `results[].name`, `results[].formatted_address`
  (`a8/i.java:528`).
- `POST /data/photos/image-search` (`ad/x.java:26`): multipart `query`.
  Response JSON `results[].MediaUrl` + `results[].Thumbnail.MediaUrl`
  (`hb/c.java:521`). Android 3.0.3 contains a "Bing image search failed!"
  log message, but a live 2026 request returned Brave Search proxy thumbnail
  URLs, so the current upstream provider has changed or is no longer inferable
  from that historical client string.
- `GET /data/product-lookup/{upc}` (`sd/m.java:308`, `cd/n.java:2329`):
  barcode appended to the path; premium-gated client-side. Response
  `PBProductLookupResponse { listItem, product_thumbnail_url }`; HTTP 404 =
  no match (`sd/b.java`).

## 6. Protobuf schema findings

- `research/android/model.proto` (156 messages) matches
  `src/aioanylist/proto/schema.json` **exactly** (name-for-name). The
  web-derived schema is complete — no hidden message to harvest there.
- `research/android/server.proto` adds exactly two messages, both Mixpanel
  people-profile shaped and absent from the SDK schema:

```proto
message PBUserProfileProperty {
  oneof value {
    string string_value = 1;
    double numeric_value = 2;
    bool boolean_value = 3;
    int64 date_value = 4;  // Unix timestamp in milliseconds
  }
}
message PBUserProfileInfo {
  required string identifier = 1;  // user id
  map<string, PBUserProfileProperty> profile_properties = 2;
}
```

- Hidden-endpoint message shapes worth knowing (`model.proto` /
  `schema.json`): `PBAppNoticesResponse`, `PBAppNoticeOperation(List)`,
  `PBAppNoticesUserData`, `PBAccountChangePasswordResponse`,
  `PBProductLookupResponse`, `PBMealPlanSetICalendarEnabledRequest(+Response)`,
  `PBRecipeLinkRequest`, `PBGooglePlayPurchase`, `PBAuthTokenInfo`
  (documents token fields incl. `jti`, blacklist/replacement bookkeeping),
  `PBAccountInfoResponse` (§5.2), `PBTimestampList`.

## 7. Ruled out: third-party traffic on AnyList hosts

- `POST /data/track-event`, `POST /data/update-profile`, `/groups/`,
  `/flags/` on `production.anylist.com` are **Mixpanel 8.2.0** with custom
  endpoints (`kc/k.java:122-141`) — not AnyList API.
- eMeals grocery integration (bundled SDK, likely out of Python-SDK scope):
  `grocery-sdk.emeals.com/cart-upload-url?partner=anylist&filename=…`,
  `/latest-vendor-version?name=…&version=…`,
  `/v3/vendor-availability?postalcode=…`, `serverless.api.emeals.com{route}`;
  Instacart WebView `https://www.instacart.com/v3/containers/…`.
- Analytics/crash SDKs: Firebase, Segment, Bugsnag, Google ads/measurement.

## 8. Realtime listener

`wss://production.anylist.com/data/add-user-listener` (`hg/n.java:134` via
`xc.h` client): bearer-authenticated WebSocket upgrade with
`permessage-deflate`; exponential reconnect 500 ms → 600 s cap; close code
1000 "client closing"; skipped while in background-fetch state. Server pushes
drive sync refreshes. Matches the SDK's `realtime.py`.

Other hosts (non-API): `photos.anylist.com` (photo image base),
`https://icalendar.anylist.com/{icalendar_id}.ics` (personal feed; id from
`PBAccountInfoResponse.icalendar_id`), `help.anylist.com` articles,
`www.anylist.com/{reset-password, google-assistant/list-id/, recipes/home,
jobs, privacy}`.

Android deep links: `https://www.anylist.com/reset-password`,
`https://www.anylist.com/google-assistant/list-id/`, plus `.anylistrecipes`
file import.

## 9. Web-only endpoints (in SDK, absent from Android 3.0.3)

`/auth/logout`, `*/send-as-email` (meal plan, recipes, shopping lists),
`/data/photos/upload-url`,
`/data/user-recipe-data/desktop-recipe-import-extension`,
`/data/web/set-mac-app-download-prompt-cookie`,
`/data/web/set-welcome-screen-cookie`.

## 10. Routes kept out of the high-level SDK

The remaining routes stay in the research inventory instead of the high-level SDK:

1. `auth/token/exchange-signed-user-id`, `signup`, `send-password-reset`, and
   `reset-password` — account creation/recovery/social-auth flows.
2. `account/change-password` — changes credentials and rotates both token values.
3. `account/add-subuser`, `account/remove-subuser`, `account/request-delete`,
   and `account/update-locale` — account/family/global-state management rather
   than ordinary AnyList data access.
4. `account/unlock-google-play-purchase` — specifically tied to Google Play
   Billing purchase JSON and platform-store state.
5. `app-notices/get` and `app-notices/update` — AnyList's in-app announcement
   UI plus read/dismiss bookkeeping, not user list/recipe data.
6. `update-push-token` — FCM device-registration lifecycle plumbing.
7. `gassistant/link-list` and `gassistant/unlink-list` — retained for protocol
   documentation only; the Android source describes Google Assistant support as
   shut down in 2023.
8. `increment-metric` and `contact/app-rating-prompt-feedback` — app telemetry and rating-dialog feedback.

Still out of scope: eMeals/Instacart, Mixpanel proxy endpoints, and the two
`PBUserProfile*` server/Mixpanel profile messages. Web-only email/cookie flows
remain implemented where they were already part of the Web-derived SDK.
