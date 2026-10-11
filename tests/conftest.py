"""Shared fixtures: an in-memory keyring and a fake Home Assistant server."""

import asyncio
import copy
import json
import threading
from typing import Any

import keyring
import pytest
from keyring.backend import KeyringBackend
from websockets.asyncio.server import serve

GOOD_TOKEN = "good-token"


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password



@pytest.fixture(autouse=True)
def memory_keyring():
    """Never touch the real system keyring during tests."""
    original = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(original)


@pytest.fixture(autouse=True)
def no_prompt(monkeypatch):
    """Fail loudly if a test prompts for a token without arranging an answer."""

    def fail(prompt=""):
        raise AssertionError(f"unexpected getpass prompt: {prompt!r}")

    monkeypatch.setattr("getpass.getpass", fail)


def entity(entity_id: str, name: str | None = None, original_name: str | None = None, **extra):
    return {
        "entity_id": entity_id,
        "name": name,
        "original_name": original_name,
        "has_entity_name": False,
        **extra,
    }


SAMPLE_ENTITIES = [
    entity("sensor.skyvane_skyvane_station_backyard_station_temperature", original_name="Backyard Station Temperature"),
    entity("switch.skyvane_skyvane_station_backyard_station_heater", name="Backyard Station Heater"),
    entity("sensor.skyvane_skyvane_station_backyard_station_humidity", original_name="Humidity", has_entity_name=True),
    entity("light.kitchen", original_name="Kitchen"),
]


class FakeHomeAssistant:
    """Speaks just enough of the Home Assistant WebSocket API for these tests."""

    def __init__(self, entities: list[dict[str, Any]], token: str = GOOD_TOKEN):
        self.entities = copy.deepcopy(entities)
        self.token = token
        self.updates: list[dict[str, Any]] = []
        self.fail_updates: set[str] = set()
        self.first_message: dict[str, Any] = {"type": "auth_required", "ha_version": "2026.10.0"}
        self.url = ""

    async def handler(self, ws):
        await ws.send(json.dumps(self.first_message))
        auth = json.loads(await ws.recv())
        if auth.get("access_token") != self.token:
            await ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token or password"}))
            return
        await ws.send(json.dumps({"type": "auth_ok", "ha_version": "2026.10.0"}))

        async for raw in ws:
            msg = json.loads(raw)
            # Interleave unrelated traffic so the client has to match replies by id.
            await ws.send(json.dumps({"id": 0, "type": "event", "event": {}}))
            await ws.send(json.dumps({"id": msg["id"], "type": "event", "event": {}}))
            await ws.send(json.dumps(self.respond(msg)))

    def respond(self, msg: dict[str, Any]) -> dict[str, Any]:
        reply = {"id": msg["id"], "type": "result"}
        if msg["type"] == "config/entity_registry/list":
            return {**reply, "success": True, "result": self.entities}
        if msg["type"] == "config/entity_registry/update":
            self.updates.append({k: v for k, v in msg.items() if k != "id"})
            if msg["entity_id"] in self.fail_updates:
                return {**reply, "success": False, "error": {"code": "invalid_info", "message": "Entity is already registered"}}
            return {**reply, "success": True, "result": {}}
        return {**reply, "success": False, "error": {"code": "unknown_command", "message": "Unknown command."}}


@pytest.fixture
def fake_ha():
    """A FakeHomeAssistant served on localhost from a background thread."""
    ha = FakeHomeAssistant(SAMPLE_ENTITIES)
    loop = asyncio.new_event_loop()
    started = threading.Event()
    stop: asyncio.Event | None = None

    async def serve_until_stopped():
        nonlocal stop
        stop = asyncio.Event()
        async with serve(ha.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ha.url = f"ws://127.0.0.1:{port}/api/websocket"
            started.set()
            await stop.wait()

    thread = threading.Thread(target=loop.run_until_complete, args=(serve_until_stopped(),), daemon=True)
    thread.start()
    assert started.wait(5), "fake Home Assistant server did not start"
    yield ha
    loop.call_soon_threadsafe(stop.set)
    thread.join(5)
    loop.close()
