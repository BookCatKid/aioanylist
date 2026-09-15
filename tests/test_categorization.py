from __future__ import annotations

import pytest

from aioanylist.categorization import Categorizer
from aioanylist.proto import PB
from aioanylist.tag_data import TagData


def data(*, language="en") -> TagData:
    return TagData(
        language=language,
        tags={
            "apples": {"rootCategory": "produce", "keywords": {}},
            "green-apples": {"rootCategory": "produce", "keywords": {"green": 1}},
            "seeds": {"rootCategory": "produce", "keywords": {}},
            "spices-and-herbs": {"rootCategory": "cooking-and-baking", "keywords": {}},
            "basil": {"rootCategory": "produce", "keywords": {}},
        },
        normalized_display_names_index={"apfel": "apples"},
        implied_tags={
            "green-apples": ["apples", "fruit"],
            "basil": ["spices-and-herbs"],
        },
        tag_keywords_index={
            "appl": ["apples", "green-apples"],
            "green": ["green-apples"],
            "seed": ["seeds"],
            "basil": ["basil", "spices-and-herbs"],
        },
        autocomplete_keywords={},
    )


def test_english_classifier_required_keyword_and_specificity() -> None:
    d = data()
    assert Categorizer.classify_with(d, "green apples") == "green-apples"
    assert Categorizer.classify_with(d, "apples") == "apples"


def test_seeded_special_case_drops_seeds_candidate() -> None:
    d = data()
    # Force a competing candidate so the special-case branch is meaningful.
    d.tag_keywords_index["seed"] = ["seeds", "apples"]
    assert Categorizer.classify_with(d, "seeded") != "seeds"


def test_implied_parent_is_removed_when_specific_child_survives() -> None:
    d = data()
    d.tag_keywords_index["green"] = ["green-apples", "apples"]
    assert Categorizer.classify_with(d, "green") == "green-apples"


class Manager:
    def __init__(self, active, english):
        self.active = active
        self.english = english

    async def active_and_english(self):
        return self.active, self.english


@pytest.mark.asyncio
async def test_non_english_classifier_uses_local_index_then_english_fallback() -> None:
    german = data(language="de")
    english = data(language="en")
    german.normalized_display_names_index["apfel"] = "apples"
    english.normalized_display_names_index["apple"] = "apples"
    categorizer = Categorizer(Manager(german, english))
    assert await categorizer.classify("birne") is None
    assert await categorizer.classify("apfel") == "apples"
    assert await categorizer.classify("apple") == "apples"


@pytest.mark.asyncio
async def test_ingredient_grocery_tag_uses_shared_classifier() -> None:
    english = data(language="en")
    categorizer = Categorizer(Manager(english, english))

    assert await categorizer.classify_ingredient(PB.PBIngredient(name="apples")) == "apples"
    assert await categorizer.classify_ingredient(PB.PBIngredient()) is None
