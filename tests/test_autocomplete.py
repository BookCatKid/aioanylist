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


def test_generic_candidates_preserve_official_raw_token_position() -> None:
    # TY increments its token counter for the leading empty split token, so the first real
    # token is an intersection against the initially-empty candidate array.
    assert AutocompleteEngine._generic_candidates(_data(), " milk", contains_mode=1) == []


@pytest.mark.asyncio
async def test_add_row_does_not_dedupe_equal_item_row() -> None:
    engine = AutocompleteEngine(DataManager(_data()))
    suggestions = await engine.suggestions(
        "Milk", current_items=[PB.ListItem(identifier="current", name="Milk")], include_generic=False
    )
    assert [(x.text, x.source) for x in suggestions] == [
        ("Milk", "add"),
        ("Milk", "current-list"),
    ]


@pytest.mark.asyncio
async def test_dedup_uses_full_list_item_equality_not_name_only() -> None:
    engine = AutocompleteEngine(DataManager(_data()))
    current = PB.ListItem(identifier="current", name="Milk", details="2%")
    favorite = PB.ListItem(identifier="favorite", name="Milk", details="Whole")
    recent_equal_to_current = PB.ListItem(identifier="recent", name="Milk", details="2%")

    suggestions = await engine.suggestions(
        "mil",
        current_items=[current],
        favorites=[favorite],
        recents=[recent_equal_to_current],
        include_generic=False,
    )

    assert [(x.text, x.source, getattr(x.payload, "details", "")) for x in suggestions] == [
        ("mil", "add", ""),
        ("Milk", "current-list", "2%"),
        ("Milk", "favorite", "Whole"),
    ]


@pytest.mark.asyncio
async def test_generic_bare_item_only_dedupes_equal_bare_local_item() -> None:
    engine = AutocompleteEngine(DataManager(_data()))
    detailed = PB.ListItem(identifier="current", name="Milk", details="2%")
    suggestions = await engine.suggestions("mil", current_items=[detailed])

    # The generic "Milk" ListItem is bare and therefore not equal to the detailed local
    # item, so both survive the official autocomplete set.
    assert [(x.text, x.source) for x in suggestions[:3]] == [
        ("mil", "add"),
        ("Milk", "current-list"),
        ("Milk", "generic"),
    ]
