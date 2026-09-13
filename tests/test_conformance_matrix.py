from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "anylist_sdk"
MATRIX = ROOT / "CONFORMANCE_MATRIX.md"


def _public_callables() -> list[tuple[str, bool]]:
    """Return every source-visible public callable that should be checklist-addressable.

    Runtime-generated protobuf classes are represented by their schema/runtime rows rather
    than by parsing the generated ``proto/__init__.py`` namespace as ordinary Python source.
    """
    result: list[tuple[str, bool]] = []
    for path in SOURCE.rglob("*.py"):
        if path.name == "__init__.py" and path.parent.name == "proto":
            continue
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name.startswith("_"):
                            continue
                        result.append((f"{node.name}.{child.name}()", True))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    result.append((f"{node.name}()", False))
    return result


def test_conformance_matrix_accounts_for_every_public_callable() -> None:
    text = MATRIX.read_text()
    missing: list[str] = []
    for token, is_method in _public_callables():
        if is_method:
            present = f"`{token}`" in text
        else:
            name = token[:-2]
            # Top-level helpers are commonly written with a module/service prefix in the
            # matrix (for example ``normalization.remove_diacritics()``).
            present = bool(re.search(rf"`(?:[A-Za-z0-9_]+\.)*{re.escape(name)}\(\)`", text))
        if not present:
            missing.append(token)

    assert missing == [], "CONFORMANCE_MATRIX.md is missing public callables: " + ", ".join(missing)
