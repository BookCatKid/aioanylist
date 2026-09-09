from __future__ import annotations

import pytest

from anylist_sdk.proto import PB
from anylist_sdk.services.recipes import RecipesService
from anylist_sdk.services.shopping import ShoppingListsService
from anylist_sdk.state import AnyListState


@pytest.mark.asyncio
async def test_new_recipe_gets_creation_timestamp_and_all_recipes_membership(fake_transport) -> None:
    state = AnyListState(user_id="user", recipe_data_id="recipe-data")
    state.all_recipes_collection = PB.PBRecipeCollection(identifier="all")
    service = RecipesService(fake_transport, state, user_id="user")

    recipe = await service.save(PB.PBRecipe(identifier="recipe", name="Soup"))

    assert recipe.creationTimestamp > 0
    assert list(state.all_recipes_collection.recipeIds) == ["recipe"]
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "save-recipe"
    assert operation.recipe.creationTimestamp == recipe.creationTimestamp
    assert operation.recipeDataId == "recipe-data"


@pytest.mark.asyncio
async def test_updating_existing_recipe_does_not_reset_creation_timestamp(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.all_recipes_collection = PB.PBRecipeCollection(identifier="all", recipeIds=["recipe"])
    state.recipes["recipe"] = PB.PBRecipe(
        identifier="recipe", name="Old", creationTimestamp=123.0
    )
    service = RecipesService(fake_transport, state, user_id="user")

    updated = await service.save(
        PB.PBRecipe(identifier="recipe", name="New", creationTimestamp=123.0)
    )

    assert updated.creationTimestamp == 123.0
    assert list(state.all_recipes_collection.recipeIds) == ["recipe"]


@pytest.mark.asyncio
async def test_remove_many_prunes_all_recipe_collections(fake_transport) -> None:
    state = AnyListState(user_id="user")
    for rid in ("a", "b", "c"):
        state.recipes[rid] = PB.PBRecipe(identifier=rid)
    state.all_recipes_collection = PB.PBRecipeCollection(
        identifier="all", recipeIds=["a", "b", "c"]
    )
    state.recipe_collections["collection"] = PB.PBRecipeCollection(
        identifier="collection", recipeIds=["a", "b", "c"]
    )
    service = RecipesService(fake_transport, state, user_id="user")

    await service.remove_many(["a", "c"])

    assert set(state.recipes) == {"b"}
    assert list(state.all_recipes_collection.recipeIds) == ["b"]
    assert list(state.recipe_collections["collection"].recipeIds) == ["b"]
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "remove-recipe-ids"
    assert list(operation.recipeIds) == ["a", "c"]


@pytest.mark.asyncio
async def test_remove_from_collection_matches_official_one_operation_per_recipe(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.recipe_collections["collection"] = PB.PBRecipeCollection(
        identifier="collection", recipeIds=["a", "b", "c"]
    )
    service = RecipesService(fake_transport, state, user_id="user")

    await service.remove_from_collection("collection", ["a", "c"])

    assert list(state.recipe_collections["collection"].recipeIds) == ["b"]
    operations = fake_transport.calls[-1][1]["operations"].operations
    assert len(operations) == 2
    assert [list(op.recipeCollection.recipeIds) for op in operations] == [["a"], ["c"]]


@pytest.mark.asyncio
async def test_recipe_update_reconciles_identity_stable_provenance(fake_transport) -> None:
    from anylist_sdk.derived import ingredient_to_item_ingredient, recipe_list_item_identifier
    from anylist_sdk.services.shopping import ShoppingListsService

    state = AnyListState(user_id="user")
    old_recipe = PB.PBRecipe(identifier="recipe", name="Soup")
    old_ing = old_recipe.ingredients.add(identifier="ing", quantity="1 cup", name="tomatoes")
    old_ing.rawIngredient = "1 cup tomatoes"
    new_recipe = PB.PBRecipe(identifier="recipe", name="Tomato Soup")
    new_ing = new_recipe.ingredients.add(identifier="ing", quantity="1 cup", name="tomatoes")
    new_ing.rawIngredient = "1 cup tomatoes"

    source = ingredient_to_item_ingredient(old_ing, old_recipe)
    item_id = recipe_list_item_identifier(source, "0123456789abcdef0123456789abcdef")
    shopping = PB.ShoppingList(identifier="0123456789abcdef0123456789abcdef")
    item = shopping.items.add(identifier=item_id, listId=shopping.identifier, name="tomatoes")
    item.ingredients.add().CopyFrom(source)
    state.shopping_lists[shopping.identifier] = shopping
    service = ShoppingListsService(fake_transport, state, user_id="user")

    changed = await service.sync_recipe_update(shopping.identifier, new_recipe, old_recipe)

    assert changed == 1
    live = state.shopping_lists[shopping.identifier].items[0]
    assert live.ingredients[0].recipeName == "Tomato Soup"
    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "add-item-ingredient-to-list-item"
    assert operation.listItemId == item_id


@pytest.mark.asyncio
async def test_recipe_update_moves_identity_changed_provenance(fake_transport) -> None:
    from anylist_sdk.derived import ingredient_to_item_ingredient, recipe_list_item_identifier
    from anylist_sdk.services.shopping import ShoppingListsService

    list_id = "0123456789abcdef0123456789abcdef"
    state = AnyListState(user_id="user")
    old_recipe = PB.PBRecipe(identifier="recipe", name="Soup")
    old_ing = old_recipe.ingredients.add(identifier="ing", quantity="1 cup", name="tomatoes")
    old_ing.rawIngredient = "1 cup tomatoes"
    new_recipe = PB.PBRecipe(identifier="recipe", name="Soup")
    new_ing = new_recipe.ingredients.add(identifier="ing", quantity="1 lb", name="tomatoes")
    new_ing.rawIngredient = "1 lb tomatoes"

    old_source = ingredient_to_item_ingredient(old_ing, old_recipe)
    old_id = recipe_list_item_identifier(old_source, list_id)
    new_source = ingredient_to_item_ingredient(new_ing, new_recipe)
    new_id = recipe_list_item_identifier(new_source, list_id)
    assert old_id != new_id

    shopping = PB.ShoppingList(identifier=list_id)
    item = shopping.items.add(identifier=old_id, listId=list_id, name="tomatoes")
    item.ingredients.add().CopyFrom(old_source)
    state.shopping_lists[list_id] = shopping
    service = ShoppingListsService(fake_transport, state, user_id="user")

    changed = await service.sync_recipe_update(list_id, new_recipe, old_recipe)

    assert changed == 2
    ids = [x.identifier for x in state.shopping_lists[list_id].items]
    assert old_id not in ids
    assert new_id in ids
    operations = fake_transport.calls[-1][1]["operations"].operations
    assert [x.metadata.handlerId for x in operations] == [
        "remove-ingredient-id-from-list-item",
        "add-item-ingredient-to-list-item",
    ]


@pytest.mark.asyncio
async def test_recipe_update_does_not_recreate_checked_identity_changed_item(fake_transport) -> None:
    from anylist_sdk.derived import ingredient_to_item_ingredient, recipe_list_item_identifier
    from anylist_sdk.services.shopping import ShoppingListsService

    list_id = "0123456789abcdef0123456789abcdef"
    state = AnyListState(user_id="user")
    old_recipe = PB.PBRecipe(identifier="recipe", name="Soup")
    old_ing = old_recipe.ingredients.add(identifier="ing", quantity="1 cup", name="tomatoes")
    new_recipe = PB.PBRecipe(identifier="recipe", name="Soup")
    new_ing = new_recipe.ingredients.add(identifier="ing", quantity="1 lb", name="tomatoes")
    old_source = ingredient_to_item_ingredient(old_ing, old_recipe)
    old_id = recipe_list_item_identifier(old_source, list_id)

    shopping = PB.ShoppingList(identifier=list_id)
    item = shopping.items.add(identifier=old_id, listId=list_id, name="tomatoes", checked=True)
    item.ingredients.add().CopyFrom(old_source)
    state.shopping_lists[list_id] = shopping
    service = ShoppingListsService(fake_transport, state, user_id="user")

    changed = await service.sync_recipe_update(list_id, new_recipe, old_recipe)

    assert changed == 1
    assert not state.shopping_lists[list_id].items
    operations = fake_transport.calls[-1][1]["operations"].operations
    assert [x.metadata.handlerId for x in operations] == ["remove-ingredient-id-from-list-item"]

@pytest.mark.asyncio
async def test_recipe_item_inherits_favorite_properties_without_overwriting_recipe_quantity(fake_transport) -> None:
    from anylist_sdk.services.starter import favorite_list_id
    state=AnyListState(user_id='u')
    lst=PB.ShoppingList(identifier='0123456789abcdef0123456789abcdef')
    state.shopping_lists[lst.identifier]=lst
    favorite=PB.StarterList(identifier=favorite_list_id(lst.identifier))
    saved=favorite.items.add(identifier='fav',name='Fresh Tomatoes',details='favorite note',productUpc='123')
    saved.storeIds.append('store')
    saved.quantityPb.amount='99'
    saved.packageSizePb.rawPackageSize='28 oz'
    saved.priceQuantityPb.amount='2'
    saved.priceQuantityShouldOverrideItemQuantity=True
    state.favorite_item_lists[favorite.identifier]=favorite
    service=ShoppingListsService(fake_transport,state,user_id='u')
    source=PB.PBItemIngredient(recipeId='r',recipeName='Recipe')
    source.ingredient.identifier='ing';source.ingredient.name='fresh tomato'
    source.quantityPb.amount='1';source.quantityPb.unit='can';source.quantityPb.rawQuantity='1 can'
    source.packageSizePb.size='28';source.packageSizePb.unit='oz';source.packageSizePb.rawPackageSize='28 oz'
    created=await service.add_recipe_ingredient(lst.identifier,source,flush=False)
    assert created.details=='favorite note' and created.productUpc=='123' and list(created.storeIds)==['store']
    assert created.priceQuantityPb.amount=='2' and created.priceQuantityShouldOverrideItemQuantity
    assert created.packageSizePb.rawPackageSize=='28 oz'
    assert created.ingredients[0].quantityPb.amount=='1'


@pytest.mark.asyncio
async def test_recipe_item_prefers_favorite_over_newest_recent(fake_transport) -> None:
    from anylist_sdk.services.starter import favorite_list_id, recent_list_id
    state=AnyListState(user_id='u');lid='0123456789abcdef0123456789abcdef'
    state.shopping_lists[lid]=PB.ShoppingList(identifier=lid)
    fav=PB.StarterList(identifier=favorite_list_id(lid));fav.items.add(identifier='f',name='milk',details='favorite')
    rec=PB.StarterList(identifier=recent_list_id(lid));rec.items.add(identifier='r',name='milk',details='recent')
    state.favorite_item_lists[fav.identifier]=fav;state.recent_item_lists[rec.identifier]=rec
    service=ShoppingListsService(fake_transport,state,user_id='u')
    source=PB.PBItemIngredient(recipeId='r');source.ingredient.identifier='i';source.ingredient.name='milk'
    created=await service.add_recipe_ingredient(lid,source,flush=False)
    assert created.details=='favorite'


@pytest.mark.asyncio
async def test_request_link_applies_successful_full_recipe_state(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.recipe_data_id = "old"
    state.recipes["old"] = PB.PBRecipe(identifier="old")
    service = RecipesService(fake_transport, state, user_id="user")
    recipe_data = PB.PBRecipeDataResponse(recipeDataId="new", timestamp=12.0)
    recipe_data.recipes.add(identifier="fresh", name="Fresh")
    response = PB.PBRecipeLinkRequestResponse(statusCode=0)
    response.recipeDataResponse.CopyFrom(recipe_data)
    fake_transport.responses.append(response)

    result = await service.request_link("friend@example.com")

    assert result.statusCode == 0
    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/user-recipe-data/request-recipe-link-v2"
    assert response_type == "PBRecipeLinkRequestResponse"
    assert fields["link_request"].confirmingEmail == "friend@example.com"
    assert fields["link_request"].requestingUserId == "user"
    assert state.recipe_data_id == "new"
    assert set(state.recipes) == {"fresh"}


@pytest.mark.asyncio
async def test_accept_link_uses_request_id_and_user_id_and_replaces_state(fake_transport) -> None:
    state = AnyListState(user_id="user")
    service = RecipesService(fake_transport, state, user_id="user")
    response = PB.PBRecipeDataResponse(recipeDataId="rd", timestamp=3.0)
    response.recipes.add(identifier="r")
    fake_transport.responses.append(response)
    request = PB.PBRecipeLinkRequest(identifier="request1")

    result = await service.accept_link(request)

    assert result.recipeDataId == "rd"
    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/user-recipe-data/accept-recipe-link-request"
    assert fields == {"link_request_id": "request1", "user_id": "user"}
    assert response_type == "PBRecipeDataResponse"
    assert set(state.recipes) == {"r"}


@pytest.mark.asyncio
async def test_cancel_link_posts_request_proto_and_receives_full_recipe_data(fake_transport) -> None:
    state = AnyListState(user_id="user")
    service = RecipesService(fake_transport, state, user_id="user")
    response = PB.PBRecipeDataResponse(recipeDataId="rd2", timestamp=4.0)
    fake_transport.responses.append(response)
    request = PB.PBRecipeLinkRequest(identifier="request2")

    await service.cancel_link(request)

    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/user-recipe-data/cancel-recipe-link-request"
    assert fields["link_request"] is request
    assert response_type == "PBRecipeDataResponse"
    assert state.recipe_data_id == "rd2"


@pytest.mark.asyncio
async def test_recipe_email_can_include_meal_plan_event_context(fake_transport) -> None:
    state = AnyListState(user_id="user")
    state.meal_plan_events["event"] = PB.PBCalendarEvent(identifier="event", eventType=1)
    service = RecipesService(fake_transport, state, user_id="user")

    await service.send_as_email("recipe", "a@example.com", event_id="event")

    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "POST /data/recipes/send-as-email"
    assert fields == {
        "recipe_id": "recipe",
        "email": "a@example.com",
        "event_id": "event",
        "event_type": 1,
    }
    assert response_type is None
