from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
import time
from typing import Any

from google.protobuf.message import Message

from ..identifiers import uuid4_hex
from ..derived import sort_recipes
from ..operations import QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message


class RecipesService(OperationService):
    def __init__(self, transport: AnyListTransport, state: AnyListState, *, user_id: str, journal=None):
        super().__init__(transport, state, user_id=user_id,
            spec=QueueSpec(f"{user_id}:recipes", "/data/user-recipe-data/update",
                           "PBRecipeOperation", "PBRecipeOperationList"), journal=journal)
        self.user_id = user_id
        self.queue.on_response = self._on_response
        self.on_recipe_removed: Callable[[str, bool], Awaitable[None]] | None = None
        self.on_recipe_updated: Callable[[Message, Message, bool], Awaitable[None]] | None = None

    async def _on_response(self, response: Message) -> None:
        if not response.originalTimestamps or not response.newTimestamps:
            return
        original = float(response.originalTimestamps[0].timestamp)
        if original == float(self.state.recipe_timestamp):
            self.state.recipe_timestamp = float(response.newTimestamps[0].timestamp)
        else:
            await self.refresh()

    def all(self) -> list[Message]:
        return list(self.state.recipes.values())

    def get(self, recipe_id: str) -> Message | None:
        return self.state.recipes.get(recipe_id)

    def collections(self) -> list[Message]:
        return list(self.state.recipe_collections.values())

    def sorted(
        self,
        recipes: Sequence[Message] | None = None,
        *,
        settings: Message | None = None,
        collection_id: str | None = None,
        today: str | None = None,
    ) -> list[Message]:
        """Return recipes ordered with AnyList Web's client-side collection sorter."""
        if collection_id is not None:
            collection = self._collection(collection_id)
            if recipes is None:
                recipes = [
                    self.state.recipes[rid]
                    for rid in collection.recipeIds
                    if rid in self.state.recipes
                ]
            if settings is None and collection.HasField("collectionSettings"):
                settings = collection.collectionSettings
        values = list(recipes) if recipes is not None else self.all()
        return sort_recipes(
            values,
            settings,
            meal_plan_events=list(self.state.meal_plan_events.values()),
            today=today,
        )

    async def refresh(self, *, desktop_import_extension: bool = False) -> Message:
        fields: dict[str, Message] = {}
        if self.state.recipe_data_id:
            fields["timestamp"] = PB.PBTimestamp(
                identifier=self.state.recipe_data_id, timestamp=self.state.recipe_timestamp
            )
        endpoint = (
            "/data/user-recipe-data/desktop-recipe-import-extension"
            if desktop_import_extension
            else "/data/user-recipe-data/all"
        )
        response = await self.transport.post_proto(
            endpoint, fields=fields, response_type="PBRecipeDataResponse"
        )
        assert isinstance(response, Message)
        self.state.apply_recipes(response)
        return response

    async def operation(self, handler_id: str, *, flush: bool = True, **fields: Any) -> str:
        if self.state.recipe_data_id and "recipeDataId" not in fields:
            fields["recipeDataId"] = self.state.recipe_data_id
        return await super().operation(handler_id, flush=flush, **fields)

    async def save(self, recipe: Message, *, from_web_import: bool = False, flush: bool = True) -> Message:
        recipe = clone_message(recipe)
        if not recipe.identifier:
            recipe.identifier = uuid4_hex()
        if not self.state.recipe_data_id and getattr(recipe, "recipeDataId", ""):
            self.state.recipe_data_id = recipe.recipeDataId
        if self.state.recipe_data_id and "recipeDataId" in recipe.DESCRIPTOR.fields_by_name:
            recipe.recipeDataId = self.state.recipe_data_id
        is_new = recipe.identifier not in self.state.recipes
        previous = clone(self.state.recipes[recipe.identifier]) if not is_new else None
        if is_new:
            # The web manager stamps newly-created recipes client-side before enqueueing.
            recipe.creationTimestamp = time.time()
            all_recipes = self.state.all_recipes_collection
            if all_recipes is not None and recipe.identifier not in all_recipes.recipeIds:
                all_recipes.recipeIds.append(recipe.identifier)
        self.state.recipes[recipe.identifier] = clone(recipe)
        await self.operation("save-recipe", recipe=recipe,
                             isNewRecipeFromWebImport=from_web_import, flush=flush)
        if previous is not None and self.on_recipe_updated is not None:
            await self.on_recipe_updated(recipe, previous, flush)
        return self.state.recipes[recipe.identifier]

    async def create(self, name: str, *, ingredients: Sequence[Message] = (),
                     preparation_steps: Sequence[str] = (), servings: str | None = None,
                     source_name: str | None = None, source_url: str | None = None,
                     flush: bool = True) -> Message:
        recipe = PB.PBRecipe(identifier=uuid4_hex(), name=name)
        for ingredient in ingredients:
            recipe.ingredients.add().CopyFrom(ingredient)
        recipe.preparationSteps.extend(preparation_steps)
        if servings is not None: recipe.servings = servings
        if source_name is not None: recipe.sourceName = source_name
        if source_url is not None: recipe.sourceUrl = source_url
        return await self.save(recipe, flush=flush)

    async def remove(self, recipe_id: str, *, flush: bool = True) -> None:
        recipe = self.state.recipes.get(recipe_id)
        if recipe is None:
            raise KeyError(recipe_id)
        if self.on_recipe_removed is not None:
            await self.on_recipe_removed(recipe_id, flush)
        self.state.recipes.pop(recipe_id, None)
        for collection in self.state.recipe_collections.values():
            while recipe_id in collection.recipeIds:
                collection.recipeIds.remove(recipe_id)
        if self.state.all_recipes_collection is not None:
            while recipe_id in self.state.all_recipes_collection.recipeIds:
                self.state.all_recipes_collection.recipeIds.remove(recipe_id)
        await self.operation("remove-recipe", recipe=clone_message(recipe), flush=flush)

    async def remove_many(self, recipe_ids: Sequence[str], *, flush: bool = True) -> None:
        ids = list(recipe_ids)
        if self.on_recipe_removed is not None:
            for rid in ids:
                if rid in self.state.recipes:
                    await self.on_recipe_removed(rid, flush)
        for rid in ids:
            self.state.recipes.pop(rid, None)
        for collection in self.state.recipe_collections.values():
            kept = [rid for rid in collection.recipeIds if rid not in ids]
            del collection.recipeIds[:]
            collection.recipeIds.extend(kept)
        if self.state.all_recipes_collection is not None:
            all_recipes = self.state.all_recipes_collection
            kept = [rid for rid in all_recipes.recipeIds if rid not in ids]
            del all_recipes.recipeIds[:]
            all_recipes.recipeIds.extend(kept)
        await self.operation("remove-recipe-ids", recipeIds=ids, flush=flush)

    async def create_collection(self, name: str, *, collection_id: str | None = None,
                                flush: bool = True) -> Message:
        collection = PB.PBRecipeCollection(identifier=collection_id or uuid4_hex(), name=name)
        self.state.recipe_collections[collection.identifier] = clone(collection)
        self.state.recipe_collection_ids.append(collection.identifier)
        await self.operation("new-recipe-collection", recipeCollection=collection, flush=flush)
        return self.state.recipe_collections[collection.identifier]

    async def remove_collection(self, collection_id: str, *, flush: bool = True) -> None:
        collection = self.state.recipe_collections.pop(collection_id, None)
        if collection is None: raise KeyError(collection_id)
        if collection_id in self.state.recipe_collection_ids:
            self.state.recipe_collection_ids.remove(collection_id)
        await self.operation("remove-recipe-collection", recipeCollection=collection, flush=flush)

    async def rename_collection(self, collection_id: str, name: str, *, flush: bool = True) -> None:
        collection = self._collection(collection_id)
        collection.name = name
        await self.operation("set-recipe-collection-name", recipeCollection=clone_message(collection), flush=flush)

    async def add_to_collection(self, collection_id: str, recipe_ids: Sequence[str], *, flush: bool = True) -> None:
        collection = self._collection(collection_id)
        added = []
        for rid in recipe_ids:
            if rid in self.state.recipes and rid not in collection.recipeIds:
                collection.recipeIds.append(rid); added.append(rid)
        # AnyList Web sends a clone of the complete collection so metadata/settings are
        # retained, but narrows recipeIds to only IDs newly added by this mutation.
        partial = clone_message(collection)
        del partial.recipeIds[:]
        partial.recipeIds.extend(added)
        await self.operation("add-recipes-to-collection", recipeCollection=partial, flush=flush)

    async def remove_from_collection(self, collection_id: str, recipe_ids: Sequence[str], *, flush: bool = True) -> None:
        collection = self._collection(collection_id)
        ids = list(recipe_ids)
        # The official web method queues one remove operation per recipe ID.  Preserve that
        # shape while still exposing an ergonomic sequence API.
        for rid in ids:
            while rid in collection.recipeIds:
                collection.recipeIds.remove(rid)
            partial = clone_message(collection)
            del partial.recipeIds[:]
            partial.recipeIds.append(rid)
            await self.operation(
                "remove-recipes-from-collection", recipeCollection=partial, flush=False
            )
        if flush and ids:
            await self.flush()

    async def reorder_collections(self, collection_ids: Sequence[str], *, flush: bool = True) -> None:
        self.state.recipe_collection_ids = list(collection_ids)
        await self.operation("set-ordered-recipe-collection-ids", recipeCollectionIds=list(collection_ids), flush=flush)

    async def reorder_recipes(self, collection_id: str, recipe_ids: Sequence[str], *, flush: bool = True) -> None:
        collection = self._collection(collection_id)
        del collection.recipeIds[:]; collection.recipeIds.extend(recipe_ids)
        await self.operation("set-ordered-recipe-ids-for-collection", recipeCollection=clone_message(collection), flush=flush)

    async def set_collection_icon(self, collection_id: str, icon: str | Message, *, flush: bool = True) -> None:
        c = self._collection(collection_id)
        value = icon if isinstance(icon, Message) else PB.PBIcon(iconName=icon)
        c.collectionSettings.icon.CopyFrom(value)
        await self.operation("set-recipe-collection-icon", recipeCollection=clone_message(c), flush=flush)

    async def set_collection_sort(self, collection_id: str, sort_order: int, *, reversed: bool = False,
                                  flush: bool = True) -> None:
        c = self._collection(collection_id)
        if c.HasField("collectionSettings"):
            c.collectionSettings.recipesSortOrder = sort_order
            c.collectionSettings.useReversedSortDirection = reversed
        else:
            # Bz.jz(sort_order) in the web client initializes exactly these fields.
            # Notably, the first call does not apply the requested reversed flag; that field
            # is only mutated once collectionSettings already exists.
            settings = PB.PBRecipeCollectionSettings(
                recipesSortOrder=sort_order, showOnlyRecipesWithNoCollection=False
            )
            c.collectionSettings.CopyFrom(settings)
        await self.operation("set-recipe-collection-sort-order", recipeCollection=clone_message(c), flush=flush)

    async def set_max_recipe_count(self, count: int, *, flush: bool = True) -> None:
        self.state.recipe_max_count = count
        await self.operation("set-max-recipe-count", maxRecipeCount=count, flush=flush)

    async def set_system_collection_recipe_sort(
        self, collection_id: str, sort_order: int, *, reversed: bool = False, flush: bool = True
    ) -> Message:
        settings = self.state.system_recipe_collection_settings.get(collection_id)
        if settings is None:
            settings = PB.PBRecipeCollectionSettings()
        else:
            settings = clone_message(settings)
        settings.recipesSortOrder = sort_order
        settings.useReversedSortDirection = reversed
        self.state.system_recipe_collection_settings[collection_id] = clone(settings)
        collection = PB.PBRecipeCollection(identifier=collection_id)
        collection.collectionSettings.CopyFrom(settings)
        await self.operation(
            "set-system-collection-sort-order", recipeCollection=collection, flush=flush
        )
        return settings

    async def set_system_collection_collection_sort(
        self, collection_id: str, sort_order: int, *, reversed: bool = False, flush: bool = True
    ) -> Message:
        settings = self.state.system_recipe_collection_settings.get(collection_id)
        if settings is None:
            settings = PB.PBRecipeCollectionSettings()
        else:
            settings = clone_message(settings)
        settings.collectionsSortOrder = sort_order
        settings.useReversedCollectionsSortDirection = reversed
        self.state.system_recipe_collection_settings[collection_id] = clone(settings)
        collection = PB.PBRecipeCollection(identifier=collection_id)
        collection.collectionSettings.CopyFrom(settings)
        await self.operation(
            "set-system-collection-collections-sort-order", recipeCollection=collection, flush=flush
        )
        return settings

    async def web_import(self, url: str, *, html: str | None = None) -> Message:
        fields: dict[str, bytes | str] = {"url": url}
        if html is not None: fields["html"] = html
        return await self.transport.post_proto("/data/recipes/web-import", fields=fields,
                                               response_type="PBRecipeWebImportResponse")

    async def send_as_email(
        self,
        recipe_id: str,
        email: str,
        *,
        event_id: str | None = None,
        event_type: int | None = None,
    ) -> bytes:
        # The official form optionally carries meal-plan provenance so recipe quantities can
        # be rendered for the specific scaled event being emailed.
        fields: dict[str, str | int] = {"recipe_id": recipe_id, "email": email}
        if event_id is not None:
            fields["event_id"] = event_id
            if event_type is None:
                event = self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
                if event is not None:
                    event_type = int(event.eventType)
        if event_type is not None:
            fields["event_type"] = event_type
        return await self.transport.request(
            "POST", "/data/recipes/send-as-email", fields=fields
        )

    async def request_link(self, email: str) -> Message:
        req = PB.PBRecipeLinkRequest(
            identifier=uuid4_hex(), requestingUserId=self.user_id, confirmingEmail=email
        )
        response = await self.transport.post_proto(
            "/data/user-recipe-data/request-recipe-link-v2",
            fields={"link_request": req},
            response_type="PBRecipeLinkRequestResponse",
        )
        # OX treats a successful link request as a full recipe-data replacement.
        if (
            response is not None
            and int(response.statusCode) == 0
            and response.HasField("recipeDataResponse")
        ):
            self.state.apply_recipes_full(response.recipeDataResponse)
        return response

    async def accept_link(self, request: Message | str) -> Message:
        request_id = request if isinstance(request, str) else str(request.identifier)
        response = await self.transport.post_proto(
            "/data/user-recipe-data/accept-recipe-link-request",
            fields={"link_request_id": request_id, "user_id": self.user_id},
            response_type="PBRecipeDataResponse",
        )
        if response is not None:
            self.state.apply_recipes_full(response)
        return response

    async def cancel_link(self, request: Message) -> Message | None:
        # RecipeManager.DX is intentionally a no-op unless the request belongs to one of
        # the two current link-request collections.  Compare identifiers because state
        # snapshots are cloned protobufs rather than the same JS object identity.
        request_id = str(request.identifier)
        known = any(
            str(candidate.identifier) == request_id
            for candidate in (
                *self.state.pending_recipe_link_requests,
                *self.state.recipe_link_requests_to_confirm,
            )
        )
        if not known:
            return None
        response = await self.transport.post_proto(
            "/data/user-recipe-data/cancel-recipe-link-request",
            fields={"link_request": request},
            response_type="PBRecipeDataResponse",
        )
        if response is not None:
            self.state.apply_recipes_full(response)
        return response

    async def unlink(self, user_id: str) -> Message:
        # The web client posts the linked user's ID as a plain multipart string and receives
        # a fresh PBRecipeDataResponse, which becomes the new local recipe/link state.
        response = await self.transport.post_proto(
            "/data/user-recipe-data/unlink-recipes",
            fields={"user_id": user_id},
            response_type="PBRecipeDataResponse",
        )
        if response is not None:
            # The official unlink callback uses RecipeManager.MX, a full replacement,
            # rather than the normal incremental FX merge path.
            self.state.apply_recipes_full(response)
        return response

    def _collection(self, cid: str) -> Message:
        if self.state.all_recipes_collection is not None and self.state.all_recipes_collection.identifier == cid:
            return self.state.all_recipes_collection
        c = self.state.recipe_collections.get(cid)
        if c is None:
            raise KeyError(cid)
        return c
