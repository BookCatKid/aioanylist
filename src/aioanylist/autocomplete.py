from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from google.protobuf.message import Message

from .item_semantics import items_equal
from .normalization import (
    localized_sort_key,
    normalized_for_search,
    ranges_for_search_terms,
    remove_diacritics,
)
from .tag_data import TagDataManager
from .types import AutocompleteSuggestion


def _name(value: Message | str) -> str:
    return value if isinstance(value, str) else str(getattr(value, "name", ""))


class AutocompleteEngine:
    def __init__(self, tag_data: TagDataManager) -> None:
        self.tag_data = tag_data

    @staticmethod
    def _generic_candidates(data: Any, query: str, *, contains_mode: int = 1) -> list[str]:
        # Port of tag_data autocompleteKeywords lookup. Each leaf is keyword -> [[text, weight], ...].
        normalized = remove_diacritics(query.lower())
        # TY() deliberately keeps the raw split position in its `u` counter even when a
        # token is empty.  This is observable for a leading separator: the first real token
        # is treated as an intersection token against an empty result, yielding no generic
        # suggestions.  Do not collapse empty tokens here.
        tokens = re.split(r"[ ,():/\-]", normalized)
        candidates: list[str] = []
        for token_index, token in enumerate(tokens):
            if not token.strip():
                continue
            groups = []
            if contains_mode == 1 and token:
                group = data.autocomplete_keywords.get(token[0])
                if group is not None:
                    groups = [group]
            else:
                groups = list(data.autocomplete_keywords.values())
            found: list[list[Any]] = []
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for keyword, rows in group.items():
                    match = (
                        keyword.startswith(token)
                        if contains_mode == 1
                        else keyword.endswith(token)
                        if contains_mode == 2
                        else token in keyword
                    )
                    if match:
                        found.extend(rows)
            found.sort(
                key=lambda row: (
                    0
                    if token_index == 0 and str(row[0]).lower().startswith(token) and row[1] > 0
                    else 1,
                    -float(row[1]),
                    localized_sort_key(str(row[0]).lower()),
                )
            )
            names = [str(row[0]) for row in found]
            if token_index == 0:
                candidates = list(dict.fromkeys(names))
            else:
                allowed = set(names)
                candidates = [name for name in candidates if name in allowed]
        return candidates

    @staticmethod
    def _is_loose_boundary_language(language: str, query: str) -> bool:
        # AnyList disables the recommended start-of-word boundary for German and CJK text.
        return language == "de" or bool(re.search(r"[\u4e00-\u9fff]", query))

    @staticmethod
    def _local_candidates(
        values: Iterable[Message | str], query: str, *, language: str
    ) -> list[Message | str]:
        query_normalized = normalized_for_search(
            query, normalize_apostrophes=True, normalize_quotes=True, normalize_dashes=True
        )
        query_normalized = re.sub(r"\s+", " ", query_normalized).strip()
        terms = query_normalized.split()
        if not terms:
            return []
        loose = AutocompleteEngine._is_loose_boundary_language(language, query)
        name_strict: list[Message | str] = []
        name_loose: list[Message | str] = []
        details_strict: list[Message | str] = []
        details_loose: list[Message | str] = []
        for value in values:
            text = _name(value)
            if not text:
                continue
            candidate = normalized_for_search(
                text, normalize_apostrophes=True, normalize_quotes=True, normalize_dashes=True
            )
            matches = ranges_for_search_terms(
                candidate, terms, require_start_boundary=True, language=language
            )
            if matches:
                name_strict.append(value)
                continue
            if loose:
                matches = ranges_for_search_terms(candidate, terms, language=language)
                if matches:
                    name_loose.append(value)
                    continue
            if not isinstance(value, Message):
                continue
            details = str(getattr(value, "details", "") or "")
            if not details:
                continue
            detail_text = normalized_for_search(
                details, normalize_apostrophes=True, normalize_quotes=True, normalize_dashes=True
            )
            detail_matches = ranges_for_search_terms(
                detail_text,
                terms,
                require_start_boundary=True,
                require_end_boundary=not loose,
                language=language,
            )
            if detail_matches:
                details_strict.append(value)
                continue
            if loose and ranges_for_search_terms(detail_text, terms, language=language):
                details_loose.append(value)

        key = lambda value: localized_sort_key(_name(value))
        for group in (name_strict, name_loose, details_strict, details_loose):
            group.sort(key=key)
        return name_strict + name_loose + details_strict + details_loose

    async def suggestions(
        self,
        query: str,
        *,
        current_items: Iterable[Message | str] = (),
        favorites: Iterable[Message | str] = (),
        recents: Iterable[Message | str] = (),
        include_favorites: bool = True,
        include_recents: bool = True,
        include_generic: bool = True,
        limit: int = 50,
    ) -> list[AutocompleteSuggestion]:
        active = await self.tag_data.get()
        language = active.language
        result = [AutocompleteSuggestion(query, "add", payload=None)]
        # AnyList's duplicate set contains only rows already appended *after* the Add row.
        # A current/favorite/recent/generic item equal to the typed text may therefore appear
        # directly below Add.  AutocompleteItem equality delegates to full ListItem equality,
        # not name equality, so same-name rows with different quantity/details/etc. coexist.
        seen_items: list[Message] = []

        def as_item(value: Message | str) -> Message:
            if isinstance(value, Message):
                return value
            from .proto import PB

            return PB.ListItem(name=value)

        def already_seen(value: Message | str) -> bool:
            item = as_item(value)
            if any(items_equal(existing, item) for existing in seen_items):
                return True
            seen_items.append(item)
            return False

        def merge(values: Iterable[Message | str], source: str) -> None:
            for value in self._local_candidates(values, query, language=language):
                text = _name(value)
                if not text or already_seen(value):
                    continue
                result.append(AutocompleteSuggestion(text, source, payload=value))

        merge(current_items, "current-list")
        if include_favorites:
            merge(favorites, "favorite")
        if include_recents:
            merge(recents, "recent")
        # AnyList does not query its generic grocery dictionary for a one-character input.
        if include_generic and len(query) > 1:
            generic = self._generic_candidates(active, query, contains_mode=1)
            if language == "de":
                generic.extend(self._generic_candidates(active, query, contains_mode=0))
            for text in generic:
                if not already_seen(text):
                    result.append(AutocompleteSuggestion(text, "generic"))
        return result[:limit]
