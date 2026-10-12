"""Command-line interface for ha-rename."""

import argparse
import asyncio
import os
import re
import shlex
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import shtab
import websockets

from ha_rename import __version__, client, credentials, undo
from ha_rename.rename import check_prefix_separators, plan_renames, validate_id_prefix
from ha_rename.undo import Change, UndoLog

DEFAULT_URL = "ws://homeassistant.local:8123/api/websocket"

DESCRIPTION = """\
Bulk-rename Home Assistant entities via the WebSocket API.

Every entity whose ID (the part after "<domain>.") starts with --old-id-prefix
has that prefix replaced with --new-id-prefix. If the entity's name starts with
--entity-name-prefix, that text is removed from the name as well.

By default the changes are applied. Use --dry-run first to see what would change.
Every run that changes something saves an undo file; reverse that run with
--undo FILE.
"""

EPILOG = f"""\
access token:
  The first time you connect to a Home Assistant URL you are asked for a
  long-lived access token (input is hidden). Create one in Home Assistant under
  Profile -> Security -> Long-lived access tokens. Once Home Assistant accepts
  it, the token is saved in your system keyring (service "{credentials.SERVICE_NAME}",
  one entry per URL) and reused on later runs. Use --reset-token to replace it.

examples:
  Preview renaming sensor.skyvane_skyvane_station_backyard_station_temperature to
  sensor.skyvane_station_temperature, and "Backyard Station Temperature" to "Temperature":

    ha-rename --dry-run \\
      --old-id-prefix skyvane_skyvane_station_backyard_station_ \\
      --new-id-prefix skyvane_station_ \\
      --entity-name-prefix "Backyard Station"

  Run the same command without --dry-run to apply it. It saves an undo file such
  as ha-rename-undo-20261011-224433.jsonl; to reverse those changes:

    ha-rename --undo ha-rename-undo-20261011-224433.jsonl

shell completion:
  zsh:   ha-rename --print-completion zsh > ~/.zfunc/_ha-rename
  bash:  ha-rename --print-completion bash > ~/.local/share/bash-completion/completions/ha-rename
  fish:  ha-rename --print-completion fish > ~/.config/fish/completions/ha-rename.fish
"""


def _id_prefix(*, allow_empty: bool) -> Callable[[str], str]:
    def parse(value: str) -> str:
        try:
            return validate_id_prefix(value, allow_empty=allow_empty)
        except ValueError as e:
            raise argparse.ArgumentTypeError(str(e)) from e

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ha-rename",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    required = parser.add_argument_group("rename options (required unless --undo is used)")
    required.add_argument(
        "--old-id-prefix",
        metavar="PREFIX",
        type=_id_prefix(allow_empty=False),
        help="entity ID prefix to match and replace, "
        "e.g. skyvane_skyvane_station_backyard_station_",
    )
    required.add_argument(
        "--new-id-prefix",
        metavar="PREFIX",
        type=_id_prefix(allow_empty=True),
        help="replacement entity ID prefix, e.g. skyvane_station_; must end with '_' if "
        '--old-id-prefix does (may be "" to just remove the old prefix)',
    )
    required.add_argument(
        "--entity-name-prefix",
        metavar="TEXT",
        help='text to remove from the start of matching entity names, e.g. "Backyard Station"',
    )

    undo_options = parser.add_argument_group("undo")
    undo_options.add_argument(
        "--undo",
        metavar="FILE",
        type=Path,
        help="reverse the changes recorded in an undo file, instead of renaming",
    ).complete = shtab.FILE
    undo_options.add_argument(
        "--undo-file",
        metavar="PATH",
        type=Path,
        help="where to save the undo file (default: ha-rename-undo-<date>-<time>.jsonl "
        "in the current directory)",
    ).complete = shtab.FILE

    parser.add_argument(
        "--ha-url",
        metavar="URL",
        help=f"Home Assistant WebSocket API URL (default: {DEFAULT_URL}, or with --undo "
        "the URL recorded in the undo file); use wss://<host>/api/websocket for HTTPS",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without changing anything",
    )
    parser.add_argument(
        "--reset-token",
        action="store_true",
        help="ignore the stored access token and prompt for a new one",
    )
    shtab.add_argument_to(
        parser,
        "--print-completion",
        help="print a shell completion script and exit",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


RENAME_OPTIONS = {
    "--old-id-prefix": "old_id_prefix",
    "--new-id-prefix": "new_id_prefix",
    "--entity-name-prefix": "entity_name_prefix",
}


def check_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Checks argparse can't express: the rename options are required unless --undo."""
    given = [opt for opt, attr in RENAME_OPTIONS.items() if getattr(args, attr) is not None]
    if args.undo is not None:
        if given:
            parser.error(f"--undo can't be combined with {', '.join(given)}")
        return
    missing = [opt for opt in RENAME_OPTIONS if opt not in given]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    try:
        check_prefix_separators(args.old_id_prefix, args.new_id_prefix)
    except ValueError as e:
        parser.error(str(e))


_PLAIN = re.compile(r"[A-Za-z0-9_./:-]+")
_PLAIN_WINDOWS = re.compile(r"[A-Za-z0-9_./:\\-]+")
# Characters cmd.exe or PowerShell still interpret inside double quotes.
_UNQUOTABLE_ON_WINDOWS = set('%!$`"')


def shell_quote(value: str, *, windows: bool = os.name == "nt") -> str | None:
    """Quote ``value`` so it's safe to paste into a shell, or None if that isn't possible.

    POSIX quoting handles anything. On Windows no quoting is safe in both cmd.exe and
    PowerShell for every character, so double quotes are used where they are, and None
    is returned otherwise.
    """
    if (_PLAIN_WINDOWS if windows else _PLAIN).fullmatch(value):
        return value
    if not windows:
        return shlex.quote(value)
    if _UNQUOTABLE_ON_WINDOWS.intersection(value):
        return None
    return f'"{value}"'


def default_undo_path() -> Path:
    """A timestamped file name in the current directory, numbered if already taken."""
    stem = f"ha-rename-undo-{datetime.now():%Y%m%d-%H%M%S}"
    path = Path(f"{stem}.jsonl")
    n = 1
    while path.exists():
        n += 1
        path = Path(f"{stem}-{n}.jsonl")
    return path


async def run(
    url: str,
    token: str,
    args: argparse.Namespace,
    undo_file: undo.UndoFile | None = None,
    on_authenticated: Callable[[], None] | None = None,
) -> int:
    """Connect, plan and (unless dry run) apply the changes. Returns the exit code."""
    async with client.connect(url, token) as ha:
        if on_authenticated:
            on_authenticated()
        entries = await ha.list_entities()

        failures = 0
        items: list[tuple[list[str], Change | None]]
        if undo_file is not None:
            steps = undo.plan_undo(entries, undo_file.changes)
            print(f"Undoing {len(steps)} change(s) from {undo_file.path}\n")
            items = [(step.describe(), step.change) for step in steps]
            failures = sum(step.change is None for step in steps)
        else:
            renames = plan_renames(
                entries, args.old_id_prefix, args.new_id_prefix, args.entity_name_prefix
            )
            print(f"Found {len(renames)} matching entities\n")
            items = [(rename.describe(), rename.to_change()) for rename in renames]

        failures += await _apply(ha, items, url, args)

    if args.dry_run:
        print("\nDry run only; nothing changed.")
        return 0
    if failures:
        print(f"\nDone with {failures} error(s).")
        return 1
    print("\nDone.")
    return 0


async def _apply(
    ha: client.HomeAssistantClient,
    items: list[tuple[list[str], Change | None]],
    url: str,
    args: argparse.Namespace,
) -> int:
    """Print each item and, unless dry run, apply it and record it. Returns the failures."""
    pending = [change for _, change in items if change is not None and change.has_changes]
    log = None
    if pending and not args.dry_run:
        log = UndoLog.create(args.undo_file or default_undo_path(), url)

    failures = 0
    try:
        for lines, change in items:
            print("\n".join(lines))
            if log is None or change is None or not change.has_changes:
                continue
            # Record first: if the reply is lost, the change may still have been applied.
            log.record(change)
            try:
                await ha.call(change.payload())
            except client.CommandError as e:
                log.forget(change)
                failures += 1
                print(f"  ERROR: {e}")
    finally:
        if log is not None and not log.discard_if_empty():
            print(f"\nUndo file: {log.path}")
            quoted = shell_quote(str(log.path))
            if quoted is None:
                print("To reverse these changes, pass that file to: ha-rename --undo")
            else:
                print(f"To reverse these changes, run: ha-rename --undo {quoted}")
    return failures


def _error(message: str) -> int:
    print(f"ha-rename: error: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    check_args(parser, args)

    undo_file = None
    if args.undo is not None:
        try:
            undo_file = undo.load(args.undo)
        except undo.UndoFileError as e:
            return _error(str(e))
        if not undo_file.changes:
            print(f"Nothing to undo: {args.undo} has no recorded changes.")
            return 0
        url = args.ha_url or undo_file.ha_url
        if url != undo_file.ha_url:
            print(
                f"Note: {args.undo} was recorded against {undo_file.ha_url}; using {url}.",
                file=sys.stderr,
            )
    else:
        url = args.ha_url or DEFAULT_URL

    try:
        token = None if args.reset_token else credentials.load_token(url)
        entered = token is None
        if entered:
            token = credentials.prompt_token(url)
    except credentials.CredentialError as e:
        return _error(str(e))
    except (KeyboardInterrupt, EOFError):
        print(file=sys.stderr)
        return 130

    def save() -> None:
        if entered:
            credentials.save_token(url, token)

    try:
        return asyncio.run(run(url, token, args, undo_file=undo_file, on_authenticated=save))
    except client.AuthenticationError as e:
        hint = "" if entered else " (the stored token may have expired; rerun with --reset-token)"
        return _error(f"Home Assistant rejected the access token: {e}{hint}")
    except (credentials.CredentialError, undo.UndoFileError) as e:
        return _error(str(e))
    except client.HomeAssistantError as e:
        return _error(str(e))
    except (OSError, websockets.exceptions.WebSocketException) as e:
        return _error(f"connection to {url} failed: {e}")
    except KeyboardInterrupt:
        return 130
