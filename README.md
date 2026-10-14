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

Some integrations create entities with long, repetitive IDs and names, such as
`sensor.skyvane_skyvane_station_backyard_station_temperature` named "Backyard Station
Temperature". `ha-rename` fixes dozens of them in one command through Home Assistant's
WebSocket API.

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
- If an update fails (for example because the new entity ID is already taken), the
  error is printed and the remaining entities are still processed. References to an
  entity whose rename failed are left alone.

Changes are applied by default. **Always run with `--dry-run` first** to check what
will change. Every run that changes something also saves an
[undo file](#undoing-changes), so you can put everything back with `--undo`.

## Requirements

- Python 3.11 or newer
- A Home Assistant instance you can reach over the network
- A Home Assistant user with administrator rights (needed to edit the entity registry)

## Installation

Install with [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/):

```bash
uv tool install git+https://github.com/sfelf/home-assistant-rename.git
```

```bash
pipx install git+https://github.com/sfelf/home-assistant-rename.git
```

Either puts `ha-rename` on your `PATH`. To install a specific
[release](https://github.com/sfelf/home-assistant-rename/releases), add its tag to the
URL (`...home-assistant-rename.git@vX.Y.Z`). To upgrade, rerun the command with
`--force`. Each release also has a wheel attached, which `uv tool install <wheel URL>`
can install.

## Getting an access token

`ha-rename` signs in with a Home Assistant **long-lived access token**:

1. In Home Assistant, click your user name at the bottom of the sidebar to open your
   profile.
2. Open the **Security** tab.
3. Under **Long-lived access tokens**, click **Create token**, give it a name
   (e.g. `ha-rename`), and copy the token. Home Assistant only shows it once.

The first time you run `ha-rename` against a Home Assistant URL, it asks for the token
(input is hidden). Once Home Assistant accepts it, it's saved in your operating
system's keyring (macOS Keychain, Windows Credential Manager, or Secret
Service/KWallet on Linux) under the service name `ha-rename`, one per URL, so you can
use several instances. Rejected tokens aren't saved.

If a saved token stops working, rerun with `--reset-token`. To remove one, delete the
`ha-rename` entry in your keyring app or run
`uvx keyring del ha-rename ws://homeassistant.local:8123/api/websocket`.

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
  open: http://homeassistant.local:8123/config/automation/edit/1700000000001
automation 'Garage lights' (defined in YAML)
  triggers[0].entity_id: sensor.skyvane_skyvane_station_backyard_station_temperature -> sensor.skyvane_station_temperature
  fix in the YAML file that defines it
  view: http://homeassistant.local:8123/config/automation/show/automation.garage_lights

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

Only values that are exactly a renamed entity ID are changed. IDs that merely share a
prefix (`sensor.skyvane_station_temperature_max`) and script calls such as
`action: script.morning` are left alone, as is everything else in the config.

Saving works like saving in Home Assistant's own editors:

- Saving an automation, script or scene rewrites its whole YAML file, so comments and
  custom formatting in that file are lost.
- Each saved automation, script or scene is reloaded, restarting it if it's running.
- Don't edit the same automation, script or dashboard in the UI while `ha-rename` runs:
  one of the two edits would be lost.

**Needs manual fix** lists references the tool found but won't or can't change, with
the old and new ID and where to fix it:

- entity IDs inside templates or other text, such as
  `{{ states('sensor.skyvane_skyvane_station_backyard_station_temperature') }}`
- automations, scripts and scenes defined in YAML outside the three files above (for
  example in packages), YAML-mode dashboards and persons defined in YAML
- references that can't be changed where they are, such as a scene that already has an
  entry for the new ID
- groups, including group helpers

Not checked at all: template entities and other UI helpers (apart from groups),
templates that build entity IDs from text, and tools outside Home Assistant such as
Node-RED. Helpers that follow a single entity, such as derivative or utility meter
sensors, are updated by Home Assistant itself.

<details>
<summary>More about manual-fix entries</summary>

Each entry ends with where to fix it:

- `open:` links to the page in Home Assistant where you make the fix: the automation,
  script, scene or person editor, a dashboard in edit mode, the energy settings, or the
  helpers list for a group helper.
- `fix in the YAML file that defines it` for anything defined in YAML. For YAML
  automations, scripts and dashboards, `view:` links to their read-only page. Home
  Assistant doesn't say which file defines them.
- `managed by the <name> integration` for an entity whose members come from that
  integration (such as a light group from a hub), which has no page to fix it in.

Links use the same address as `--ha-url` (`ws://` becomes `http://`, `wss://` becomes
`https://`), not Home Assistant's external URL. If `--ha-url`'s path doesn't end in
`/api/websocket`, links are left out. Current Home Assistant releases open a scene's
editor in review mode, but older ones apply the scene as soon as its editor opens.

Groups are found through the members they report, so a group that's already
unavailable (for example because none of its members exist) isn't listed.

</details>

### Undoing changes

Every run that changes something saves a [JSON Lines](https://jsonlines.org/) undo
file recording each change: entity IDs and names before and after, and each updated
reference. It doesn't contain your access token. Dry runs and runs that change nothing
don't save one.

To reverse a run, pass its undo file to `--undo` (add `--dry-run` to preview). The
output uses the same format as a rename:

```bash
ha-rename --undo ha-rename-undo-20261011-224433.jsonl
```

- Entities are matched by Home Assistant's internal registry ID, so undo still finds
  an entity you've renamed since, and never touches a different entity that has taken
  over its old ID.
- If you've changed an entity's ID or name since the run, your change is kept and the
  output says so. Entities, automations and other places that no longer exist are
  skipped and reported.
- References are put back by their position in the config (such as
  `actions[0].target.entity_id`), wherever that position still has the new ID. If
  you've added or reordered actions since, whatever is now at that position is changed,
  so check the output: it lists anywhere else the new ID is still used.
- The undo run saves its own undo file, so you can redo the original changes.
- Each change is written to the undo file before it's sent, so a run that stops partway
  (for example because the connection drops) can still be undone. Changes that never
  got applied show as "nothing to undo".

<details>
<summary>More about undo</summary>

- Entities are renamed back first (newest first), then references are put back.
- A reference is put back only if its entity got its old ID back. References added
  elsewhere after the run, for example in a new automation, aren't changed.
- Names are restored exactly. A name that came from the integration, shown as
  `(integration's name)`, goes back to following the integration rather than being
  fixed to a copy of the old text.
- Undo files from `ha-rename` 0.2.0 still work. Files written by 0.3.0 and later can't
  be read by 0.2.0.

</details>

### Options

| Option | Required | Description |
| --- | --- | --- |
| `--old-id-prefix PREFIX` | yes, unless `--undo` | Entity ID prefix to match and replace, e.g. `skyvane_skyvane_station_backyard_station_`. Matched against the part of the ID after `<domain>.`. Lowercase letters, digits and underscores only. |
| `--new-id-prefix PREFIX` | yes, unless `--undo` | Replacement for the old prefix, e.g. `skyvane_station_`, or `""` to remove it. Lowercase letters, digits and underscores only. Must end with `_` exactly when `--old-id-prefix` does. |
| `--entity-name-prefix TEXT` | yes, unless `--undo` | Text to remove from the start of matching entity names, e.g. `"Backyard Station"`. Quote it if it contains spaces. |
| `--no-update-references` | no | Only rename entities: don't update or list references to them. |
| `--undo FILE` | no | Reverse the changes recorded in an undo file instead of renaming. Can't be combined with the three prefix options or `--no-update-references`. |
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
| 1 | An error occurred: connection, authentication, keyring, undo file, one or more failed updates (of entities or references), or changes skipped by `--undo` (also with `--dry-run`, so a script can check before undoing). References listed under "Needs manual fix" don't count. |
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
