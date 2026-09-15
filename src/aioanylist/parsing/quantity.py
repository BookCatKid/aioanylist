from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction

from ..normalization import trim_whitespace_and_punctuation
from ..proto import PB, PBItemPackageSize, PBItemQuantity, PBItemQuantityAndPackageSize

_VULGAR = {
    "½": Fraction(1, 2),
    "⅓": Fraction(1, 3),
    "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4),
    "¾": Fraction(3, 4),
    "⅕": Fraction(1, 5),
    "⅖": Fraction(2, 5),
    "⅗": Fraction(3, 5),
    "⅘": Fraction(4, 5),
    "⅙": Fraction(1, 6),
    "⅚": Fraction(5, 6),
    "⅛": Fraction(1, 8),
    "⅜": Fraction(3, 8),
    "⅝": Fraction(5, 8),
    "⅞": Fraction(7, 8),
}
_SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹०१२३४५६७८९",
    "012345678901234567890123456789",
)
_DASH_CLASS = r"\-֊־᐀᠆‐‑‒–—―⸗⸚⸺⸻〜〰゠︱︲﹘﹣－"
_DASH_RE = re.compile(f"[{_DASH_CLASS}]")

# JD is the parser's recognition table. Recognition is intentionally broader than rP/aP's
# normalization table below: e.g. "becher" is accepted as a unit but is not rewritten to "cup".
_UNIT_MATCH_GROUPS: dict[str, tuple[str, ...]] = {
    "cup": ("cup", "cups", "c", "tasse", "tassen", "tas", "tasse/n", "becher", "be", "bch"),
    "fl oz": ("fluid ounce", "fluid ounces", "fl oz"),
    "gal": ("gallon", "gallons", "gal"),
    "troy oz": ("troy ounce", "troy ounces", "oz t", "t oz"),
    "oz": ("ounce", "ounces", "oz"),
    "pt": ("pint", "pints", "pt"),
    "lb": ("pound", "pounds", "lb", "lbs", "pfund", "pf"),
    "qt": ("quart", "quarts", "qt", "qts"),
    "Tbsp": (
        "tablespoon",
        "tablespoons",
        "tbsp",
        "tbs",
        "tbl",
        "t",
        "msk",
        "ss",
        "spsk",
        "rkl",
        "el",
        "esslöffel",
    ),
    "tsp": ("teaspoon", "teaspoons", "tsp", "ts", "tsk", "tl", "teelöffel"),
    "g": ("gram", "grams", "g", "gr", "gramm"),
    "kg": ("kilogram", "kilograms", "kg", "kilogramm"),
    "mg": ("milligram", "milligrams", "mg"),
    "L": ("liter", "liters", "l"),
    "dl": ("deciliter", "deciliters", "dl"),
    "ml": ("milliliter", "milliliters", "ml", "krm"),
    "in": ("inch", "inches", "in"),
}

# rP is the exact PBItemQuantity.normalizedUnit alias table. Do not fold JD-only terms into
# this mapping: those spellings deliberately survive normalization in the official client.
_UNIT_NORMALIZATION_GROUPS: dict[str, tuple[str, ...]] = {
    "cup": ("cup", "cups", "c"),
    "fl oz": ("fluid ounce", "fluid ounces", "fl oz"),
    "gal": ("gallon", "gallons", "gal"),
    "oz": ("ounce", "ounces", "oz"),
    "pt": ("pint", "pints", "pt"),
    "lb": ("pound", "pounds", "lb", "lbs"),
    "qt": ("quart", "quarts", "qt", "qts"),
    "troy oz": ("oz t", "t oz"),
    "Tbsp": (
        "tablespoon",
        "tablespoons",
        "tbsp",
        "tbs",
        "tbl",
        "t",
        "msk",
        "ss",
        "spsk",
        "el",
        "rkl",
        "esslöffel",
    ),
    "tsp": ("teaspoon", "teaspoons", "tsp", "ts", "tsk", "tl", "teelöffel"),
    "g": ("gram", "grams", "g", "gr", "gramm"),
    "kg": ("kilogram", "kilograms", "kg", "kilogramm"),
    "mg": ("milligram", "milligrams", "mg"),
    "L": ("liter", "liters", "l"),
    "dl": ("deciliter", "deciliters", "dl"),
    "ml": ("milliliter", "milliliters", "ml", "krm"),
    "dozen": ("doz",),
    "Tasse": ("tasse", "tassen", "tas"),
    "Pfund": ("pfund", "pf"),
    "can": ("dose", "dosen", "do"),
    "glas": ("glas", "gläser", "gl"),
}
_UNIT_LOOKUP = {
    re.sub(r"[.\s]+", " ", alias.casefold()).strip(): canonical
    for canonical, aliases in _UNIT_NORMALIZATION_GROUPS.items()
    for alias in aliases
}
_UNIT_MATCH_KEYS = {
    re.sub(r"[.\s]+", " ", alias.casefold()).strip()
    for aliases in _UNIT_MATCH_GROUPS.values()
    for alias in aliases
}

_UNIT_ABBREVIATION_GROUPS: dict[str, tuple[str, ...]] = {
    "fl oz": ("fluid ounce", "fluid ounces"),
    "gal": ("gallon", "gallons"),
    "oz": ("ounce", "ounces"),
    "pt": ("pint", "pints"),
    "lb": ("pound", "pounds"),
    "qt": ("quart", "quarts"),
    "Tbsp": ("tablespoon", "tablespoons"),
    "tsp": ("teaspoon", "teaspoons"),
    "g": ("gram", "grams", "gramm"),
    "kg": ("kilogram", "kilograms", "kilogramm"),
    "mg": ("milligram", "milligrams"),
    "L": ("liter", "liters"),
    "dl": ("deciliter", "deciliters"),
    "ml": ("milliliter", "milliliters"),
    "pkg": ("package", "packages"),
    "TL": ("teelöffel",),
    "EL": ("esslöffel",),
    "Pkg.": ("packung", "packungen"),
    "Päck.": ("päckchen",),
}

_PACKAGE_WORDS = (
    "peck",
    "pecks",
    "bushel",
    "bushels",
    "bucket",
    "buckets",
    "slice",
    "slices",
    "doz",
    "doz.",
    "dozen",
    "clove",
    "cloves",
    "loaf",
    "loaves",
    "pinch",
    "pinches",
    "package",
    "packages",
    "pkg",
    "pkg.",
    "can",
    "cans",
    "drop",
    "drops",
    "bunch",
    "bunches",
    "dash",
    "dashes",
    "carton",
    "cartons",
    "each",
    "piece",
    "pieces",
    "to taste",
    "square",
    "squares",
    "tube",
    "tubes",
    "strip",
    "strips",
    "stem",
    "stems",
    "stalk",
    "stalks",
    "sprig",
    "sprigs",
    "spear",
    "spears",
    "sprout",
    "sprouts",
    "sheet",
    "sheets",
    "scoop",
    "scoops",
    "pouch",
    "pouches",
    "packet",
    "packets",
    "pack",
    "packs",
    "leaf",
    "leaves",
    "glass",
    "glasses",
    "cube",
    "cubes",
    "container",
    "containers",
    "cone",
    "cones",
    "box",
    "boxes",
    "bottle",
    "bottles",
    "block",
    "blocks",
    "bag",
    "bags",
    "part",
    "parts",
    "stick",
    "sticks",
    "head",
    "heads",
    "bar",
    "bars",
    "ear",
    "ears",
    "jar",
    "jars",
    "tub",
    "tubs",
    "small",
    "medium",
    "large",
    "dose",
    "dosen",
    "glas",
    "gläser",
    "packung",
    "packungen",
    "päckchen",
    "beutel",
    "flasche",
    "flaschen",
    "zehe",
    "zehen",
    "knolle",
    "knollen",
    "kopf",
    "köpfe",
    "bund",
    "bünde",
    "blatt",
    "blätter",
    "spritzer",
    "tropf",
    "tropfen",
    "prise",
    "prisen",
    "stück",
    "stücke",
    "stiel",
    "stiele",
    "stange",
    "stangen",
    "würfel",
    "etwas",
    "nach belieben",
    "viel",
    "do.",
    "gl.",
    "pck",
    "pck.",
    "pk",
    "pk.",
    "pckg",
    "pckg.",
    "btl",
    "btl.",
    "bt",
    "bt.",
    "fl",
    "fl.",
    "kn",
    "kn.",
    "bd",
    "bd.",
    "bn",
    "bn.",
    "bl",
    "bl.",
    "spr",
    "spr.",
    "tr",
    "tr.",
    "pr",
    "pr.",
    "stk",
    "stk.",
    "st",
    "st.",
    "stck",
    "stck.",
    "stg",
    "stg.",
    "wf",
    "wf.",
    "n. b.",
)

_SINGULAR_PLURAL = {
    "peck": "pecks",
    "bushel": "bushels",
    "bucket": "buckets",
    "slice": "slices",
    "clove": "cloves",
    "loaf": "loaves",
    "pinch": "pinches",
    "package": "packages",
    "can": "cans",
    "drop": "drops",
    "bunch": "bunches",
    "dash": "dashes",
    "carton": "cartons",
    "piece": "pieces",
    "square": "squares",
    "tube": "tubes",
    "strip": "strips",
    "stem": "stems",
    "stalk": "stalks",
    "sprig": "sprigs",
    "spear": "spears",
    "sprout": "sprouts",
    "sheet": "sheets",
    "scoop": "scoops",
    "pouch": "pouches",
    "packet": "packets",
    "pack": "packs",
    "leaf": "leaves",
    "glass": "glasses",
    "cube": "cubes",
    "container": "containers",
    "cone": "cones",
    "box": "boxes",
    "bottle": "bottles",
    "block": "blocks",
    "bag": "bags",
    "part": "parts",
    "stick": "sticks",
    "head": "heads",
    "bar": "bars",
    "ear": "ears",
    "jar": "jars",
    "inch": "inches",
    "tub": "tubs",
    "cup": "cups",
    "ounce": "ounces",
    "gallon": "gallons",
    "pint": "pints",
    "pound": "pounds",
    "quart": "quarts",
    "tablespoon": "tablespoons",
    "teaspoon": "teaspoons",
    "gram": "grams",
    "kilogram": "kilograms",
    "milligram": "milligrams",
    "liter": "liters",
    "deciliter": "deciliters",
    "milliliter": "milliliters",
    "tasse": "tassen",
    "dose": "dosen",
    "flasche": "flaschen",
    "zehe": "zehen",
    "knolle": "knollen",
    "kopf": "köpfe",
    "bund": "bünde",
    "blatt": "blätter",
    "tropf": "tropfen",
    "prise": "prisen",
    "stück": "stücke",
    "stiel": "stiele",
    "stange": "stangen",
}
_PLURAL_SINGULAR = {v: k for k, v in _SINGULAR_PLURAL.items()}


@dataclass(slots=True, frozen=True)
class ParsedAmount:
    raw: str
    value: float
    is_range: bool = False
    range_end: float | None = None


def normalize_digits(text: str) -> str:
    return text.translate(_DIGITS)


def _fraction_value(text: str) -> float:
    text = unicodedata.normalize("NFKD", normalize_digits(text)).strip()
    text = text.translate(_SUPER).translate(_SUB).replace("⁄", "/")
    # NFKD expands most vulgar fractions to numerator + fraction slash + denominator.
    if text in _VULGAR:
        return float(_VULGAR[text])
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    return 0.0


def amount_as_float(raw: str, *, decimal_separator: str = ".") -> float:
    if not raw:
        return 0.0
    text = normalize_digits(raw.strip())
    # Mixed fraction with whitespace or dash, including vulgar/superscript forms.
    mixed = re.match(
        rf"^(\d+)(?:\s+|[{_DASH_CLASS}])\s*(\d+\s*[\/⁄]\s*\d+|[{''.join(_VULGAR)}]|[⁰¹²³⁴⁵⁶⁷⁸⁹]+\s*[\/⁄]\s*[₀₁₂₃₄₅₆₇₈₉]+)",
        text,
    )
    if mixed:
        return float(mixed.group(1)) + _fraction_value(mixed.group(2))
    vulgar = re.match(rf"^(\d*)\s*([{''.join(_VULGAR)}])", text)
    if vulgar:
        return (float(vulgar.group(1)) if vulgar.group(1) else 0.0) + float(
            _VULGAR[vulgar.group(2)]
        )
    frac_match = re.match(
        r"^(\d+\s*[\/⁄]\s*\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹]+\s*[\/⁄]\s*[₀₁₂₃₄₅₆₇₈₉]+)",
        text,
    )
    if frac_match:
        frac = _fraction_value(frac_match.group(1))
        if frac:
            return frac
    if decimal_separator == ".":
        text = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", text)
    # JavaScript parseFloat consumes the leading numeric prefix rather than requiring the
    # entire string to be numeric.  PBItemQuantity.amount may itself contain a range such as
    # "2 - 3", so this behavior is observable in amountAsDouble/derived quantity helpers.
    text = text.replace(",", ".", 1)
    numeric = re.match(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text)
    return float(numeric.group(0)) if numeric else 0.0


def _numeric_atom_pattern() -> str:
    vulgar = "".join(_VULGAR)
    supers = "⁰¹²³⁴⁵⁶⁷⁸⁹"
    subs = "₀₁₂₃₄₅₆₇₈₉"
    frac = rf"(?:\d+\s*[\/⁄]\s*\d+|[{vulgar}]|[{supers}]+\s*[\/⁄]\s*[{subs}]+)"
    mixed = rf"(?:\d+)?(?:\s+|[{_DASH_CLASS}])?{frac}"
    decimal = r"(?:\d+(?:,\d+)?(?:\.\d+)?|\.\d+)"
    return rf"(?:{mixed}|{decimal})"


_NUM = _numeric_atom_pattern()
_RANGE = re.compile(
    rf"^(?P<a>{_NUM})(?P<sep>(?:\s+(?:to|or)\s+)|(?:\s*[{_DASH_CLASS}]\s*))(?P<b>{_NUM})(?=$|\s|[A-Za-z(])",
    re.IGNORECASE,
)
_SINGLE = re.compile(rf"^(?P<a>{_NUM})(?=$|\s|[A-Za-z(\-])", re.IGNORECASE)


def parse_leading_amount(
    text: str, *, decimal_separator: str = "."
) -> tuple[ParsedAmount | None, str]:
    value = normalize_digits(text.strip())
    # A hyphen between an integer and a *fractional* atom is AnyList's mixed-fraction
    # spelling (``2-1/2``), not a range.  Test this before the generic range regex,
    # whose left-most alternation would otherwise happily consume just ``2``.
    vulgar = "".join(_VULGAR)
    mixed_prefix = re.match(
        rf"^(?P<a>\d+)(?:\s+|[{_DASH_CLASS}])\s*(?P<f>\d+\s*[\/⁄]\s*\d+|[{vulgar}]|[⁰¹²³⁴⁵⁶⁷⁸⁹]+\s*[\/⁄]\s*[₀₁₂₃₄₅₆₇₈₉]+)(?=$|\s|[A-Za-z(])",
        value,
    )
    if mixed_prefix:
        raw = mixed_prefix.group(0)
        return ParsedAmount(raw, amount_as_float(raw, decimal_separator=decimal_separator)), value[
            mixed_prefix.end() :
        ]
    m = _RANGE.match(value)
    if m:
        raw_a, raw_b = m.group("a"), m.group("b")
        # A hyphen inside a mixed fraction (2-1/2) is already consumed by the atom, so this is a
        # true range at this point.
        raw = f"{raw_a} - {raw_b}"
        return (
            ParsedAmount(
                raw=raw,
                value=amount_as_float(raw_a, decimal_separator=decimal_separator),
                is_range=True,
                range_end=amount_as_float(raw_b, decimal_separator=decimal_separator),
            ),
            value[m.end() :],
        )
    m = _SINGLE.match(value)
    if not m:
        return None, value
    raw = m.group("a")
    return ParsedAmount(raw, amount_as_float(raw, decimal_separator=decimal_separator)), value[
        m.end() :
    ]


def normalize_unit(unit: str) -> str:
    raw = unit.strip()
    key = re.sub(r"[.\s]+", " ", raw.casefold()).strip()
    value = _UNIT_LOOKUP.get(key, raw)
    # aP runs cP after alias replacement, so container/plural units such as "jars" are
    # singularized even when they did not appear in the alias table.
    return singularize_units_in_text(value)


def _replace_unit_aliases(text: str, groups: dict[str, tuple[str, ...]]) -> str:
    value = text or ""
    for canonical, aliases in groups.items():
        # The web tables are applied sequentially with case-insensitive word-boundary regexes.
        for alias in aliases:
            pattern = re.escape(alias).replace(r"\ ", r"\s+")
            value = re.sub(rf"(?<!\w){pattern}\.?(?!\w)", canonical, value, flags=re.IGNORECASE)
    return value


def normalize_units_in_text(text: str) -> str:
    """Port aP: normalize recognized aliases, then singularize known container words."""
    return singularize_units_in_text(_replace_unit_aliases(text, _UNIT_NORMALIZATION_GROUPS))


def abbreviate_units_in_text(text: str) -> str:
    """Port uP: display-oriented abbreviation table used by ingredient totals."""
    return _replace_unit_aliases(text, _UNIT_ABBREVIATION_GROUPS)


def singularize_unit(unit: str) -> str:
    raw = unit.strip()
    lower = raw.casefold().rstrip(".")
    if lower in _PLURAL_SINGULAR:
        return _PLURAL_SINGULAR[lower]
    return raw


def singularize_units_in_text(text: str) -> str:
    """Singularize AnyList's known unit/container words inside arbitrary text.

    The web parser's ``cP`` helper walks the same singular/plural table with
    word-boundary replacements; it is used not only for a standalone unit but also for
    ``rawPackageSize`` such as ``"12 ounces jars"``.
    """
    value = text or ""
    # Longest forms first avoids a shorter token stealing a larger phrase.
    for plural, singular in sorted(
        _PLURAL_SINGULAR.items(), key=lambda pair: len(pair[0]), reverse=True
    ):
        value = re.sub(rf"(?<!\w){re.escape(plural)}(?!\w)", singular, value, flags=re.IGNORECASE)
    return value


def pluralize_unit(unit: str) -> str:
    raw = unit.strip()
    lower = raw.casefold().rstrip(".")
    if lower in _SINGULAR_PLURAL:
        return _SINGULAR_PLURAL[lower]
    return raw


def unit_for_amount(unit: str, amount: float) -> str:
    return singularize_unit(unit) if 0 < amount <= 1 else pluralize_unit(unit)


def _match_unit_or_package(rest: str) -> tuple[str, str]:
    value = rest.lstrip(" ,-")
    candidates: list[str] = []
    for aliases in _UNIT_MATCH_GROUPS.values():
        candidates.extend(aliases)
    candidates.extend(_PACKAGE_WORDS)
    for candidate in sorted(set(candidates), key=len, reverse=True):
        pattern = re.compile(rf"^{re.escape(candidate)}\.?\b", re.IGNORECASE)
        m = pattern.match(value)
        if m:
            return value[: m.end()].strip(), value[m.end() :]
    return "", value


def parse_package_size(
    text: str, *, require_unit: bool = True, decimal_separator: str = "."
) -> PBItemPackageSize | None:
    raw = trim_whitespace_and_punctuation(text)
    if not raw:
        return None
    # Parentheses are syntactic wrappers in common official test cases.
    candidate = raw.strip()
    if candidate.startswith("(") and candidate.endswith(")"):
        candidate = candidate[1:-1].strip()
    amount, rest = parse_leading_amount(candidate, decimal_separator=decimal_separator)
    if amount is None:
        return None
    unit, tail = _match_unit_or_package(rest)
    if require_unit and not unit:
        return None
    package_type = ""
    # If the first token was a measurement unit, a following container word is packageType.
    if unit and re.sub(r"[.\s]+", " ", unit.casefold()).strip() in _UNIT_MATCH_KEYS:
        pt, tail2 = _match_unit_or_package(tail)
        if pt and pt.casefold().rstrip(".") in {x.casefold().rstrip(".") for x in _PACKAGE_WORDS}:
            package_type, tail = pt, tail2
    consumed_len = len(candidate) - len(tail)
    raw_package = candidate[:consumed_len].strip(" ,-") or candidate
    out = PB.PBItemPackageSize(size=amount.raw, rawPackageSize=raw_package)
    if unit:
        out.unit = unit.strip()
    if package_type:
        out.packageType = package_type.strip()
    return out


def parse_quantity_and_package_size(
    text: str, *, decimal_separator: str = "."
) -> PBItemQuantityAndPackageSize | None:
    raw = text.strip()
    if not raw:
        return None
    amount, rest = parse_leading_amount(raw, decimal_separator=decimal_separator)
    if amount is None:
        return None
    rest = rest.strip()

    quantity = None
    package = None

    # Explicit parenthesized package size after a count/container: "2 cans (28 oz)".
    unit, tail = _match_unit_or_package(rest)
    parenthetical = re.search(r"\(([^()]*)\)", tail if unit else rest)

    if unit:
        # Distinguish measurement quantity from count + package-size shape.
        is_measurement = re.sub(r"[.\s]+", " ", unit.casefold()).strip() in _UNIT_MATCH_KEYS
        is_container = unit.casefold().rstrip(".") in {
            p.casefold().rstrip(".") for p in _PACKAGE_WORDS
        }
        if is_measurement or is_container:
            quantity = PB.PBItemQuantity(
                amount=amount.raw,
                unit=unit.strip(),
                rawQuantity=f"{amount.raw} {unit.strip()}",
            )

        if parenthetical:
            package = parse_package_size(
                parenthetical.group(1), require_unit=True, decimal_separator=decimal_separator
            )
        elif is_container:
            # "2 12 ounce jars" / "4 6-inch sprigs": package size can precede the container.
            prefix_source = rest
            inner_amount, _ = parse_leading_amount(
                prefix_source, decimal_separator=decimal_separator
            )
            if inner_amount is not None:
                package = parse_package_size(
                    prefix_source, require_unit=True, decimal_separator=decimal_separator
                )
        else:
            # A second package expression may follow a quantity unit, e.g. 1 cup 8 oz package.
            package = parse_package_size(
                tail, require_unit=True, decimal_separator=decimal_separator
            )
    else:
        # "1 (6-oz can)" or plain numeric quantity.
        if parenthetical:
            quantity = PB.PBItemQuantity(amount=amount.raw, rawQuantity=amount.raw)
            package = parse_package_size(
                parenthetical.group(1), require_unit=True, decimal_separator=decimal_separator
            )
        else:
            # Try to interpret the remainder as package size before falling back to bare quantity.
            package_candidate = parse_package_size(
                rest, require_unit=True, decimal_separator=decimal_separator
            )
            if package_candidate is not None and rest:
                quantity = PB.PBItemQuantity(amount=amount.raw, rawQuantity=amount.raw)
                package = package_candidate
            else:
                quantity = PB.PBItemQuantity(amount=amount.raw, rawQuantity=amount.raw)

    # vP initially stores only the parsed amount/unit.  It restores the complete original
    # string to rawQuantity only when no package-size parse succeeded.
    if quantity is not None and package is None:
        quantity.rawQuantity = raw

    # Official behavior preserves the entire original user input in rawQuantity when a quantity is
    # accepted and no conflicting package-only parse takes over.
    result = PB.PBItemQuantityAndPackageSize()
    if quantity is not None:
        result.quantityPb.CopyFrom(quantity)
    if package is not None:
        result.packageSizePb.CopyFrom(package)
    return result if result.HasField("quantityPb") or result.HasField("packageSizePb") else None


def replace_quantity_amount(quantity: PBItemQuantity, new_amount: str) -> PBItemQuantity:
    out = PB.PBItemQuantity()
    out.CopyFrom(quantity)
    value = amount_as_float(new_amount)
    out.amount = new_amount
    if out.unit:
        out.unit = unit_for_amount(out.unit, value)
    if not new_amount:
        out.rawQuantity = ""
    else:
        replaced = False
        if quantity.rawQuantity:
            # yP replaces the leading quantity while preserving the original suffix.
            parsed, _ = parse_leading_amount(quantity.rawQuantity)
            if parsed and quantity.rawQuantity.startswith(parsed.raw):
                out.rawQuantity = new_amount + quantity.rawQuantity[len(parsed.raw) :]
                replaced = True
        if not replaced:
            out.rawQuantity = f"{new_amount} {out.unit}".strip()
    return out


def decimal_to_friendly_fraction(value: float, *, unicode: bool = True) -> str:
    whole = math.floor(value)
    remainder = value - whole
    if remainder < 0.05:
        if whole == 0 and math.isclose(remainder, 1 / 32):
            return "1/32"
        if whole == 0 and math.isclose(remainder, 1 / 64):
            return "1/64"
        return str(round(value, 3)).rstrip("0").rstrip(".")
    if remainder > 0.95:
        return str(whole + 1)
    frac = Fraction(remainder).limit_denominator(20)
    glyph = None
    if unicode:
        for ch, mapped in _VULGAR.items():
            if mapped == frac:
                glyph = ch
                break
    part = glyph or f"{frac.numerator}/{frac.denominator}"
    return f"{whole} {part}" if whole else part


def scale_quantity_text(text: str, scale: float) -> str:
    parsed, _ = parse_leading_amount(text)
    if parsed is None:
        return text
    if parsed.is_range and parsed.range_end is not None:
        left = decimal_to_friendly_fraction(parsed.value * scale)
        right = decimal_to_friendly_fraction(parsed.range_end * scale)
        replacement = f"{left} - {right}"
    else:
        replacement = decimal_to_friendly_fraction(parsed.value * scale)
    # Match the original consumed amount rather than parsed.raw's normalized range separator.
    m = _RANGE.match(normalize_digits(text.strip())) or _SINGLE.match(
        normalize_digits(text.strip())
    )
    if not m:
        return text
    return replacement + text.strip()[m.end() :]
