from __future__ import annotations

from typing import assert_type

from aioanylist import AnyListClient
from aioanylist.proto import (
    PB,
    ListItem,
    PBCalendarEvent,
    PBIngredient,
    PBItemQuantity,
    PBRecipe,
    PBRecipeCollection,
    PBUserCategory,
    ShoppingList,
)


async def exercise_public_types(client: AnyListClient) -> None:
    assert_type(client.user_id, str | None)
    assert_type(client.state.shopping_lists, dict[str, ShoppingList])
    assert_type(client.state.recipes, dict[str, PBRecipe])
    assert_type(client.state.meal_plan_events, dict[str, PBCalendarEvent])

    if client.lists is not None:
        assert_type(client.lists.all(), list[ShoppingList])
        assert_type(client.lists.get("list"), ShoppingList | None)
        assert_type(client.lists.item("list", "item"), ListItem | None)
        assert_type(await client.lists.create("Groceries", flush=False), ShoppingList)
        assert_type(await client.lists.add_item("list", "Milk", flush=False), ListItem)

    if client.recipes is not None:
        assert_type(client.recipes.all(), list[PBRecipe])
        assert_type(client.recipes.collections(), list[PBRecipeCollection])
        assert_type(await client.recipes.create("Soup", flush=False), PBRecipe)

    if client.categories is not None:
        assert_type(client.categories.all(), list[PBUserCategory])

    if client.meal_plan is not None:
        assert_type(client.meal_plan.events(), list[PBCalendarEvent])

    assert_type(PB.PBCalendarEventType.MealPlanCalendarEvent, int)
    assert_type(PB.PBCalendarEventType.Name(PB.PBCalendarEventType.MealPlanCalendarEvent), str)
    assert_type(
        PB.PBRecipeCollectionSettings.SortOrder.Name(
            PB.PBRecipeCollectionSettings.SortOrder.DateCreatedSortOrder
        ),
        str,
    )

    quantity = PB.PBItemQuantity(amount="2", unit="cup")
    assert_type(quantity, PBItemQuantity)
    assert_type(quantity.amount, str)

    ingredient = PB.PBIngredient(name="tomatoes")
    assert_type(ingredient, PBIngredient)

    shopping_list = PB.ShoppingList(identifier="list", name="Groceries")
    assert_type(shopping_list, ShoppingList)
    assert_type(shopping_list.items[0], ListItem)

    recipe = PB.PBRecipe(identifier="recipe", name="Soup")
    assert_type(recipe, PBRecipe)
    assert_type(recipe.ingredients[0], PBIngredient)
