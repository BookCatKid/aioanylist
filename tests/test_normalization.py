from anylist_sdk.normalization import (
    canonical_category_match_id,
    contains_word_or_phrase,
    normalized_for_search,
    normalized_recipe_source_name,
    remove_diacritics,
)
from anylist_sdk.stemming import english_stem


def test_search_folding_and_equivalence() -> None:
    assert remove_diacritics("Crème Brûlée") == "Creme Brulee"
    assert normalized_for_search("CAFÉ") == "cafe"
    assert contains_word_or_phrase("salt and pepper", "salt & pepper")
    assert contains_word_or_phrase("strasse", "straße")


def test_category_and_source_normalization() -> None:
    assert canonical_category_match_id("Bread & Bakery!") == "bread-and-bakery"
    assert normalized_recipe_source_name("Bon Appétit & More") == "bon appetit and more"


def test_official_embedded_english_stemmer_basics() -> None:
    assert english_stem("skies") == "sky"
    assert english_stem("running") == "run"
    assert english_stem("news") == "news"
