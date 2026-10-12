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

Details:

- The name used is the one you set in Home Assistant if there is one, otherwise the
  name the integration supplied. Name matching is case-sensitive. Names that would
  become empty are left alone.
- Entities whose name comes from their device (`has_entity_name`) are flagged in the
  output. To change those names, rename the device in Home Assistant instead.
- Entities that don't match `--old-id-prefix` are never touched.
- If one update fails (for example because the new entity ID is already taken), the
  error is printed and the remaining entities are still processed. The exit code is
  then 1.

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
uv tool install git+https://github.com/sfelf/home-assistant-rename.git@v0.2.0
```

Or with [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/sfelf/home-assistant-rename.git@v0.2.0
```

Either command puts `ha-rename` on your `PATH`. To move to a newer release, run the
same command with the new version and `--force`, for example
`uv tool install --force git+https://github.com/sfelf/home-assistant-rename.git@v0.3.0`.

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
          [--undo-file PATH] [--ha-url URL] [--dry-run] [--reset-token]

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

Dry run only; nothing changed.
```

When you're happy with the preview, run the same command without `--dry-run`. The
output ends with where the undo file was saved:

```
Undo file: ha-rename-undo-20261011-224433.json
To reverse these changes, run: ha-rename --undo ha-rename-undo-20261011-224433.json

Done.
```

### Undoing changes

Every run that changes something saves an undo file listing each change sent to Home
Assistant: the entity ID and name before and after. By default it's
`ha-rename-undo-<date>-<time>.json` in the current directory; choose another location
with `--undo-file`. The file doesn't contain your access token. Dry runs, and runs that
change nothing, don't save one.

To reverse a run, pass its undo file to `--undo` (add `--dry-run` to preview):

```bash
ha-rename --undo ha-rename-undo-20261011-224433.json
```

```
Undoing 3 change(s) from ha-rename-undo-20261011-224433.json

sensor.skyvane_station_humidity
  -> sensor.skyvane_skyvane_station_backyard_station_humidity
switch.skyvane_station_heater
  -> switch.skyvane_skyvane_station_backyard_station_heater
  name: 'Heater' -> 'Backyard Station Heater'
sensor.skyvane_station_temperature
  -> sensor.skyvane_skyvane_station_backyard_station_temperature
  name: 'Temperature' -> (integration's name)

Undo file: ha-rename-undo-20261011-224510.json
To reverse these changes, run: ha-rename --undo ha-rename-undo-20261011-224510.json

Done.
```

- Changes are reversed newest first. It connects to the Home Assistant URL recorded in
  the undo file unless you pass `--ha-url`.
- Names are restored exactly. A name that came from the integration, shown as
  `(integration's name)`, goes back to following the integration rather than being
  fixed to a copy of the old text.
- Entities are matched by Home Assistant's internal registry ID, which doesn't change
  when an entity is renamed. Undo still finds an entity whose ID you've changed since,
  and never touches a different entity that has since taken over the same ID.
- If you've changed an entity's ID or name yourself since, your change is kept and the
  output says so. Entities that no longer exist are skipped and reported, and the exit
  code is 1.
- The undo run saves its own undo file, so you can redo the original changes.
- Each change is written to the undo file before it's sent, so if a run stops partway
  (for example because the connection drops), the undo file still covers every change
  that may have been applied. Any that weren't are shown as "nothing to undo".

### Options

| Option | Required | Description |
| --- | --- | --- |
| `--old-id-prefix PREFIX` | yes, unless `--undo` | Entity ID prefix to match and replace, e.g. `skyvane_skyvane_station_backyard_station_`. Matched against the part of the ID after `<domain>.`. Lowercase letters, digits and underscores only. |
| `--new-id-prefix PREFIX` | yes, unless `--undo` | Replacement for the old prefix, e.g. `skyvane_station_`. Pass `""` to remove the old prefix. Lowercase letters, digits and underscores only. Must end with `_` exactly when `--old-id-prefix` does; otherwise the tool stops with an error, since the IDs would run together (`skyvane_stationtemperature`) or get a double underscore. |
| `--entity-name-prefix TEXT` | yes, unless `--undo` | Text to remove from the start of matching entity names, e.g. `"Backyard Station"`. Quote it if it contains spaces. |
| `--undo FILE` | no | Reverse the changes recorded in an undo file instead of renaming. Can't be combined with the three prefix options. See [Undoing changes](#undoing-changes). |
| `--undo-file PATH` | no | Where to save the undo file. Default: `ha-rename-undo-<date>-<time>.json` in the current directory. An existing file is never overwritten; the run stops before changing anything. |
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
| 1 | An error occurred: connection, authentication, keyring, undo file, one or more failed updates, or entities skipped by `--undo` |
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
