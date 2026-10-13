"""Shared fixtures: an in-memory keyring and a fake Home Assistant server."""

import asyncio
import copy
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote

import keyring
import pytest
from keyring.backend import KeyringBackend
from websockets.asyncio.server import serve

from ha_rename import client

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


@pytest.fixture(autouse=True)
def in_tmp_dir(tmp_path, monkeypatch):
    """Run each test in its own directory, so undo files never land in the repo."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def entity(entity_id: str, name: str | None = None, original_name: str | None = None, **extra):
    return {
        # Home Assistant's registry id: fixed for the entry's lifetime, unlike entity_id.
        "id": f"reg-{entity_id}",
        "entity_id": entity_id,
        "name": name,
        "original_name": original_name,
        "has_entity_name": False,
        **extra,
    }


SAMPLE_ENTITIES = [
    entity(
        "sensor.skyvane_skyvane_station_backyard_station_temperature",
        original_name="Backyard Station Temperature",
    ),
    entity(
        "switch.skyvane_skyvane_station_backyard_station_heater", name="Backyard Station Heater"
    ),
    entity(
        "sensor.skyvane_skyvane_station_backyard_station_humidity",
        original_name="Humidity",
        has_entity_name=True,
    ),
    entity("light.kitchen", original_name="Kitchen"),
]


def state(entity_id: str, friendly_name: str | None = None, /, **attributes):
    """An entry of ``get_states``."""
    if friendly_name is not None:
        attributes["friendly_name"] = friendly_name
    return {"entity_id": entity_id, "state": "on", "attributes": attributes}


class FakeHomeAssistant:
    """Speaks just enough of the Home Assistant WebSocket and REST APIs for these tests."""

    def __init__(self, entities: list[dict[str, Any]], token: str = GOOD_TOKEN):
        self.entities = copy.deepcopy(entities)
        self.token = token
        self.updates: list[dict[str, Any]] = []
        self.fail_updates: set[str] = set()
        self.drop_after_update: set[str] = set()  # apply, then close without replying
        self.first_message: dict[str, Any] = {"type": "auth_required", "ha_version": "2026.10.0"}
        self.url = ""
        self.rest_url = ""

        # Places that refer to entities. The UI-managed YAML files, by id or script key:
        self.states: list[dict[str, Any]] = []
        self.automations: dict[str, dict[str, Any]] = {}
        self.scripts: dict[str, dict[str, Any]] = {}
        self.scenes: dict[str, dict[str, Any]] = {}
        # What automation/config and script/config return, by entity ID:
        self.raw_configs: dict[str, dict[str, Any]] = {}
        # Dashboards by url_path; the default dashboard's panel is "lovelace".
        self.panels: dict[str, dict[str, Any]] = {}
        self.dashboards: dict[str, dict[str, Any]] = {}
        self.persons: dict[str, list[dict[str, Any]]] = {"storage": [], "config": []}
        self.energy: dict[str, Any] | None = None

        # Every save of a referring place, as (kind, key, body).
        self.saves: list[tuple[str, str, Any]] = []
        self.fail_saves: set[str] = set()  # keys whose save is rejected
        self.drop_saves: set[str] = set()  # keys whose save closes the connection
        # Commands that fail, by type, with this error code.
        self.fail_commands: dict[str, str] = {}
        self._dropping = False

    def add_dashboard(self, url_path: str, config: dict[str, Any], mode: str = "storage"):
        title = "Overview" if url_path == "lovelace" else url_path.title()
        self.panels[url_path] = {
            "component_name": "lovelace",
            "url_path": url_path,
            "title": None if url_path == "lovelace" else title,
            "config": {"mode": mode},
        }
        self.dashboards[url_path] = config

    def save(self, kind: str, key: str, body: Any) -> str | None:
        """Record a save; returns an error message if it is to be rejected."""
        self.saves.append((kind, key, copy.deepcopy(body)))
        if key in self.drop_saves:
            self._dropping = True
        if key in self.fail_saves:
            return f"Message malformed: can't save {key}"
        return None

    async def handler(self, ws):
        await ws.send(json.dumps(self.first_message))
        auth = json.loads(await ws.recv())
        if auth.get("access_token") != self.token:
            await ws.send(
                json.dumps({"type": "auth_invalid", "message": "Invalid access token or password"})
            )
            return
        await ws.send(json.dumps({"type": "auth_ok", "ha_version": "2026.10.0"}))

        async for raw in ws:
            msg = json.loads(raw)
            # Interleave unrelated traffic so the client has to match replies by id.
            await ws.send(json.dumps({"id": 0, "type": "event", "event": {}}))
            await ws.send(json.dumps({"id": msg["id"], "type": "event", "event": {}}))
            reply = self.respond(msg)
            if msg.get("entity_id") in self.drop_after_update or self._dropping:
                await ws.close()
                return
            await ws.send(json.dumps(reply))

    def respond(self, msg: dict[str, Any]) -> dict[str, Any]:
        reply = {"id": msg["id"], "type": "result"}
        if msg["type"] in self.fail_commands:
            return self.error(reply, self.fail_commands[msg["type"]], "Something went wrong")
        if msg["type"] == "config/entity_registry/list":
            return {**reply, "success": True, "result": self.entities}
        if msg["type"] == "config/entity_registry/update":
            self.updates.append({k: v for k, v in msg.items() if k != "id"})
            if msg["entity_id"] in self.fail_updates:
                return self.error(reply, "invalid_info", "Entity is already registered")
            entry = self.find(msg["entity_id"])
            if entry is None:
                return self.error(reply, "not_found", "Entity not found")
            new_id = msg.get("new_entity_id")
            if new_id and self.find(new_id):
                return self.error(reply, "invalid_info", "Entity is already registered")
            if new_id:
                entry["entity_id"] = new_id
            if "name" in msg:
                entry["name"] = msg["name"]
            return {**reply, "success": True, "result": {"entity_entry": entry}}
        if msg["type"] == "get_states":
            return {**reply, "success": True, "result": self.states}
        if msg["type"] in ("automation/config", "script/config"):
            config = self.raw_configs.get(msg["entity_id"])
            if config is None:
                return self.error(reply, "not_found", "Entity not found")
            return {**reply, "success": True, "result": {"config": config}}
        if msg["type"] == "get_panels":
            return {**reply, "success": True, "result": self.panels}
        if msg["type"].startswith("lovelace/config"):
            return self.respond_lovelace(msg, reply)
        if msg["type"] == "person/list":
            return {**reply, "success": True, "result": self.persons}
        if msg["type"] == "person/update":
            person = next(p for p in self.persons["storage"] if p["id"] == msg["person_id"])
            body = {k: v for k, v in msg.items() if k not in ("id", "type", "person_id")}
            if error := self.save("person", msg["person_id"], body):
                return self.error(reply, "invalid_format", error)
            person.update(body)
            return {**reply, "success": True, "result": person}
        if msg["type"] == "energy/get_prefs":
            if self.energy is None:
                return self.error(reply, "not_found", "No prefs")
            return {**reply, "success": True, "result": self.energy}
        if msg["type"] == "energy/save_prefs":
            body = {k: v for k, v in msg.items() if k not in ("id", "type")}
            if error := self.save("energy", "energy", body):
                return self.error(reply, "invalid_format", error)
            self.energy = {**(self.energy or {}), **body}
            return {**reply, "success": True, "result": self.energy}
        return self.error(reply, "unknown_command", "Unknown command.")

    def respond_lovelace(self, msg: dict[str, Any], reply: dict[str, Any]) -> dict[str, Any]:
        # Like Home Assistant, url_path None means the default dashboard.
        url_path = msg.get("url_path") or "lovelace"
        if url_path not in self.dashboards:
            return self.error(reply, "config_not_found", f"Unknown config specified: {url_path}")
        if msg["type"] == "lovelace/config":
            return {**reply, "success": True, "result": self.dashboards[url_path]}
        if self.panels[url_path]["config"]["mode"] == "yaml":
            return self.error(reply, "error", "Not supported")
        if error := self.save("dashboard", url_path, msg["config"]):
            return self.error(reply, "error", error)
        self.dashboards[url_path] = msg["config"]
        return {**reply, "success": True, "result": None}

    def rest(self, method: str, path: str, token: str, body: Any) -> tuple[int, Any]:
        """Answer a REST request with (status, JSON body); status 0 drops the connection."""
        if token != self.token:
            return 401, {"message": "Unauthorized"}
        match = re.fullmatch(r"/api/config/(automation|script|scene)/config/([^/]+)", path)
        if match is None:
            return 404, {"message": "Not found"}
        kind, key = match[1], unquote(match[2])
        items = {"automation": self.automations, "script": self.scripts, "scene": self.scenes}
        store = items[kind]
        if method == "GET":
            if key not in store:
                return 404, {"message": "Resource not found"}
            return 200, store[key]
        if error := self.save(kind, key, body):
            self._dropping = False
            return 400, {"message": error}
        if self._dropping:
            self._dropping = False
            return 0, None
        store[key] = body
        return 200, {"result": "ok"}

    def find(self, entity_id: str) -> dict[str, Any] | None:
        return next((e for e in self.entities if e["entity_id"] == entity_id), None)

    @staticmethod
    def error(reply: dict[str, Any], code: str, message: str) -> dict[str, Any]:
        return {**reply, "success": False, "error": {"code": code, "message": message}}


def rest_handler(ha: FakeHomeAssistant) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.answer()

        def do_POST(self):
            self.answer()

        def answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            status, reply = ha.rest(self.command, self.path, token, body)
            if status == 0:
                self.close_connection = True
                return
            data = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    return Handler


@pytest.fixture
def fake_ha(monkeypatch):
    """A FakeHomeAssistant served on localhost from background threads.

    The WebSocket and REST APIs listen on different ports here, so the client's REST
    base URL is pointed at the fake REST server.
    """
    ha = FakeHomeAssistant(SAMPLE_ENTITIES)

    http_server = ThreadingHTTPServer(("127.0.0.1", 0), rest_handler(ha))
    ha.rest_url = f"http://127.0.0.1:{http_server.server_address[1]}"
    http_thread = threading.Thread(
        target=http_server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    http_thread.start()
    monkeypatch.setattr(client, "rest_base_url", lambda url: ha.rest_url)

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

    thread = threading.Thread(
        target=loop.run_until_complete, args=(serve_until_stopped(),), daemon=True
    )
    thread.start()
    assert started.wait(5), "fake Home Assistant server did not start"
    yield ha
    loop.call_soon_threadsafe(stop.set)
    thread.join(5)
    loop.close()
    http_server.shutdown()
    http_server.server_close()
    http_thread.join(5)
