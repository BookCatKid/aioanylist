"""Generate an evidence-backed AnyList protocol coverage report.

Authorities:
- ``src/anylist_sdk/official_surface.json``: current AnyList Web endpoints/handlers.
- ``research/android/endpoints.json``: Android routes, methods, evidence and intentional status.
- Python source under ``src/anylist_sdk``: implemented SDK literals and their locations.

The report is intentionally conservative. A route or handler is marked implemented only when an
exact source literal is found (or an explicit, source-proven override says so). Unknown rows are
for human review; the generator never silently assumes parity.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SDK_ROOT = ROOT / "src" / "anylist_sdk"
WEB_SURFACE = SDK_ROOT / "official_surface.json"
ANDROID_SURFACE = ROOT / "research" / "android" / "endpoints.json"

# Proven official operations that are absent from the deliberately lightweight web extractor.
# Keep this list tiny and source-evidenced; these are not speculative aliases.
EXTRA_OPERATION_HANDLERS: dict[str, dict[str, Any]] = {
    "save-custom-dark-theme": {
        "authority": "android",
        "evidence": [
            "research/android/jadx/sources/a2/e.java:1253",
            "research/android/jadx/sources/a2/e.java:1300",
        ],
        "notes": "PBListSettingsOperation carrying only customDarkTheme in updatedSettings.",
    },
    "remove-list-notification-location": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/zc/l.java:549"],
        "notes": "PBListOperation carrying the removed PBNotificationLocation.",
    },
    "save-initial-list-settings": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/cd/q6.java:883"],
        "notes": (
            "Android atomic new-list PBListSettings creation primitive. Intentionally not exposed "
            "as a second public list-settings creation API; the SDK already exposes the equivalent "
            "web-derived initialization workflow."
        ),
        "sdk_policy": "intentionally_unimplemented",
    },
    "save-retail-product-submission": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/sd/o.java:140"],
        "notes": (
            "Android Submit Public Item flow carrying an edited ListItem. Intentionally not exposed "
            "as a convenience API because it writes user-supplied metadata into AnyList's shared "
            "public product database and has little ordinary SDK value relative to its abuse risk."
        ),
        "sdk_policy": "intentionally_unimplemented",
    },
    "set-cross-off-gesture": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/zc/b.java:151"],
        "notes": "PBMobileAppSettings.crossOffGesture mutation.",
    },
    "set-keep-screen-on-behavior": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/od/n0.java:21"],
        "notes": "PBMobileAppSettings.keepScreenOnBehavior mutation.",
    },
    "set-meal-plan-week-start-day": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/c/e0.java:125"],
        "notes": "PBMobileAppSettings.mealPlanWeekStartDay mutation.",
    },
    "set-online-shopping-disabled": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/cd/g4.java:539"],
        "notes": "PBMobileAppSettings.isOnlineShoppingDisabled mutation.",
    },
    "set-should-use-metric-units": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/od/x0.java:27"],
        "notes": "PBMobileAppSettings.shouldUseMetricUnits mutation.",
    },
    "set-web-decimal-separator": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/yc/m0.java:328"],
        "notes": "Synchronizes Android locale decimal separator into PBMobileAppSettings.",
    },
    "set-web-currency-code": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/yc/m0.java:351"],
        "notes": "Synchronizes Android locale currency code into PBMobileAppSettings.",
    },
    "set-web-currency-symbol": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/yc/m0.java:373"],
        "notes": "Synchronizes Android locale currency symbol into PBMobileAppSettings.",
    },
    "dismiss-notice-ids": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/od/b0.java:73"],
        "notes": "Native app-notice UI bookkeeping.",
        "sdk_policy": "intentionally_unimplemented",
    },
    "mark-notice-ids-as-read": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/od/c0.java:103"],
        "notes": "Native app-notice UI bookkeeping.",
        "sdk_policy": "intentionally_unimplemented",
    },
    "set-client-has-shown-alexa-onboarding": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/zc/b.java:169"],
        "notes": "Native onboarding UI bookkeeping, not domain state worth promoting.",
        "sdk_policy": "intentionally_unimplemented",
    },
    "set-client-has-shown-google-assistant-onboarding": {
        "authority": "android",
        "evidence": [
            "research/android/jadx/sources/com/purplecover/anylist/ui/MainActivity.java:291"
        ],
        "notes": "Obsolete Google Assistant onboarding UI bookkeeping.",
        "sdk_policy": "intentionally_unimplemented",
    },
    "set-should-not-link-new-lists-with-google-assistant-by-default": {
        "authority": "android",
        "evidence": ["research/android/jadx/sources/cd/a.java:280"],
        "notes": "Obsolete Google Assistant integration preference.",
        "sdk_policy": "intentionally_unimplemented",
    },
}

ANDROID_ACTION_FALSE_POSITIVES = {
    "add-household-users",  # in-app navigation action
    "categorized-item-operations",  # queue identifier
    "set-cookie",  # HTTP header name
    "settings-screen",  # UTM campaign
    "share-list",  # UTM campaign
    "share-recipes",  # UTM campaign
    "update-purchase-buttons",  # in-app UI action
}

WEB_ENDPOINT_EXCLUSIONS = {
    "/auth/logout": (
        "Browser/XSRF HTML form session logout; token-auth SDK uses the official native "
        "/data/auth/sign-out endpoint instead."
    ),
}

# Android's aggregate documentation rows describe whole route families and are not concrete HTTP
# operations. The individual routes are covered by AnyList Web and/or SDK source literals.
ANDROID_META_PATHS = {
    "/data/shopping-lists/*",
    (
        "/data/{list-folders,list-settings,starter-list-settings,starter-lists,user-categories,"
        "categorized-items,meal-planning-calendar,user-recipe-data,mobile-app-settings}/{all,update,by-id}"
    ),
}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line}


def source_files() -> list[Path]:
    return sorted(path for path in SDK_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def source_hits(needle: str, files: Iterable[Path]) -> list[Hit]:
    hits: list[Hit] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            if needle in line:
                hits.append(Hit(str(path.relative_to(ROOT)), line_number))
    return hits


def android_action_like_literals() -> dict[str, list[Hit]]:
    """Return native action-like string literals for parity review.

    This is intentionally a *candidate* inventory rather than an authority by itself: obfuscated
    Android source contains route fragments and UI state names that can look handler-like. The
    report exposes native-only candidates so they can be reviewed and, when proven, promoted into
    ``EXTRA_OPERATION_HANDLERS`` with exact evidence.
    """

    root = ROOT / "research" / "android" / "jadx" / "sources"
    if not root.exists():
        return {}
    verbs = (
        "add",
        "bulk",
        "categorize",
        "clear",
        "create",
        "delete",
        "dismiss",
        "mark",
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
    pattern = re.compile(r'["\'](' + "(?:" + "|".join(verbs) + r')[a-z0-9]*(?:-[a-z0-9]+)+)["\']')
    result: dict[str, list[Hit]] = {}
    for path in root.rglob("*.java"):
        for line_number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            for match in pattern.finditer(line):
                result.setdefault(match.group(1), []).append(
                    Hit(str(path.relative_to(ROOT)), line_number)
                )
    return result


def concrete_android_endpoints(android: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in android.get("endpoints", [])
        if isinstance(row, dict) and row.get("path") not in ANDROID_META_PATHS
    ]


def endpoint_key(method: str | None, path: str) -> str:
    return f"{method or 'ANY'} {path}"


def build_report() -> dict[str, Any]:
    web = json.loads(WEB_SURFACE.read_text(encoding="utf-8"))
    android = json.loads(ANDROID_SURFACE.read_text(encoding="utf-8"))
    files = source_files()

    android_rows = concrete_android_endpoints(android)
    android_by_path: dict[str, list[dict[str, Any]]] = {}
    for row in android_rows:
        android_by_path.setdefault(str(row["path"]), []).append(row)

    endpoint_rows: list[dict[str, Any]] = []
    seen_endpoint_keys: set[str] = set()

    # Android is method-aware, so preserve each observed route+method pair.
    for row in android_rows:
        path = str(row["path"])
        method = str(row.get("method") or "ANY")
        key = endpoint_key(method, path)
        if key in seen_endpoint_keys:
            continue
        seen_endpoint_keys.add(key)
        hits = source_hits(path, files)
        android_status = str(row.get("sdk_status") or "unknown")
        if hits:
            status = "implemented"
        elif android_status == "intentionally_unimplemented":
            status = "intentionally_unimplemented"
        else:
            status = "unknown"
        endpoint_rows.append(
            {
                "key": key,
                "method": method,
                "path": path,
                "status": status,
                "authorities": ["android"] + (["web"] if path in web.get("endpoints", []) else []),
                "android_sdk_status": android_status,
                "android_evidence": row.get("evidence", []),
                "notes": row.get("notes", ""),
                "sdk_hits": [hit.as_dict() for hit in hits],
            }
        )

    # Add web-only direct routes. Method is intentionally ANY because the lightweight surface
    # extractor records URL literals, not the call's verb.
    for path_value in web.get("endpoints", []):
        path = str(path_value)
        if path in android_by_path:
            continue
        key = endpoint_key(None, path)
        if key in seen_endpoint_keys:
            continue
        seen_endpoint_keys.add(key)
        hits = source_hits(path, files)
        if path in WEB_ENDPOINT_EXCLUSIONS:
            status = "intentionally_unimplemented"
        else:
            status = "implemented" if hits else "unknown"
        endpoint_rows.append(
            {
                "key": key,
                "method": None,
                "path": path,
                "status": status,
                "authorities": ["web"],
                "android_sdk_status": None,
                "android_evidence": [],
                "notes": WEB_ENDPOINT_EXCLUSIONS.get(
                    path, "Direct AnyList Web route not present as a concrete Android endpoint row."
                ),
                "sdk_hits": [hit.as_dict() for hit in hits],
            }
        )

    handlers = {str(value) for value in web.get("operation_handlers", [])}
    handlers.update(EXTRA_OPERATION_HANDLERS)
    handler_rows: list[dict[str, Any]] = []
    for handler in sorted(handlers):
        hits = source_hits(handler, files)
        extra = EXTRA_OPERATION_HANDLERS.get(handler)
        if hits:
            status = "implemented"
        elif extra and extra.get("sdk_policy") == "intentionally_unimplemented":
            status = "intentionally_unimplemented"
        else:
            status = "unknown"
        handler_rows.append(
            {
                "handler": handler,
                "status": status,
                "authority": str(extra["authority"]) if extra else "web",
                "override_evidence": list(extra.get("evidence", [])) if extra else [],
                "notes": str(extra.get("notes", "")) if extra else "",
                "sdk_hits": [hit.as_dict() for hit in hits],
            }
        )

    android_candidates = android_action_like_literals()
    known_handlers = set(handlers)
    android_native_only_candidates = [
        {
            "literal": literal,
            "evidence": [hit.as_dict() for hit in hits],
        }
        for literal, hits in sorted(android_candidates.items())
        if literal not in known_handlers and literal not in ANDROID_ACTION_FALSE_POSITIVES
    ]

    endpoint_statuses = Counter(row["status"] for row in endpoint_rows)
    handler_statuses = Counter(row["status"] for row in handler_rows)

    return {
        "generated_from": {
            "web_surface": str(WEB_SURFACE.relative_to(ROOT)),
            "android_surface": str(ANDROID_SURFACE.relative_to(ROOT)),
        },
        "summary": {
            "endpoints_total": len(endpoint_rows),
            "endpoints_by_status": dict(sorted(endpoint_statuses.items())),
            "handlers_total": len(handler_rows),
            "handlers_by_status": dict(sorted(handler_statuses.items())),
            "protobuf_messages_web": len(web.get("protobuf_messages", [])),
            "protobuf_messages_android": android.get("proto_schema", {}).get(
                "apk_model_proto_messages"
            ),
            "protobuf_schema_note": android.get("proto_schema", {}).get("comparison"),
        },
        "endpoints": sorted(endpoint_rows, key=lambda row: row["key"]),
        "operation_handlers": handler_rows,
        "android_native_only_action_like_candidates": android_native_only_candidates,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    endpoint_counts = summary["endpoints_by_status"]
    handler_counts = summary["handlers_by_status"]
    lines = [
        "# Protocol Coverage Audit",
        "",
        "Generated from the checked-in AnyList Web surface and Android endpoint research. The",
        "generator is conservative: `unknown` means the SDK could not prove an implementation by",
        "exact source evidence and requires review.",
        "",
        "## Summary",
        "",
        f"- Endpoints: **{summary['endpoints_total']}** total — "
        + ", ".join(f"{key}={value}" for key, value in endpoint_counts.items()),
        f"- Operation handlers: **{summary['handlers_total']}** total — "
        + ", ".join(f"{key}={value}" for key, value in handler_counts.items()),
        f"- Web protobuf messages: **{summary['protobuf_messages_web']}**",
        f"- Android protobuf messages: **{summary['protobuf_messages_android']}**",
        f"- Schema comparison: {summary['protobuf_schema_note']}",
        "",
        "## Endpoint unknowns",
        "",
    ]
    unknown_endpoints = [row for row in report["endpoints"] if row["status"] == "unknown"]
    if not unknown_endpoints:
        lines.append("None.")
    else:
        lines.extend(["| Route | Authority | Android status | Notes |", "|---|---|---|---|"])
        for row in unknown_endpoints:
            notes = str(row.get("notes") or "").replace("|", "\\|")
            lines.append(
                f"| `{row['key']}` | {', '.join(row['authorities'])} | "
                f"{row.get('android_sdk_status') or ''} | {notes} |"
            )

    lines.extend(["", "## Operation-handler unknowns", ""])
    unknown_handlers = [row for row in report["operation_handlers"] if row["status"] == "unknown"]
    if not unknown_handlers:
        lines.append("None.")
    else:
        for row in unknown_handlers:
            lines.append(f"- `{row['handler']}`")

    lines.extend(
        [
            "",
            "## Intentional endpoint exclusions",
            "",
            "These are source-proven official routes deliberately not promoted to the public SDK.",
            "",
        ]
    )
    exclusions = [
        row for row in report["endpoints"] if row["status"] == "intentionally_unimplemented"
    ]
    for row in exclusions:
        lines.append(f"- `{row['key']}` — {row.get('notes') or 'intentional exclusion'}")

    lines.extend(["", "## Intentional operation-handler exclusions", ""])
    handler_exclusions = [
        row
        for row in report["operation_handlers"]
        if row["status"] == "intentionally_unimplemented"
    ]
    for row in handler_exclusions:
        lines.append(f"- `{row['handler']}` — {row.get('notes') or 'intentional exclusion'}")

    lines.extend(
        [
            "",
            "## Evidence model",
            "",
            "The generator can emit a detailed JSON report containing every endpoint/handler row,",
            "exact SDK source hits, and Android decompiler evidence paths. That verbose artifact is",
            "generated on demand rather than tracked in Git. This Markdown intentionally focuses on",
            "unresolved and intentionally excluded surface rather than duplicating every implemented",
            "row.",
            "",
            "## Android native-only action-like candidates",
            "",
            "These are quoted native literals that resemble operation handlers but are not present",
            "in the web handler inventory. They are review candidates, not automatically treated as",
            "protocol operations because obfuscated Android code also contains handler-like UI/state",
            "strings.",
            "",
        ]
    )
    candidates = report.get("android_native_only_action_like_candidates", [])
    if candidates:
        for row in candidates:
            refs = ", ".join(f"{hit['path']}:{hit['line']}" for hit in row["evidence"][:4])
            lines.append(f"- `{row['literal']}` — {refs}")
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    parser.add_argument("--fail-on-unknown", action="store_true")
    args = parser.parse_args()

    report = build_report()
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown_out:
        args.markdown_out.write_text(render_markdown(report) + "\n")
    if not args.json_out and not args.markdown_out:
        print(json.dumps(report["summary"], indent=2, sort_keys=True))

    unknown = (
        any(row["status"] == "unknown" for row in report["endpoints"])
        or any(row["status"] == "unknown" for row in report["operation_handlers"])
        or bool(report["android_native_only_action_like_candidates"])
    )
    if args.fail_on_unknown and unknown:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
