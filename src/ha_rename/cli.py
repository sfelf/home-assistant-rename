"""Command-line interface for ha-rename."""

import argparse
import asyncio
import sys
from collections.abc import Callable

import shtab
import websockets

from ha_rename import __version__, client, credentials
from ha_rename.rename import check_prefix_separators, plan_renames, validate_id_prefix

DEFAULT_URL = "ws://homeassistant.local:8123/api/websocket"

DESCRIPTION = """\
Bulk-rename Home Assistant entities via the WebSocket API.

Every entity whose ID (the part after "<domain>.") starts with --old-id-prefix
has that prefix replaced with --new-id-prefix. If the entity's name starts with
--entity-name-prefix, that text is removed from the name as well.

By default the changes are applied. Use --dry-run first to see what would change.
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

  Run the same command without --dry-run to apply it.

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

    required = parser.add_argument_group("required arguments")
    required.add_argument(
        "--old-id-prefix",
        required=True,
        metavar="PREFIX",
        type=_id_prefix(allow_empty=False),
        help="entity ID prefix to match and replace, e.g. skyvane_skyvane_station_backyard_station_",
    )
    required.add_argument(
        "--new-id-prefix",
        required=True,
        metavar="PREFIX",
        type=_id_prefix(allow_empty=True),
        help="replacement entity ID prefix, e.g. skyvane_station_; must end with '_' if "
        '--old-id-prefix does (may be "" to just remove the old prefix)',
    )
    required.add_argument(
        "--entity-name-prefix",
        required=True,
        metavar="TEXT",
        help='text to remove from the start of matching entity names, e.g. "Backyard Station"',
    )

    parser.add_argument(
        "--ha-url",
        default=DEFAULT_URL,
        metavar="URL",
        help=f"Home Assistant WebSocket API URL (default: {DEFAULT_URL}); "
        "use wss://<host>/api/websocket for HTTPS",
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


async def run(
    url: str,
    token: str,
    args: argparse.Namespace,
    on_authenticated: Callable[[], None] | None = None,
) -> int:
    """Connect, plan and (unless dry run) apply the renames. Returns the exit code."""
    async with client.connect(url, token) as ha:
        if on_authenticated:
            on_authenticated()

        renames = plan_renames(
            await ha.list_entities(),
            args.old_id_prefix,
            args.new_id_prefix,
            args.entity_name_prefix,
        )
        print(f"Found {len(renames)} matching entities\n")

        failures = 0
        for rename in renames:
            print("\n".join(rename.describe()))
            if args.dry_run or not rename.has_changes:
                continue
            try:
                await ha.call(rename.update_payload())
            except client.CommandError as e:
                failures += 1
                print(f"  ERROR: {e}")

    if args.dry_run:
        print("\nDry run only; nothing changed.")
        return 0
    if failures:
        print(f"\nDone with {failures} error(s).")
        return 1
    print("\nDone.")
    return 0


def _error(message: str) -> int:
    print(f"ha-rename: error: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        check_prefix_separators(args.old_id_prefix, args.new_id_prefix)
    except ValueError as e:
        parser.error(str(e))
    url = args.ha_url

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
        return asyncio.run(run(url, token, args, on_authenticated=save))
    except client.AuthenticationError as e:
        hint = "" if entered else " (the stored token may have expired; rerun with --reset-token)"
        return _error(f"Home Assistant rejected the access token: {e}{hint}")
    except credentials.CredentialError as e:
        return _error(str(e))
    except client.HomeAssistantError as e:
        return _error(str(e))
    except (OSError, websockets.exceptions.WebSocketException) as e:
        return _error(f"could not connect to {url}: {e}")
    except KeyboardInterrupt:
        return 130
