from __future__ import annotations

from collections import Counter
from functools import lru_cache
from typing import Any

from .normalization import contains_word_or_phrase, remove_diacritics
from .stemming import english_stem
from .tag_data import TagData, TagDataManager

_SYSTEM_ROOT_CATEGORIES = {
    "baby", "bakery", "beverages", "breakfast-and-cereal",
    "condiments-oils-and-salad-dressings", "cooking-and-baking", "dairy", "deli",
    "frozen-foods", "grains-pasta-and-side-dishes", "health-and-personal-care",
    "household-and-cleaning", "meat", "pet-supplies", "produce", "seafood",
    "snacks-cookies-and-candy", "soups-and-canned-goods", "wine-beer-spirits", "other",
}


def _tokenize(text: str) -> list[str]:
    folded = remove_diacritics(text.lower())
    return [part for part in __import__("re").split(r"[ ,():/\-]", folded) if part.strip()]


def _keyword_condition_matches(stemmed_text: str, keyword_expr: str) -> bool:
    return any(
        contains_word_or_phrase(
            stemmed_text,
            keyword,
            require_start_boundary=True,
            require_end_boundary=True,
        )
        for keyword in keyword_expr.split("|")
    )


class Categorizer:
    def __init__(self, data: TagDataManager) -> None:
        self.data = data
        self._cache: dict[tuple[str, str], str | None] = {}

    @staticmethod
    def _implies(data: TagData, tag: str, possible_parent: str) -> bool:
        return (
            possible_parent in data.implied_tags.get(tag, ())
            or data.tags.get(tag, {}).get("rootCategory") == possible_parent
        )

    @staticmethod
    def _parents(data: TagData, tag: str) -> set[str]:
        out = set(data.implied_tags.get(tag, ()))
        root = data.tags.get(tag, {}).get("rootCategory")
        if root:
            out.add(root)
        return out

    @staticmethod
    def classify_with(data: TagData, text: str) -> str | None:
        if not text:
            return None
        if data.language != "en":
            normalized = remove_diacritics(text.lower()).replace("-", " ").strip()
            return data.normalized_display_names_index.get(normalized)

        original_tokens = _tokenize(text)
        stems = [english_stem(token) for token in original_tokens]
        stemmed_text = " ".join(stems)
        unique_stems = list(dict.fromkeys(stems))
        if not unique_stems:
            return None

        votes: Counter[str] = Counter()
        for stem in unique_stems:
            votes.update(data.tag_keywords_index.get(stem, ()))
        if not votes:
            return None
        candidates = list(votes)

        # Each tag can require (+1) or forbid (-1) keyword expressions.
        for tag in tuple(candidates):
            rules = data.tags.get(tag, {}).get("keywords", {}) or {}
            for expr, required in rules.items():
                if not required:
                    continue
                present = _keyword_condition_matches(stemmed_text, expr)
                if (required == 1 and not present) or (required == -1 and present):
                    candidates.remove(tag)
                    break
        if len(candidates) == 1:
            return candidates[0]

        lowered = text.lower()
        exact = next((tag for tag in candidates if tag.replace("-", " ") == lowered), None)
        if exact:
            return exact

        if candidates:
            max_vote = max(votes[tag] for tag in candidates)
            candidates = [tag for tag in candidates if votes[tag] == max_vote]

        if "seeds" in candidates and "seeded" in original_tokens:
            candidates.remove("seeds")
        if len(candidates) == 1:
            return candidates[0]

        # Prefer a single specific non-produce root where the tag is not merely under spices/herbs.
        specific = []
        for tag in candidates:
            root = data.tags.get(tag, {}).get("rootCategory")
            if root and root != "produce" and not Categorizer._implies(data, tag, "spices-and-herbs"):
                specific.append(tag)
        if len(specific) == 1:
            return specific[0]
        if specific:
            candidates = specific

        # Remove less-specific candidates if another candidate implies them.
        remaining = list(candidates)
        for child in candidates:
            for parent in candidates:
                if child == parent:
                    continue
                if Categorizer._implies(data, child, parent) and parent in remaining:
                    remaining.remove(parent)
        candidates = remaining
        if len(candidates) == 1:
            return candidates[0]

        common: set[str] | None = None
        for tag in candidates:
            parents = Categorizer._parents(data, tag)
            common = parents if common is None else common.intersection(parents)
            if not common:
                break
        if common:
            common.difference_update(_SYSTEM_ROOT_CATEGORIES)
            common.difference_update({"fruit", "vegetables", "fresh-fruit", "fresh-vegetables"})
            if len(common) == 1:
                return next(iter(common))
        return None

    async def classify(self, text: str) -> str | None:
        active, english = await self.data.active_and_english()
        key = (active.language, text)
        if key in self._cache:
            return self._cache[key]
        if active.language == "en":
            result = self.classify_with(active, text)
        else:
            normalized = remove_diacritics(text.lower()).replace("-", " ").strip()
            result = active.normalized_display_names_index.get(normalized)
            if result is None:
                result = english.normalized_display_names_index.get(normalized)
        self._cache[key] = result
        return result
