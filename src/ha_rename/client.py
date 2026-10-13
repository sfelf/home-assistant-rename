"""A minimal Home Assistant API client: WebSocket commands, plus a few REST requests."""

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets

WEBSOCKET_PATH = "/api/websocket"
REST_TIMEOUT = 30


class HomeAssistantError(Exception):
    """Base class for errors talking to Home Assistant."""


class AuthenticationError(HomeAssistantError):
    """Home Assistant rejected the access token."""


class CommandError(HomeAssistantError):
    """Home Assistant returned an error result for a command."""

    def __init__(self, error: Any):
        self.error = error
        if isinstance(error, dict):
            message = f"{error.get('code', 'error')}: {error.get('message', error)}"
        else:
            message = str(error)
        super().__init__(message)


def rest_base_url(url: str) -> str:
    """The REST API base for a WebSocket API URL.

    ``ws://h:8123/api/websocket`` becomes ``http://h:8123``, and ``wss://`` becomes
    ``https://``. Any path before ``/api/websocket`` (e.g. from a reverse proxy) is kept.
    """
    parts = urlsplit(url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme)
    if scheme is None or not parts.path.endswith(WEBSOCKET_PATH):
        raise HomeAssistantError(
            f"can't work out the REST API address from {url} "
            f"(expected ws:// or wss:// and a path ending in {WEBSOCKET_PATH})"
        )
    return urlunsplit((scheme, parts.netloc, parts.path[: -len(WEBSOCKET_PATH)], "", ""))


class HomeAssistantClient:
    """Sends commands over an authenticated WebSocket connection, and REST requests."""

    def __init__(self, ws, url: str = "", token: str = ""):
        self._ws = ws
        self._next_id = 1
        self._url = url
        self._token = token

    async def authenticate(self, token: str) -> None:
        message = json.loads(await self._ws.recv())
        if message.get("type") != "auth_required":
            raise HomeAssistantError(f"Unexpected message from server: {message}")

        await self._ws.send(json.dumps({"type": "auth", "access_token": token}))
        message = json.loads(await self._ws.recv())
        if message.get("type") != "auth_ok":
            raise AuthenticationError(message.get("message") or str(message))

    async def call(self, payload: dict[str, Any]) -> Any:
        """Send a command and return its ``result``, raising CommandError on failure."""
        msg_id = self._next_id
        self._next_id += 1
        await self._ws.send(json.dumps({**payload, "id": msg_id}))
        while True:
            response = json.loads(await self._ws.recv())
            # Skip anything that isn't the reply to this command (e.g. events).
            if response.get("id") == msg_id and response.get("type") == "result":
                break
        if not response.get("success"):
            raise CommandError(response.get("error"))
        return response.get("result")

    async def list_entities(self) -> list[dict[str, Any]]:
        return await self.call({"type": "config/entity_registry/list"})

    async def rest(self, method: str, path: str, body: Any = None) -> Any:
        """Send a REST request for ``path`` (e.g. ``/api/config/...``) and return its JSON.

        Returns None if a GET is answered 404 Not Found, and raises CommandError for any
        other error status (including a 404 for a write, which saved nothing).
        """
        request = urllib.request.Request(
            rest_base_url(self._url) + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        return await asyncio.to_thread(_send, request)


def _send(request: urllib.request.Request) -> Any:
    try:
        with urllib.request.urlopen(request, timeout=REST_TIMEOUT) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        if e.code == 404 and request.get_method() == "GET":
            return None
        try:
            message = json.loads(e.read())["message"]
        except (ValueError, TypeError, KeyError):
            message = e.reason
        raise CommandError({"code": f"http_{e.code}", "message": message}) from e


@asynccontextmanager
async def connect(url: str, token: str) -> AsyncIterator[HomeAssistantClient]:
    """Connect to ``url`` and authenticate with ``token``."""
    async with websockets.connect(url, max_size=None) as ws:
        client = HomeAssistantClient(ws, url, token)
        await client.authenticate(token)
        yield client
