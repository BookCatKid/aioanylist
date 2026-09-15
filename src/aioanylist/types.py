from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from google.protobuf.message import Message


class Domain(StrEnum):
    SHOPPING_LISTS = "shopping-lists"
    LIST_FOLDERS = "list-folders"
    LIST_SETTINGS = "list-settings"
    STARTER_LISTS = "starter-lists"
    STARTER_LIST_SETTINGS = "starter-list-settings"
    CATEGORIZED_ITEMS = "categorized-items"
    USER_CATEGORIES = "user-categories"
    RECIPES = "user-recipe-data"
    MEAL_PLAN = "meal-plan-calendar"
    MOBILE_SETTINGS = "mobile-app-settings"
    ACCOUNT = "account-info"
    SUBSCRIPTION = "subscription-info"


@dataclass(slots=True, frozen=True)
class AuthTokens:
    user_id: str
    access_token: str
    refresh_token: str
    is_premium_user: bool | None = None
    user_locale: str | None = None


@dataclass(slots=True, frozen=True)
class OperationAck:
    processed_operation_ids: tuple[str, ...]
    raw_response: Message

    @property
    def processed_ids(self) -> tuple[str, ...]:
        """Short alias for :attr:`processed_operation_ids`."""
        return self.processed_operation_ids


@dataclass(slots=True, frozen=True)
class AutocompleteSuggestion:
    text: str
    source: str
    score: float = 0.0
    payload: Message | str | None = None


@dataclass(slots=True, frozen=True)
class MatchRange:
    start: int
    length: int


@dataclass(slots=True, frozen=True)
class ImageSearchResult:
    media_url: str
    thumbnail_url: str | None = None


@dataclass(slots=True, frozen=True)
class PlaceSearchResult:
    name: str
    formatted_address: str
    latitude: float
    longitude: float


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONMapping: TypeAlias = dict[str, JSONValue]
