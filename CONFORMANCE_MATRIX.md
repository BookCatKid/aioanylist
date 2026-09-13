# AnyList SDK Conformance Matrix

This is the authoritative verification checklist for the SDK. **Official executable `app.js` behavior is the primary specification.** Exact source reconstruction is the default; captured requests or server acceptance alone never justify invented behavior. A deliberate divergence is allowed only when the source defect/limitation is explicit, the alternative is supported by the official schema/runtime model, and both offline regression evidence and a disposable live test prove the alternative. Such cases are labeled as intentional divergences rather than parity.

## Status legend

- **✅ LIVE VERIFIED** — the current code path has been exercised against the real AnyList service and the exercised result was validated. This is the strongest evidence available; it does **not** mean every imaginable edge case is mathematically proven.
- **✅ LOCAL VERIFIED** — behavior is intentionally local-only and has been verified end-to-end locally (for example token logout).
- **🧪 OFFLINE VERIFIED** — official `app.js` / embedded schema behavior is covered by offline regression tests, but the method has not yet been proven with a live server mutation/readback.
- **🟡 LIVE PARTIAL** — a meaningful live path passed, but another direction/side effect remains intentionally untested.
- **🟠 LIVE RETEST REQUIRED** — relevant implementation changed after the last live attempt; do not treat older live results as current proof.
- **🔷 VERIFIED INTENTIONAL DIVERGENCE** — differs deliberately from a precisely identified `app.js` path because the official path has a concrete defect/limitation; the alternative is schema-supported and locked by both offline and live evidence.
- **⚪ NOT LIVE TESTED** — no current live evidence.
- **🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE** — deliberately not tested because it would modify pre-existing non-disposable state, another person, an account-wide setting that cannot be isolated, or an external/irreversible side effect without a cleanup path. Tests may create and fully remove uniquely identified disposable resources in otherwise global domains.
- **⚠️ OFFICIAL CONTRADICTION** — official JavaScript and embedded protobuf schema conflict; do not invent a wire format.

## Current checkpoint

- Offline suite: **476 passing** at the latest local SDK gate.
- Current read-only live suite: **12/12 passing** with the corrected multipart transport, including live autocomplete/categorization against official English/German tag resources plus live sync-hook, raw-API, service-view, and transport-close coverage.
- Guarded live mutation suite: **48 passed, 1 safely skipped without writing** in the latest complete run. In addition to the disposable shopping-list ecosystem, coverage now includes uniquely identified disposable global categories/groupings and learned categorization memory, disposable recipes/collections with exact collection-order restoration, disposable per-recipe cooking-state add/remove with byte-for-byte preservation of every pre-existing cooking-state record, disposable meal-plan events/labels/list-items, disposable templates/template events/template groups with exact root-item restoration, and recipe-linked deletion across both normal and template event stores.
- The reusable server-side disposable list **`AnyList SDK Conformance Test`** exists and is retained for future verification. Mutation guards require both its reserved ID and exact name before any write.
- No normal shopping list or pre-existing recipe/category/meal-plan resource is intentionally mutated. Temporary shopping, starter, folder, category, recipe, collection, meal-plan event/label/template/group, rule, and provenance resources created by live tests are removed again and fresh cleanup audits require zero residue. The one label-order experiment that necessarily renumbered existing labels was immediately restored to the exact original `[0,1,2,3,4]` sort indices and is excluded from routine reruns.
- The protobuf multipart correction is now **live write verified**: AnyList requires binary protobuf fields as ordinary multipart form fields with no filename and no per-part Content-Type.
- Live conformance found and fixed a categorization-rule identity bug: single, bulk, and migration rule creation now use the official deterministic UUIDv5 of `lower(itemName) + categoryGroupId + listId`. Offline regressions and live server readback both confirm it.
- Live conformance also found and fixed a cross-service flush bug: `clear()` and `remove_checked()` could commit the shopping-list removal while leaving their required Recent Items promotion queued locally. Both now propagate the caller's flush request to the Recent/Favorite starter queue, matching the official web flow; offline regressions and live readback confirm the fix.
- Known official quirk: `ShoppingListsResponse.orderedIds` is populated on a full response and empty on unchanged deltas; `app.js` stores its private `$oj$JK` value but never reads it. Real ordering is folder-managed.
- Known official contradiction: `set-web-selected-meal-plan-event-id` exists in JavaScript but `PBMobileAppSettings` has no `webSelectedMealPlanEventId` field.

## Client

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AnyListClient.tokens()` | ✅ LIVE VERIFIED | Read from authenticated live clients throughout the read-only and guarded mutation suites, including token rotation. |
| `AnyListClient.user_id()` | ✅ LIVE VERIFIED | Read from authenticated live clients and matched the authenticated token user ID. |
| `AnyListClient.sign_in()` | ✅ LIVE VERIFIED | Succeeded repeatedly with current multipart implementation; AnyList later began returning HTTP 503 after repeated test logins. |
| `AnyListClient.load()` | ✅ LIVE VERIFIED |  |
| `AnyListClient.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `AnyListClient.flush()` | ✅ LIVE VERIFIED | Deferred Favorite mutation remained absent from a fresh server read until `client.flush()`; explicit flush then persisted exactly one item. |
| `AnyListClient.logout()` | ✅ LOCAL VERIFIED |  |
| `AnyListClient.close()` | ✅ LIVE VERIFIED |  |

## Transport

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AnyListTransport.session()` | ✅ LIVE VERIFIED | Live HTTP and WebSocket requests lazily created the owned aiohttp session; close/reopen behavior was also verified. |
| `AnyListTransport.close()` | ✅ LIVE VERIFIED | Live read-only clients were closed and their owned aiohttp sessions were verified closed; a subsequent lazy session reopen/close also succeeded. |
| `AnyListTransport.sign_in()` | ✅ LIVE VERIFIED | Succeeded repeatedly with current multipart implementation; AnyList later began returning HTTP 503 after repeated test logins. |
| `AnyListTransport.refresh_access_token()` | ✅ LIVE VERIFIED |  |
| `AnyListTransport.logout()` | ✅ LOCAL VERIFIED |  |
| `AnyListTransport.request()` | ✅ LIVE VERIFIED | Current read and protobuf-write paths both succeeded live after exact multipart correction. |
| `AnyListTransport.post_proto()` | ✅ LIVE VERIFIED | Current read and protobuf-write paths both succeeded live after exact multipart correction. |

## Sync

| Functionality | Status | Evidence / next check |
|---|---|---|
| `SyncCoordinator.set_field_guard()` | ✅ LIVE VERIFIED | A real full sync was guarded for `shoppingListsResponse`; the busy callback fired and the guarded live state was preserved. |
| `SyncCoordinator.add_listener()` | ✅ LIVE VERIFIED | A live full sync invoked the registered domain listener with the applied domain set. |
| `SyncCoordinator.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |

## Realtime

| Functionality | Status | Evidence / next check |
|---|---|---|
| `RealtimeClient.add_listener()` | ✅ LIVE VERIFIED | A second authenticated client mutated the disposable shopping list; the registered client listener received `refresh-shopping-lists` and refreshed state without a manual refresh. |
| `RealtimeClient.add_reconnect_listener()` | ✅ LIVE VERIFIED | A transport-level socket abort triggered automatic reconnect; reconnect callbacks ran and the built-in catch-up refresh recovered a disposable mutation made while disconnected. |
| `RealtimeClient.events()` | ✅ LIVE VERIFIED | The live event iterator yielded the real `refresh-shopping-lists` invalidation produced by a second client. |
| `RealtimeClient.start()` | ✅ LIVE VERIFIED | Real WebSocket connected, survived multiple heartbeat intervals, stopped, and restarted. |
| `RealtimeClient.stop()` | ✅ LIVE VERIFIED | Real WebSocket connected, survived multiple heartbeat intervals, stopped, and restarted. |

## Tag data

| Functionality | Status | Evidence / next check |
|---|---|---|
| `TagDataManager.language()` | 🧪 OFFLINE VERIFIED |  |
| `TagDataManager.path_for_language()` | 🧪 OFFLINE VERIFIED |  |
| `TagDataManager.get()` | ✅ LIVE VERIFIED | English and German official resources both loaded live; German missing tagKeywordsIndex behavior is covered. |
| `TagDataManager.active_and_english()` | ✅ LIVE VERIFIED | English and German official resources both loaded live; German missing tagKeywordsIndex behavior is covered. |

## Autocomplete

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AutocompleteEngine.suggestions()` | ✅ LIVE VERIFIED | Queried a real official German autocomplete keyword from live tag data and returned the corresponding generic suggestion. |

## Categorization

| Functionality | Status | Evidence / next check |
|---|---|---|
| `Categorizer.classify_with()` | ✅ LIVE VERIFIED | Classified a real German normalized display-name entry from AnyList's official live tag resource to its exact tag. |
| `Categorizer.classify()` | ✅ LIVE VERIFIED | Live German classification succeeded and English fallback correctly classified an English-only official tag-data entry. |

## Operation queue

| Functionality | Status | Evidence / next check |
|---|---|---|
| `OperationQueue.pending_count()` | ✅ LIVE VERIFIED | Guarded live tests observed pending queue state before explicit flush, while paused, and after journal restore. |
| `OperationQueue.paused()` | ✅ LIVE VERIFIED | Starter queue remained paused while a Favorite mutation stayed server-absent, then cleared on resume. |
| `OperationQueue.pause()` | ✅ LIVE VERIFIED | Paused the disposable Favorite queue; `flush=True` enqueue did not reach the server while paused. |
| `OperationQueue.resume()` | ✅ LIVE VERIFIED | `resume(flush=True)` flushed the retained Favorite mutation and fresh readback confirmed persistence. |
| `OperationQueue.new_operation()` | ✅ LIVE VERIFIED | Exercised by live domain mutations, including the deferred Favorite queue/replay tests. |
| `OperationQueue.enqueue()` | ✅ LIVE VERIFIED | Deferred live Favorite mutation remained locally pending until explicit client flush. |
| `OperationQueue.add()` | ✅ LIVE VERIFIED | Directly queued the official `set-list-item-name` handler on the disposable shopping list and fresh readback confirmed the mutation. |
| `OperationQueue.restore()` | ✅ LIVE VERIFIED | A journaled Favorite operation survived simulated abrupt loss, restored into a fresh queue, flushed, and was confirmed by a new server read. |
| `OperationQueue.flush()` | ✅ LIVE VERIFIED | Explicit client/service flush, pause/resume flush, and restored-operation replay all succeeded against the real edit endpoint. |

## File operation journal

| Functionality | Status | Evidence / next check |
|---|---|---|
| `OperationJournal.save()` | 🧪 OFFLINE VERIFIED | Abstract durable-journal contract exercised through the file journal and queue persistence tests. |
| `OperationJournal.load()` | 🧪 OFFLINE VERIFIED | Abstract durable-journal contract exercised through the file journal and queue restoration tests. |
| `OperationJournal.clear()` | 🧪 OFFLINE VERIFIED | Abstract durable-journal contract exercised through queue acknowledgment/account-boundary cleanup tests. |
| `FileOperationJournal.save()` | ✅ LIVE VERIFIED | Deferred Favorite mutation produced a durable local archive before any server write. |
| `FileOperationJournal.load()` | ✅ LIVE VERIFIED | A fresh client restored the archived real-service Favorite operation after simulated abrupt loss. |
| `FileOperationJournal.clear()` | ✅ LIVE VERIFIED | Successful replay/ack removed the archived operation file; fresh server read confirmed the mutation before cleanup. |

## Shopping lists & list-local resources

| Functionality | Status | Evidence / next check |
|---|---|---|
| `ShoppingListsService.operation()` | ✅ LIVE VERIFIED | Direct legacy-queue rename of a temporary disposable item was deferred, explicitly flushed, and confirmed from a fresh server read. |
| `ShoppingListsService.all()` | ✅ LIVE VERIFIED | Compared directly with the synchronized real shopping-list state. |
| `ShoppingListsService.get()` | ✅ LIVE VERIFIED | Used by the guarded disposable-list tests and fresh-session server readback. |
| `ShoppingListsService.item()` | ✅ LIVE VERIFIED | Used repeatedly for live temporary-item verification and cleanup. |
| `ShoppingListsService.has_pending_new_list()` | ✅ LOCAL VERIFIED | Explicit regression verifies false → true while a `new-shopping-list` operation is pending on the legacy queue → false after removal. |
| `ShoppingListsService.remove_list_local()` | ✅ LOCAL VERIFIED | Explicit regression verifies removal from shopping state, duplicate ordered IDs, and all list-local indexes without any server mutation. |
| `ShoppingListsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `ShoppingListsService.create()` | ✅ LIVE VERIFIED | Created the reserved disposable shopping list with starter-list side effects disabled; a fresh authenticated state confirmed server persistence. |
| `ShoppingListsService.rename()` | ✅ LIVE VERIFIED | Temporary rename persisted on a fresh read and was restored to the exact guard name. |
| `ShoppingListsService.set_password()` | ✅ LIVE VERIFIED | Temporary password persisted on fresh read and was restored exactly once the server-side optional field was materialized. First-ever set/clear changes protobuf presence from absent to present-empty, matching server behavior. |
| `ShoppingListsService.prepare_item_for_add()` | 🧪 OFFLINE VERIFIED | Source-traced fresh-item constructor builds the client-side category/tag metadata before upload; regressions assert the official category precedence and single-add payload behavior. |
| `ShoppingListsService.prepare_autocomplete_item_for_add()` | 🧪 OFFLINE VERIFIED | Source-traced Favorite/Recent autocomplete branch keeps freshly computed fields and fills only missing/empty fields from the selected suggestion. |
| `ShoppingListsService.apply_category_to_prepared_item()` | 🧪 OFFLINE VERIFIED | Local prepared-item helper applies the explicit category assignment/match metadata before the item is queued, matching the UI's pre-upload edit path. |
| `ShoppingListsService.add_prepared_item()` | 🧪 OFFLINE VERIFIED | Regression verifies one complete `add-shopping-list-item` operation containing the already-enriched ListItem rather than a bulk add plus follow-up category mutations. |
| `ShoppingListsService.add_item()` | ✅ LIVE VERIFIED | Temporary items persisted on fresh server reads and were removed with Recent Items suppressed. |
| `ShoppingListsService.add_items()` | ✅ LIVE VERIFIED | Bulk-created temporary items persisted and were used for ordering/uncheck tests. |
| `ShoppingListsService.revive_matching_item()` | ✅ LIVE VERIFIED | Checked temporary item was revived via the safe unchecked path and verified on a fresh read. |
| `ShoppingListsService.remove_item()` | ✅ LIVE VERIFIED | Normal removal persisted on a fresh shopping-list read and created the corresponding unchecked clone in the disposable Recent Items list. |
| `ShoppingListsService.set_checked()` | ✅ LIVE VERIFIED | Both cross-off and uncross directions are live verified; cross-off created exactly one disposable Recent clone with a fresh ID. |
| `ShoppingListsService.rename_item()` | ✅ LIVE VERIFIED | Persisted on a fresh server read. |
| `ShoppingListsService.set_details()` | ✅ LIVE VERIFIED | Persisted on a fresh server read. |
| `ShoppingListsService.set_product_upc()` | ✅ LIVE VERIFIED | Persisted on a fresh server read. |
| `ShoppingListsService.set_photo()` | ✅ LIVE VERIFIED | Temporary item photo-reference set/clear persisted on fresh reads; no photo upload was performed. |
| `ShoppingListsService.set_quantity()` | ✅ LIVE VERIFIED | Persisted exact protobuf quantity on a fresh read. |
| `ShoppingListsService.set_package_size()` | ✅ LIVE VERIFIED | Persisted exact protobuf package size on a fresh read. |
| `ShoppingListsService.set_quantity_override()` | ✅ LIVE VERIFIED | Persisted on a fresh read. |
| `ShoppingListsService.set_package_override()` | ✅ LIVE VERIFIED | Persisted on a fresh read. |
| `ShoppingListsService.set_price_quantity()` | ✅ LIVE VERIFIED | Persisted exact protobuf value on a fresh read. |
| `ShoppingListsService.set_price_package_size()` | ✅ LIVE VERIFIED | Persisted exact protobuf value on a fresh read. |
| `ShoppingListsService.set_price_quantity_override()` | ✅ LIVE VERIFIED | Persisted on a fresh read. |
| `ShoppingListsService.set_price_package_override()` | ✅ LIVE VERIFIED | Persisted on a fresh read. |
| `ShoppingListsService.assign_category()` | ✅ LIVE VERIFIED | Temporary-item category assignment persisted on a fresh read. |
| `ShoppingListsService.set_category_match_id()` | ✅ LIVE VERIFIED | Temporary-item category match/category fields persisted on a fresh read. |
| `ShoppingListsService.add_store()` | ✅ LIVE VERIFIED | Temporary store ID assignment persisted on a fresh read and was reversed. |
| `ShoppingListsService.remove_store()` | ✅ LIVE VERIFIED | Temporary store ID removal persisted on a fresh read. |
| `ShoppingListsService.save_price()` | ✅ LIVE VERIFIED | Temporary item price add/remove persisted on fresh reads. |
| `ShoppingListsService.set_price_matchup_tag()` | ✅ LIVE VERIFIED | Persisted on a fresh server read. |
| `ShoppingListsService.set_allows_multiple_category_groups()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Guarded live test safely skipped because the server omitted the optional field needed for exact restoration; no write occurred. |
| `ShoppingListsService.set_new_item_position()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Guarded live test safely skipped because the server omitted the optional field needed for exact restoration; no write occurred. |
| `ShoppingListsService.move_item()` | ✅ LIVE VERIFIED | Single-item movement persisted in server ordering. |
| `ShoppingListsService.bulk_set_checked()` | ✅ LIVE VERIFIED | Bulk cross-off and bulk uncheck are both live verified; cross-off populated only the disposable Recent list. |
| `ShoppingListsService.bulk_remove_items()` | ✅ LIVE VERIFIED | Both `remember_recent=False` and default Recent Items promotion are live verified with fresh readback. |
| `ShoppingListsService.clear()` | ✅ LIVE VERIFIED | Cleared the disposable shopping list and populated its Recent Items list; this live test exposed and then confirmed the cross-service flush fix. |
| `ShoppingListsService.remove_checked()` | ✅ LIVE VERIFIED | Removed only checked disposable items while preserving their existing Recent entries without duplicates; live-confirmed after the cross-service flush fix. |
| `ShoppingListsService.uncheck_all()` | ✅ LIVE VERIFIED | Temporary checked items were uncrossed without Recent Items writes and verified on a fresh read. |
| `ShoppingListsService.unshare()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Could affect another user/share relationship; intentionally not exercised. |
| `ShoppingListsService.add_notification_location()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Could create location-notification/geofence state and has no same-scope cleanup path in this SDK surface. |
| `ShoppingListsService.add_store_ids_to_items()` | ✅ LIVE VERIFIED | Temporary item/store association persisted on fresh read. |
| `ShoppingListsService.remove_store_ids_from_items()` | ✅ LIVE VERIFIED | Temporary association removal persisted on fresh read. |
| `ShoppingListsService.remove_store_id_from_all_items()` | ✅ LIVE VERIFIED | Temporary store cleanup persisted on fresh read. |
| `ShoppingListsService.save_store()` | ✅ LIVE VERIFIED | Temporary store create/update persisted on fresh reads. |
| `ShoppingListsService.delete_store()` | ✅ LIVE VERIFIED | Temporary store deletion persisted on fresh read. |
| `ShoppingListsService.set_sorted_store_ids()` | ✅ LIVE VERIFIED | Temporary store ordering persisted on fresh read. |
| `ShoppingListsService.save_store_filter()` | ✅ LIVE VERIFIED | Temporary filter create/update persisted on fresh reads. |
| `ShoppingListsService.delete_store_filter()` | ✅ LIVE VERIFIED | Temporary filter deletion persisted on fresh read. |
| `ShoppingListsService.set_sorted_store_filter_ids()` | ✅ LIVE VERIFIED | Temporary filter ordering persisted on fresh read. |
| `ShoppingListsService.save_list_category()` | ✅ LIVE VERIFIED | Temporary list category creation persisted on fresh read. |
| `ShoppingListsService.migrate_list_category()` | ✅ LIVE VERIFIED | Migration handler accepted a temporary category and fresh read confirmed it. |
| `ShoppingListsService.rename_list_category()` | ✅ LIVE VERIFIED | Temporary category rename persisted on fresh read. |
| `ShoppingListsService.set_list_category_icon()` | ✅ LIVE VERIFIED | Temporary category icon persisted on fresh read. |
| `ShoppingListsService.save_category_group()` | ✅ LIVE VERIFIED | Temporary category group/categories persisted on fresh read. |
| `ShoppingListsService.migrate_category_group()` | ✅ LIVE VERIFIED | Migration handler persisted the temporary group. |
| `ShoppingListsService.delete_category_group()` | ✅ LIVE VERIFIED | Temporary group deletion and category cleanup persisted. |
| `ShoppingListsService.rename_category_group()` | ✅ LIVE VERIFIED | Temporary group rename persisted. |
| `ShoppingListsService.set_default_category()` | ✅ LIVE VERIFIED | Temporary default-category change persisted. |
| `ShoppingListsService.set_sorted_category_ids()` | ✅ LIVE VERIFIED | Temporary category ordering persisted. |
| `ShoppingListsService.remove_category_ids()` | ✅ LIVE VERIFIED | Temporary category removal persisted and associated rule cleanup was verified. |
| `ShoppingListsService.save_categorization_rule()` | ✅ LIVE VERIFIED | Server readback confirms official deterministic UUIDv5 identity; live-discovered mismatch fixed and regression-tested. |
| `ShoppingListsService.bulk_save_categorization_rules()` | ✅ LIVE VERIFIED | Bulk rules persisted with official deterministic UUIDv5 identities; live-discovered mismatch fixed. |
| `ShoppingListsService.migrate_categorization_rules()` | ✅ LIVE VERIFIED | Migration rules persisted with official deterministic UUIDv5 identities; live-discovered mismatch fixed. |
| `ShoppingListsService.remove_categorization_rules_for_category_ids()` | ✅ LIVE VERIFIED | Temporary categorization rules were removed and absence confirmed on fresh read. |
| `ShoppingListsService.reorder_items()` | ✅ LIVE VERIFIED | Manual ordering of temporary items persisted on a fresh read. |
| `ShoppingListsService.add_recipe_ingredient()` | ✅ LIVE VERIFIED | Recipe-provenance shopping item creation was verified entirely within the disposable list. |
| `ShoppingListsService.remove_recipe_ingredient()` | ✅ LIVE VERIFIED | Recipe provenance removal persisted without touching a server recipe object. |
| `ShoppingListsService.sync_recipe_update()` | ✅ LIVE VERIFIED | Recipe-update reconciliation changed only disposable-list provenance/items and persisted live. |
| `ShoppingListsService.sync_recipe_event_update()` | ✅ LIVE VERIFIED | Event-linked recipe provenance update persisted on the disposable list. |
| `ShoppingListsService.sync_event_list_update()` | ✅ LIVE VERIFIED | Free-form event-list provenance update persisted on the disposable list. |
| `ShoppingListsService.remove_event_references()` | ✅ LIVE VERIFIED | Event provenance cleanup persisted on fresh read. |
| `ShoppingListsService.remove_recipe_references()` | ✅ LIVE VERIFIED | Recipe provenance cleanup persisted on fresh read. |
| `ShoppingListsService.raw_legacy_operation()` | ✅ LIVE VERIFIED | Direct legacy handler enqueue renamed a temporary disposable item and fresh readback confirmed persistence after explicit flush. |
| `ShoppingListsService.flush()` | ✅ LIVE VERIFIED | Explicitly flushed deferred direct shopping operations on the disposable list and fresh reads confirmed the server state. |
| `ShoppingListsService.restore()` | ✅ LIVE VERIFIED | A journaled deferred legacy rename survived simulated abrupt loss, restored through the shopping service wrapper, flushed, and was confirmed by fresh server readback. |

## Per-list settings

| Functionality | Status | Evidence / next check |
|---|---|---|
| `ListSettingsService.get()` | ✅ LIVE VERIFIED | Real synchronized list settings were read back through the service view and matched the state objects by identity. |
| `ListSettingsService.ensure()` | ✅ LIVE VERIFIED | Exercised against synchronized real list settings and through disposable Favorite starter-settings creation; returned/reused the official deterministic settings object. |
| `ListSettingsService.initialize_new_list()` | ✅ LIVE VERIFIED | Exercised by creation of the retained disposable list with fresh-session persistence. |
| `ListSettingsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `ListSettingsService.set()` | ✅ LIVE VERIFIED | Multiple per-list settings were toggled, verified on fresh reads, then restored exactly. |
| `ListSettingsService.clear_store_filter_id()` | ✅ LIVE VERIFIED | Temporary selected filter was cleared and fresh read confirmed the effective empty value. The live server normalizes the optional field back to present-empty instead of absent. |
| `ListSettingsService.set_migrated_list_category_group_id()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Wire payload exactly matches `app.js`, but the live server ignores standalone calls outside AnyList's full user-category migration flow. A valid live test would require modifying pre-existing global migration state rather than a fully isolated disposable resource. |
| `ListSettingsService.remove()` | ✅ LIVE VERIFIED | Disposable Favorite starter-list settings were created, removed through the shared ListSettingsService implementation, and fresh readback confirmed absence. |

## Mobile/global settings

| Functionality | Status | Evidence / next check |
|---|---|---|
| `MobileSettingsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `MobileSettingsService.get()` | ✅ LIVE VERIFIED | Returned the real synchronized mobile-settings object from live state. |
| `MobileSettingsService.set()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `MobileSettingsService.save_recipe_cooking_states()` | ✅ LIVE VERIFIED | Added one cooking-state record keyed only to a disposable recipe, verified its fields from a fresh client, and proved every pre-existing cooking-state protobuf remained byte-for-byte identical. |
| `MobileSettingsService.remove_recipe_cooking_states()` | ✅ LIVE VERIFIED | Removed only the disposable recipe's cooking-state key; fresh state exactly matched the complete pre-test cooking-state snapshot. |

## User categories

| Functionality | Status | Evidence / next check |
|---|---|---|
| `UserCategoriesService.all()` | ✅ LIVE VERIFIED | Live service view matched the synchronized user-category state exactly. |
| `UserCategoriesService.groupings()` | ✅ LIVE VERIFIED | Live service view matched the synchronized category-grouping state exactly. |
| `UserCategoriesService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `UserCategoriesService.add_category()` | ✅ LIVE VERIFIED | Created uniquely named disposable global categories; fresh readback confirmed persistence and final fresh read confirmed deletion. |
| `UserCategoriesService.remove_category()` | ✅ LIVE VERIFIED | Removed both test-created global categories; fresh server state confirmed absence. |
| `UserCategoriesService.rename_category()` | ✅ LIVE VERIFIED | Renamed a test-created category and verified the new name from a fresh client. |
| `UserCategoriesService.set_category_icon()` | ✅ LIVE VERIFIED | Set the icon on a test-created category and verified it from fresh state. |
| `UserCategoriesService.add_grouping()` | ✅ LIVE VERIFIED | Created a disposable grouping containing only test-created categories; fresh readback confirmed it. |
| `UserCategoriesService.remove_grouping()` | ✅ LIVE VERIFIED | Removed the disposable grouping and fresh read confirmed absence. |
| `UserCategoriesService.set_grouping_categories()` | ✅ LIVE VERIFIED | Membership and ordering variants both persisted for a grouping containing only disposable categories. |
| `UserCategoriesService.rename_grouping()` | ✅ LIVE VERIFIED | Renamed the disposable grouping and verified fresh server state. |
| `UserCategoriesService.hide_grouping_from_browse()` | ✅ LIVE VERIFIED | Hide flag persisted on the disposable grouping and was verified from fresh state. |

## Learned categorized items

| Functionality | Status | Evidence / next check |
|---|---|---|
| `CategorizedItemsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `CategorizedItemsService.memory_id()` | ✅ LIVE VERIFIED | Used to derive the live disposable learned-memory ID and matched the fresh server entry. |
| `CategorizedItemsService.lookup()` | ✅ LIVE VERIFIED | Resolved the live disposable learned-memory entry before migration/removal. |
| `CategorizedItemsService.categorize()` | ✅ LIVE VERIFIED | Created a learned categorization memory for a unique test item and verified it from fresh state. |
| `CategorizedItemsService.remove()` | ✅ LIVE VERIFIED | Removed the disposable learned-memory entry; fresh server read confirmed absence. |
| `CategorizedItemsService.migrate_category()` | ✅ LIVE VERIFIED | Migrated the disposable learned memory from one test-created category match ID to another and verified fresh state. |

## Folders

| Functionality | Status | Evidence / next check |
|---|---|---|
| `FoldersService.operation()` | ✅ LIVE VERIFIED | Exercised by every disposable folder mutation in the guarded live round trip. |
| `FoldersService.all()` | ✅ LIVE VERIFIED | Live folder service view matched the synchronized folder tree. |
| `FoldersService.get()` | ✅ LIVE VERIFIED | Every synchronized folder ID resolved to the same live state object through the service getter. |
| `FoldersService.has_pending_delete_items()` | 🧪 OFFLINE VERIFIED | Queue-introspection helper; folder delete operations are live verified, but this helper itself is not a distinct server behavior. |
| `FoldersService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `FoldersService.create()` | ✅ LIVE VERIFIED | Created a temporary parent folder and nested child containing only disposable resources; fresh reads confirmed both. |
| `FoldersService.rename()` | ✅ LIVE VERIFIED | Temporary folder rename persisted on a fresh read. |
| `FoldersService.set_hex_color()` | ✅ LIVE VERIFIED | Temporary folder color persisted on a fresh read. |
| `FoldersService.set_icon()` | ✅ LIVE VERIFIED | Temporary folder icon persisted on a fresh read. |
| `FoldersService.set_lists_sort_order()` | ✅ LIVE VERIFIED | Temporary folder setting persisted on a fresh read. |
| `FoldersService.set_folder_sort_position()` | ✅ LIVE VERIFIED | Temporary folder setting persisted on a fresh read. |
| `FoldersService.reorder()` | ✅ LIVE VERIFIED | Reordered only items inside the temporary disposable folder and restored the original parent ordering exactly. |
| `FoldersService.move()` | ✅ LIVE VERIFIED | Moved only the disposable shopping list into and back out of the temporary folder; fresh reads confirmed both directions. |
| `FoldersService.delete_items()` | ✅ LIVE VERIFIED | Exercised by recursive disposable-folder cleanup; fresh read confirmed removed folder items were absent. |
| `FoldersService.delete_folder()` | ✅ LIVE VERIFIED | Recursively removed the temporary child/parent folder tree; final fresh state matched the exact original folder tree. |

## Starter / Favorites / Recents

| Functionality | Status | Evidence / next check |
|---|---|---|
| `StarterListsService.all()` | ✅ LIVE VERIFIED | Live service view matched every synchronized starter list. |
| `StarterListsService.recent()` | ✅ LIVE VERIFIED | Live service view matched the synchronized Recent Items map. |
| `StarterListsService.favorites()` | ✅ LIVE VERIFIED | Live service view matched the synchronized Favorite Items map. |
| `StarterListsService.favorite_for_shopping_list()` | ✅ LIVE VERIFIED | Every synchronized Favorite list resolved through its shopping-list ID. |
| `StarterListsService.recent_for_shopping_list()` | ✅ LIVE VERIFIED | Every synchronized Recent list resolved through its shopping-list ID. |
| `StarterListsService.aggregate_favorites()` | ✅ LIVE VERIFIED | Aggregated live favorites into the expected FavoriteItemsType starter-list view. |
| `StarterListsService.autocomplete_items()` | ✅ LIVE VERIFIED | Returned the synchronized live starter-list items used for autocomplete. |
| `StarterListsService.ordered_user_lists()` | ✅ LIVE VERIFIED | Live ordered view contained exactly the synchronized starter-list IDs. |
| `StarterListsService.get()` | ✅ LIVE VERIFIED | Used throughout fresh-session verification of the disposable Recent/Favorite and temporary custom starter lists. |
| `StarterListsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `StarterListsService.refresh_order()` | ✅ LIVE VERIFIED | Fresh clients read the ordered-ID endpoint repeatedly during temporary custom starter-list ordering tests. |
| `StarterListsService.create()` | ✅ LIVE VERIFIED | Created two temporary user starter lists linked to the disposable shopping list and confirmed both on a fresh client. |
| `StarterListsService.ensure_favorites()` | ✅ LIVE VERIFIED | Created/verified the deterministic Favorite Items list for the disposable shopping list. |
| `StarterListsService.ensure_recents()` | ✅ LIVE VERIFIED | Created/verified the deterministic Recent Items list for the disposable shopping list. |
| `StarterListsService.initialize_for_shopping_list()` | ✅ LIVE VERIFIED | Created Recent then Favorite as one deterministic post-create batch; both survived fresh-session readback. |
| `StarterListsService.remove()` | ✅ LIVE VERIFIED | Removed both disposable deterministic starter lists and temporary custom starter lists; fresh clients confirmed absence before recreation/cleanup. |
| `StarterListsService.add_item()` | ✅ LIVE VERIFIED | Favorite item CRUD persisted on fresh readback. |
| `StarterListsService.bulk_add_items()` | ✅ LIVE VERIFIED | Favorite bulk adds and the Recent 201→200 cap test persisted on the server. |
| `StarterListsService.record_recent_items()` | ✅ LIVE VERIFIED | Exercised naturally by shopping cross-off/remove/bulk-remove/clear/remove-checked; fresh Recents readback confirmed replacement/skip-existing behavior. |
| `StarterListsService.remove_item()` | ✅ LIVE VERIFIED | Favorite item removal persisted on fresh readback. |
| `StarterListsService.bulk_remove_items()` | ✅ LIVE VERIFIED | Favorite/Recent bulk cleanup persisted on fresh readback. |
| `StarterListsService.clear()` | ✅ LIVE VERIFIED | Cleared the disposable Favorite list and fresh read confirmed zero items. |
| `StarterListsService.rename()` | ✅ LIVE VERIFIED | Temporary Favorite rename persisted and was restored. |
| `StarterListsService.set_item_name()` | ✅ LIVE VERIFIED | Temporary Favorite item field persisted on fresh read. |
| `StarterListsService.set_item_details()` | ✅ LIVE VERIFIED | Temporary Favorite item field persisted on fresh read. |
| `StarterListsService.set_product_upc()` | ✅ LIVE VERIFIED | Temporary Favorite item field persisted on fresh read. |
| `StarterListsService.set_photo()` | ✅ LIVE VERIFIED | Photo-reference set/clear persisted on a temporary Favorite item; no external photo upload occurred. |
| `StarterListsService.set_quantity()` | ✅ LIVE VERIFIED | Exact quantity protobuf persisted on a temporary Favorite item. |
| `StarterListsService.set_quantity_override()` | ✅ LIVE VERIFIED | Persisted on a temporary Favorite item. |
| `StarterListsService.set_price_quantity()` | ✅ LIVE VERIFIED | Exact price-quantity protobuf persisted on a temporary Favorite item. |
| `StarterListsService.set_price_quantity_override()` | ✅ LIVE VERIFIED | Persisted on a temporary Favorite item. |
| `StarterListsService.set_package_size()` | ✅ LIVE VERIFIED | Exact package-size protobuf persisted on a temporary Favorite item. |
| `StarterListsService.set_package_override()` | ✅ LIVE VERIFIED | Persisted on a temporary Favorite item. |
| `StarterListsService.set_price_package_size()` | ✅ LIVE VERIFIED | Exact price package-size protobuf persisted on a temporary Favorite item. |
| `StarterListsService.set_price_package_override()` | ✅ LIVE VERIFIED | Persisted on a temporary Favorite item. |
| `StarterListsService.add_store()` | ✅ LIVE VERIFIED | Temporary store ID persisted on a Favorite item. |
| `StarterListsService.remove_store()` | ✅ LIVE VERIFIED | Temporary store ID removal persisted. |
| `StarterListsService.add_store_ids_to_items()` | ✅ LIVE VERIFIED | Bulk store-ID association persisted on the disposable Favorite list. |
| `StarterListsService.remove_store_ids_from_items()` | ✅ LIVE VERIFIED | Bulk association removal persisted. |
| `StarterListsService.remove_store_from_all_items()` | ✅ LIVE VERIFIED | Temporary store ID cleanup executed and fresh item state was clean. |
| `StarterListsService.save_price()` | ✅ LIVE VERIFIED | Temporary Favorite item price add persisted. |
| `StarterListsService.remove_price()` | ✅ LIVE VERIFIED | Temporary Favorite item price removal persisted. |
| `StarterListsService.reorder_lists()` | ✅ LIVE VERIFIED | Two temporary disposable-linked starter IDs were reordered while all pre-existing IDs retained their exact relative order, then the original order was restored. |
| `StarterListsService.flush()` | ✅ LIVE VERIFIED | Exercised by bulk starter mutations, including the live 201-item Recent cap batch. |
| `StarterListsService.restore()` | ✅ LIVE VERIFIED | The disposable starter journal-replay test restored the inherited starter queue after simulated abrupt loss and fresh server readback confirmed persistence. |

## Recipes

| Functionality | Status | Evidence / next check |
|---|---|---|
| `RecipesService.all()` | ✅ LIVE VERIFIED | Live recipe service view matched synchronized recipes. |
| `RecipesService.get()` | ✅ LIVE VERIFIED | Every synchronized live recipe resolved through the service getter. |
| `RecipesService.collections()` | ✅ LIVE VERIFIED | Live collection view matched synchronized recipe collections. |
| `RecipesService.source_collections()` | ✅ LIVE VERIFIED | Executed against real synchronized recipe/source state without mutation. |
| `RecipesService.not_in_collection()` | ✅ LIVE VERIFIED | Live helper returned the official deterministic not-in-collection smart collection ID. |
| `RecipesService.sorted()` | ✅ LIVE VERIFIED | Executed against real synchronized recipe state and returned exactly the synchronized recipe IDs. |
| `RecipesService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `RecipesService.operation()` | ✅ LIVE VERIFIED | Exercised by disposable recipe and collection mutations against the real recipe update queue. |
| `RecipesService.save()` | ✅ LIVE VERIFIED | Updated a test-created recipe name/note/rating and verified fresh server state. |
| `RecipesService.create()` | ✅ LIVE VERIFIED | Created disposable recipes through an isolated recipe service; fresh readback confirmed persistence. |
| `RecipesService.remove()` | ✅ LIVE VERIFIED | Removed a disposable recipe individually after recipe-linked event deletion; fresh read confirmed absence. |
| `RecipesService.remove_many()` | ✅ LIVE VERIFIED | Removed two disposable recipes in one operation; fresh state confirmed cleanup. |
| `RecipesService.create_collection()` | ✅ LIVE VERIFIED | Created two disposable recipe collections and verified both from fresh state. |
| `RecipesService.remove_collection()` | ✅ LIVE VERIFIED | Removed both disposable collections; fresh state confirmed absence. |
| `RecipesService.rename_collection()` | ✅ LIVE VERIFIED | Renamed a disposable collection and verified fresh server state. |
| `RecipesService.add_to_collection()` | ✅ LIVE VERIFIED | Added only disposable recipes to a disposable collection and verified membership. |
| `RecipesService.remove_from_collection()` | ✅ LIVE VERIFIED | Removed and re-added a disposable recipe; server membership matched. |
| `RecipesService.reorder_collections()` | ✅ LIVE VERIFIED | Temporarily appended/reordered only disposable collection IDs while preserving/restoring the exact pre-existing order. |
| `RecipesService.reorder_recipes()` | ✅ LIVE VERIFIED | Reordered two disposable recipes inside a disposable collection and verified fresh state. |
| `RecipesService.set_collection_icon()` | ✅ LIVE VERIFIED | Disposable collection icon persisted on fresh readback. |
| `RecipesService.set_collection_sort()` | ✅ LIVE VERIFIED | Disposable collection sort/reversed settings persisted on fresh readback. |
| `RecipesService.set_max_recipe_count()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `RecipesService.set_system_collection_recipe_sort()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `RecipesService.set_system_collection_collection_sort()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `RecipesService.web_import()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | The official endpoint accepts both URL and optional HTML but does not prove that supplying HTML prevents server-side URL fetching; its response also carries `freeRecipeImportsRemainingCount`, so a live probe could create an external request and/or consume account import quota without a cleanup path. |
| `RecipesService.send_as_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `RecipesService.request_link()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `RecipesService.accept_link()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `RecipesService.cancel_link()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `RecipesService.unlink()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |

## Meal plan

| Functionality | Status | Evidence / next check |
|---|---|---|
| `MealPlanService.operation()` | ✅ LIVE VERIFIED | Exercised by disposable event, label, template and template-group operations against the real calendar queue. |
| `MealPlanService.events()` | ✅ LIVE VERIFIED | Live service view matched synchronized meal-plan events. |
| `MealPlanService.labels()` | ✅ LIVE VERIFIED | Live service view matched synchronized meal-plan labels. |
| `MealPlanService.templates()` | ✅ LIVE VERIFIED | Live service view matched synchronized meal-plan templates. |
| `MealPlanService.template_groups()` | ✅ LIVE VERIFIED | Live service view matched synchronized meal-plan template groups. |
| `MealPlanService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `MealPlanService.save_event()` | ✅ LIVE VERIFIED | Created both normal and template disposable events; fresh server readback confirmed each. |
| `MealPlanService.delete_event()` | ✅ LIVE VERIFIED | Deleted disposable normal/template events and fresh state confirmed absence. |
| `MealPlanService.save_events()` | ✅ LIVE VERIFIED | Batch-created a disposable meal-plan event and verified it from fresh state. |
| `MealPlanService.set_event_date()` | ✅ LIVE VERIFIED | Moved a disposable queue event to a dated calendar event and back; live queue remained acknowledged. |
| `MealPlanService.add_event_list_item()` | ✅ LIVE VERIFIED | Added disposable event-list items and verified fresh event state. |
| `MealPlanService.update_event_list_item()` | ✅ LIVE VERIFIED | Underlying event-list-item update path exercised live by name/details/quantity/package-size setters. |
| `MealPlanService.remove_event_list_item()` | ✅ LIVE VERIFIED | Removed a disposable event-list item and completed clean event deletion. |
| `MealPlanService.reorder_event_list_items()` | ✅ LIVE VERIFIED | Reordered two disposable event-list items and verified order from fresh state. |
| `MealPlanService.save_label()` | ✅ LIVE VERIFIED | Created and updated a disposable meal-plan label; fresh state confirmed both. |
| `MealPlanService.delete_label()` | ✅ LIVE VERIFIED | Deleted the disposable label and fresh state confirmed absence. |
| `MealPlanService.reorder_labels()` | ✅ LIVE VERIFIED | Live operation succeeded and demonstrated official global renumbering semantics; all five pre-existing label sort indices were immediately restored exactly. Unsafe for routine disposable reruns because it necessarily affects existing label order. |
| `MealPlanService.save_template()` | ✅ LIVE VERIFIED | Created a disposable template under a disposable group and verified fresh state. |
| `MealPlanService.delete_template()` | ✅ LIVE VERIFIED | Deleted disposable templates; fresh server state confirmed absence. |
| `MealPlanService.set_event_title()` | ✅ LIVE VERIFIED | Disposable event title persisted on fresh readback. |
| `MealPlanService.set_event_details()` | ✅ LIVE VERIFIED | Disposable event details persisted on fresh readback. |
| `MealPlanService.set_event_icon()` | ✅ LIVE VERIFIED | Disposable event icon persisted on fresh readback. |
| `MealPlanService.set_event_label()` | ✅ LIVE VERIFIED | Assigned only the disposable label to the disposable event and verified fresh state. |
| `MealPlanService.set_event_label_sort_index()` | ✅ LIVE VERIFIED | Set the disposable event label sort index through the live calendar queue. |
| `MealPlanService.set_event_list_item_name()` | ✅ LIVE VERIFIED | Disposable event-list-item name persisted on fresh readback. |
| `MealPlanService.set_event_list_item_details()` | ✅ LIVE VERIFIED | Disposable event-list-item details persisted on fresh readback. |
| `MealPlanService.set_event_list_item_quantity()` | ✅ LIVE VERIFIED | Disposable event-list-item quantity protobuf persisted on fresh readback. |
| `MealPlanService.set_event_list_item_package_size()` | ✅ LIVE VERIFIED | Disposable event-list-item package-size protobuf persisted on fresh readback. |
| `MealPlanService.delete_events_for_recipe_id()` | 🔷 VERIFIED INTENTIONAL DIVERGENCE | `app.js` 84361-84383 fetches matching template events but maps the normal-event array twice, leaving the fetched template array unused. The SDK deliberately sends normal IDs followed by the actual template-event IDs. The protobuf shape supports this, an offline regression locks the difference, and a disposable live recipe test confirmed both event stores are cleared. |
| `MealPlanService.set_template_name()` | ✅ LIVE VERIFIED | Disposable template name persisted on fresh readback. |
| `MealPlanService.set_template_icon()` | ✅ LIVE VERIFIED | Disposable template icon persisted on fresh readback. |
| `MealPlanService.add_template_day_ids()` | ✅ LIVE VERIFIED | Added real UUID template-day IDs to a disposable template; operation acknowledged live. |
| `MealPlanService.remove_template_day_ids()` | ✅ LIVE VERIFIED | Removed a real UUID template-day ID and verified the resulting template from fresh state. |
| `MealPlanService.set_template_day_ids()` | ✅ LIVE VERIFIED | Replaced disposable template day IDs with real UUIDs and verified fresh state. |
| `MealPlanService.set_template_day_id_for_events()` | ✅ LIVE VERIFIED | Moved a disposable template event between disposable template-day IDs and verified fresh state. |
| `MealPlanService.create_root_template_group()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | The official root lifecycle is deterministic/special-purpose. Creating an extra synthetic root would invent a server state the official UI does not create, and the normal delete API requires a parent relationship, so exact cleanup is not proven. |
| `MealPlanService.create_template_group()` | ✅ LIVE VERIFIED | Created disposable child/nested template groups under the existing root, then removed them completely. |
| `MealPlanService.delete_template_group()` | ✅ LIVE VERIFIED | Deleted all disposable template groups leaf-first; fresh state confirmed zero residue. |
| `MealPlanService.set_ordered_template_group_items()` | ✅ LIVE VERIFIED | Reordered only items inside a disposable template group and verified the resulting fresh group state. |
| `MealPlanService.move_template_group_items()` | ✅ LIVE VERIFIED | Moved a disposable nested group between two disposable parents and verified fresh state. |
| `MealPlanService.set_template_group_items_sort_order()` | ✅ LIVE VERIFIED | Disposable template-group item sort setting persisted on fresh state. |
| `MealPlanService.set_template_group_groups_sort_position()` | ✅ LIVE VERIFIED | Disposable template-group group-position setting persisted on fresh state. |
| `MealPlanService.set_icalendar_enabled()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `MealPlanService.send_as_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |

## Account

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AccountService.get()` | ✅ LIVE VERIFIED |  |
| `AccountService.update_name()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |

## Photos

| Functionality | Status | Evidence / next check |
|---|---|---|
| `PhotosService.upload_bytes()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `PhotosService.upload_url()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `PhotosService.url()` | 🧪 OFFLINE VERIFIED |  |

## Sharing / email

| Functionality | Status | Evidence / next check |
|---|---|---|
| `SharingService.share_list()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `SharingService.send_list_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `SharingService.send_recipe_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `SharingService.send_meal_plan_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |

## Alexa

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AlexaService.link_list()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `AlexaService.unlink_list()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `AlexaService.unlink_anylist_list()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `AlexaService.set_enabled_lists()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |

## Web state

| Functionality | Status | Evidence / next check |
|---|---|---|
| `WebStateService.mark_mac_app_download_prompt_seen()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |
| `WebStateService.mark_welcome_screen_seen()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Not exercised because this path changes pre-existing/account-wide state or can trigger an external effect that cannot be isolated to a uniquely disposable resource with a proven cleanup path. |

## Raw protocol escape hatch

| Functionality | Status | Evidence / next check |
|---|---|---|
| `RawAPI.request()` | ✅ LIVE VERIFIED | Read `/data/account/info` through the raw wrapper and decoded the real `PBAccountInfoResponse`. |
| `RawAPI.post_proto()` | ✅ LIVE VERIFIED | Called the real aggregate `/data/user-data/get` protobuf endpoint through the raw wrapper and decoded `PBUserDataResponse`. |

## Derived recipe / meal-plan / pricing helpers

| Functionality | Status | Evidence / next check |
|---|---|---|
| `derived.recipe_source_aliases()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.source_domain()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.source_display_name()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.normalized_source_name()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.source_collection_identifier()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.source_smart_collection()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.source_smart_collections()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.recipes_not_in_collection()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.not_in_collection_smart_collection()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.duplicate_recipe_ids()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.sort_recipes()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.effective_recipe_scale_factor()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.effective_event_scale_factor()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.full_ingredient_string()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.ingredient_to_item_ingredient()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.normalized_raw_package_size()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.recipe_list_item_identifier()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.same_recipe_ingredient()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.add_item_ingredient()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.remove_item_ingredient()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.item_quantity()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.ingredient_package_size()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.total_ingredient_quantity()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.list_quantity()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.active_quantity_for_total_cost()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.total_cost()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.active_package_size()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.unit_price()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.display_quantity_and_package_size()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.event_list_item_to_item_ingredient()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.recipe_servings_after_scaling()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.recipe_ingredients_excluding_headings()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.is_recipe_heading()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.recipe_heading_text()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.recipe_prep_steps_excluding_headings()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.duplicate_ingredient()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.duplicate_recipe()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.cooking_states_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.icons_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.icon_resource_path()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.calendar_event_descriptor()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptor_for_calendar_event()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptor_for_queue_event()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptor_for_favorite_event()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptor_for_template_event()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.event_descriptor()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptors_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptor_is_calendar_event()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptor_is_queue_event()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptor_is_favorite_event()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.descriptor_is_template_event()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.template_group_item_for_template()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.template_group_item_for_group()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.template_group_items_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.event_list_items_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `derived.event_list_item_arrays_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |

## Item equality / mutation semantics

| Functionality | Status | Evidence / next check |
|---|---|---|
| `item_semantics.quantity_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.package_size_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.price_empty()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.price_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.prices_match()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.ingredient_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.same_recipe_ingredient()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.item_ingredient_identical()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.item_ingredients_identical()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.item_hash()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.items_equal()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.apply_properties_from_item()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.quantity_empty()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.package_size_empty()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `item_semantics.quantity_to_deprecated_string()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |

## Normalization / search helpers

| Functionality | Status | Evidence / next check |
|---|---|---|
| `normalization.remove_diacritics()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.normalized_for_search()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.collapse_whitespace()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.split_into_words()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.trim_whitespace_and_punctuation()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.range_of_word_or_phrase()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.contains_word_or_phrase()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.ranges_for_search_terms()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.canonical_category_match_id()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.normalized_recipe_source_name()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.recipe_source_domain()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `normalization.localized_sort_key()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |

## Identifiers

| Functionality | Status | Evidence / next check |
|---|---|---|
| `identifiers.uuid4_hex()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `identifiers.uuid5_hex()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |

## English stemming

| Functionality | Status | Evidence / next check |
|---|---|---|
| `stemming.english_stem()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `stemming.stem_words()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |

## State application / timestamp builders

| Functionality | Status | Evidence / next check |
|---|---|---|
| `state.clone()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |
| `state.by_identifier()` | 🧪 OFFLINE VERIFIED | Pure/local semantics; covered by source-derived tests. Live mutation proof is not applicable or will be obtained indirectly through the owning service. |

## State mirror methods

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AnyListState.get_list()` | 🧪 OFFLINE VERIFIED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.get_item()` | 🧪 OFFLINE VERIFIED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_list_response()` | 🧪 OFFLINE VERIFIED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_shopping_lists()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_list_folders()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_recipes()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_recipes_full()` | 🧪 OFFLINE VERIFIED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_meal_plan()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_categorized_items()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_user_categories()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_list_settings()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_starter_lists()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_ordered_starter_ids()` | 🧪 OFFLINE VERIFIED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_mobile_settings()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.apply_user_data()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.shopping_list_timestamps()` | 🧪 OFFLINE VERIFIED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.shopping_list_logical_timestamps()` | 🧪 OFFLINE VERIFIED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.list_folder_timestamps()` | 🧪 OFFLINE VERIFIED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.user_data_timestamps()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |
| `AnyListState.user_data_client_info()` | ✅ LIVE READ-DERIVED | State application is validated by aggregate/direct live reads where the account returned that domain; edge cases remain regression-tested offline. |

## Protobuf, typing, packaging, and surface completeness

| Functionality | Status | Evidence / next check |
|---|---|---|
| Dynamic official protobuf schema: 156 messages | ✅ LOCAL VERIFIED | Runtime load/import and wheel install verified. |
| `proto.descriptor_pool_for_official_schema()` | ✅ LOCAL VERIFIED | Dynamic schema construction is exercised by protobuf runtime/import tests. |
| `proto.message_class()` | ✅ LOCAL VERIFIED | Dynamic message lookup is exercised throughout the SDK and runtime tests. |
| `proto.enum_type()` | ✅ LOCAL VERIFIED | Official enum lookup is runtime-tested. |
| `proto.new()` | ✅ LOCAL VERIFIED | Dynamic protobuf construction helper. |
| `proto.assign()` | ✅ LOCAL VERIFIED | Dynamic protobuf assignment helper. |
| `proto.decode()` | ✅ LOCAL VERIFIED | Wire decode helper is exercised throughout transport/queue/state tests. |
| `proto.encode()` | ✅ LOCAL VERIFIED | Wire encode helper is exercised throughout transport/queue/state tests. |
| Generated protobuf `.pyi` stubs | ✅ LOCAL VERIFIED | Strict consumer mypy test passes, including from installed wheel. |
| PEP 561 `py.typed` packaging | ✅ LOCAL VERIFIED | Present in built wheel and recognized by consumer type check. |
| 185 official operation handler strings accounted for | ✅ LOCAL VERIFIED | Every handler is represented in implementation/tests; one is the documented schema contradiction. |
| 48 official endpoint strings accounted for | ✅ LOCAL VERIFIED | `/auth/logout` is intentionally not used by token auth because official bearer-token flow has no such request. |
| Wheel build / external import | ✅ LOCAL VERIFIED | Correct `anylist_sdk-0.1.0` wheel built, installed and imported outside source tree at the last release gate. |

## Live verification order

1. Re-run the full read-only suite once AnyList `/auth/token` stops returning 503.
2. Create/confirm exactly one server-side list named **`AnyList SDK Conformance Test`** using the reserved ID; verify from a fresh client/token-backed state.
3. Lock every mutation test to both that exact ID and exact name. If either check fails, abort all writes.
4. Verify reversible shopping-list/item mutations that do not touch Recent Items; use `remember_recent=False` for cleanup.
5. Verify list-local stores, store filters, category groups/categories/rules and per-list settings, restoring/deleting temporary objects afterward.
6. Do **not** mutate Starter/Favorites/Recents, folders, recipes, meal plan, global categories/settings, account, sharing, email, photos, Alexa, or web-state endpoints until scope is explicitly expanded.
7. For each write: require processed operation acknowledgment **and** a fresh-client server readback before changing its row to ✅ LIVE VERIFIED.

## Parsing API

| Functionality | Status | Evidence / next check |
|---|---|---|
| `parsing.amount_as_float()` | 🧪 OFFLINE VERIFIED | Exact number/fraction semantics are source-derived and regression-tested. |
| `parsing.decimal_to_friendly_fraction()` | 🧪 OFFLINE VERIFIED | Pure formatter; regression-tested offline. |
| `parsing.normalize_unit()` | 🧪 OFFLINE VERIFIED | Official alias/singularization behavior is regression-tested. |
| `parsing.parse_package_size()` | 🧪 OFFLINE VERIFIED | Exact package/unit parsing is regression-tested. |
| `parsing.parse_quantity_and_package_size()` | 🧪 OFFLINE VERIFIED | Exact quantity/package parsing is regression-tested. |
| `parsing.replace_quantity_amount()` | 🧪 OFFLINE VERIFIED | Exact quantity rewrite behavior is regression-tested. |
| `parsing.scale_quantity_text()` | 🧪 OFFLINE VERIFIED | Exact scaling/friendly-fraction behavior is regression-tested. |
| `parsing.parse_ingredient_line()` | 🧪 OFFLINE VERIFIED | Official pasted-ingredient behavior is regression-tested. |
| `parsing.parse_ingredient_lines()` | 🧪 OFFLINE VERIFIED | Multi-line ingredient import is regression-tested. |
| `parsing.parse_recipe_steps()` | 🧪 OFFLINE VERIFIED | Numbered-step continuation behavior is regression-tested. |
| `ingredient.split_quantity_prefix()` | 🧪 OFFLINE VERIFIED | Lower-level parser helper; exact source-derived tests. |
| `ingredient.split_ingredient_note()` | 🧪 OFFLINE VERIFIED | Lower-level parser helper; exact source-derived tests. |
| `quantity.normalize_digits()` | 🧪 OFFLINE VERIFIED | Lower-level parser helper. |
| `quantity.parse_leading_amount()` | 🧪 OFFLINE VERIFIED | Lower-level parser helper; mixed fractions/ranges covered. |
| `quantity.normalize_units_in_text()` | 🧪 OFFLINE VERIFIED | Lower-level official unit-table behavior. |
| `quantity.abbreviate_units_in_text()` | 🧪 OFFLINE VERIFIED | Display unit abbreviation table. |
| `quantity.singularize_unit()` | 🧪 OFFLINE VERIFIED | Pure official unit semantics. |
| `quantity.singularize_units_in_text()` | 🧪 OFFLINE VERIFIED | Pure official unit semantics. |
| `quantity.pluralize_unit()` | 🧪 OFFLINE VERIFIED | Pure official unit semantics. |
| `quantity.unit_for_amount()` | 🧪 OFFLINE VERIFIED | Pure official unit semantics. |

## Exported utility/service edges

| Functionality | Status | Evidence / next check |
|---|---|---|
| `services.category_rule_identifier()` | ✅ LIVE VERIFIED | The deterministic UUIDv5 output was independently enforced by the live server for single, bulk, and migration categorization-rule writes. |
| `services.recent_list_id()` | ✅ LIVE VERIFIED | Deterministic ID resolved to the disposable list's real Recent Items list across create/remove/recreate and side-effect tests. |
| `services.favorite_list_id()` | ✅ LIVE VERIFIED | Deterministic ID resolved to the disposable list's real Favorite Items list across CRUD, settings, lifecycle, and replay tests. |
| `services.aggregate_favorites_id()` | 🧪 OFFLINE VERIFIED | Official synthetic aggregate-favorites identifier. |
| `services.enrich_item_from_starter()` | 🧪 OFFLINE VERIFIED | Exact starter-item property inheritance behavior is source-derived and regression-tested. |
| `TagData.from_json()` | ✅ LIVE VERIFIED | Parsed the real official English and German tag-data resources, including German's missing `tagKeywordsIndex`. |
| `OperationAck.processed_ids()` | ✅ LOCAL VERIFIED | Typed alias for processed operation identifiers. |
| `OperationService.operation()` | ✅ LIVE VERIFIED | Inherited operation path is exercised by multiple concrete services and directly by the live generic-domain test. |
| `OperationService.flush()` | ✅ LIVE VERIFIED | Inherited flush path persisted deferred disposable operations in concrete services and the generic-domain test. |
| `OperationService.pause()` | ✅ LIVE VERIFIED | Inherited pause path held a disposable starter mutation server-side absent until resume. |
| `OperationService.resume()` | ✅ LIVE VERIFIED | Inherited resume path flushed the held disposable starter mutation and fresh readback confirmed persistence. |
| `OperationService.restore()` | ✅ LIVE VERIFIED | Inherited restore path was exercised by StarterListsService during durable starter replay after simulated abrupt loss. |
| `services.partial_message()` | 🧪 OFFLINE VERIFIED | Dynamic partial-protobuf helper used for official operation payloads. |
| `services.clone_message()` | 🧪 OFFLINE VERIFIED | Type-preserving protobuf clone helper. |
| `GenericDomainService` | ✅ LIVE VERIFIED | Configured with AnyList's official starter-list queue types/endpoint; pause/deferred rename/resume persisted only the disposable starter list and exact name restoration was verified. |
| Public exception hierarchy | ✅ LOCAL VERIFIED | Typed exceptions are exercised by transport/auth/sync/tag tests. |
| `AuthTokens` / `OperationAck` / `AutocompleteSuggestion` / `Domain` | ✅ LOCAL VERIFIED | Typed public data models covered by strict consumer typing and runtime tests. |
