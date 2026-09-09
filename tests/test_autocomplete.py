from __future__ import annotations

import pytest

from anylist_sdk.autocomplete import AutocompleteEngine
from anylist_sdk.proto import PB
from anylist_sdk.tag_data import TagData


class DataManager:
    def __init__(self, data): self.data=data
    async def get(self, *args, **kwargs): return self.data


def _data(language="en"):
    return TagData(
        language=language,
        tags={},
        normalized_display_names_index={},
        implied_tags={},
        tag_keywords_index={},
        autocomplete_keywords={
            "m": {"milk": [["Milk", 10], ["Milk chocolate", 3]], "meal": [["Meal", 5]]},
            "c": {"choc": [["Chocolate", 9], ["Milk chocolate", 8]]},
        },
    )


def test_local_candidates_use_word_start_and_alphabetical_groups() -> None:
    values = [
        PB.ListItem(name="Whole Milk"),
        PB.ListItem(name="Milk chocolate"),
        PB.ListItem(name="Buttermilk"),
        PB.ListItem(name="Other", details="milk"),
    ]
    found = AutocompleteEngine._local_candidates(values, "mil", language="en")
    assert [x.name for x in found] == ["Milk chocolate", "Whole Milk"]


def test_german_local_search_falls_back_to_non_boundary_match() -> None:
    values = [PB.ListItem(name="Buttermilch"), PB.ListItem(name="Milch")]
    found = AutocompleteEngine._local_candidates(values, "mil", language="de")
    assert [x.name for x in found] == ["Milch", "Buttermilch"]


@pytest.mark.asyncio
async def test_suggestions_match_source_precedence_and_generic_minimum_length() -> None:
    engine = AutocompleteEngine(DataManager(_data()))
    current = [PB.ListItem(identifier="c", name="Milk")]
    favorite = [PB.ListItem(identifier="f", name="Milk chocolate")]
    recent = [PB.ListItem(identifier="r", name="Milk")]
    suggestions = await engine.suggestions(
        "mil", current_items=current, favorites=favorite, recents=recent
    )
    assert [(x.text, x.source) for x in suggestions] == [
        ("mil", "add"),
        ("Milk", "current-list"),
        ("Milk chocolate", "favorite"),
    ]
    one = await engine.suggestions("m")
    assert [(x.text, x.source) for x in one] == [("m", "add")]


def test_generic_candidates_intersect_multiple_query_tokens() -> None:
    found = AutocompleteEngine._generic_candidates(_data(), "milk choc", contains_mode=1)
    assert found == ["Milk chocolate"]
