"""Store the Home Assistant access token in the system keyring."""

import getpass
import sys

import keyring
from keyring.errors import KeyringError

SERVICE_NAME = "ha-rename"


class CredentialError(Exception):
    """The token couldn't be read, stored or entered."""


def load_token(url: str) -> str | None:
    """Return the token stored for ``url``, or None if there isn't one."""
    try:
        return keyring.get_password(SERVICE_NAME, url)
    except KeyringError as e:
        raise CredentialError(f"Could not read from the system keyring: {e}") from e


def save_token(url: str, token: str) -> None:
    try:
        keyring.set_password(SERVICE_NAME, url, token)
    except KeyringError as e:
        raise CredentialError(f"Could not save to the system keyring: {e}") from e


def prompt_token(url: str) -> str:
    """Ask the user for a token without echoing it to the screen."""
    print(
        f"Enter a long-lived access token for {url}.\n"
        "Create one in Home Assistant under Profile -> Security -> Long-lived access tokens.",
        file=sys.stderr,
    )
    token = getpass.getpass("Home Assistant access token: ").strip()
    if not token:
        raise CredentialError("No token entered.")
    return token
