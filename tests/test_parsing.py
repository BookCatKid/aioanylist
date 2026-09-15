from __future__ import annotations

import math

import pytest

from aioanylist.parsing.ingredient import parse_ingredient_line, parse_recipe_steps
from aioanylist.parsing.quantity import (
    amount_as_float,
    normalize_unit,
    parse_leading_amount,
    parse_quantity_and_package_size,
    scale_quantity_text,
)


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("2¼", 2.25),
        ("2-1/2", 2.5),
        ("⅞", 0.875),
        ("١ ١/٢", 1.5),
        ("१ १/२", 1.5),
    ],
)
def test_official_numeric_forms(raw: str, value: float) -> None:
    assert math.isclose(amount_as_float(raw), value)


def test_mixed_fraction_is_not_range() -> None:
    parsed, rest = parse_leading_amount("2-1/2 lb.")
    assert parsed is not None
    assert parsed.is_range is False
    assert parsed.value == 2.5
    assert rest.strip() == "lb."


def test_true_range() -> None:
    parsed, rest = parse_leading_amount("1/2 to 3/4 cups")
    assert parsed is not None and parsed.is_range
    assert parsed.value == 0.5
    assert parsed.range_end == 0.75
    assert rest.strip() == "cups"


@pytest.mark.parametrize(
    "raw",
    [
        "2 cans (28 Ounce)",
        "1 pkg. (12 oz)",
        "50 g (1.8oz)",
        "12-ounce bottle",
        "4 6-inch sprigs",
        "60 3-inch pieces",
    ],
)
def test_quantity_package_corpus_does_not_crash(raw: str) -> None:
    result = parse_quantity_and_package_size(raw)
    assert result is not None


def test_ingredient_heading_note_and_raw_preservation() -> None:
    heading = parse_ingredient_line("# Sauce")
    assert heading.isHeading and heading.name == "Sauce"
    ingredient = parse_ingredient_line("1 1/2 cups onions, finely chopped")
    assert ingredient.rawIngredient == "1 1/2 cups onions, finely chopped"
    assert ingredient.name == "onions"
    assert ingredient.note.lower() == "finely chopped"


def test_numbered_recipe_steps_absorb_continuations() -> None:
    steps = parse_recipe_steps("1. Heat pan\nKeep it hot\n2. Add onions")
    assert steps == ["Heat pan\n\nKeep it hot", "Add onions"]


def test_recipe_step_numbering_matches_official_dot_only_prefix() -> None:
    # The web regex is /^\d+[.]?\s*/: both the period and whitespace are optional, so
    # "2)" matches only its leading digit and leaves the ')' in the resulting step text.
    assert parse_recipe_steps("1. Heat pan\n2) Add onions") == ["Heat pan", ") Add onions"]


def test_ingredient_split_matches_repeated_unit_and_trailing_adjective_rules() -> None:
    ingredient = parse_ingredient_line("1/2 small head cabbage")
    assert ingredient.quantity == "1/2 small head"
    assert ingredient.name == "cabbage"

    ingredient = parse_ingredient_line("1 large tomato")
    assert ingredient.quantity == "1"
    assert ingredient.name == "large tomato"

    ingredient = parse_ingredient_line("12 ounces jars tomatoes")
    assert ingredient.quantity == "12 ounces jars"
    assert ingredient.name == "tomatoes"


def test_ingredient_raw_line_and_heading_shape_match_official_paste_parser() -> None:
    ingredient = parse_ingredient_line("  1 cup onions  ")
    assert ingredient.rawIngredient == "  1 cup onions  "
    heading = parse_ingredient_line("# Sauce ")
    assert heading.isHeading is True
    assert heading.name == "Sauce "
    assert not heading.HasField("rawIngredient")


@pytest.mark.parametrize(
    ("raw", "value"),
    [("2 - 3", 2.0), ("1/2 - 3/4", 0.5), ("1.25 cups", 1.25)],
)
def test_amount_as_float_matches_parsefloat_prefix_semantics(raw: str, value: float) -> None:
    assert math.isclose(amount_as_float(raw), value)


def test_quantity_raw_text_excludes_parsed_package_size() -> None:
    parsed = parse_quantity_and_package_size("2 cans (28 Ounce)")
    assert parsed is not None
    assert parsed.quantityPb.amount == "2"
    assert parsed.quantityPb.unit.lower() == "cans"
    assert parsed.quantityPb.rawQuantity.lower() == "2 cans"
    assert parsed.packageSizePb.rawPackageSize.lower() == "28 ounce"

    parsed = parse_quantity_and_package_size("1 12-ounce bottle")
    assert parsed is not None
    assert parsed.quantityPb.rawQuantity == "1"
    assert parsed.packageSizePb.rawPackageSize.lower() == "12-ounce bottle"


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("cups", "cup"),
        ("jars", "jar"),
        ("tasse", "Tasse"),
        ("becher", "becher"),
        ("pfund", "Pfund"),
        ("Dosen", "can"),
        ("gläser", "glas"),
        ("oz t", "troy oz"),
        ("T", "Tbsp"),
        ("liter", "L"),
    ],
)
def test_normalized_unit_matches_official_rp_ap_tables(unit: str, expected: str) -> None:
    assert normalize_unit(unit) == expected


def test_scale_quantity_text_preserves_surrounding_text() -> None:
    scaled = scale_quantity_text("1 1/2 cups chopped onions", 2)
    assert scaled.startswith("3")
    assert "cups chopped onions" in scaled


_OFFICIAL_QUANTITY_CORPUS = [
    "1 pound",
    "3/4 pound",
    "2¼ pounds",
    "2-1/2 lb.",
    "1 lb",
    "2lbs",
    "2lbs.",
    "0.5lbs",
    "¼lb",
    "2 - 3lb",
    "2  lbs",
    "1 lb.",
    "1/2 cup",
    "3/4 cup",
    "1 / 2 cup",
    "1/2 to 3 / 4 cups",
    "⅖ to ⅞ cups",
    "1 to 1 ½ cup",
    "1/2-3/4 cups",
    ".35-.45 cups",
    "1/2 cup",
    "¼ – ½ cup",
    "1/4 cup (1/2 Stick Or 4 Tablespoons)",
    "1 (6-oz can)",
    "2 (6 ounce) cans",
    "1 (28 ounce) can",
    "1 – 28 ounce can",
    "1 can (28 Ounce)",
    "2 cans (28 Ounce)",
    "1 can",
    "2 cans",
    "2 to 3 cans",
    "16 ounces",
    "½ oz",
    "¹/₉ oz",
    "¹⁄₂ oz",
    "2 tablespoons",
    "1 tablespoon",
    "4 tablespoons",
    "1 tablespoon",
    "3 tbsps",
    "3 - 4 tablespoons",
    "2 Tbsp.",
    "5 Tbs.",
    "1-1/2 Tbs.",
    "2 tablespoons",
    "1 Tbl",
    "1/2 teaspoon",
    "1 1/2 teaspoons",
    "1/2 tsp",
    "1 teaspoon",
    "2-4 teaspoons",
    "12-ounce bottle",
    "12-oz bottle",
    "12",
    "1",
    "2 or 3",
    "10–12",
    "1 bunch",
    "1 large",
    "6-7 large",
    "3 parts",
    "2 cloves",
    "3 dashes",
    "3 to 4 medium",
    "3 medium",
    "1 box",
    "1 pkg. (12 oz)",
    "50 g (1.8oz)",
    "3-5 Drops",
    ".35 ounces (10 grams, about 2 teaspoons)",
    "1 stick (1/2 cup)",
    "1 head",
    "1/2 small head",
    "4 bars",
    "2 medium ears",
    "1 12-ounce bottle",
    "2 12 ounce jars",
    "1 5–6-pound",
    "1 1.5 L",
    "1 can small",
    "6 inch sprig",
    "4 6-inch sprigs",
    "1 Small pkg",
    "1 (16 oz) tub",
    "3 in piece",
    "60 3-inch pieces",
]


@pytest.mark.parametrize("raw", _OFFICIAL_QUANTITY_CORPUS)
def test_entire_official_quantity_parser_corpus_is_recognized(raw: str) -> None:
    # app.js exposes this exact corpus from its PBItemQuantity parser diagnostics.  Every
    # non-empty entry is expected to produce a quantity/package parse.
    assert parse_quantity_and_package_size(raw) is not None
