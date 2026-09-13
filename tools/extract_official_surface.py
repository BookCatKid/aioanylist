#!/usr/bin/env python3
"""Extract protocol surface from AnyList's official web app bundle.

This tool intentionally knows nothing about third-party AnyList clients.
"""

from __future__ import annotations
import argparse, json, re
from pathlib import Path


def extract(text: str) -> dict:
    endpoints = sorted(set(re.findall(r'["\'](/(?:auth|data)/[^"\']+)["\']', text)))

    # Operation handler IDs in the official bundle are ordinary strings. Some are passed
    # directly to metadata helpers, while others are selected through local variables or
    # ternaries before those helpers are called. The web client consistently names handlers
    # with one of these mutation verbs, so extracting those literal strings gives the complete
    # current operation surface without depending on minified symbol names.
    action_verbs = (
        "add",
        "bulk",
        "categorize",
        "clear",
        "create",
        "delete",
        "migrate",
        "move",
        "new",
        "remove",
        "rename",
        "save",
        "set",
        "share",
        "uncheck",
        "unshare",
        "update",
    )
    verb_pattern = "(?:" + "|".join(action_verbs) + ")"
    handlers = set(
        m.group(2) for m in re.finditer(rf'(["\'])({verb_pattern}[a-z0-9]*(?:-[a-z0-9]+)+)\1', text)
    )
    return {"endpoints": endpoints, "operation_handlers": sorted(handlers)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("app_js", type=Path)
    p.add_argument("--schema", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    result = extract(a.app_js.read_text(encoding="utf-8"))
    if a.schema:
        schema = json.loads(a.schema.read_text())
        result["protobuf_package"] = schema.get("package")
        result["protobuf_messages"] = sorted(m["name"] for m in schema.get("messages", []))
        result["protobuf_enums"] = sorted(e["name"] for e in schema.get("enums", []))
    a.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
