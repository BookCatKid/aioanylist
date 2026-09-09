from anylist_sdk.proto import PB, decode, encode, new


def test_proto2_roundtrip_and_map_fields() -> None:
    response = PB.PBRecipeDataResponse(recipeDataId="r", timestamp=12.5)
    settings = PB.PBRecipeCollectionSettings(recipesSortOrder=3)
    response.settingsMapForSystemCollections["source"].CopyFrom(settings)
    clone = decode("PBRecipeDataResponse", encode(response))
    assert clone.recipeDataId == "r"
    assert clone.timestamp == 12.5
    assert clone.settingsMapForSystemCollections["source"].recipesSortOrder == 3


def test_nested_enums_are_exposed() -> None:
    assert PB.PBOperationMetadata.OperationClass.StoreOperation == 1
    assert PB.PBMealPlanTemplateGroupItem.Type.Group >= 0


def test_top_level_official_enum_is_available_from_pb_namespace() -> None:
    assert PB.PBCalendarEventType.MealPlanCalendarEvent == 0
    assert PB.PBCalendarEventType.MealPlanTemplateEvent == 2
