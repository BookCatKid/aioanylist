from __future__ import annotations

import re

from ..identifiers import uuid4_hex
from ..normalization import trim_whitespace_and_punctuation
from ..proto import PB
from .quantity import parse_leading_amount

_NOTE_RE = re.compile(
    r",\s*((?:(?:fine(?:ly)?|thin(?:ly)?|coarse(?:ly)?|roughly|lightly)\s+)?"
    r"(?:diced|sliced|chopped|minced|grated|shredded|pounded|beaten)"
    r"(?:\s+(?:fine(?:ly)?|thin(?:ly)?|coarse(?:ly)?|roughly|lightly))?(?:\s+into\s+.+)?|"
    r"(?:seeded|cut\s+(?:into|in)\s+.+|peeled|juiced|drained|rinsed|thawed|divided|melted|"
    r"mashed|cubed|quartered|halved|sifted|cored|packed|crushed|softened|blanched)"
    r"(?:\s+(?:and|&)\s+.+)?|to taste|room temperature|optional|as needed)$",
    re.I,
)

_PACKAGE_OR_UNIT = re.compile(
    r"^(?:cups?|c\.?|tassen?|tas\.?|becher|fluid ounces?|fl\.?\s*oz\.?|gallons?|gal\.?|"
    r"ounces?|oz\.?|pints?|pt\.?|pounds?|lbs?\.?|quarts?|qts?\.?|tablespoons?|tbsps?\.?|"
    r"teaspoons?|tsps?\.?|grams?|gr?\.?|kilograms?|kg\.?|milligrams?|mg\.?|liters?|l\.?|"
    r"deciliters?|dl\.?|milliliters?|ml\.?|packages?|pkg\.?|pecks?|bushels?|buckets?|"
    r"slices?|cloves?|loaf|loaves|pinches?|cans?|drops?|bunch(?:es)?|dashes?|cartons?|each|"
    r"pieces?|squares?|tubes?|strips?|stems?|stalks?|sprigs?|spears?|sprouts?|sheets?|scoops?|"
    r"pouches?|packets?|packs?|leaf|leaves|glasses?|cubes?|containers?|cones?|boxes?|bottles?|"
    r"blocks?|bags?|parts?|sticks?|heads?|bars?|ears?|jars?|inches?|tubs?|small|medium|large)\b\.?",
    re.I,
)
_NUMBERED_STEP = re.compile(r"^\d+[.]?\s*", re.I)
_STEP_TRIM = re.compile(r"^[\s,\-•–—]+|[\s,\-•–—]+$")


def split_quantity_prefix(line: str) -> tuple[str, str]:
    cleaned = line.strip(" \t,-•–—")
    amount, rest = parse_leading_amount(cleaned)
    if amount is None:
        return "", cleaned
    consumed = cleaned[: len(cleaned) - len(rest)]
    remainder = rest.lstrip()
    unit = _PACKAGE_OR_UNIT.match(remainder)
    if unit:
        # sP moves a trailing size adjective back to the ingredient-name side rather than
        # treating it as part of the quantity (e.g. "1/2 small head cabbage").
        if unit.group(0).rstrip(".").casefold() not in {"small", "medium", "large"}:
            consumed += rest[: len(rest) - len(remainder)] + unit.group(0)
            remainder = remainder[unit.end() :]
            # Include a directly attached parenthetical package-size expression.
            p = re.match(r"^\s*(\([^()]+\))", remainder)
            if p:
                consumed += remainder[: p.end()]
                remainder = remainder[p.end() :]
    return trim_whitespace_and_punctuation(consumed), trim_whitespace_and_punctuation(remainder)


def split_ingredient_note(name: str) -> tuple[str, str]:
    match = _NOTE_RE.search(name)
    if match is None:
        return name.strip(), ""
    return name[: match.start()].strip(), match.group(1).strip()


def parse_ingredient_line(line: str):
    if not line.strip():
        return None
    if line.startswith("# ") and len(line) > 2:
        ingredient = PB.PBIngredient(identifier=uuid4_hex())
        ingredient.isHeading = True
        ingredient.name = line[2:]
        return ingredient

    ingredient = PB.PBIngredient(identifier=uuid4_hex(), rawIngredient=line)
    quantity, remainder = split_quantity_prefix(line)
    name, note = split_ingredient_note(remainder)
    ingredient.name = name
    if quantity:
        ingredient.quantity = quantity
    if note:
        ingredient.note = note
    return ingredient


def parse_ingredient_lines(text: str) -> list:
    result = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parsed = parse_ingredient_line(line)
        if parsed is not None:
            result.append(parsed)
    return result


def parse_recipe_steps(text: str) -> list[str]:
    """Parse pasted directions with AnyList's numbered-step continuation behavior."""
    steps: list[str] = []
    numbered_mode = False
    pending = ""
    for raw_line in text.split("\n"):
        # The source regex is not global: replace removes one matching edge (leading first
        # when both exist), rather than Python's ordinary strip-both-edges behavior.
        line = _STEP_TRIM.sub("", raw_line, count=1)
        if not line:
            continue
        match = _NUMBERED_STEP.match(line)
        if match:
            line = line[match.end() :]
            numbered_mode = True
            if pending:
                steps.append(pending[:-2])
                pending = ""
        if numbered_mode:
            pending += line + "\n\n"
        else:
            steps.append(line)
    if pending:
        steps.append(pending[:-2])
    return steps
