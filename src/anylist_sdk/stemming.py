from __future__ import annotations

from functools import lru_cache

_VOWELS = set("aeiouy")
_EXCEPTION1 = {
    "skis": "ski",
    "skies": "sky",
    "dying": "die",
    "lying": "lie",
    "tying": "tie",
    "idly": "idl",
    "gently": "gentl",
    "ugly": "ugli",
    "early": "earli",
    "only": "onli",
    "singly": "singl",
    "sky": "sky",
    "news": "news",
    "howe": "howe",
    "atlas": "atlas",
    "cosmos": "cosmos",
    "bias": "bias",
    "andes": "andes",
}
_EXCEPTION2 = {"inning", "outing", "canning", "herring", "earring", "proceed", "exceed", "succeed"}
_R1_SPECIAL = ("gener", "commun", "arsen")
_STEP2 = {
    "ization": "ize",
    "ational": "ate",
    "fulness": "ful",
    "ousness": "ous",
    "iveness": "ive",
    "tional": "tion",
    "biliti": "ble",
    "lessli": "less",
    "entli": "ent",
    "ation": "ate",
    "alism": "al",
    "aliti": "al",
    "ousli": "ous",
    "iviti": "ive",
    "fulli": "ful",
    "enci": "ence",
    "anci": "ance",
    "abli": "able",
    "izer": "ize",
    "ator": "ate",
    "alli": "al",
    "bli": "ble",
}
_STEP2_SUFFIXES = tuple(sorted(_STEP2, key=len, reverse=True))
_STEP3 = {
    "ational": "ate",
    "tional": "tion",
    "alize": "al",
    "icate": "ic",
    "iciti": "ic",
    "ical": "ic",
    "ful": "",
    "ness": "",
}
_STEP3_SUFFIXES = tuple(sorted(_STEP3, key=len, reverse=True))


def _vowel(ch: str) -> bool:
    return ch.lower() in _VOWELS


def _mark_ys(word: str) -> str:
    if not word:
        return word
    chars = list(word)
    if chars[0] == "y":
        chars[0] = "Y"
    for i in range(1, len(chars)):
        if chars[i] == "y" and _vowel(chars[i - 1]):
            chars[i] = "Y"
    return "".join(chars)


def _regions(word: str) -> tuple[int, int]:
    r1 = len(word)
    for prefix in _R1_SPECIAL:
        if word.startswith(prefix):
            r1 = len(prefix)
            break
    else:
        for i in range(1, len(word)):
            if _vowel(word[i - 1]) and not _vowel(word[i]):
                r1 = i + 1
                break
    r2 = len(word)
    for i in range(r1 + 1, len(word)):
        if _vowel(word[i - 1]) and not _vowel(word[i]):
            r2 = i + 1
            break
    return r1, r2


def _contains_vowel(text: str) -> bool:
    return any(_vowel(ch) for ch in text)


def _short_syllable(word: str) -> bool:
    if len(word) >= 3:
        a, b, c = word[-3], word[-2], word[-1]
        return (not _vowel(a)) and _vowel(b) and (not _vowel(c)) and c.lower() not in "wxyY"
    if len(word) == 2:
        return _vowel(word[0]) and not _vowel(word[1])
    return False


def _short_word(word: str, r1: int) -> bool:
    return r1 >= len(word) and _short_syllable(word)


def _in_region(word: str, suffix: str, region: int) -> bool:
    return word.endswith(suffix) and len(word) - len(suffix) >= region


@lru_cache(maxsize=32768)
def english_stem(raw: str) -> str:
    """Port of the English Snowball stemmer embedded in the official AnyList web bundle."""
    word = raw.lower()
    if len(word) <= 2:
        return word
    if word in _EXCEPTION1:
        return _EXCEPTION1[word]

    word = word.removeprefix("'")
    word = _mark_ys(word)
    r1, r2 = _regions(word)

    # Step 0
    for suffix in ("'s'", "'s", "'"):
        if word.endswith(suffix):
            word = word[: -len(suffix)]
            break

    # Step 1a
    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith(("ied", "ies")):
        stem = word[:-3]
        word = stem + ("i" if len(stem) > 1 else "ie")
    elif word.endswith(("us", "ss")):
        pass
    elif word.endswith("s"):
        stem = word[:-1]
        # Delete if there is a vowel not immediately before the s (Snowball's backward scan).
        if len(stem) >= 2 and _contains_vowel(stem[:-1]):
            word = stem

    if word.lower() in _EXCEPTION2:
        return word.replace("Y", "y")

    # Step 1b
    changed_1b = False
    if _in_region(word, "eedly", r1):
        word = word[:-3]
    elif _in_region(word, "eed", r1):
        word = word[:-1]
    else:
        for suffix in ("ingly", "edly", "ing", "ed"):
            if word.endswith(suffix):
                stem = word[: -len(suffix)]
                if _contains_vowel(stem):
                    word = stem
                    changed_1b = True
                break
        if changed_1b:
            if word.endswith(("at", "bl", "iz")):
                word += "e"
            elif word.endswith(("bb", "dd", "ff", "gg", "mm", "nn", "pp", "rr", "tt")):
                word = word[:-1]
            elif _short_word(word, r1):
                word += "e"

    # Step 1c
    if len(word) > 2 and word[-1] in "yY" and not _vowel(word[-2]):
        word = word[:-1] + "i"

    # Step 2: longest suffix first.
    applied = False
    for suffix in _STEP2_SUFFIXES:
        if _in_region(word, suffix, r1):
            word = word[: -len(suffix)] + _STEP2[suffix]
            applied = True
            break
    if not applied and _in_region(word, "ogi", r1):
        base = word[:-3]
        if base.endswith("l"):
            word = base + "og"
    elif not applied and _in_region(word, "li", r1):
        base = word[:-2]
        if base and base[-1] in "cdeghkmnrt":
            word = base

    # Step 3
    applied = False
    for suffix in _STEP3_SUFFIXES:
        if _in_region(word, suffix, r1):
            word = word[: -len(suffix)] + _STEP3[suffix]
            applied = True
            break
    if not applied and _in_region(word, "ative", r2):
        word = word[:-5]

    # Step 4
    for suffix in (
        "ement",
        "ment",
        "ance",
        "ence",
        "able",
        "ible",
        "ate",
        "ive",
        "ize",
        "iti",
        "al",
        "ism",
        "er",
        "ous",
        "ant",
        "ent",
        "ic",
    ):
        if _in_region(word, suffix, r2):
            word = word[: -len(suffix)]
            break
    else:
        if _in_region(word, "ion", r2):
            base = word[:-3]
            if base.endswith(("s", "t")):
                word = base

    # Step 5
    if word.endswith("e"):
        base = word[:-1]
        epos = len(base)
        if epos >= r2 or (epos >= r1 and not _short_syllable(base)):
            word = base
    elif word.endswith("ll") and len(word) - 1 >= r2:
        word = word[:-1]

    return word.replace("Y", "y")


def stem_words(words: list[str] | tuple[str, ...]) -> list[str]:
    """Stem each non-empty word with the same English stemmer used by AnyList Web."""
    return [english_stem(word) for word in words if word != ""]
