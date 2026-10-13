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
from ha_rename.references import Edit, Fix, Target

REST_KINDS = ("automation", "script", "scene")
# The registry platform of each kind's UI-managed entities (scenes belong to Home
# Assistant's own scene platform, not to other integrations' scenes).
REST_KIND_PLATFORMS = {"automation": "automation", "script": "script", "scene": "homeassistant"}
DEFAULT_DASHBOARD = "lovelace"  # the default dashboard's panel URL path
YAML = "defined in YAML"
# Error codes Home Assistant uses when the thing asked for doesn't exist, or when the
# integration it belongs to isn't loaded (e.g. no energy or person on a minimal setup).
MISSING = ("not_found", "config_not_found", "unknown_command")


def _is_missing(error: CommandError) -> bool:
    return isinstance(error.error, dict) and error.error.get("code") in MISSING


async def _call_if_loaded(ha: HomeAssistantClient, payload: dict[str, Any], default: Any) -> Any:
    """``ha.call(payload)``, or ``default`` if its integration isn't loaded."""
    try:
        return await ha.call(payload)
    except CommandError as e:
        if not _is_missing(e):
            raise
        return default


async def load_targets(ha: HomeAssistantClient, entries: list[dict[str, Any]]) -> list[Target]:
    """Every target, with its current config. ``entries`` is the entity registry."""
    # A script's key is its registry unique_id; its entity ID may have been changed since.
    script_keys = {
        e["entity_id"]: e["unique_id"]
        for e in entries
        if e.get("platform") == "script" and e.get("unique_id")
    }
    registry = {e["entity_id"]: e for e in entries}
    targets: list[Target | None] = []
    states = await ha.call({"type": "get_states"})
    for state in states:
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
            label = f"group {name!r} ({entity_id})"
            fix = _group_fix(registry.get(entity_id))
            targets.append(Target("group", None, label, config, False, fix))
    # Entities disabled in the registry have no state, but their config still counts.
    with_state = {state["entity_id"] for state in states}
    for entry in entries:
        if entry["entity_id"] not in with_state:
            targets.append(await _without_state(ha, entry))
    targets += await _dashboards(ha)
    targets += await _persons(ha)
    targets += await _energy(ha)
    return [t for t in targets if t is not None]


def _group_fix(entry: dict[str, Any] | None) -> Fix:
    """Where to fix the members of a group, or of another entity that lists its members.

    ``entry`` is its registry entry. Only YAML defines entities without one: those set up
    through config entries are always registered.
    """
    if entry is None:
        return Fix(in_yaml=True)
    if entry.get("platform") != "group":
        return Fix(integration=entry.get("platform"))
    if entry.get("config_entry_id"):
        return Fix("config/helpers")  # a group helper: no link to its own options
    return Fix(in_yaml=True)


def _rest_path(kind: str, key: str) -> str:
    return f"/api/config/{kind}/config/{quote(key, safe='')}"


def _edit_page(kind: str, key: str) -> Fix:
    """The UI editor of an automation, script, scene or person."""
    return Fix(f"config/{kind}/edit/{quote(key, safe='')}")


def _yaml_page(kind: str, entity_id: str) -> Fix:
    """The read-only page of an automation or script defined in YAML."""
    return Fix(f"config/{kind}/show/{entity_id}", entity_id, in_yaml=True)


async def _raw_config(ha: HomeAssistantClient, kind: str, entity_id: str) -> Any:
    try:
        result = await ha.call({"type": f"{kind}/config", "entity_id": entity_id})
    except CommandError as e:
        if not _is_missing(e):
            raise
        return None  # e.g. an automation that failed to load
    return result["config"]


async def _automation(
    ha: HomeAssistantClient, entity_id: str, name: str, automation_id: Any
) -> Target | None:
    if automation_id is not None:
        key = str(automation_id)
        config = await ha.rest("GET", _rest_path("automation", key))
        if config is not None:
            label = f"automation {name!r} (id {key})"
            return Target("automation", key, label, config, fix=_edit_page("automation", key))
    config = await _raw_config(ha, "automation", entity_id)
    if config is None:
        return None
    label = f"automation {name!r} ({YAML})"
    return Target("automation", None, label, config, False, _yaml_page("automation", entity_id))


async def _script(ha: HomeAssistantClient, entity_id: str, name: str, key: str) -> Target | None:
    config = await ha.rest("GET", _rest_path("script", key))
    if config is not None:
        label = f"script {name!r} ({entity_id})"
        return Target("script", key, label, config, fix=_edit_page("script", key))
    config = await _raw_config(ha, "script", entity_id)
    if config is None:
        return None
    return Target(
        "script", None, f"script {name!r} ({YAML})", config, False, _yaml_page("script", entity_id)
    )


async def _scene(ha: HomeAssistantClient, name: str, attributes: dict[str, Any]) -> Target:
    if attributes.get("id") is not None:
        key = str(attributes["id"])
        config = await ha.rest("GET", _rest_path("scene", key))
        if config is not None:
            return Target(
                "scene", key, f"scene {name!r} (id {key})", config, fix=_edit_page("scene", key)
            )
    # A YAML scene: its state lists the entities it sets.
    config = {"entity_id": attributes.get("entity_id") or []}
    return Target("scene", None, f"scene {name!r} ({YAML})", config, False, Fix(in_yaml=True))


async def _without_state(ha: HomeAssistantClient, entry: dict[str, Any]) -> Target | None:
    """A UI-managed automation, script or scene known only from its registry entry.

    Its registry unique_id is its key in the UI-managed file. One defined elsewhere in
    YAML can't be read without a state, so it's left out.
    """
    kind = entry["entity_id"].split(".", 1)[0]
    key = entry.get("unique_id")
    if REST_KIND_PLATFORMS.get(kind) != entry.get("platform") or not key:
        return None
    config = await ha.rest("GET", _rest_path(kind, key))
    if config is None:
        return None
    name = entry.get("name") or entry.get("original_name") or entry["entity_id"]
    where = entry["entity_id"] if kind == "script" else f"id {key}"
    return Target(kind, key, f"{kind} {name!r} ({where})", config, fix=_edit_page(kind, key))


def _dashboard_url_path(key: str) -> str | None:
    # Home Assistant's dashboard commands use None for the default dashboard.
    return None if key == DEFAULT_DASHBOARD else key


async def _dashboards(ha: HomeAssistantClient) -> list[Target]:
    targets = []
    for url_path, panel in (await _call_if_loaded(ha, {"type": "get_panels"}, {})).items():
        if panel.get("component_name") != "lovelace":
            continue
        try:
            config = await ha.call(
                {"type": "lovelace/config", "url_path": _dashboard_url_path(url_path)}
            )
        except CommandError as e:
            if not _is_missing(e):
                raise
            continue  # e.g. the default dashboard before it's been edited: generated
        default = url_path == DEFAULT_DASHBOARD
        title = panel.get("title") or ("Overview" if default else url_path)
        where = "default" if default else url_path
        # The default dashboard has no panel config in storage mode.
        if (panel.get("config") or {}).get("mode") == "yaml":
            label = f"dashboard {title!r} ({where}, YAML mode)"
            page = quote(url_path, safe="")
            targets.append(Target("dashboard", None, label, config, False, Fix(page, in_yaml=True)))
        else:
            label = f"dashboard {title!r} ({where})"
            fix = Fix(f"{quote(url_path, safe='')}?edit=1")
            targets.append(Target("dashboard", url_path, label, config, fix=fix))
    return targets


def _person_config(person: dict[str, Any]) -> dict[str, Any]:
    return {"device_trackers": person.get("device_trackers") or []}


async def _persons(ha: HomeAssistantClient) -> list[Target]:
    persons = await _call_if_loaded(ha, {"type": "person/list"}, {})
    targets = []
    for person in persons.get("storage", []):
        label = f"person {person['name']!r}"
        fix = _edit_page("person", person["id"])
        targets.append(Target("person", person["id"], label, _person_config(person), fix=fix))
    for person in persons.get("config", []):
        label = f"person {person['name']!r} ({YAML})"
        targets.append(
            Target("person", None, label, _person_config(person), False, Fix(in_yaml=True))
        )
    return targets


async def _energy(ha: HomeAssistantClient) -> list[Target]:
    try:
        prefs = await ha.call({"type": "energy/get_prefs"})
    except CommandError as e:
        if not _is_missing(e):
            raise
        return []  # the energy dashboard hasn't been set up
    return [Target("energy", "energy", "energy settings", prefs, fix=Fix("config/energy"))]


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
    except CommandError as e:
        if not _is_missing(e):
            raise
        return None
    if kind == "person":
        persons = (await _call_if_loaded(ha, {"type": "person/list"}, {})).get("storage", [])
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
