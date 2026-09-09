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
    r"^(?:cups?|c\.?|tassen?|tas\.?|tasse/n|becher|be\.?|bch\.?|fluid ounces?|"
    r"fl\.?\s*oz\.?|gallons?|gal\.?|troy ounces?|oz\.?\s*t\.?|t\.?\s*oz\.?|"
    r"ounces?|oz\.?|pints?|pt\.?|pounds?|lbs?\.?|pfund|pf\.?|quarts?|qts?\.?|"
    r"tablespoons?|tbsps?\.?|tbs?\.?|tbl\.?|t\.?|msk\.?|ss\.?|spsk\.?|rkl\.?|"
    r"el\.?|esslöffel|teaspoons?|tsps?\.?|ts?\.?|tsk\.?|tl\.?|teelöffel|grams?|"
    r"gr?\.?|gramm|kilograms?|kg\.?|kilogramm|milligrams?|mg\.?|liters?|l\.?|"
    r"deciliters?|dl\.?|milliliters?|ml\.?|krm\.?|pecks?|bushels?|buckets?|slices?|"
    r"doz(?:en|\.)?|cloves?|loaf|loaves|pinch(?:es)?|packages?|pkg\.?|cans?|drops?|"
    r"bunch(?:es)?|dash(?:es)?|cartons?|each|pieces?|to taste|squares?|tubes?|strips?|"
    r"stems?|stalks?|sprigs?|spears?|sprouts?|sheets?|scoops?|pouch(?:es)?|packets?|"
    r"packs?|leaf|leaves|glass(?:es)?|cubes?|containers?|cones?|box(?:es)?|bottles?|"
    r"blocks?|bags?|parts?|sticks?|heads?|bars?|ears?|jars?|inches?|tubs?|small|medium|"
    r"large|dosen?|do\.?|glas|gläser|gl\.?|packung(?:en)?|pck\.?|pk\.?|pckg\.?|"
    r"päckchen|beutel|btl\.?|bt\.?|flaschen?|fl\.?|zehen?|knollen?|kn\.?|kopf|köpfe|"
    r"bund|bünde|bd\.?|bn\.?|blatt|blätter|bl\.?|spritzer|spr?\.?|tropf(?:en)?|tr\.?|"
    r"prisen?|prise\(n\)|pr\.?|stücke?|stk\.?|st\.?|stck\.?|stiele?|stangen?|stg\.?|"
    r"würfel|wf\.?|etwas|nach belieben|n\.\s*b\.|viel)(?=$|\s|,)",
    re.I,
)
_TRAILING_SIZE_ADJECTIVE = re.compile(r"(?:(?:small|medium|large)\s*)+$", re.I)
_NUMBERED_STEP = re.compile(r"^\d+[.]?\s*", re.I)
_STEP_TRIM = re.compile(r"^[\s,\-•–—]+|[\s,\-•–—]+$")


def split_quantity_prefix(line: str) -> tuple[str, str]:
    cleaned = line.strip(" \t,-•–—")
    amount, rest = parse_leading_amount(cleaned)
    if amount is None:
        return "", cleaned
    consumed = cleaned[: len(cleaned) - len(rest)]
    remainder = rest

    # JD is a repeated group: consume every adjacent recognized unit/container token, not
    # just the first one. This is observable for "1/2 small head cabbage" and
    # "12 ounces jars".
    while True:
        stripped = remainder.lstrip()
        gap = remainder[: len(remainder) - len(stripped)]
        token = _PACKAGE_OR_UNIT.match(stripped)
        if token is None:
            break
        consumed += gap + token.group(0)
        remainder = stripped[token.end() :]

    # AD lets a parenthesized package expression remain part of the pasted quantity prefix.
    parenthetical = re.match(r"^\s*(\([^()]+\))", remainder)
    if parenthetical:
        consumed += remainder[: parenthetical.end()]
        remainder = remainder[parenthetical.end() :]

    consumed = trim_whitespace_and_punctuation(consumed)
    remainder = trim_whitespace_and_punctuation(remainder)
    # XD moves only a *trailing* size adjective back to the ingredient side. If another
    # recognized container follows it ("small head"), the adjective stays in the quantity.
    adjective = _TRAILING_SIZE_ADJECTIVE.search(consumed)
    if adjective:
        moved = adjective.group(0).strip()
        consumed = trim_whitespace_and_punctuation(consumed[: adjective.start()])
        remainder = trim_whitespace_and_punctuation(f"{moved} {remainder}")
    return consumed, remainder


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
