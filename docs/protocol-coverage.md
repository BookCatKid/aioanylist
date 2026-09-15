# Protocol coverage

Generated from the checked-in AnyList Web surface and Android endpoint research. `unknown`
means no implementation match was found with source evidence and the row needs review.

## Summary

- Endpoints: **72** total — implemented=54, intentionally_unimplemented=18
- Operation handlers: **202** total — implemented=195, intentionally_unimplemented=7
- Web protobuf messages: **156**
- Android protobuf messages: **156**
- Schema comparison: exact name match on all 156 web-derived messages

## Endpoint unknowns

None.

## Operation-handler unknowns

None.

## Endpoint exclusions

These official routes were found in source but are not exposed through the public SDK.

- `ANY /auth/logout` — Browser/XSRF HTML form session logout; token-auth SDK uses the official native /data/auth/sign-out endpoint instead.
- `POST /auth/token/exchange-signed-user-id` — Used when f14698b is set (Google sign-in path). Signed user id stored in prefs ALSIGNEDUserIDKey, POSTed once, then cleared.
- `POST /data/account/add-subuser` — Family/household management. Response carries updated account info incl. subusers.
- `POST /data/account/change-password` — All three passwords must be >= 4 chars client-side. Response rotates BOTH tokens.
- `POST /data/account/remove-subuser` — Response carries updated account info incl. subusers.
- `POST /data/account/request-delete` — Null map -> form POST with no fields. Shows success/failure alert using localized_reason.
- `POST /data/account/unlock-google-play-purchase` — Raw Google Play Billing purchase JSON. Retried on app foreground from ALUnprocessedPurchaseKey. Marks purchase processed via ALProcessedPurchaseKey.
- `POST /data/account/update-locale` — Sent on startup when a user exists, and on locale change. Format e.g. en-US.
- `POST /data/app-notices/get` — Sync service yc/b. Empty-field form POST when the local queue state is not X.
- `POST /data/app-notices/update` — Operation queue id app-notice-operations; ops serialized as PBAppNoticeOperationList. Android handlers are mark-notice-ids-as-read and dismiss-notice-ids. Not exposed by the SDK because this is native-app UI bookkeeping.
- `POST /data/contact/app-rating-prompt-feedback` — In-app rating dialog feedback. Success shows thanks dialog; response body not parsed.
- `POST /data/gassistant/link-list` — Client refuses duplicate links across lists before calling. Not exposed because the Google Assistant integration is legacy/shut down.
- `POST /data/gassistant/unlink-list` — Response has success flag only. Not exposed because the Google Assistant integration is legacy/shut down.
- `POST /data/increment-metric` — Full metric name list: anylist.client.{app_notice_notification_permission_denied, did_show_app_notice_notification, did_tap_app_notice_notification, did_show_whats_new_notification, did_tap_whats_new_notification, did_view_app_notice_from_settings, did_view_whats_new_from_settings, settings_did_scroll_to_app_notice, settings_did_scroll_to_whats_new, whats_new_notification_permission_denied}.
- `POST /data/reset-password` — Deep-link entry: www.anylist.com/reset-password. On success the app stores the new token pair and refreshes sync.
- `POST /data/send-password-reset` — Plain-text (not JSON) body. HTTP 500 maps to server-error state.
- `POST /data/signup` — Password omitted for passwordless (Google/Apple) signups. err=EMAIL_ALREADY_REGISTERED on duplicate.
- `POST /data/update-push-token` — Hyphenated field names (unlike sign-out underscores). Sent on login/startup and token refresh; gated on user present. Not exposed because this is native mobile push-registration lifecycle plumbing.

## Operation-handler exclusions

- `dismiss-notice-ids` — Native app-notice UI bookkeeping.
- `mark-notice-ids-as-read` — Native app-notice UI bookkeeping.
- `save-initial-list-settings` — Android atomic new-list PBListSettings creation primitive. Not exposed as a second public list-settings creation API; the SDK already exposes the equivalent initialization workflow.
- `save-retail-product-submission` — Android Submit Public Item flow carrying an edited ListItem. Not exposed as a convenience API because it writes user-supplied metadata into AnyList's shared public product database.
- `set-client-has-shown-alexa-onboarding` — Native onboarding UI bookkeeping.
- `set-client-has-shown-google-assistant-onboarding` — Obsolete Google Assistant onboarding UI bookkeeping.
- `set-should-not-link-new-lists-with-google-assistant-by-default` — Obsolete Google Assistant integration preference.

## Generated report

The generator can emit JSON containing every endpoint/handler row, SDK source hits,
and Android decompiler evidence paths. The JSON report is generated on demand and is
not tracked in Git. This file lists unresolved and excluded surface only.

## Android native-only action-like candidates

These are quoted native literals that resemble operation handlers but are not present
in the web handler inventory. They are review candidates, not automatically treated as
protocol operations because obfuscated Android code also contains handler-like UI/state
strings.

None.

