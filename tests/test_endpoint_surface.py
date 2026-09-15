from __future__ import annotations

import pytest

from aioanylist.proto import PB, decode
from aioanylist.services.categories import CategorizedItemsService, UserCategoriesService
from aioanylist.services.folders import FoldersService
from aioanylist.services.http_api import (
    AlexaService,
    PhotosService,
    SharingService,
    WebStateService,
)
from aioanylist.services.meal_plan import MealPlanService
from aioanylist.services.recipes import RecipesService
from aioanylist.services.settings import ListSettingsService, MobileSettingsService
from aioanylist.services.shopping import ShoppingListsService
from aioanylist.services.starter import StarterListsService
from aioanylist.state import AnyListState


@pytest.mark.asyncio
async def test_alexa_direct_endpoint_contracts(fake_transport) -> None:
    service = AlexaService(fake_transport)
    fake_transport.responses.extend([b'{"success":true}'] * 4)

    assert await service.link_list(alexa_list_id="alexa", anylist_list_id="any") == {
        "success": True
    }
    assert await service.unlink_list("alexa") == {"success": True}
    assert await service.unlink_anylist_list("any") == {"success": True}
    assert await service.set_enabled_lists(["on-a", "on-b"], ["off"]) == {"success": True}

    assert fake_transport.calls[0] == (
        "POST /data/alexa/link-list",
        {"alexa_list_id": "alexa", "anylist_list_id": "any"},
        None,
    )
    assert fake_transport.calls[1] == (
        "POST /data/alexa/unlink-list",
        {"alexa_list_id": "alexa"},
        None,
    )
    assert fake_transport.calls[2] == (
        "POST /data/alexa/unlink-anylist-list",
        {"anylist_list_id": "any"},
        None,
    )
    endpoint, fields, response_type = fake_transport.calls[3]
    assert endpoint == "POST /data/alexa/set-is-enabled-for-alexa-for-list-ids"
    assert response_type is None
    assert list(decode("PBValue", fields["enabled_list_ids"]).stringValue) == ["on-a", "on-b"]
    assert list(decode("PBValue", fields["disabled_list_ids"]).stringValue) == ["off"]


@pytest.mark.asyncio
async def test_auxiliary_direct_endpoint_contracts(fake_transport) -> None:
    fake_transport.responses.extend([b"photo-ok", b'{"status":"success"}', b"mac", b"welcome"])
    photos = PhotosService(fake_transport)
    sharing = SharingService(fake_transport, "user")
    web = WebStateService(fake_transport)

    assert await photos.upload_url("https://example.test/image.png", photo_id="photo") == "photo"
    assert await sharing.send_list_email("list", "a@example.com", decimal_separator=",") == {
        "status": "success"
    }
    assert await web.mark_mac_app_download_prompt_seen() == b"mac"
    assert await web.mark_welcome_screen_seen() == b"welcome"

    assert fake_transport.calls == [
        (
            "POST /data/photos/upload-url",
            {"photo_url": "https://example.test/image.png", "photo_id": "photo"},
            None,
        ),
        (
            "POST /data/shopping-lists/send-as-email",
            {"email": "a@example.com", "list_id": "list", "decimal_separator": ","},
            None,
        ),
        ("GET /data/web/set-mac-app-download-prompt-cookie", {}, None),
        ("GET /data/web/set-welcome-screen-cookie", {}, None),
    ]


@pytest.mark.asyncio
async def test_meal_plan_direct_auxiliary_endpoint_contracts(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="calendar")
    service = MealPlanService(fake_transport, state, user_id="user")
    response = PB.PBMealPlanSetICalendarEnabledRequestResponse(statusCode=0)
    fake_transport.responses.extend([response, b"email-result"])

    result = await service.set_icalendar_enabled(True)
    assert result.statusCode == 0
    assert await service.send_as_email("a@example.com", "<p>meal plan</p>") == b"email-result"

    endpoint, fields, response_type = fake_transport.calls[0]
    assert endpoint == "/data/meal-planning-calendar/set-icalendar-enabled"
    assert response_type == "PBMealPlanSetICalendarEnabledRequestResponse"
    assert fields["icalendar_request"].shouldEnableIcalendarGeneration is True
    assert fake_transport.calls[1] == (
        "POST /data/meal-planning-calendar/send-as-email",
        {"email": "a@example.com", "markup": "<p>meal plan</p>"},
        None,
    )


@pytest.mark.asyncio
async def test_direct_refresh_endpoint_contracts(fake_transport) -> None:
    state = AnyListState(user_id="user")

    categories = UserCategoriesService(fake_transport, state, user_id="user")
    categorized = CategorizedItemsService(fake_transport, state, user_id="user")
    folders = FoldersService(fake_transport, state, user_id="user")
    mobile = MobileSettingsService(fake_transport, state, user_id="user")
    starter = StarterListsService(fake_transport, state, user_id="user")
    recipes = RecipesService(fake_transport, state, user_id="user")
    meal_plan = MealPlanService(fake_transport, state, user_id="user")

    fake_transport.responses.extend(
        [
            PB.PBUserCategoryData(identifier="all", timestamp=1),
            PB.PBCategorizedItemsList(
                timestamp=PB.PBTimestamp(identifier="last-categorized-item-timestamp", timestamp=1)
            ),
            PB.PBListFoldersResponse(listDataId="data", rootFolderId="root"),
            PB.PBMobileAppSettings(identifier="user", timestamp=1),
            PB.PBIdentifierList(timestamp=1),
            PB.PBRecipeDataResponse(recipeDataId="recipes", timestamp=1),
            PB.PBRecipeDataResponse(recipeDataId="recipes", timestamp=2),
            PB.PBCalendarResponse(calendarId="calendar", responseVersion=1, isFullSync=True),
        ]
    )

    await categories.refresh()
    await categorized.refresh()
    await folders.refresh()
    await mobile.refresh()
    await starter.refresh_order()
    await recipes.refresh()
    await recipes.refresh(desktop_import_extension=True)
    await meal_plan.refresh()

    assert [call[0] for call in fake_transport.calls] == [
        "/data/user-categories/all",
        "/data/categorized-items/all",
        "/data/list-folders/all",
        "/data/mobile-app-settings/by-id",
        "/data/starter-lists/ordered-ids",
        "/data/user-recipe-data/all",
        "/data/user-recipe-data/desktop-recipe-import-extension",
        "/data/meal-planning-calendar/get",
    ]
    assert fake_transport.calls[1][1]["timestamp"].identifier == "last-categorized-item-timestamp"
    assert fake_transport.calls[4][1]["timestamp"].identifier == "user"
    assert fake_transport.calls[5][2] == fake_transport.calls[6][2] == "PBRecipeDataResponse"
    assert fake_transport.calls[7][2] == "PBCalendarResponse"


@pytest.mark.asyncio
async def test_timestamped_direct_refreshes_treat_304_as_noop(fake_transport) -> None:
    state = AnyListState(user_id="user")
    services = [
        ShoppingListsService(fake_transport, state, user_id="user"),
        UserCategoriesService(fake_transport, state, user_id="user"),
        CategorizedItemsService(fake_transport, state, user_id="user"),
        FoldersService(fake_transport, state, user_id="user"),
        ListSettingsService(fake_transport, state, user_id="user"),
        ListSettingsService(fake_transport, state, user_id="user", starter=True),
        MobileSettingsService(fake_transport, state, user_id="user"),
        StarterListsService(fake_transport, state, user_id="user"),
        RecipesService(fake_transport, state, user_id="user"),
        MealPlanService(fake_transport, state, user_id="user"),
    ]
    # FakeTransport uses None to model AnyListTransport.post_proto's HTTP-304 no-op result.
    fake_transport.responses.extend([None] * 12)

    assert await services[0].refresh() is None
    assert await services[1].refresh() is None
    assert await services[2].refresh() is None
    assert await services[3].refresh() is None
    assert await services[4].refresh() is None
    assert await services[5].refresh() is None
    assert await services[6].refresh() is None
    assert await services[7].refresh() is None
    assert await services[7].refresh_order() is None
    assert await services[8].refresh() is None
    assert await services[8].refresh(desktop_import_extension=True) is None
    assert await services[9].refresh() is None


@pytest.mark.asyncio
async def test_recipe_unlink_direct_endpoint_contract(fake_transport) -> None:
    state = AnyListState(user_id="user")
    service = RecipesService(fake_transport, state, user_id="user")
    fake_transport.responses.append(PB.PBRecipeDataResponse(recipeDataId="recipes", timestamp=2))

    await service.unlink("linked-user")

    assert fake_transport.calls == [
        (
            "/data/user-recipe-data/unlink-recipes",
            {"user_id": "linked-user"},
            "PBRecipeDataResponse",
        )
    ]


def test_operation_queue_endpoint_routing_for_remaining_domains(fake_transport) -> None:
    state = AnyListState(user_id="user")
    assert UserCategoriesService(fake_transport, state, user_id="user").queue.spec.endpoint == (
        "/data/user-categories/update"
    )
    assert CategorizedItemsService(fake_transport, state, user_id="user").queue.spec.endpoint == (
        "/data/categorized-items/update"
    )
    assert RecipesService(fake_transport, state, user_id="user").queue.spec.endpoint == (
        "/data/user-recipe-data/update"
    )
