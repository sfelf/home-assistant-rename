"""A minimal Home Assistant WebSocket API client."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import websockets


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


class HomeAssistantClient:
    """Sends commands over an authenticated WebSocket connection."""

    def __init__(self, ws):
        self._ws = ws
        self._next_id = 1

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


@asynccontextmanager
async def connect(url: str, token: str) -> AsyncIterator[HomeAssistantClient]:
    """Connect to ``url`` and authenticate with ``token``."""
    async with websockets.connect(url, max_size=None) as ws:
        client = HomeAssistantClient(ws)
        await client.authenticate(token)
        yield client
