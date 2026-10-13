import asyncio

import pytest

from ha_rename import client, targets
from ha_rename.references import Edit, Target
from tests.conftest import GOOD_TOKEN, entity, state

HEATER_OFF = {
    "id": "1700000000001",
    "alias": "Heater off",
    "triggers": [{"trigger": "state", "entity_id": "sensor.a_temp"}],
    "actions": [{"action": "switch.turn_off", "target": {"entity_id": "switch.a_heater"}}],
}
FROM_BLUEPRINT = {
    "id": "1700000000002",
    "alias": "Motion light",
    "use_blueprint": {
        "path": "homeassistant/motion_light.yaml",
        "input": {"motion_entity": "binary_sensor.a_motion", "light_target": {}},
    },
}
GARAGE = {"alias": "Garage lights", "triggers": [{"entity_id": "sensor.a_temp"}]}


def run(ha_fake, action):
    async def go():
        async with client.connect(ha_fake.url, GOOD_TOKEN) as ha:
            return await action(ha)

    return asyncio.run(go())


def load(fake_ha, entries=()):
    return run(fake_ha, lambda ha: targets.load_targets(ha, list(entries)))


class TestAutomations:
    def test_ui_managed(self, fake_ha):
        fake_ha.states = [
            state("automation.heater_off", "Heater off", id="1700000000001"),
            state("automation.motion_light", "Motion light", id="1700000000002"),
        ]
        fake_ha.automations = {"1700000000001": HEATER_OFF, "1700000000002": FROM_BLUEPRINT}
        assert load(fake_ha) == [
            Target(
                "automation",
                "1700000000001",
                "automation 'Heater off' (id 1700000000001)",
                HEATER_OFF,
            ),
            Target(
                "automation",
                "1700000000002",
                "automation 'Motion light' (id 1700000000002)",
                FROM_BLUEPRINT,
            ),
        ]

    @pytest.mark.parametrize("attributes", [{}, {"id": "from-a-package"}])
    def test_defined_in_yaml(self, fake_ha, attributes):
        # No id, or an id that isn't in automations.yaml (e.g. from a package).
        fake_ha.states = [state("automation.garage_lights", "Garage lights", **attributes)]
        fake_ha.raw_configs = {"automation.garage_lights": GARAGE}
        assert load(fake_ha) == [
            Target(
                "automation", None, "automation 'Garage lights' (defined in YAML)", GARAGE, False
            )
        ]

    def test_without_config_is_skipped(self, fake_ha):
        fake_ha.states = [state("automation.broken")]
        assert load(fake_ha) == []

    def test_name_falls_back_to_entity_id(self, fake_ha):
        fake_ha.states = [state("automation.garage_lights")]
        fake_ha.raw_configs = {"automation.garage_lights": GARAGE}
        assert load(fake_ha)[0].label == "automation 'automation.garage_lights' (defined in YAML)"


class TestScripts:
    def test_ui_managed_by_registry_key(self, fake_ha):
        # The entity ID was changed, but the script's key is still its unique_id.
        script = {"alias": "Morning", "sequence": []}
        fake_ha.states = [state("script.good_morning", "Morning")]
        fake_ha.scripts = {"morning": script}
        entries = [entity("script.good_morning", platform="script", unique_id="morning")]
        assert load(fake_ha, entries) == [
            Target("script", "morning", "script 'Morning' (script.good_morning)", script)
        ]

    def test_key_defaults_to_object_id(self, fake_ha):
        fake_ha.states = [state("script.morning", "Morning")]
        fake_ha.scripts = {"morning": {"sequence": []}}
        entries = [entity("light.kitchen", platform="hue", unique_id="x")]
        assert load(fake_ha, entries)[0].key == "morning"

    def test_defined_in_yaml(self, fake_ha):
        fake_ha.states = [state("script.evening", "Evening")]
        fake_ha.raw_configs = {"script.evening": {"sequence": []}}
        assert load(fake_ha) == [
            Target("script", None, "script 'Evening' (defined in YAML)", {"sequence": []}, False)
        ]

    def test_without_config_is_skipped(self, fake_ha):
        fake_ha.states = [state("script.broken")]
        assert load(fake_ha) == []


class TestScenes:
    def test_ui_managed(self, fake_ha):
        scene = {"id": "1", "name": "Evening", "entities": {"light.a_lamp": {"state": "on"}}}
        fake_ha.states = [state("scene.evening", "Evening", id="1", entity_id=["light.a_lamp"])]
        fake_ha.scenes = {"1": scene}
        assert load(fake_ha) == [Target("scene", "1", "scene 'Evening' (id 1)", scene)]

    @pytest.mark.parametrize("attributes", [{}, {"id": "not-in-scenes-yaml"}])
    def test_defined_in_yaml(self, fake_ha, attributes):
        fake_ha.states = [state("scene.night", "Night", entity_id=["light.a_lamp"], **attributes)]
        assert load(fake_ha) == [
            Target(
                "scene",
                None,
                "scene 'Night' (defined in YAML)",
                {"entity_id": ["light.a_lamp"]},
                False,
            )
        ]

    def test_yaml_without_entities(self, fake_ha):
        fake_ha.states = [state("scene.empty")]
        assert load(fake_ha)[0].config == {"entity_id": []}


def test_groups(fake_ha):
    fake_ha.states = [
        state("group.downstairs", "Downstairs", entity_id=["light.a_lamp", "light.kitchen"]),
        state("light.kitchen", "Kitchen"),
        state("sensor.odd", entity_id="sensor.not_a_list"),
    ]
    assert load(fake_ha) == [
        Target(
            "group",
            None,
            "group 'Downstairs' (group.downstairs)",
            {"entity_id": ["light.a_lamp", "light.kitchen"]},
            False,
        )
    ]


class TestDashboards:
    def test_storage(self, fake_ha):
        fake_ha.add_dashboard("lovelace", {"views": [{"title": "Home"}]})
        fake_ha.panels["lovelace"]["config"] = None  # as for the default dashboard
        fake_ha.add_dashboard("weather", {"views": []})
        fake_ha.panels["map"] = {"component_name": "map", "url_path": "map", "config": None}
        assert load(fake_ha) == [
            Target(
                "dashboard",
                "lovelace",
                "dashboard 'Overview' (default)",
                {"views": [{"title": "Home"}]},
            ),
            Target("dashboard", "weather", "dashboard 'Weather' (weather)", {"views": []}),
        ]

    def test_yaml(self, fake_ha):
        fake_ha.add_dashboard("lovelace", {"views": []}, mode="yaml")
        fake_ha.add_dashboard("garden", {"views": []}, mode="yaml")
        fake_ha.panels["garden"]["title"] = None
        assert load(fake_ha) == [
            Target(
                "dashboard", None, "dashboard 'Overview' (default, YAML mode)", {"views": []}, False
            ),
            Target(
                "dashboard", None, "dashboard 'garden' (garden, YAML mode)", {"views": []}, False
            ),
        ]

    def test_generated_default_is_skipped(self, fake_ha):
        fake_ha.add_dashboard("lovelace", {})
        del fake_ha.dashboards["lovelace"]  # never edited: Home Assistant generates it
        assert load(fake_ha) == []


class TestPersons:
    def test_storage_and_yaml(self, fake_ha):
        fake_ha.persons = {
            "storage": [
                {"id": "alice", "name": "Alice", "device_trackers": ["device_tracker.a_phone"]}
            ],
            "config": [{"id": "bob", "name": "Bob"}],
        }
        assert load(fake_ha) == [
            Target(
                "person", "alice", "person 'Alice'", {"device_trackers": ["device_tracker.a_phone"]}
            ),
            Target(
                "person", None, "person 'Bob' (defined in YAML)", {"device_trackers": []}, False
            ),
        ]


class TestEnergy:
    def test_configured(self, fake_ha):
        fake_ha.energy = {"energy_sources": [{"type": "grid"}], "device_consumption": []}
        assert load(fake_ha) == [Target("energy", "energy", "energy settings", fake_ha.energy)]

    def test_not_set_up(self, fake_ha):
        assert load(fake_ha) == []


class TestFetch:
    def fetch(self, fake_ha, kind, key):
        return run(fake_ha, lambda ha: targets.fetch(ha, kind, key))

    def test_rest(self, fake_ha):
        fake_ha.automations = {"1": HEATER_OFF}
        assert self.fetch(fake_ha, "automation", "1") == HEATER_OFF
        assert self.fetch(fake_ha, "script", "gone") is None

    def test_key_is_quoted(self, fake_ha):
        fake_ha.scenes = {"a b": {"name": "Spaced"}}
        assert self.fetch(fake_ha, "scene", "a b") == {"name": "Spaced"}

    def test_dashboard(self, fake_ha):
        fake_ha.add_dashboard("lovelace", {"views": [1]})
        fake_ha.add_dashboard("weather", {"views": [2]})
        assert self.fetch(fake_ha, "dashboard", "lovelace") == {"views": [1]}
        assert self.fetch(fake_ha, "dashboard", "weather") == {"views": [2]}
        assert self.fetch(fake_ha, "dashboard", "gone") is None

    def test_person(self, fake_ha):
        fake_ha.persons["storage"] = [
            {"id": "alice", "name": "Alice", "device_trackers": ["device_tracker.p"]}
        ]
        assert self.fetch(fake_ha, "person", "alice") == {"device_trackers": ["device_tracker.p"]}
        assert self.fetch(fake_ha, "person", "bob") is None

    def test_energy(self, fake_ha):
        assert self.fetch(fake_ha, "energy", "energy") is None
        fake_ha.energy = {"energy_sources": []}
        assert self.fetch(fake_ha, "energy", "energy") == {"energy_sources": []}

    def test_unknown_kind(self, fake_ha):
        with pytest.raises(ValueError, match="unknown target kind 'group'"):
            self.fetch(fake_ha, "group", "x")


class TestSave:
    def save(self, fake_ha, kind, key, config, edits=()):
        return run(fake_ha, lambda ha: targets.save(ha, kind, key, config, edits))

    @pytest.mark.parametrize("kind", ["automation", "script", "scene"])
    def test_rest(self, fake_ha, kind):
        self.save(fake_ha, kind, "1", {"alias": "New"})
        assert fake_ha.saves == [(kind, "1", {"alias": "New"})]

    def test_rest_error(self, fake_ha):
        fake_ha.fail_saves = {"1"}
        with pytest.raises(client.CommandError, match="http_400"):
            self.save(fake_ha, "automation", "1", {})

    def test_dashboard(self, fake_ha):
        fake_ha.add_dashboard("lovelace", {})
        fake_ha.add_dashboard("weather", {})
        self.save(fake_ha, "dashboard", "lovelace", {"views": [1]})
        self.save(fake_ha, "dashboard", "weather", {"views": [2]})
        assert fake_ha.dashboards == {"lovelace": {"views": [1]}, "weather": {"views": [2]}}

    def test_person(self, fake_ha):
        fake_ha.persons["storage"] = [
            {"id": "alice", "name": "Alice", "device_trackers": ["device_tracker.a"]}
        ]
        self.save(fake_ha, "person", "alice", {"device_trackers": ["device_tracker.b"]})
        assert fake_ha.saves == [("person", "alice", {"device_trackers": ["device_tracker.b"]})]
        assert fake_ha.persons["storage"][0]["device_trackers"] == ["device_tracker.b"]

    def test_energy_sends_only_changed_settings(self, fake_ha):
        prefs = {
            "energy_sources": [{"type": "grid", "stat_energy_from": "sensor.b_energy"}],
            "device_consumption": [{"stat_consumption": "sensor.b_plug"}],
            "device_consumption_water": [],
        }
        fake_ha.energy = prefs
        edits = [
            Edit(("energy_sources", 0, "stat_energy_from"), "sensor.a_energy", "sensor.b_energy"),
            Edit(("device_consumption", 0, "stat_consumption"), "sensor.a_plug", "sensor.b_plug"),
            Edit(("energy_sources", 1, "stat_energy_to"), "sensor.a_x", "sensor.b_x"),
        ]
        self.save(fake_ha, "energy", "energy", prefs, edits)
        assert fake_ha.saves == [
            (
                "energy",
                "energy",
                {
                    "energy_sources": prefs["energy_sources"],
                    "device_consumption": prefs["device_consumption"],
                },
            )
        ]

    def test_unknown_kind(self, fake_ha):
        with pytest.raises(ValueError, match="unknown target kind 'group'"):
            self.save(fake_ha, "group", "x", {})


@pytest.mark.parametrize(
    "command",
    ["automation/config", "lovelace/config", "energy/get_prefs"],
)
def test_unexpected_read_errors_are_raised(fake_ha, command):
    # Only "not found" means a target doesn't exist; anything else must stop the run.
    fake_ha.states = [state("automation.garage", "Garage")]
    fake_ha.add_dashboard("weather", {"views": []})
    fake_ha.fail_commands = {command: "unauthorized"}
    with pytest.raises(client.CommandError, match="unauthorized"):
        load(fake_ha)


@pytest.mark.parametrize(
    "kind, command", [("dashboard", "lovelace/config"), ("energy", "energy/get_prefs")]
)
def test_unexpected_fetch_errors_are_raised(fake_ha, kind, command):
    fake_ha.fail_commands = {command: "home_assistant_error"}
    with pytest.raises(client.CommandError, match="home_assistant_error"):
        run(fake_ha, lambda ha: targets.fetch(ha, kind, "x"))


class TestWithoutState:
    """Automations, scripts and scenes disabled in the entity registry have no state."""

    def test_found_through_the_registry(self, fake_ha):
        fake_ha.automations = {"17": HEATER_OFF}
        fake_ha.scripts = {"morning": {"sequence": []}}
        fake_ha.scenes = {"1": {"id": "1", "entities": {}}}
        entries = [
            entity("automation.heater_off", "Heater off", platform="automation", unique_id="17"),
            entity(
                "script.good_morning",
                original_name="Morning",
                platform="script",
                unique_id="morning",
            ),
            entity("scene.evening", platform="homeassistant", unique_id="1"),
        ]
        assert load(fake_ha, entries) == [
            Target("automation", "17", "automation 'Heater off' (id 17)", HEATER_OFF),
            Target("script", "morning", "script 'Morning' (script.good_morning)", {"sequence": []}),
            Target("scene", "1", "scene 'scene.evening' (id 1)", {"id": "1", "entities": {}}),
        ]

    def test_entities_with_a_state_are_not_loaded_twice(self, fake_ha):
        fake_ha.states = [state("automation.heater_off", "Heater off", id="17")]
        fake_ha.automations = {"17": HEATER_OFF}
        entries = [entity("automation.heater_off", platform="automation", unique_id="17")]
        assert len(load(fake_ha, entries)) == 1

    @pytest.mark.parametrize(
        "entry",
        [
            entity("automation.from_yaml", platform="automation", unique_id="not-in-the-file"),
            entity("automation.no_key", platform="automation"),
            entity("light.kitchen", platform="hue", unique_id="17"),
            entity("scene.other_platform", platform="hue", unique_id="17"),
            entity("scene.wrong_platform", platform="scene", unique_id="17"),
        ],
    )
    def test_others_are_left_out(self, fake_ha, entry):
        fake_ha.automations = {"17": HEATER_OFF}
        fake_ha.scenes = {"17": {"id": "17"}}
        assert load(fake_ha, [entry]) == []
