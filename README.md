<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.svg">
  <img alt="Home Assistant Rename" src="assets/banner-light.svg" width="100%">
</picture>

---

[![Release](https://img.shields.io/github/v/release/sfelf/home-assistant-rename?sort=semver)](https://github.com/sfelf/home-assistant-rename/releases/latest)
[![Tests](https://github.com/sfelf/home-assistant-rename/actions/workflows/tests.yml/badge.svg)](https://github.com/sfelf/home-assistant-rename/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/sfelf/home-assistant-rename/graph/badge.svg)](https://codecov.io/gh/sfelf/home-assistant-rename)
[![Python 3.11 | 3.12 | 3.13 | 3.14](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/sfelf/home-assistant-rename/blob/main/pyproject.toml)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](LICENSE)

Bulk-rename [Home Assistant](https://www.home-assistant.io/) entities from the command line.

Some integrations create entities with long, repetitive IDs and names. For example,
a weather-station integration can produce entities like `sensor.skyvane_skyvane_station_backyard_station_temperature`
named "Backyard Station Temperature". Renaming dozens of these by hand in the UI is tedious.
`ha-rename` does it in one command through Home Assistant's WebSocket API.

## What it does

For every entity in the entity registry whose ID (the part after `<domain>.`) starts
with `--old-id-prefix`:

1. **Entity ID:** the old prefix is replaced with `--new-id-prefix`.
   `sensor.skyvane_skyvane_station_backyard_station_temperature` → `sensor.skyvane_station_temperature`
2. **Name:** if the entity's name starts with `--entity-name-prefix`, that text is
   removed (along with any whitespace after it).
   `Backyard Station Temperature` → `Temperature`
3. **References:** automations, scripts, scenes, dashboards, persons and energy
   settings that refer to a renamed entity are updated to the new ID. References the
   tool can't update, such as IDs inside templates, are listed so you can fix them by
   hand. See [Updating references](#updating-references).

Details:

- The name used is the one you set in Home Assistant if there is one, otherwise the
  name the integration supplied. Name matching is case-sensitive. Names that would
  become empty are left alone.
- Entities whose name comes from their device (`has_entity_name`) are flagged in the
  output. To change those names, rename the device in Home Assistant instead.
- Entities that don't match `--old-id-prefix` are never touched.
- If one update fails (for example because the new entity ID is already taken), the
  error is printed and the remaining entities are still processed. The exit code is
  then 1. References to an entity whose rename failed are left alone.

Changes are applied by default. **Always run with `--dry-run` first** to check what
will change. Every run that changes something also saves an
[undo file](#undoing-changes), so you can put everything back with `--undo`.

## Requirements

- Python 3.11 or newer
- A Home Assistant instance you can reach over the network
- A Home Assistant user with administrator rights (needed to edit the entity registry)

## Installation

Install a release (see [Releases](https://github.com/sfelf/home-assistant-rename/releases)
for the latest version) with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/sfelf/home-assistant-rename.git@v0.3.0
```

Or with [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/sfelf/home-assistant-rename.git@v0.3.0
```

Either command puts `ha-rename` on your `PATH`. To move to a newer release, run the
same command with the new version and `--force`, for example
`uv tool install --force git+https://github.com/sfelf/home-assistant-rename.git@v0.4.0`.

To try the latest unreleased changes instead, leave off the `@v…` suffix. Each release
also has a wheel attached, which you can install directly with
`uv tool install <wheel URL>`.

## Getting an access token

`ha-rename` signs in with a Home Assistant **long-lived access token**:

1. In Home Assistant, click your user name at the bottom of the sidebar to open your
   profile.
2. Open the **Security** tab.
3. Under **Long-lived access tokens**, click **Create token**, give it a name
   (e.g. `ha-rename`), and copy the token. Home Assistant only shows it once.

The first time you run `ha-rename` against a Home Assistant URL, it asks for the
token. Your input is hidden. Once Home Assistant accepts the token, it is saved in
your operating system's keyring (macOS Keychain, Windows Credential Manager, or the
Secret Service/KWallet on Linux) and reused on later runs. Tokens are stored per URL
under the service name `ha-rename`, so you can use several Home Assistant instances.

If a token is rejected, it is not saved. If a saved token stops working (for example
because you deleted it in Home Assistant), rerun with `--reset-token` to enter a new
one. To remove a saved token completely, delete the `ha-rename` entry in your
keyring app, or run:

```bash
uvx keyring del ha-rename ws://homeassistant.local:8123/api/websocket
```

## Usage

```
ha-rename --old-id-prefix PREFIX --new-id-prefix PREFIX --entity-name-prefix TEXT
          [--no-update-references] [--undo-file PATH] [--ha-url URL] [--dry-run]
          [--reset-token]

ha-rename --undo FILE [--undo-file PATH] [--ha-url URL] [--dry-run] [--reset-token]

ha-rename --print-completion SHELL | --version | --help
```

### Example

Preview the changes:

```bash
ha-rename --dry-run \
  --old-id-prefix skyvane_skyvane_station_backyard_station_ \
  --new-id-prefix skyvane_station_ \
  --entity-name-prefix "Backyard Station"
```

```
Found 3 matching entities

sensor.skyvane_skyvane_station_backyard_station_temperature
  -> sensor.skyvane_station_temperature
  name: 'Backyard Station Temperature' -> 'Temperature'
switch.skyvane_skyvane_station_backyard_station_heater
  -> switch.skyvane_station_heater
  name: 'Backyard Station Heater' -> 'Heater'
sensor.skyvane_skyvane_station_backyard_station_humidity
  -> sensor.skyvane_station_humidity
  name: uses device name as prefix (rename the device instead)

References to update (3):
automation 'Heater off' (id 1700000000001)
  triggers[0].entity_id: sensor.skyvane_skyvane_station_backyard_station_temperature -> sensor.skyvane_station_temperature
  actions[0].target.entity_id: switch.skyvane_skyvane_station_backyard_station_heater -> switch.skyvane_station_heater
dashboard 'Weather' (weather)
  views[0].cards[0].entity: sensor.skyvane_skyvane_station_backyard_station_temperature -> sensor.skyvane_station_temperature

Needs manual fix (2):
automation 'Heater off' (id 1700000000001)
  conditions[0].value_template: mentions sensor.skyvane_skyvane_station_backyard_station_temperature (now sensor.skyvane_station_temperature)
automation 'Garage lights' (defined in YAML)
  triggers[0].entity_id: sensor.skyvane_skyvane_station_backyard_station_temperature -> sensor.skyvane_station_temperature

Dry run only; nothing changed.
```

When you're happy with the preview, run the same command without `--dry-run`. The
output ends with where the undo file was saved:

```
Undo file: ha-rename-undo-20261011-224433.jsonl
To reverse these changes, run: ha-rename --undo ha-rename-undo-20261011-224433.jsonl

Done.
```

### Updating references

After renaming, `ha-rename` updates references to the renamed entity IDs in:

- automations created in the UI (`automations.yaml`), including the inputs of
  automations created from blueprints
- scripts created in the UI (`scripts.yaml`)
- scenes created in the UI (`scenes.yaml`)
- dashboards in storage mode (those edited in the UI)
- persons created in the UI (their tracked devices)
- the energy dashboard settings

Only values that are exactly a renamed entity ID are changed. Entity IDs that weren't
renamed are never touched, even when they share a prefix
(`sensor.skyvane_station_temperature_max` stays as it is). An action that calls a
script, such as `action: script.morning`, names the script itself rather than its
entity, so it isn't changed either. Everything else in an automation, script, scene or
dashboard stays as it was.

The output lists every update under **References to update**. Under **Needs manual
fix**, it lists references it found but won't or can't change, each with where it is
and the old and new ID:

- entity IDs inside templates or other text, such as
  `{{ states('sensor.skyvane_skyvane_station_backyard_station_temperature') }}`
- automations, scripts and scenes defined in YAML outside the three files above (for
  example in packages), YAML-mode dashboards and persons defined in YAML
- references that can't be changed where they are, such as a scene that already has an
  entry for the new ID
- groups, including group helpers. Groups are found through the members they report,
  so a group that's already unavailable when you run `ha-rename` (for example because
  none of its members exist) isn't listed.

Fixing those doesn't change the exit code. To only rename entities, without updating
or listing references, use `--no-update-references`.

Some places can't be checked at all: template entities and other helpers set up in the
UI (apart from groups), templates that build entity IDs from text, and tools outside
Home Assistant such as Node-RED. Helpers that follow a single entity, such as
derivative or utility meter sensors, are updated by Home Assistant itself.

Saving works like saving in Home Assistant's own editors:

- Saving an automation, script or scene rewrites its whole YAML file
  (`automations.yaml`, `scripts.yaml` or `scenes.yaml`), so comments and custom
  formatting in that file are lost, just as when you save in the UI editor.
- Each saved automation, script or scene is reloaded. A running automation or script is
  restarted.
- Avoid editing the same automation, script or dashboard in the UI while `ha-rename`
  runs: one of the two edits would be lost.

### Undoing changes

Every run that changes something saves an undo file listing each change sent to Home
Assistant: the entity ID and name before and after, and each updated reference with
its old and new value. It's a [JSON Lines](https://jsonlines.org/)
file, with one line added per change. By default it's
`ha-rename-undo-<date>-<time>.jsonl` in the current directory; choose another location
with `--undo-file`. The file doesn't contain your access token. Dry runs, and runs that
change nothing, don't save one.

To reverse a run, pass its undo file to `--undo` (add `--dry-run` to preview):

```bash
ha-rename --undo ha-rename-undo-20261011-224433.jsonl
```

```
Undoing 5 change(s) from ha-rename-undo-20261011-224433.jsonl

sensor.skyvane_station_humidity
  -> sensor.skyvane_skyvane_station_backyard_station_humidity
switch.skyvane_station_heater
  -> switch.skyvane_skyvane_station_backyard_station_heater
  name: 'Heater' -> 'Backyard Station Heater'
sensor.skyvane_station_temperature
  -> sensor.skyvane_skyvane_station_backyard_station_temperature
  name: 'Temperature' -> (integration's name)
dashboard 'Weather' (weather)
  views[0].cards[0].entity: sensor.skyvane_station_temperature -> sensor.skyvane_skyvane_station_backyard_station_temperature
automation 'Heater off' (id 1700000000001)
  triggers[0].entity_id: sensor.skyvane_station_temperature -> sensor.skyvane_skyvane_station_backyard_station_temperature
  actions[0].target.entity_id: switch.skyvane_station_heater -> switch.skyvane_skyvane_station_backyard_station_heater

Undo file: ha-rename-undo-20261011-224510.jsonl
To reverse these changes, run: ha-rename --undo ha-rename-undo-20261011-224510.jsonl

Done.
```

- Entities are renamed back first (newest first), then references are put back. It
  connects to the Home Assistant URL recorded in the undo file unless you pass
  `--ha-url`.
- A reference is put back only where it still has the new ID, and only if its entity got
  its old ID back. If you've changed it since, or another entity has taken the old ID,
  it's left as is and the output says so. If the target still refers to the new ID
  somewhere else (for example in an action you've moved since), that's listed too, so
  you can fix it by hand. References added after the run, for example in a new
  automation, aren't changed.
- Names are restored exactly. A name that came from the integration, shown as
  `(integration's name)`, goes back to following the integration rather than being
  fixed to a copy of the old text.
- Entities are matched by Home Assistant's internal registry ID, which doesn't change
  when an entity is renamed. Undo still finds an entity whose ID you've changed since,
  and never touches a different entity that has since taken over the same ID.
- If you've changed an entity's ID or name yourself since, your change is kept and the
  output says so. Entities, automations and other places that no longer exist are
  skipped and reported, and the exit code is 1, with `--dry-run` too, so a script can
  check before undoing.
- The undo run saves its own undo file, so you can redo the original changes.
- Undo files from `ha-rename` 0.2.0 still work. Files written by 0.3.0 and later can't
  be read by 0.2.0.
- Each change is written to the undo file before it's sent, so if a run stops partway
  (for example because the connection drops), the undo file still covers every change
  that may have been applied. Any that weren't are shown as "nothing to undo".

### Options

| Option | Required | Description |
| --- | --- | --- |
| `--old-id-prefix PREFIX` | yes, unless `--undo` | Entity ID prefix to match and replace, e.g. `skyvane_skyvane_station_backyard_station_`. Matched against the part of the ID after `<domain>.`. Lowercase letters, digits and underscores only. |
| `--new-id-prefix PREFIX` | yes, unless `--undo` | Replacement for the old prefix, e.g. `skyvane_station_`. Pass `""` to remove the old prefix. Lowercase letters, digits and underscores only. Must end with `_` exactly when `--old-id-prefix` does; otherwise the tool stops with an error, since the IDs would run together (`skyvane_stationtemperature`) or get a double underscore. |
| `--entity-name-prefix TEXT` | yes, unless `--undo` | Text to remove from the start of matching entity names, e.g. `"Backyard Station"`. Quote it if it contains spaces. |
| `--no-update-references` | no | Only rename entities: don't update or list references to them. See [Updating references](#updating-references). |
| `--undo FILE` | no | Reverse the changes recorded in an undo file instead of renaming. Can't be combined with the three prefix options or `--no-update-references`. See [Undoing changes](#undoing-changes). |
| `--undo-file PATH` | no | Where to save the undo file. Default: `ha-rename-undo-<date>-<time>.jsonl` in the current directory. An existing file is never overwritten; the run stops before changing anything. |
| `--ha-url URL` | no | Home Assistant WebSocket API URL. Default: `ws://homeassistant.local:8123/api/websocket`, or with `--undo` the URL recorded in the undo file. Use `wss://<host>/api/websocket` if Home Assistant is served over HTTPS (e.g. via Nabu Casa or a reverse proxy). |
| `--dry-run` | no | Show what would change without changing anything. |
| `--reset-token` | no | Ignore the saved access token and ask for a new one. The new token replaces the saved one once Home Assistant accepts it. |
| `--print-completion SHELL` | no | Print a tab-completion script for `bash`, `zsh`, `fish`, `tcsh` or `powershell`, then exit. |
| `--version` | no | Print the version and exit. |
| `-h`, `--help` | no | Show help, including all options and examples, then exit. |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success (or dry run completed) |
| 1 | An error occurred: connection, authentication, keyring, undo file, one or more failed updates (of entities or references), or changes skipped by `--undo` (also with `--dry-run`). References listed under "Needs manual fix" don't count. |
| 2 | Invalid command-line arguments |
| 130 | Cancelled with Ctrl-C |

## Tab completion

Generate a completion script for your shell once:

**zsh**: make sure `~/.zfunc` is in your `fpath` before `compinit` runs in `~/.zshrc`
(`fpath=(~/.zfunc $fpath)`), then:

```bash
mkdir -p ~/.zfunc && ha-rename --print-completion zsh > ~/.zfunc/_ha-rename
```

**bash** (requires the `bash-completion` package):

```bash
mkdir -p ~/.local/share/bash-completion/completions && ha-rename --print-completion bash > ~/.local/share/bash-completion/completions/ha-rename
```

**fish**:

```bash
ha-rename --print-completion fish > ~/.config/fish/completions/ha-rename.fish
```

Then open a new shell.

## License

Copyright (C) 2026 Home Assistant Rename Contributors

This program is free software: you can redistribute it and/or modify it under the
terms of the GNU Affero General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version.
See [LICENSE](LICENSE) for the full text.
