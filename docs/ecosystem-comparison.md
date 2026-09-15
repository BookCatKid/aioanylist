# AnyList client ecosystem comparison

This document is the evidence behind the README's claim that this project is the most complete public general-purpose AnyList client we could identify. It compares implemented surface area, fidelity, verification depth, and measured performance across the current public client ecosystem.

## Scope and methodology

Audit date: **2026-09-14**.

We searched GitHub, PyPI, and npm for public projects that directly implement AnyList's undocumented service API. The primary comparison covers general-purpose client libraries. Downstream applications such as Home Assistant integrations, CLIs, MCP servers, recipe importers, and dashboards were excluded.

The general-purpose libraries identified were:

| Project | Audited revision | Published package | Notes |
| --- | --- | --- | --- |
| **This project** | current repository | Python package, release candidate 1.0.0 | Async-first pure Python client |
| PyPI [`anylist`](https://pypi.org/project/anylist/0.0.1rc1/) | published `0.0.1rc1` artifact (2025-05-31) | PyPI `anylist` 0.0.1rc1 | Very small async Python client; no repository/project URL is published in its package metadata |
| [`kevdliu/anylist`](https://github.com/kevdliu/anylist/tree/d69278a6a7ec04750dadfdf9c6f8b1b157b3a7e8) | `d69278a` (2026-05-03) | npm `anylist` 0.8.6 | JavaScript client; README explicitly says much of the API is not implemented and list create/remove/update is unavailable |
| [`phildenhoff/anylist_rs`](https://github.com/phildenhoff/anylist_rs/tree/0698dc9de81dd7a50856f2d890a5397277644251) | `0698dc9` (2026-07-08) | Rust crate `anylist_rs` 0.4.0 | Broad Rust client with shopping, recipes, stores, categories, meal planning, photos, and realtime support |
| [`ozonejunkieau/pyanylist`](https://github.com/ozonejunkieau/pyanylist/tree/c34ae722dd3daf87430bf4f4de56c6552b2a0006) | `c34ae72` (2026-03-17) | PyPI `pyanylist` 0.0.6 | Python/PyO3 bindings over `anylist_rs`; public stub exposes a deliberately smaller Python surface |

The comparison uses the checked-in source at those revisions. README feature lists are treated as secondary evidence because they can lag behind implementation; for example, the `anylist_rs` README still lists realtime sync as a future feature even though its current source contains a full realtime module.

## Feature coverage

Legend: **Yes** = a public, generally usable API is present; **Partial** = some underlying data/protocol support exists but the domain is incomplete or substantially narrower; **No** = no comparable public SDK surface was found at the audited revision.

| Capability | This project | PyPI `anylist` | npm `anylist` | `anylist_rs` | `pyanylist` |
| --- | :---: | :---: | :---: | :---: | :---: |
| Shopping-list read | **Yes** | Yes | Yes | Yes | Yes |
| Shopping-list create / rename / delete | **Yes** | No | **No** | Yes | Yes |
| Shopping-item CRUD and bulk operations | **Yes** | No | Yes | Yes | Partial |
| List folders and hierarchy | **Yes** | No | No | Partial¹ | No |
| Stores and store filters | **Yes** | No | No | Yes | No |
| Per-list categories / category groups | **Yes** | No | Partial | Yes | No |
| User categories and learned categorization memory | **Yes** | No | No | No | No |
| Favorites / starter lists | **Yes** | No | Read-oriented | Yes | Yes |
| Recents and ordered starter-list state | **Yes** | No | Partial | No | No |
| Recipe CRUD | **Yes** | No | Yes | Yes | Yes |
| Recipe collections | **Yes** | No | Yes | Yes | No |
| Recipe photos upload / download | **Yes** | No | No | Yes | No |
| Meal-plan event CRUD | **Yes** | No | Yes | Yes | No² |
| Meal-plan labels and per-event list items | **Yes** | No | Partial | Partial | No |
| Meal-plan templates and template groups | **Yes** | No | No | No | No |
| iCalendar export management | **Yes** | No | No | Yes | Yes |
| List settings | **Yes** | No | No | Partial¹ | No |
| Mobile-app settings | **Yes** | No | No | No | No |
| Sharing operations and shared-user state | **Yes** | No | No | Read-only state | No |
| Account and subscription data | **Yes** | Minimal auth state | Minimal auth state | Minimal auth state | Minimal auth state |
| Native remote config / UPC / place / image lookup | **Yes** | No | No | No | No |
| Official-style autocomplete | **Yes** | No | No | No | No |
| Official-style grocery categorization | **Yes** | No | No | No | No |
| Quantity / package / ingredient parsing | **Yes** | No | No | No | No |
| Derived totals and client display semantics | **Yes** | No | No | No | No |
| Aggregate user-data synchronization | **Yes** | Full fetch | Yes | Full fetch | Through Rust core |
| Incremental timestamp-based aggregate sync | **Yes** | No | No comparable public API | No comparable public API | No comparable public API |
| WebSocket realtime invalidation | **Yes** | No | Yes | Yes | Yes |
| Reconnect / heartbeat behavior | **Yes** | No | Yes | Yes | Yes |
| Durable operation journal and replay | **Yes** | No | No | No | No |
| Deferred/batched operation queues | **Yes** | No | No comparable public API | No comparable public API | No comparable public API |
| Typed Python API | **Yes** | Partial | N/A | N/A | Yes |
| Pure-Python install, no native extension | **Yes** | Yes | N/A | N/A | No |
| Python 3.11 support | **Yes** | No (3.13+) | N/A | N/A | No (3.12+) |
| Raw protobuf / proven-handler escape hatch | **Yes** | Generated protobuf module | Internal protobuf use | Internal protobuf use | No comparable Python API |
| Protocol-wide endpoint/handler inventory | **Yes** | No | No | No | No |
| Public conformance matrix for SDK callables | **Yes** | No | No | No | No |
| Guarded real-account mutation verification | **Yes** | No mutation surface | Limited tests | Integration/tests exist | Integration tests exist |

¹ `anylist_rs` contains lower-level folder/settings operation builders used as part of list deletion, but no general folder or list-settings service comparable to this project's high-level APIs was found.

² `pyanylist` exposes iCalendar meal-plan export management but its checked-in Python type stub does not expose meal-plan event CRUD even though the underlying Rust project does.

## Protocol and verification depth

This project publishes detailed evidence for reverse-engineered behavior so protocol and implementation claims can be audited.

At the current release-candidate checkpoint:

- **72** method-aware endpoint rows are classified: 54 implemented and 18 intentionally excluded, with zero unknown rows.
- **202** proven operation handlers are classified: 195 implemented and 7 intentionally excluded, with zero unknown rows.
- The embedded protocol contains **156 protobuf messages**; the independently extracted Android schema matches all 156 message names.
- The default offline/local suite contains **530 passing tests**.
- The current read-only real-service suite is **12/12 passing**.
- The guarded live mutation suite is **48 passing with 1 safely skipped without writing** at its latest complete run.
- Public callables are checked against the conformance matrix, generated protobuf typing is checked for drift, and the package is tested across Python 3.11–3.14.

The detailed evidence for those numbers lives in [`conformance.md`](conformance.md) and [`protocol-coverage.md`](protocol-coverage.md).

## Performance comparison

The September 2026 audit also measured the public clients on the same machine,
account, and network. AnyList round-trip time dominates the measured refresh paths,
so the table mainly reflects end-to-end network-path latency.

Audited versions:

- this project: `1.0.0` release-candidate tree;
- PyPI `anylist`: `0.0.1rc1`;
- npm `anylist`: `0.8.6`;
- `pyanylist`: `0.0.6`, which executes the `anylist_rs` Rust core through PyO3.

Five interleaved rounds were run per client to reduce ordering/network bias. Each
round measured a fresh authentication plus the first high-level account/list load,
then five subsequent high-level refresh/list-fetch calls. The warm table therefore
contains 25 samples per client. Static AnyList grocery tag data was disabled for
this project's cold load because the other clients do not load that separate UI
resource as part of their list-fetch path.

| Client | Cold auth + first load, median | Warm high-level fetch/refresh, median | Warm p95 | Cached list access |
| --- | ---: | ---: | ---: | ---: |
| **This project** | **0.829 s** | **145.6 ms** | 221.0 ms | **0.125 µs** |
| PyPI `anylist` 0.0.1rc1 | 0.723 s | 153.2 ms | **201.2 ms** | not separately measured |
| npm `anylist` 0.8.6 | 0.878 s | 165.3 ms | 310.3 ms | 0.361 µs |
| `pyanylist` 0.0.6 | 1.091 s | 153.8 ms | 224.6 ms | no equivalent cached accessor benchmarked |

In these runs, this project's timestamp-based incremental refresh had the lowest
median warm latency in the measured set while synchronizing the broader account
state. The minimal PyPI `anylist` release candidate had the fastest cold median.
Cached access was negligible for both this project and the npm client, with
`lists.all()` measuring about three times faster than `getLists(false)` in this
microbenchmark.

Direct `anylist_rs` was not timed independently because the benchmark host did not
have a Rust toolchain installed. `pyanylist` uses the `anylist_rs` core through
PyO3 and is therefore listed separately from the native Rust crate.

Network conditions, account size, AnyList server behavior, and upstream client
changes can move these numbers, so the benchmark should be rerun when the ecosystem
comparison is refreshed.

## Why the gap is large

Most existing AnyList libraries understandably focus on the operations an integration immediately needs: authenticate, read shopping lists, mutate items, recipes, and perhaps meal planning. That is enough for many applications.

This project treats the official clients themselves as an executable specification and keeps going until the useful protocol and client behavior are accounted for. Coverage includes server calls, incremental timestamps, operation queues, Favorites/Recents semantics, settings fallbacks, grocery categorization, autocomplete, quantity and ingredient parsing, derived values, themes/assets, native-only lookup services, and realtime catch-up behavior.

That is what the README means by **"the last AnyList client you need."** The feature matrix and protocol/conformance evidence above are the basis for that positioning.

## Reproducing or updating this audit

The comparison should be treated as time-sensitive. Before repeating the claim after a substantial period, re-check the upstream projects and add any new general-purpose AnyList client that has appeared.

Useful discovery queries include GitHub repositories with `anylist` in the name or description, PyPI projects containing `anylist`, and npm packages tagged for AnyList/grocery use. For each serious candidate, inspect its public API and source directly; README feature lists can be stale.

If another public client catches up or exceeds this project's surface in a category, this document should say so plainly.
