from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import urlparse

from .types import MatchRange

_APOSTROPHES = re.compile(r"['‘’‚‛]")
_QUOTES = re.compile(r'["“”„‟〝〞]')
_DASHES = re.compile(r"[\-֊־᐀᠆‐‑‒–—―⸗⸚⸺⸻〜〰゠︱︲﹘﹣－]")
_WHITESPACE_RUN = re.compile(
    r"[\t \u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]{2,}", re.I
)
_BOUNDARY = re.compile(r"[\W_]", re.UNICODE)

# Characters for which NFKD does not provide the same useful ASCII-like folding as AnyList's
# explicit table in app.js.
_EXTRA_DIACRITIC_FOLD = str.maketrans(
    {
        "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe", "Ø": "O", "ø": "o",
        "Đ": "D", "đ": "d", "Ł": "L", "ł": "l", "Þ": "TH", "þ": "th",
        "Ð": "D", "ð": "d", "ß": "s", "ẞ": "S", "Ƶ": "Z", "ƶ": "z",
        "ı": "i", "ſ": "l",
    }
)


def remove_diacritics(text: str) -> str:
    """AnyList-style search folding, including ligatures beyond plain NFD stripping."""
    text = text.translate(_EXTRA_DIACRITIC_FOLD)
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalized_for_search(
    text: str | None,
    *,
    normalize_apostrophes: bool = False,
    normalize_quotes: bool = False,
    normalize_dashes: bool = False,
) -> str:
    if text is None:
        return ""
    value = remove_diacritics(text).lower()
    if normalize_apostrophes:
        value = _APOSTROPHES.sub("'", value)
    if normalize_quotes:
        value = _QUOTES.sub('"', value)
    if normalize_dashes:
        value = _DASHES.sub("-", value)
    return value


def collapse_whitespace(text: str) -> str:
    return _WHITESPACE_RUN.sub(" ", text).strip()


def split_into_words(text: str) -> list[str]:
    value = collapse_whitespace(text)
    return value.split() if value else []


def trim_whitespace_and_punctuation(text: str) -> str:
    chars = r"\s,\-•–—֊־᐀᠆‐‑‒–—―⸗⸚⸺⸻〜〰゠︱︲﹘﹣－"
    return re.sub(rf"^[{chars}]+|[{chars}]+$", "", text, flags=re.I)


def _boundary(text: str, index: int, *, before: bool) -> bool:
    if before:
        return index == 0 or bool(_BOUNDARY.match(text[index - 1]))
    end = index
    return end == len(text) - 1 or bool(_BOUNDARY.match(text[end + 1]))


def _equivalent_consumption(text: str, ti: int, pattern: str, pi: int, *, language: str) -> tuple[int, int]:
    if text[ti] == pattern[pi]:
        return 1, 1
    # Official English search makes '&' equivalent to 'and', plus its partial stop-word forms
    # used during incremental search.
    if pattern[pi] == "&" and text.startswith("and", ti):
        return 3, 1
    if text[ti] == "&":
        for token in ("and", "an", "a"):
            if pattern.startswith(token, pi):
                return 1, len(token)
    # German search treats ß and ss as equivalent.
    if pattern[pi] == "ß" and text.startswith("ss", ti):
        return 2, 1
    if text[ti] == "ß" and pattern.startswith("ss", pi):
        return 1, 2
    return 0, 0


def range_of_word_or_phrase(
    text: str,
    pattern: str,
    *,
    require_start_boundary: bool = False,
    require_end_boundary: bool = False,
    language: str = "en",
    expand_to_word_boundaries: bool = False,
) -> MatchRange | None:
    if not text or not pattern:
        return None
    for start in range(len(text)):
        ti, pi = start, 0
        while ti < len(text) and pi < len(pattern):
            tc, pc = _equivalent_consumption(text, ti, pattern, pi, language=language)
            if tc == 0 and pc == 0:
                break
            ti += tc
            pi += pc
        if pi != len(pattern):
            continue
        end = ti - 1
        if require_start_boundary and not _boundary(text, start, before=True):
            continue
        if require_end_boundary and not _boundary(text, end, before=False):
            continue
        if expand_to_word_boundaries:
            left, right = start, end
            while left > 0 and not _boundary(text, left, before=True):
                left -= 1
            while right < len(text) - 1 and not _boundary(text, right, before=False):
                right += 1
            return MatchRange(left, right - left + 1)
        return MatchRange(start, ti - start)
    return None


def contains_word_or_phrase(
    text: str,
    pattern: str,
    *,
    require_start_boundary: bool = False,
    require_end_boundary: bool = False,
    language: str = "en",
) -> bool:
    return (
        range_of_word_or_phrase(
            text,
            pattern,
            require_start_boundary=require_start_boundary,
            require_end_boundary=require_end_boundary,
            language=language,
        )
        is not None
    )


def ranges_for_search_terms(
    text: str,
    terms: list[str],
    *,
    require_start_boundary: bool = False,
    require_end_boundary: bool = False,
    language: str = "en",
    expand_to_word_boundaries: bool = False,
) -> list[MatchRange]:
    out: list[MatchRange] = []
    for term in terms:
        match = range_of_word_or_phrase(
            text,
            term,
            require_start_boundary=require_start_boundary,
            require_end_boundary=require_end_boundary,
            language=language,
            expand_to_word_boundaries=expand_to_word_boundaries,
        )
        if match is None:
            return []
        out.append(match)
    return out


def canonical_category_match_id(name: str) -> str:
    value = normalized_for_search(name, normalize_apostrophes=True, normalize_dashes=True).strip()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s-]+", "-", value).strip("-")
    return value


def normalized_recipe_source_name(source: str) -> str:
    value = normalized_for_search(source, normalize_apostrophes=True)
    value = value.replace("&", "and")
    return collapse_whitespace(value)


def recipe_source_domain(source_url: str) -> str:
    try:
        host = (urlparse(source_url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


@lru_cache(maxsize=8192)
def localized_sort_key(text: str) -> tuple[object, ...]:
    """Locale-independent approximation of Intl.Collator(numeric=True, sensitivity='base')."""
    folded = normalized_for_search(text)
    parts = re.split(r"(\d+)", folded)
    return tuple(int(part) if part.isdigit() else part for part in parts)
