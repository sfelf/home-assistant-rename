"""Read and save the places in Home Assistant that refer to entities.

Each kind of target has its own API:

- automations, scripts and scenes created in the UI (``automations.yaml``,
  ``scripts.yaml``, ``scenes.yaml``) are read and saved over REST, like the UI editors
  do; those defined elsewhere in YAML can only be read;
- dashboards, persons and the energy settings use WebSocket commands, and YAML
  dashboards and persons can only be read;
- groups (anything whose state lists its members in ``entity_id``) can only be read.
"""

from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

from ha_rename.client import CommandError, HomeAssistantClient
from ha_rename.references import Edit, Target

REST_KINDS = ("automation", "script", "scene")
DEFAULT_DASHBOARD = "lovelace"  # the default dashboard's panel URL path
YAML = "defined in YAML"


async def load_targets(ha: HomeAssistantClient, entries: list[dict[str, Any]]) -> list[Target]:
    """Every target, with its current config. ``entries`` is the entity registry."""
    # A script's key is its registry unique_id; its entity ID may have been changed since.
    script_keys = {
        e["entity_id"]: e["unique_id"]
        for e in entries
        if e.get("platform") == "script" and e.get("unique_id")
    }
    targets: list[Target | None] = []
    for state in await ha.call({"type": "get_states"}):
        entity_id = state["entity_id"]
        domain, object_id = entity_id.split(".", 1)
        attributes = state.get("attributes") or {}
        name = attributes.get("friendly_name") or entity_id
        if domain == "automation":
            targets.append(await _automation(ha, entity_id, name, attributes.get("id")))
        elif domain == "script":
            key = script_keys.get(entity_id, object_id)
            targets.append(await _script(ha, entity_id, name, key))
        elif domain == "scene":
            targets.append(await _scene(ha, name, attributes))
        elif isinstance(attributes.get("entity_id"), list):
            config = {"entity_id": attributes["entity_id"]}
            targets.append(Target("group", None, f"group {name!r} ({entity_id})", config, False))
    targets += await _dashboards(ha)
    targets += await _persons(ha)
    targets += await _energy(ha)
    return [t for t in targets if t is not None]


def _rest_path(kind: str, key: str) -> str:
    return f"/api/config/{kind}/config/{quote(key, safe='')}"


async def _raw_config(ha: HomeAssistantClient, kind: str, entity_id: str) -> Any:
    try:
        result = await ha.call({"type": f"{kind}/config", "entity_id": entity_id})
    except CommandError:
        return None  # e.g. an automation that failed to load
    return result["config"]


async def _automation(
    ha: HomeAssistantClient, entity_id: str, name: str, automation_id: Any
) -> Target | None:
    if automation_id is not None:
        key = str(automation_id)
        config = await ha.rest("GET", _rest_path("automation", key))
        if config is not None:
            return Target("automation", key, f"automation {name!r} (id {key})", config)
    config = await _raw_config(ha, "automation", entity_id)
    if config is None:
        return None
    return Target("automation", None, f"automation {name!r} ({YAML})", config, False)


async def _script(ha: HomeAssistantClient, entity_id: str, name: str, key: str) -> Target | None:
    config = await ha.rest("GET", _rest_path("script", key))
    if config is not None:
        return Target("script", key, f"script {name!r} ({entity_id})", config)
    config = await _raw_config(ha, "script", entity_id)
    if config is None:
        return None
    return Target("script", None, f"script {name!r} ({YAML})", config, False)


async def _scene(ha: HomeAssistantClient, name: str, attributes: dict[str, Any]) -> Target:
    if attributes.get("id") is not None:
        key = str(attributes["id"])
        config = await ha.rest("GET", _rest_path("scene", key))
        if config is not None:
            return Target("scene", key, f"scene {name!r} (id {key})", config)
    # A YAML scene: its state lists the entities it sets.
    config = {"entity_id": attributes.get("entity_id") or []}
    return Target("scene", None, f"scene {name!r} ({YAML})", config, False)


def _dashboard_url_path(key: str) -> str | None:
    # Home Assistant's dashboard commands use None for the default dashboard.
    return None if key == DEFAULT_DASHBOARD else key


async def _dashboards(ha: HomeAssistantClient) -> list[Target]:
    targets = []
    for url_path, panel in (await ha.call({"type": "get_panels"})).items():
        if panel.get("component_name") != "lovelace":
            continue
        try:
            config = await ha.call(
                {"type": "lovelace/config", "url_path": _dashboard_url_path(url_path)}
            )
        except CommandError:
            continue  # e.g. the default dashboard before it's been edited: generated
        default = url_path == DEFAULT_DASHBOARD
        title = panel.get("title") or ("Overview" if default else url_path)
        where = "default" if default else url_path
        # The default dashboard has no panel config in storage mode.
        if (panel.get("config") or {}).get("mode") == "yaml":
            label = f"dashboard {title!r} ({where}, YAML mode)"
            targets.append(Target("dashboard", None, label, config, False))
        else:
            targets.append(Target("dashboard", url_path, f"dashboard {title!r} ({where})", config))
    return targets


def _person_config(person: dict[str, Any]) -> dict[str, Any]:
    return {"device_trackers": person.get("device_trackers") or []}


async def _persons(ha: HomeAssistantClient) -> list[Target]:
    persons = await ha.call({"type": "person/list"})
    targets = []
    for person in persons.get("storage", []):
        label = f"person {person['name']!r}"
        targets.append(Target("person", person["id"], label, _person_config(person)))
    for person in persons.get("config", []):
        label = f"person {person['name']!r} ({YAML})"
        targets.append(Target("person", None, label, _person_config(person), False))
    return targets


async def _energy(ha: HomeAssistantClient) -> list[Target]:
    try:
        prefs = await ha.call({"type": "energy/get_prefs"})
    except CommandError:
        return []  # the energy dashboard hasn't been set up
    return [Target("energy", "energy", "energy settings", prefs)]


async def fetch(ha: HomeAssistantClient, kind: str, key: str) -> Any:
    """The current config of an editable target, or None if it no longer exists."""
    if kind in REST_KINDS:
        return await ha.rest("GET", _rest_path(kind, key))
    try:
        if kind == "dashboard":
            payload = {"type": "lovelace/config", "url_path": _dashboard_url_path(key)}
            return await ha.call(payload)
        if kind == "energy":
            return await ha.call({"type": "energy/get_prefs"})
    except CommandError:
        return None
    if kind == "person":
        persons = (await ha.call({"type": "person/list"})).get("storage", [])
        person = next((p for p in persons if p["id"] == key), None)
        return None if person is None else _person_config(person)
    raise ValueError(f"unknown target kind {kind!r}")


async def save(
    ha: HomeAssistantClient, kind: str, key: str, config: Any, edits: Iterable[Edit]
) -> None:
    """Save the edited ``config`` of a target; ``edits`` are the changes made to it."""
    if kind in REST_KINDS:
        await ha.rest("POST", _rest_path(kind, key), config)
    elif kind == "dashboard":
        url_path = _dashboard_url_path(key)
        await ha.call({"type": "lovelace/config/save", "config": config, "url_path": url_path})
    elif kind == "person":
        payload = {"type": "person/update", "person_id": key}
        await ha.call({**payload, "device_trackers": config["device_trackers"]})
    elif kind == "energy":
        # Only the settings that changed, so nothing else is sent back unvalidated.
        changed = dict.fromkeys(e.path[0] for e in edits)
        await ha.call({"type": "energy/save_prefs", **{k: config[k] for k in changed}})
    else:
        raise ValueError(f"unknown target kind {kind!r}")
