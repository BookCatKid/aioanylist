# AnyList SDK Conformance Matrix

This is the authoritative verification checklist for the SDK. It is intentionally stricter than the unit-test suite: a method is not called “live verified” merely because its wire contract is reconstructed from `app.js`.

## Status legend

- **✅ LIVE VERIFIED** — the current code path has been exercised against the real AnyList service and the exercised result was validated. This is the strongest evidence available; it does **not** mean every imaginable edge case is mathematically proven.
- **✅ LOCAL VERIFIED** — behavior is intentionally local-only and has been verified end-to-end locally (for example token logout).
- **🧪 OFFLINE VERIFIED** — official `app.js` / embedded schema behavior is covered by offline regression tests, but the method has not yet been proven with a live server mutation/readback.
- **🟡 LIVE PARTIAL** — a meaningful live path passed, but another direction/side effect remains intentionally untested.
- **🟠 LIVE RETEST REQUIRED** — relevant implementation changed after the last live attempt; do not treat older live results as current proof.
- **⚪ NOT LIVE TESTED** — no current live evidence.
- **🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE** — deliberately not tested because it would affect resources outside the disposable-list ecosystem, global/account state that cannot be isolated, another person, or an external side effect without a cleanup path.
- **⚠️ OFFICIAL CONTRADICTION** — official JavaScript and embedded protobuf schema conflict; do not invent a wire format.

## Current checkpoint

- Offline suite: **475 passing** at the latest local gate.
- Current read-only live suite: **9/9 passing** with the corrected multipart transport.
- Guarded disposable-list mutation suite: **33 passed, 1 safely skipped without writing** in the latest complete live run. Coverage now includes the disposable shopping list, its deterministic Recent/Favorite starter lists, starter-list settings, temporary disposable-linked starter lists, and exact starter-list ordering restoration.
- The reusable server-side disposable list **`AnyList SDK Conformance Test`** exists and is retained for future verification. Mutation guards require both its reserved ID and exact name before any write.
- No normal shopping list was mutated. Temporary items/stores/filters/categories/rules/provenance created by live tests were removed again; removal paths suppress Recent Items where required.
- The protobuf multipart correction is now **live write verified**: AnyList requires binary protobuf fields as ordinary multipart form fields with no filename and no per-part Content-Type.
- Live conformance found and fixed a categorization-rule identity bug: single, bulk, and migration rule creation now use the official deterministic UUIDv5 of `lower(itemName) + categoryGroupId + listId`. Offline regressions and live server readback both confirm it.
- Live conformance also found and fixed a cross-service flush bug: `clear()` and `remove_checked()` could commit the shopping-list removal while leaving their required Recent Items promotion queued locally. Both now propagate the caller's flush request to the Recent/Favorite starter queue, matching the official web flow; offline regressions and live readback confirm the fix.
- Known official quirk: `ShoppingListsResponse.orderedIds` is populated on a full response and empty on unchanged deltas; `app.js` stores its private `$oj$JK` value but never reads it. Real ordering is folder-managed.
- Known official contradiction: `set-web-selected-meal-plan-event-id` exists in JavaScript but `PBMobileAppSettings` has no `webSelectedMealPlanEventId` field.

## Client

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AnyListClient.tokens()` | 🧪 OFFLINE VERIFIED |  |
| `AnyListClient.user_id()` | 🧪 OFFLINE VERIFIED |  |
| `AnyListClient.sign_in()` | ✅ LIVE VERIFIED | Succeeded repeatedly with current multipart implementation; AnyList later began returning HTTP 503 after repeated test logins. |
| `AnyListClient.load()` | ✅ LIVE VERIFIED |  |
| `AnyListClient.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `AnyListClient.flush()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `AnyListClient.logout()` | ✅ LOCAL VERIFIED |  |
| `AnyListClient.close()` | ✅ LIVE VERIFIED |  |

## Transport

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AnyListTransport.session()` | 🧪 OFFLINE VERIFIED |  |
| `AnyListTransport.close()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `AnyListTransport.sign_in()` | ✅ LIVE VERIFIED | Succeeded repeatedly with current multipart implementation; AnyList later began returning HTTP 503 after repeated test logins. |
| `AnyListTransport.refresh_access_token()` | ✅ LIVE VERIFIED |  |
| `AnyListTransport.logout()` | ✅ LOCAL VERIFIED |  |
| `AnyListTransport.request()` | ✅ LIVE VERIFIED | Current read and protobuf-write paths both succeeded live after exact multipart correction. |
| `AnyListTransport.post_proto()` | ✅ LIVE VERIFIED | Current read and protobuf-write paths both succeeded live after exact multipart correction. |

## Sync

| Functionality | Status | Evidence / next check |
|---|---|---|
| `SyncCoordinator.set_field_guard()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `SyncCoordinator.add_listener()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `SyncCoordinator.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |

## Realtime

| Functionality | Status | Evidence / next check |
|---|---|---|
| `RealtimeClient.add_listener()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Socket lifecycle is live-tested; a real invalidation message/reconnect catch-up has not yet been induced. |
| `RealtimeClient.add_reconnect_listener()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Socket lifecycle is live-tested; a real invalidation message/reconnect catch-up has not yet been induced. |
| `RealtimeClient.events()` | 🧪 OFFLINE VERIFIED | Socket lifecycle is live-tested; a real invalidation message/reconnect catch-up has not yet been induced. |
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
| `AutocompleteEngine.suggestions()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |

## Categorization

| Functionality | Status | Evidence / next check |
|---|---|---|
| `Categorizer.classify_with()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `Categorizer.classify()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |

## Operation queue

| Functionality | Status | Evidence / next check |
|---|---|---|
| `OperationQueue.pending_count()` | 🧪 OFFLINE VERIFIED |  |
| `OperationQueue.paused()` | 🧪 OFFLINE VERIFIED |  |
| `OperationQueue.pause()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `OperationQueue.resume()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `OperationQueue.new_operation()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `OperationQueue.enqueue()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `OperationQueue.add()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `OperationQueue.restore()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `OperationQueue.flush()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |

## File operation journal

| Functionality | Status | Evidence / next check |
|---|---|---|
| `OperationJournal.save()` | 🧪 OFFLINE VERIFIED | Abstract durable-journal contract exercised through the file journal and queue persistence tests. |
| `OperationJournal.load()` | 🧪 OFFLINE VERIFIED | Abstract durable-journal contract exercised through the file journal and queue restoration tests. |
| `OperationJournal.clear()` | 🧪 OFFLINE VERIFIED | Abstract durable-journal contract exercised through queue acknowledgment/account-boundary cleanup tests. |
| `FileOperationJournal.save()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `FileOperationJournal.load()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `FileOperationJournal.clear()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |

## Shopping lists & list-local resources

| Functionality | Status | Evidence / next check |
|---|---|---|
| `ShoppingListsService.operation()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Candidate for disposable-list live verification once the test list exists; must avoid Recent Items side effects unless explicitly suppressed. |
| `ShoppingListsService.all()` | 🧪 OFFLINE VERIFIED |  |
| `ShoppingListsService.get()` | ✅ LIVE VERIFIED | Used by the guarded disposable-list tests and fresh-session server readback. |
| `ShoppingListsService.item()` | ✅ LIVE VERIFIED | Used repeatedly for live temporary-item verification and cleanup. |
| `ShoppingListsService.has_pending_new_list()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Candidate for disposable-list live verification once the test list exists; must avoid Recent Items side effects unless explicitly suppressed. |
| `ShoppingListsService.remove_list_local()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Candidate for disposable-list live verification once the test list exists; must avoid Recent Items side effects unless explicitly suppressed. |
| `ShoppingListsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `ShoppingListsService.create()` | ✅ LIVE VERIFIED | Created the reserved disposable shopping list with starter-list side effects disabled; a fresh authenticated state confirmed server persistence. |
| `ShoppingListsService.rename()` | ✅ LIVE VERIFIED | Temporary rename persisted on a fresh read and was restored to the exact guard name. |
| `ShoppingListsService.set_password()` | ✅ LIVE VERIFIED | Temporary password persisted on fresh read and was restored exactly once the server-side optional field was materialized. First-ever set/clear changes protobuf presence from absent to present-empty, matching server behavior. |
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
| `ShoppingListsService.raw_legacy_operation()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Candidate for disposable-list live verification once the test list exists; must avoid Recent Items side effects unless explicitly suppressed. |
| `ShoppingListsService.flush()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Candidate for disposable-list live verification once the test list exists; must avoid Recent Items side effects unless explicitly suppressed. |
| `ShoppingListsService.restore()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Candidate for disposable-list live verification once the test list exists; must avoid Recent Items side effects unless explicitly suppressed. |

## Per-list settings

| Functionality | Status | Evidence / next check |
|---|---|---|
| `ListSettingsService.get()` | 🧪 OFFLINE VERIFIED |  |
| `ListSettingsService.ensure()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `ListSettingsService.initialize_new_list()` | ✅ LIVE VERIFIED | Exercised by creation of the retained disposable list with fresh-session persistence. |
| `ListSettingsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `ListSettingsService.set()` | ✅ LIVE VERIFIED | Multiple per-list settings were toggled, verified on fresh reads, then restored exactly. |
| `ListSettingsService.clear_store_filter_id()` | ✅ LIVE VERIFIED | Temporary selected filter was cleared and fresh read confirmed the effective empty value. The live server normalizes the optional field back to present-empty instead of absent. |
| `ListSettingsService.set_migrated_list_category_group_id()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Wire payload exactly matches `app.js`, but the live server ignores standalone calls outside AnyList's full user-category migration flow. A valid live test requires global category migration state, beyond disposable-list-only permission. |
| `ListSettingsService.remove()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |

## Mobile/global settings

| Functionality | Status | Evidence / next check |
|---|---|---|
| `MobileSettingsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `MobileSettingsService.get()` | 🧪 OFFLINE VERIFIED |  |
| `MobileSettingsService.set()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MobileSettingsService.save_recipe_cooking_states()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MobileSettingsService.remove_recipe_cooking_states()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## User categories

| Functionality | Status | Evidence / next check |
|---|---|---|
| `UserCategoriesService.all()` | 🧪 OFFLINE VERIFIED |  |
| `UserCategoriesService.groupings()` | 🧪 OFFLINE VERIFIED |  |
| `UserCategoriesService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `UserCategoriesService.add_category()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `UserCategoriesService.remove_category()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `UserCategoriesService.rename_category()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `UserCategoriesService.set_category_icon()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `UserCategoriesService.add_grouping()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `UserCategoriesService.remove_grouping()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `UserCategoriesService.set_grouping_categories()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `UserCategoriesService.rename_grouping()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `UserCategoriesService.hide_grouping_from_browse()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Learned categorized items

| Functionality | Status | Evidence / next check |
|---|---|---|
| `CategorizedItemsService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `CategorizedItemsService.memory_id()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `CategorizedItemsService.lookup()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `CategorizedItemsService.categorize()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `CategorizedItemsService.remove()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `CategorizedItemsService.migrate_category()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Folders

| Functionality | Status | Evidence / next check |
|---|---|---|
| `FoldersService.operation()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.all()` | 🧪 OFFLINE VERIFIED |  |
| `FoldersService.get()` | 🧪 OFFLINE VERIFIED |  |
| `FoldersService.has_pending_delete_items()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `FoldersService.create()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.rename()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.set_hex_color()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.set_icon()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.set_lists_sort_order()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.set_folder_sort_position()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.reorder()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.move()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.delete_items()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `FoldersService.delete_folder()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Starter / Favorites / Recents

| Functionality | Status | Evidence / next check |
|---|---|---|
| `StarterListsService.all()` | 🧪 OFFLINE VERIFIED |  |
| `StarterListsService.recent()` | 🧪 OFFLINE VERIFIED |  |
| `StarterListsService.favorites()` | 🧪 OFFLINE VERIFIED |  |
| `StarterListsService.favorite_for_shopping_list()` | 🧪 OFFLINE VERIFIED |  |
| `StarterListsService.recent_for_shopping_list()` | 🧪 OFFLINE VERIFIED |  |
| `StarterListsService.aggregate_favorites()` | 🧪 OFFLINE VERIFIED |  |
| `StarterListsService.autocomplete_items()` | 🧪 OFFLINE VERIFIED |  |
| `StarterListsService.ordered_user_lists()` | 🧪 OFFLINE VERIFIED |  |
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
| `StarterListsService.restore()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Recipes

| Functionality | Status | Evidence / next check |
|---|---|---|
| `RecipesService.all()` | 🧪 OFFLINE VERIFIED |  |
| `RecipesService.get()` | 🧪 OFFLINE VERIFIED |  |
| `RecipesService.collections()` | 🧪 OFFLINE VERIFIED |  |
| `RecipesService.source_collections()` | 🧪 OFFLINE VERIFIED |  |
| `RecipesService.not_in_collection()` | 🧪 OFFLINE VERIFIED |  |
| `RecipesService.sorted()` | 🧪 OFFLINE VERIFIED |  |
| `RecipesService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `RecipesService.operation()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.save()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.create()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.remove()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.remove_many()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.create_collection()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.remove_collection()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.rename_collection()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.add_to_collection()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.remove_from_collection()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.reorder_collections()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.reorder_recipes()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.set_collection_icon()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.set_collection_sort()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.set_max_recipe_count()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.set_system_collection_recipe_sort()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.set_system_collection_collection_sort()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.web_import()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.send_as_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.request_link()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.accept_link()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.cancel_link()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `RecipesService.unlink()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Meal plan

| Functionality | Status | Evidence / next check |
|---|---|---|
| `MealPlanService.operation()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.events()` | 🧪 OFFLINE VERIFIED |  |
| `MealPlanService.labels()` | 🧪 OFFLINE VERIFIED |  |
| `MealPlanService.templates()` | 🧪 OFFLINE VERIFIED |  |
| `MealPlanService.template_groups()` | 🧪 OFFLINE VERIFIED |  |
| `MealPlanService.refresh()` | ✅ LIVE VERIFIED | Called against the real endpoint and decoded/applied successfully. |
| `MealPlanService.save_event()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.delete_event()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.save_events()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_date()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.add_event_list_item()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.update_event_list_item()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.remove_event_list_item()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.reorder_event_list_items()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.save_label()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.delete_label()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.reorder_labels()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.save_template()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.delete_template()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_title()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_details()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_icon()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_label()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_label_sort_index()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_list_item_name()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_list_item_details()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_list_item_quantity()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_event_list_item_package_size()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.delete_events_for_recipe_id()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_template_name()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_template_icon()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.add_template_day_ids()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.remove_template_day_ids()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_template_day_ids()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_template_day_id_for_events()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.create_root_template_group()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.create_template_group()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.delete_template_group()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_ordered_template_group_items()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.move_template_group_items()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_template_group_items_sort_order()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_template_group_groups_sort_position()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.set_icalendar_enabled()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `MealPlanService.send_as_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Account

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AccountService.get()` | ✅ LIVE VERIFIED |  |
| `AccountService.update_name()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Photos

| Functionality | Status | Evidence / next check |
|---|---|---|
| `PhotosService.upload_bytes()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `PhotosService.upload_url()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `PhotosService.url()` | 🧪 OFFLINE VERIFIED |  |

## Sharing / email

| Functionality | Status | Evidence / next check |
|---|---|---|
| `SharingService.share_list()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `SharingService.send_list_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `SharingService.send_recipe_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `SharingService.send_meal_plan_email()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Alexa

| Functionality | Status | Evidence / next check |
|---|---|---|
| `AlexaService.link_list()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `AlexaService.unlink_list()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `AlexaService.unlink_anylist_list()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `AlexaService.set_enabled_lists()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Web state

| Functionality | Status | Evidence / next check |
|---|---|---|
| `WebStateService.mark_mac_app_download_prompt_seen()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |
| `WebStateService.mark_welcome_screen_seen()` | 🚫 NOT MUTATED UNDER CURRENT SAFETY SCOPE | Would change account/global/other-list state; intentionally not exercised while writes are restricted to the disposable shopping list. |

## Raw protocol escape hatch

| Functionality | Status | Evidence / next check |
|---|---|---|
| `RawAPI.request()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |
| `RawAPI.post_proto()` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED |  |

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
| `services.category_rule_identifier()` | 🧪 OFFLINE VERIFIED | Deterministic official UUIDv5 rule identity. |
| `services.recent_list_id()` | 🧪 OFFLINE VERIFIED | Deterministic Recent Items identity. No live mutation is permitted under current safety scope. |
| `services.favorite_list_id()` | 🧪 OFFLINE VERIFIED | Deterministic Favorite Items identity. No live mutation is permitted under current safety scope. |
| `services.aggregate_favorites_id()` | 🧪 OFFLINE VERIFIED | Official synthetic aggregate-favorites identifier. |
| `services.enrich_item_from_starter()` | 🧪 OFFLINE VERIFIED | Exact starter-item property inheritance behavior is source-derived and regression-tested. |
| `TagData.from_json()` | 🧪 OFFLINE VERIFIED | Official tag-data resource parser, including localized resources without `tagKeywordsIndex`. |
| `OperationAck.processed_ids()` | ✅ LOCAL VERIFIED | Typed alias for processed operation identifiers. |
| `OperationService.operation()` | 🧪 OFFLINE VERIFIED | Base typed operation construction/enqueue path used by concrete services. |
| `OperationService.flush()` | 🧪 OFFLINE VERIFIED | Base queue flush path; queue acknowledgment behavior is exhaustively regression-tested. |
| `OperationService.pause()` | 🧪 OFFLINE VERIFIED | Base queue pause behavior. |
| `OperationService.resume()` | 🧪 OFFLINE VERIFIED | Base queue resume/optional flush behavior. |
| `OperationService.restore()` | 🧪 OFFLINE VERIFIED | Base durable-operation restore path. |
| `services.partial_message()` | 🧪 OFFLINE VERIFIED | Dynamic partial-protobuf helper used for official operation payloads. |
| `services.clone_message()` | 🧪 OFFLINE VERIFIED | Type-preserving protobuf clone helper. |
| `GenericDomainService` | 🧪 OFFLINE VERIFIED / ⚪ NOT LIVE TESTED | Generic typed operation-queue wrapper; concrete service classes are preferred for all proven domains. |
| Public exception hierarchy | ✅ LOCAL VERIFIED | Typed exceptions are exercised by transport/auth/sync/tag tests. |
| `AuthTokens` / `OperationAck` / `AutocompleteSuggestion` / `Domain` | ✅ LOCAL VERIFIED | Typed public data models covered by strict consumer typing and runtime tests. |
