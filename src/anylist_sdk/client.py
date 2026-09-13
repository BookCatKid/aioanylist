from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiohttp

from .autocomplete import AutocompleteEngine
from .categorization import Categorizer
from .operations import FileOperationJournal, OperationJournal
from .proto import ListItem, PBCalendarEvent, PBRecipe
from .realtime import RealtimeClient, RealtimeEvent
from .services import (
    AccountService,
    AlexaService,
    CategorizedItemsService,
    FoldersService,
    ListSettingsService,
    UserCategoriesService,
    MealPlanService,
    MobileSettingsService,
    PhotosService,
    RawAPI,
    RecipesService,
    SharingService,
    WebStateService,
    ShoppingListsService,
    StarterListsService,
)
from .state import AnyListState
from .sync import SyncCoordinator
from .tag_data import TagDataManager
from .transport import AnyListTransport
from .types import AuthTokens, Domain
from .services.base import OperationService


class AnyListClient:
    """Async-first, protobuf-backed AnyList client reconstructed from AnyList Web."""

    lists: ShoppingListsService | None
    recipes: RecipesService | None
    folders: FoldersService | None
    categories: UserCategoriesService | None
    categorized_items: CategorizedItemsService | None
    list_settings: ListSettingsService | None
    starter_list_settings: ListSettingsService | None
    mobile_settings: MobileSettingsService | None
    starter_lists: StarterListsService | None
    meal_plan: MealPlanService | None
    account: AccountService | None
    photos: PhotosService | None
    sharing: SharingService | None
    alexa: AlexaService | None
    web_state: WebStateService | None
    raw: RawAPI

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        *,
        tokens: AuthTokens | None = None,
        user_email: str | None = None,
        client_id: str | None = None,
        cache_dir: str | Path | None = None,
        journal: OperationJournal | None = None,
        base_url: str = "https://www.anylist.com",
    ) -> None:
        self._sign_in_email: str | None = user_email
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.transport = AnyListTransport(
            session, base_url=base_url, client_id=client_id, tokens=tokens
        )
        self.state = AnyListState(user_id=(tokens.user_id if tokens else None))
        self.sync = SyncCoordinator(self.transport, self.state)
        self.realtime = RealtimeClient(self.transport)
        self.realtime.add_listener(self._on_realtime)
        self.realtime.add_reconnect_listener(self._on_reconnect)
        self.ready = asyncio.Event()
        self.tag_data = TagDataManager(
            self.transport,
            locale=(tokens.user_locale if tokens and tokens.user_locale else "en-US"),
            cache_dir=(self.cache_dir / "tags" if self.cache_dir else None),
        )
        self.categorizer = Categorizer(self.tag_data)
        self.autocomplete = AutocompleteEngine(self.tag_data)
        self._journal = journal or (
            FileOperationJournal(self.cache_dir / "operations") if self.cache_dir else None
        )
        self._services_ready = False
        self._install_services(tokens.user_id if tokens else None)

    def _install_services(self, user_id: str | None) -> None:
        self.state.user_id = user_id
        if not user_id:
            self.lists = self.recipes = self.folders = self.categories = self.categorized_items = (
                None
            )
            self.list_settings = self.starter_list_settings = self.mobile_settings = (
                self.starter_lists
            ) = None
            self.meal_plan = self.account = self.photos = self.sharing = self.alexa = (
                self.web_state
            ) = None
            self.raw = RawAPI(self.transport)
            self._services_ready = False
            return
        j = self._journal
        self.lists = ShoppingListsService(
            self.transport,
            self.state,
            user_id=user_id,
            user_email=self._sign_in_email,
            user_locale=(self.tokens.user_locale if self.tokens else None),
            journal=j,
        )
        self.recipes = RecipesService(self.transport, self.state, user_id=user_id, journal=j)
        self.folders = FoldersService(self.transport, self.state, user_id=user_id, journal=j)
        self.categories = UserCategoriesService(
            self.transport, self.state, user_id=user_id, journal=j
        )
        self.categorized_items = CategorizedItemsService(
            self.transport, self.state, user_id=user_id, journal=j
        )
        self.list_settings = ListSettingsService(
            self.transport, self.state, user_id=user_id, journal=j
        )
        self.starter_list_settings = ListSettingsService(
            self.transport, self.state, user_id=user_id, starter=True, journal=j
        )
        self.mobile_settings = MobileSettingsService(
            self.transport, self.state, user_id=user_id, journal=j
        )
        self.starter_lists = StarterListsService(
            self.transport, self.state, user_id=user_id, journal=j
        )
        self.meal_plan = MealPlanService(self.transport, self.state, user_id=user_id, journal=j)
        self.account = AccountService(self.transport, self.state)
        self.photos = PhotosService(self.transport)
        self.sharing = SharingService(self.transport, user_id, self.state)
        self.alexa = AlexaService(self.transport)
        self.web_state = WebStateService(self.transport)
        self.raw = RawAPI(self.transport)
        self.sharing.on_refresh_requested = self.lists.refresh
        self.recipes.on_recipe_removed = self._cleanup_recipe_references
        self.recipes.on_recipe_updated = self._sync_recipe_references
        self.meal_plan.on_event_updated = self._sync_event_references
        self.meal_plan.on_event_removed = self._cleanup_event_references
        self.lists.on_store_filter_removed = self._clear_selected_store_filter
        self.lists.on_category_group_removed = self._migrate_selected_category_group
        self.lists.on_items_became_recent = self._record_recent_items
        self.lists.on_classify_grocery_item = self._classify_grocery_item
        self.lists.on_new_list_settings = self._initialize_new_list_settings
        self.lists.on_new_list_starter_lists = self._initialize_new_list_starter_lists
        self.folders.on_list_removed = self._remove_list_from_folder_tree
        self.lists.on_folder_refresh_requested = self.folders.refresh
        self.folders.on_shopping_refresh_requested = self.lists.refresh
        self._bind_sync_queue_guards()
        self._services_ready = True

    async def _initialize_new_list_settings(
        self, list_id: str, category_group_id: str, list_type: int, flush: bool
    ) -> None:
        settings = self.list_settings
        assert settings is not None
        await settings.initialize_new_list(
            list_id,
            category_group_id,
            list_type=list_type,
            flush=flush,
        )

    async def _initialize_new_list_starter_lists(
        self, list_id: str, favorite_name: str, flush: bool
    ) -> None:
        starter_lists = self.starter_lists
        assert starter_lists is not None
        await starter_lists.initialize_for_shopping_list(
            list_id,
            favorite_name=favorite_name,
            flush=flush,
        )

    def _bind_sync_queue_guards(self) -> None:
        """Route aggregate snapshots through the official per-manager pending-op guards."""
        lists = self.lists
        folders = self.folders
        recipes = self.recipes
        categories = self.categories
        categorized_items = self.categorized_items
        list_settings = self.list_settings
        starter_list_settings = self.starter_list_settings
        mobile_settings = self.mobile_settings
        starter_lists = self.starter_lists
        meal_plan = self.meal_plan
        assert lists is not None
        assert folders is not None
        assert recipes is not None
        assert categories is not None
        assert categorized_items is not None
        assert list_settings is not None
        assert starter_list_settings is not None
        assert mobile_settings is not None
        assert starter_lists is not None
        assert meal_plan is not None

        self.sync.set_field_guard(
            "mobileAppSettingsResponse", lambda: mobile_settings.queue.pending_count == 0
        )

        def shopping_lists_ready() -> bool:
            if lists.legacy_queue.pending_count:
                # cQ sets tQ only for the legacy queue. bQ consumes it after the ack.
                lists._refresh_after_legacy_queue = True
                return False
            if lists.queue.pending_count:
                return False
            if folders.has_pending_delete_items():
                # cQ asks the folder manager to refresh shopping lists after deletion acks.
                folders._refresh_shopping_after_queue = True
                return False
            return True

        def list_folders_ready() -> bool:
            if folders.queue.pending_count:
                return False
            if lists.has_pending_new_list():
                # DB marks the shopping manager so bQ refreshes folders after the new-list ack.
                lists._refresh_folders_after_legacy_queue = True
                return False
            return True

        self.sync.set_field_guard("shoppingListsResponse", shopping_lists_ready)
        self.sync.set_field_guard("listFoldersResponse", list_folders_ready)
        self.sync.set_field_guard("recipeDataResponse", lambda: recipes.queue.pending_count == 0)
        self.sync.set_field_guard(
            "mealPlanningCalendarResponse", lambda: meal_plan.queue.pending_count == 0
        )
        self.sync.set_field_guard(
            "userCategoriesResponse", lambda: categories.queue.pending_count == 0
        )
        self.sync.set_field_guard(
            "categorizedItemsResponse", lambda: categorized_items.queue.pending_count == 0
        )
        self.sync.set_field_guard(
            "listSettingsResponse", lambda: list_settings.queue.pending_count == 0
        )
        self.sync.set_field_guard(
            "starterListSettingsResponse",
            lambda: starter_list_settings.queue.pending_count == 0,
        )

        def starter_lists_ready() -> bool:
            if starter_lists.queue.pending_count:
                starter_lists._refresh_after_queue = True
                return False
            return True

        self.sync.set_field_guard("starterListsResponse", starter_lists_ready)
        self.sync.set_field_guard(
            "orderedStarterListIdsResponse",
            lambda: starter_lists.order_queue.pending_count == 0,
        )

    @property
    def tokens(self) -> AuthTokens | None:
        return self.transport.tokens

    @property
    def user_id(self) -> str | None:
        return self.tokens.user_id if self.tokens else None

    async def sign_in(self, email: str, password: str) -> AuthTokens:
        previous_user = self.state.user_id
        tokens = await self.transport.sign_in(email, password)
        self._sign_in_email = email
        self.tag_data.locale = tokens.user_locale or "en-US"
        # A client instance can be reused after logout. Never expose data from a previous
        # account through the new account's services.
        if previous_user is not None and previous_user != tokens.user_id:
            self.state = AnyListState(user_id=tokens.user_id)
            self.sync = SyncCoordinator(self.transport, self.state)
        elif self.state.loaded_once and previous_user != tokens.user_id:
            self.state = AnyListState(user_id=tokens.user_id)
            self.sync = SyncCoordinator(self.transport, self.state)
        self._install_services(tokens.user_id)
        return tokens

    async def load(
        self, *, realtime: bool = False, load_tag_data: bool = True, restore_pending: bool = True
    ) -> AnyListState:
        if not self._services_ready:
            raise RuntimeError("Authenticate before loading AnyList data")
        # The official UI has a readiness barrier; aggregate sync and static tag data can load concurrently.
        tasks: list[asyncio.Task[Any]] = [asyncio.create_task(self.sync.refresh(full=True))]
        if load_tag_data:
            tasks.append(asyncio.create_task(self.tag_data.active_and_english()))
        await asyncio.gather(*tasks)
        if restore_pending:
            restored = 0
            for service in self._operation_services():
                restored += await service.restore()
            # ALArchivedOperations exists so edits made before an unload/network loss are
            # replayed, not merely rehydrated into a dormant queue.
            if restored:
                await self.flush()
        self.ready.set()
        if realtime:
            await self.realtime.start()
        return self.state

    async def refresh(self) -> AnyListState:
        await self.sync.refresh()
        return self.state

    async def flush(self) -> None:
        for service in self._operation_services():
            await service.flush()

    def _operation_services(self) -> list[OperationService]:
        if not self._services_ready:
            return []
        services = [
            self.lists,
            self.recipes,
            self.folders,
            self.categories,
            self.categorized_items,
            self.list_settings,
            self.starter_list_settings,
            self.mobile_settings,
            self.starter_lists,
            self.meal_plan,
        ]
        return [service for service in services if service is not None]

    async def _remove_list_from_folder_tree(self, list_id: str, flush: bool) -> None:
        if self.lists is not None:
            self.lists.remove_list_local(list_id)
        if self.list_settings is not None:
            await self.list_settings.remove(list_id, flush=flush)

    async def _record_recent_items(
        self, list_id: str, items: Sequence[ListItem], skip_existing: bool, flush: bool
    ) -> None:
        if self.starter_lists is None:
            return
        await self.starter_lists.record_recent_items(
            list_id, items, skip_existing=skip_existing, flush=flush
        )

    async def _classify_grocery_item(self, name: str) -> tuple[str | None, str | None]:
        """Return AnyList's grocery tag and its root category for fresh-item construction."""

        tag = await self.categorizer.classify(name)
        if not tag:
            return None, None
        active, english = await self.tag_data.active_and_english()
        metadata = active.tags.get(tag) or english.tags.get(tag) or {}
        root = metadata.get("rootCategory")
        return tag, (str(root) if root else None)

    async def _clear_selected_store_filter(
        self, list_id: str, store_filter_id: str, flush: bool
    ) -> None:
        if self.list_settings is None:
            return
        settings = self.state.list_settings.get(list_id)
        if settings is None or str(getattr(settings, "storeFilterId", "") or "") != store_filter_id:
            return
        await self.list_settings.clear_store_filter_id(list_id, flush=flush)

    async def _migrate_selected_category_group(
        self, list_id: str, removed_group_id: str, flush: bool
    ) -> None:
        if self.list_settings is None or self.lists is None:
            return
        settings = self.state.list_settings.get(list_id)
        if (
            settings is None
            or str(getattr(settings, "listCategoryGroupId", "") or "") != removed_group_id
        ):
            return
        replacement = self.lists._default_category_group(list_id)
        if replacement is None:
            return
        await self.list_settings.set(
            list_id, "listCategoryGroupId", str(replacement.identifier), flush=flush
        )

    async def _sync_recipe_references(
        self, new_recipe: PBRecipe, old_recipe: PBRecipe, flush: bool
    ) -> None:
        list_id = self._recipe_ingredients_list_id()
        if self.lists is None or not list_id:
            return
        events = dict(self.state.meal_plan_events)
        events.update(self.state.meal_plan_template_events)
        await self.lists.sync_recipe_update(
            list_id, new_recipe, old_recipe, events=events, flush=flush
        )

    def _recipe_ingredients_list_id(self) -> str:
        settings = self.state.mobile_app_settings
        return (getattr(settings, "listIdForRecipeIngredients", "") or "") if settings else ""

    async def _sync_event_references(
        self, new_event: PBCalendarEvent, old_event: PBCalendarEvent, flush: bool
    ) -> None:
        list_id = self._recipe_ingredients_list_id()
        if self.lists is None or not list_id:
            return
        recipe_id = getattr(new_event, "recipeId", "") or ""
        if recipe_id:
            recipe = self.state.recipes.get(str(recipe_id))
            if recipe is not None:
                await self.lists.sync_recipe_event_update(
                    list_id, new_event, old_event, recipe, flush=flush
                )
        else:
            await self.lists.sync_event_list_update(list_id, new_event, old_event, flush=flush)

    async def _cleanup_event_references(self, event: PBCalendarEvent, flush: bool) -> None:
        list_id = self._recipe_ingredients_list_id()
        if self.lists is not None and list_id:
            await self.lists.remove_event_references(list_id, str(event.identifier), flush=flush)

    async def _cleanup_recipe_references(self, recipe_id: str, flush: bool) -> None:
        # Recipe deletion in the web app also removes its meal-plan events and provenance
        # from the single shopping list selected for recipe ingredients.
        if self.meal_plan is not None and self.state.meal_plan_calendar_id:
            await self.meal_plan.delete_events_for_recipe_id(recipe_id, flush=flush)
        list_id = self._recipe_ingredients_list_id()
        if self.lists is not None and list_id:
            await self.lists.remove_recipe_references(list_id, recipe_id, flush=flush)

    async def _on_reconnect(self) -> None:
        # AnyList Web calls both /data/user-data/get and /data/account/info when an
        # automatically retried WebSocket opens, covering invalidations missed offline.
        await self.sync.refresh()
        if self.account is not None:
            await self.account.get()

    async def _on_realtime(self, event: RealtimeEvent) -> None:
        if event.message == "did-delete-account":
            self.ready.clear()
            return
        if event.domain in {Domain.ACCOUNT, Domain.SUBSCRIPTION}:
            if self.account:
                try:
                    await self.account.get()
                except Exception:
                    pass
            return
        # Aggregate sync carries all invalidated domains and coalesces concurrent refresh events.
        await self.sync.refresh()

    async def logout(self) -> None:
        await self.realtime.stop()
        await self.transport.logout()
        self.ready.clear()
        self.state = AnyListState()
        self.sync = SyncCoordinator(self.transport, self.state)
        self._install_services(None)

    async def close(self) -> None:
        try:
            if self._services_ready:
                await self.flush()
        finally:
            await self.realtime.stop()
            await self.transport.close()

    async def __aenter__(self) -> "AnyListClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
