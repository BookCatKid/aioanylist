from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from .autocomplete import AutocompleteEngine
from .categorization import Categorizer
from .operations import FileOperationJournal, OperationJournal
from .realtime import RealtimeClient, RealtimeEvent
from .services import (
    AccountService, AlexaService, CategorizedItemsService, FoldersService, ListSettingsService,
    UserCategoriesService,
    MealPlanService, MobileSettingsService, PhotosService, RawAPI, RecipesService, SharingService,
    WebStateService,
    ShoppingListsService, StarterListsService,
)
from .state import AnyListState
from .sync import SyncCoordinator
from .tag_data import TagDataManager
from .transport import AnyListTransport
from .types import AuthTokens, Domain


class AnyListClient:
    """Async-first, protobuf-backed AnyList client reconstructed from AnyList Web."""
    def __init__(self,session:aiohttp.ClientSession|None=None,*,tokens:AuthTokens|None=None,
                 client_id:str|None=None,cache_dir:str|Path|None=None,
                 journal:OperationJournal|None=None,base_url:str="https://www.anylist.com") -> None:
        self.cache_dir=Path(cache_dir) if cache_dir is not None else None
        self.transport=AnyListTransport(session,base_url=base_url,client_id=client_id,tokens=tokens)
        self.state=AnyListState(user_id=(tokens.user_id if tokens else None))
        self.sync=SyncCoordinator(self.transport,self.state)
        self.realtime=RealtimeClient(self.transport)
        self.realtime.add_listener(self._on_realtime)
        self.realtime.add_reconnect_listener(self._on_reconnect)
        self.ready=asyncio.Event()
        self.tag_data=TagDataManager(self.transport,locale=(tokens.user_locale if tokens and tokens.user_locale else "en-US"),
                                     cache_dir=(self.cache_dir / "tags" if self.cache_dir else None))
        self.categorizer=Categorizer(self.tag_data)
        self.autocomplete=AutocompleteEngine(self.tag_data)
        self._journal=journal or (FileOperationJournal(self.cache_dir / "operations") if self.cache_dir else None)
        self._services_ready=False
        self._install_services(tokens.user_id if tokens else None)

    def _install_services(self,user_id:str|None)->None:
        self.state.user_id = user_id
        if not user_id:
            self.lists=self.recipes=self.folders=self.categories=self.categorized_items=None
            self.list_settings=self.starter_list_settings=self.mobile_settings=self.starter_lists=None
            self.meal_plan=self.account=self.photos=self.sharing=self.alexa=self.web_state=None
            self.raw=RawAPI(self.transport);self._services_ready=False;return
        j=self._journal
        self.lists=ShoppingListsService(self.transport,self.state,user_id=user_id,journal=j)
        self.recipes=RecipesService(self.transport,self.state,user_id=user_id,journal=j)
        self.folders=FoldersService(self.transport,self.state,user_id=user_id,journal=j)
        self.categories=UserCategoriesService(self.transport,self.state,user_id=user_id,journal=j)
        self.categorized_items=CategorizedItemsService(self.transport,self.state,user_id=user_id,journal=j)
        self.list_settings=ListSettingsService(self.transport,self.state,user_id=user_id,journal=j)
        self.starter_list_settings=ListSettingsService(self.transport,self.state,user_id=user_id,starter=True,journal=j)
        self.mobile_settings=MobileSettingsService(self.transport,self.state,user_id=user_id,journal=j)
        self.starter_lists=StarterListsService(self.transport,self.state,user_id=user_id,journal=j)
        self.meal_plan=MealPlanService(self.transport,self.state,user_id=user_id,journal=j)
        self.account=AccountService(self.transport,self.state);self.photos=PhotosService(self.transport)
        self.sharing=SharingService(self.transport,user_id,self.state);self.alexa=AlexaService(self.transport)
        self.web_state=WebStateService(self.transport);self.raw=RawAPI(self.transport)
        self.recipes.on_recipe_removed = self._cleanup_recipe_references
        self.recipes.on_recipe_updated = self._sync_recipe_references
        self.meal_plan.on_event_updated = self._sync_event_references
        self.meal_plan.on_event_removed = self._cleanup_event_references
        self.lists.on_store_filter_removed = self._clear_selected_store_filter
        self.lists.on_category_group_removed = self._migrate_selected_category_group
        self._services_ready=True

    @property
    def tokens(self)->AuthTokens|None:return self.transport.tokens
    @property
    def user_id(self)->str|None:return self.tokens.user_id if self.tokens else None

    async def sign_in(self,email:str,password:str)->AuthTokens:
        previous_user = self.state.user_id
        tokens=await self.transport.sign_in(email,password)
        self.tag_data.locale=tokens.user_locale or "en-US"
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

    async def load(self,*,realtime:bool=False,load_tag_data:bool=True,restore_pending:bool=True)->AnyListState:
        if not self._services_ready:raise RuntimeError("Authenticate before loading AnyList data")
        # The official UI has a readiness barrier; aggregate sync and static tag data can load concurrently.
        tasks=[asyncio.create_task(self.sync.refresh(full=True))]
        if load_tag_data:tasks.append(asyncio.create_task(self.tag_data.active_and_english()))
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
        if realtime:await self.realtime.start()
        return self.state

    async def refresh(self)->AnyListState:
        await self.sync.refresh();return self.state

    async def flush(self)->None:
        for service in self._operation_services():await service.flush()

    def _operation_services(self):
        if not self._services_ready:return []
        return [self.lists,self.recipes,self.folders,self.categories,self.categorized_items,
                self.list_settings,self.starter_list_settings,self.mobile_settings,self.starter_lists,self.meal_plan]

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
        if settings is None or str(getattr(settings, "listCategoryGroupId", "") or "") != removed_group_id:
            return
        replacement = self.lists._default_category_group(list_id)
        if replacement is None:
            return
        await self.list_settings.set(
            list_id, "listCategoryGroupId", str(replacement.identifier), flush=flush
        )

    async def _sync_recipe_references(
        self, new_recipe, old_recipe, flush: bool
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

    async def _sync_event_references(self, new_event, old_event, flush: bool) -> None:
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

    async def _cleanup_event_references(self, event, flush: bool) -> None:
        list_id = self._recipe_ingredients_list_id()
        if self.lists is not None and list_id:
            await self.lists.remove_event_references(
                list_id, str(event.identifier), flush=flush
            )

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

    async def _on_realtime(self,event:RealtimeEvent)->None:
        if event.message=="did-delete-account":
            self.ready.clear();return
        if event.domain in {Domain.ACCOUNT,Domain.SUBSCRIPTION}:
            if self.account:
                try:await self.account.get()
                except Exception:pass
            return
        # Aggregate sync carries all invalidated domains and coalesces concurrent refresh events.
        await self.sync.refresh()

    async def logout(self)->None:
        await self.realtime.stop()
        await self.transport.logout()
        self.ready.clear()
        self.state = AnyListState()
        self.sync = SyncCoordinator(self.transport, self.state)
        self._install_services(None)

    async def close(self)->None:
        try:
            if self._services_ready:await self.flush()
        finally:
            await self.realtime.stop();await self.transport.close()

    async def __aenter__(self)->"AnyListClient":return self
    async def __aexit__(self,*_):await self.close()
