from __future__ import annotations

import ast
import json
from importlib.resources import files
from pathlib import Path

from aioanylist.proto import PB, message_class


def _surface() -> dict:
    path = files("aioanylist").joinpath("official_surface.json")
    return json.loads(path.read_text("utf-8"))


def test_official_schema_shape() -> None:
    surface = _surface()
    assert surface["protobuf_package"] == "pcov.proto"
    assert len(surface["protobuf_messages"]) == 156
    assert len(surface["protobuf_enums"]) == 1
    assert PB.ListItem.DESCRIPTOR.fields_by_name["productUpc"].number == 30
    assert (
        PB.PBDataRequestClientInfo.DESCRIPTOR.fields_by_name["supportedResponseVersion"].number == 2
    )
    assert (
        message_class("PBUserDataResponse").DESCRIPTOR.full_name == "pcov.proto.PBUserDataResponse"
    )


def _service_handler_literals(root: Path, official_handlers: set[str]) -> set[str]:
    found: set[str] = set()
    for path in (root / "services").glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value in official_handlers
                ):
                    found.add(child.value)
    return found


def test_official_surface_counts_and_sdk_structural_coverage() -> None:
    surface = _surface()
    assert len(surface["endpoints"]) == 48
    assert len(surface["operation_handlers"]) == 185
    root = Path(__file__).parents[1] / "src" / "aioanylist"
    sdk_text = "\n".join(p.read_text("utf-8") for p in root.rglob("*.py"))
    # /auth/logout is the browser/XSRF form endpoint, not part of the token API. The SDK's
    # token-session logout uses the official native /data/auth/sign-out endpoint instead.
    browser_session_only = {"/auth/logout"}
    assert [
        x for x in surface["endpoints"] if x not in sdk_text and x not in browser_session_only
    ] == []

    official_handlers = set(surface["operation_handlers"])
    # Do not count documentation/comments/random module strings as operation coverage.
    # Every official handler must be represented inside a concrete service class, either
    # as a direct operation call or as a deliberate dynamic field->handler mapping.
    represented = _service_handler_literals(root, official_handlers)
    assert represented == official_handlers


def test_direct_service_operation_fields_exist_in_official_protobuf_schema() -> None:
    """Catch typos/invented fields in direct high-level operation construction."""
    root = Path(__file__).parents[1] / "src" / "aioanylist" / "services"
    operation_types = {
        "ShoppingListsService": "PBListOperation",
        "RecipesService": "PBRecipeOperation",
        "FoldersService": "PBListFolderOperation",
        "UserCategoriesService": "PBUserCategoryOperation",
        "CategorizedItemsService": "PBCategorizeItemOperation",
        "ListSettingsService": "PBListSettingsOperation",
        "MobileSettingsService": "PBMobileAppSettingsOperation",
        "StarterListsService": "PBStarterListOperation",
        "MealPlanService": "PBCalendarOperation",
    }
    failures: list[str] = []
    ignored_keywords = {"flush", "operation_version", "operation_class"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            operation_type = operation_types.get(cls.name)
            if operation_type is None:
                continue
            valid_fields = set(message_class(operation_type).DESCRIPTOR.fields_by_name)
            for call in (node for node in ast.walk(cls) if isinstance(node, ast.Call)):
                func = call.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "operation"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "self"
                ):
                    continue
                handler = "<dynamic>"
                if call.args and isinstance(call.args[0], ast.Constant):
                    handler = str(call.args[0].value)
                for keyword in call.keywords:
                    if keyword.arg is None or keyword.arg in ignored_keywords:
                        continue
                    if keyword.arg not in valid_fields:
                        failures.append(
                            f"{path.name}:{cls.name}:{handler} uses unknown "
                            f"{operation_type}.{keyword.arg}"
                        )
    assert failures == []
