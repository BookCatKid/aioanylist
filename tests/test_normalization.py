from anylist_sdk.normalization import (
    canonical_category_match_id,
    contains_word_or_phrase,
    normalized_for_search,
    normalized_recipe_source_name,
    range_of_word_or_phrase,
    remove_diacritics,
)
from anylist_sdk.stemming import english_stem
from anylist_sdk.types import MatchRange


def test_search_folding_and_equivalence() -> None:
    assert remove_diacritics("Milk 123") == "Milk 123"
    assert remove_diacritics("Crème Brûlée") == "Creme Brulee"
    assert normalized_for_search("CAFÉ") == "cafe"
    assert contains_word_or_phrase("salt and pepper", "salt & pepper")
    assert contains_word_or_phrase("strasse", "straße")


def test_plain_search_fast_path_keeps_boundary_and_expansion_semantics() -> None:
    assert range_of_word_or_phrase(
        "whole milk powder",
        "milk",
        require_start_boundary=True,
        require_end_boundary=True,
    ) == MatchRange(6, 4)
    assert range_of_word_or_phrase("milkshake", "milk", require_end_boundary=True) is None
    assert range_of_word_or_phrase(
        "extra creamy milk powder",
        "creamy",
        expand_to_word_boundaries=True,
    ) == MatchRange(6, 6)


def test_category_and_source_normalization() -> None:
    assert canonical_category_match_id("Bread & Bakery!") == "bread-and-bakery"
    assert normalized_recipe_source_name("Bon Appétit & More") == "bon appetit and more"


def test_official_embedded_english_stemmer_basics() -> None:
    assert english_stem("skies") == "sky"
    assert english_stem("running") == "run"
    assert english_stem("news") == "news"
