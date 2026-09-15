# Textual example client

`examples/anylist_tui.py` is a user-facing frontend built on the SDK. It stays outside the installable `aioanylist` package so library users do not pull in application code.

## Install and run

```bash
python -m pip install -e '.[tui]'
python examples/anylist_tui.py
```

## Session behavior

On the first run, the launcher asks for the AnyList email and password. The password is used only for `/auth/token` sign-in and is never written to disk.

The current email/access-token/refresh-token bundle is stored in:

```text
~/.config/aioanylist/tui-tokens.json
```

The file is written with mode `0600` where supported. When AnyList rotates the token pair, the TUI persists the newest pair so a later launch can continue without another password prompt.

Useful session commands:

```bash
python examples/anylist_tui.py --login   # ignore the cached session and authenticate again
python examples/anylist_tui.py --logout  # remove the cached local session and exit
```

## Navigation

The main tabs are:

- **Lists** — shopping lists and items.
- **Recipes** — collections, recipe browsing, details, and editing.
- **Meal Plan** — Planner, Queue & Favorites, and Templates.

Global shortcuts:

| Key | Action |
|---|---|
| `1` | Lists |
| `2` | Recipes |
| `3` | Meal Plan |
| `r` | Refresh synchronized data |
| `i` | Account/client information |
| `q` | Quit |
| `Esc` | Close/cancel the active modal |
| `Ctrl+Enter` | Submit the active edit form |

Data tables update their dependent pane as the highlighted row changes, so keyboard-only navigation does not require pressing Enter to preview or select another list, recipe, meal, or template.

## Lists

The Lists tab supports normal create/edit/check/remove workflows plus AnyList-style item autocomplete. Selecting a Favorite or Recent suggestion explicitly reuses its saved metadata; typing the same text without selecting a suggestion creates a fresh item, matching the official add flow.

**List Settings…** contains:

- stores and categories;
- Favorites/Recent/custom saved-item lists;
- folders;
- behavior that directly affects the terminal client, such as autocomplete, remembered categories, completed-item visibility, categories, and store-name visibility.

Item and saved-item photos can be supplied as a local image path or an HTTP(S) image URL.

## Recipes

Recipes can be browsed by All Recipes, custom collections, source smart collections, and Not in a Collection. The details pane shows recipe metadata, photos, notes, ingredients, directions, nutrition, source information, and collection membership.

The editor supports pasted ingredient/direction text, headings, servings, rating, prep/cook time, nutrition, source fields, and photos.

## Meal Plan

The Planner shows one week at a time. Meals can be recipe-backed or free-form notes and can be moved to Queue or saved as Favorites.

Queue & Favorites provides a staging area for reusable/someday meals and can schedule entries onto the selected planner date.

Templates support nested groups, multiple days, recipe/note entries, per-entry items, and applying an entire template starting from a selected date.

## Photos

The terminal UI manages photo references/uploads but does not attempt terminal-specific inline image rendering. Existing image URLs remain visible in detail/edit views.

## UI actions not included

The terminal client leaves out actions with external or hard-to-reverse effects, including sharing/email, Alexa linking, recipe web import, and account-name changes.

## Troubleshooting

If AnyList rejects a cached session as no longer valid, the client prompts for a new sign-in. A transient network/protocol failure does **not** discard the cached session or pretend that the password is the problem.

To force a clean authentication attempt:

```bash
python examples/anylist_tui.py --login
```

To remove the local cached session completely:

```bash
python examples/anylist_tui.py --logout
```
