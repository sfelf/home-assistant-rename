import copy

import pytest

from ha_rename.references import (
    Edit,
    Target,
    TextHit,
    apply_edits,
    format_path,
    keep_accepted,
    plan_references,
    revert_edits,
    scan,
)

RENAMES = {"sensor.a_temp": "sensor.b_temp", "light.a_lamp": "light.b_lamp"}


class TestScan:
    def test_value(self):
        edits, hits = scan({"triggers": [{"entity_id": "sensor.a_temp"}]}, RENAMES)
        assert edits == [Edit(("triggers", 0, "entity_id"), "sensor.a_temp", "sensor.b_temp")]
        assert hits == []

    def test_list_of_ids(self):
        edits, _ = scan({"entity_id": ["light.kitchen", "light.a_lamp"]}, RENAMES)
        assert edits == [Edit(("entity_id", 1), "light.a_lamp", "light.b_lamp")]

    def test_key(self):
        config = {"entities": {"light.a_lamp": {"state": "on"}}}
        edits, _ = scan(config, RENAMES)
        assert edits == [
            Edit(("entities", "light.a_lamp"), "light.a_lamp", "light.b_lamp", is_key=True)
        ]

    def test_shared_prefix_untouched(self):
        assert scan({"entity_id": "sensor.a_temp_2"}, RENAMES) == ([], [])

    def test_other_domain_untouched(self):
        assert scan({"entity_id": "binary_sensor.a_temp"}, RENAMES) == ([], [])

    def test_not_renamed_untouched(self):
        assert scan({"entity_id": "sensor.other", "n": 3, "on": True}, RENAMES) == ([], [])

    @pytest.mark.parametrize(
        "text",
        [
            "{{ states('sensor.a_temp') | float > 20 }}",
            "{{ states.sensor.a_temp.state }}",
            "sensor.a_temp, light.kitchen",
            "Temperature is {{ states('sensor.a_temp') }} ({{ states('sensor.a_temp') }})",
        ],
    )
    def test_text(self, text):
        edits, hits = scan({"value_template": text}, RENAMES)
        assert edits == []
        assert hits == [TextHit(("value_template",), "sensor.a_temp", "sensor.b_temp")]

    @pytest.mark.parametrize(
        "text",
        [
            "{{ states('sensor.a_temp_2') }}",
            "{{ states('binary_sensor.a_temp') }}",
            "{{ states('Xsensor.a_temp') }}",
        ],
    )
    def test_text_needs_whole_id(self, text):
        assert scan({"value_template": text}, RENAMES) == ([], [])

    def test_text_finds_each_id(self):
        _, hits = scan(
            {"t": "{{ is_state('light.a_lamp', 'on') and states('sensor.a_temp') }}"}, RENAMES
        )
        assert [h.old for h in hits] == ["light.a_lamp", "sensor.a_temp"]

    def test_longest_id_wins(self):
        renames = {"sensor.a": "sensor.x", "sensor.a_temp": "sensor.y"}
        _, hits = scan({"t": "{{ states('sensor.a_temp') }}"}, renames)
        assert hits == [TextHit(("t",), "sensor.a_temp", "sensor.y")]

    def test_text_in_key(self):
        _, hits = scan({"{{ 'sensor.a_temp' }}": 1}, RENAMES)
        assert hits == [TextHit(("{{ 'sensor.a_temp' }}",), "sensor.a_temp", "sensor.b_temp")]

    def test_action_names_are_not_references(self):
        renames = {"script.a_morning": "script.b_morning"}
        config = {
            "actions": [
                {"action": "script.a_morning"},
                {"service": "script.a_morning"},
                {"action": "script.turn_on", "target": {"entity_id": "script.a_morning"}},
            ]
        }
        edits, hits = scan(config, renames)
        assert edits == [
            Edit(("actions", 2, "target", "entity_id"), "script.a_morning", "script.b_morning")
        ]
        assert hits == []

    def test_template_under_action_is_reported(self):
        renames = {"script.a_morning": "script.b_morning"}
        _, hits = scan({"action": "{{ 'script.a_morning' }}"}, renames)
        assert hits == [TextHit(("action",), "script.a_morning", "script.b_morning")]

    def test_no_renames(self):
        assert scan({"entity_id": "sensor.a_temp"}, {}) == ([], [])


class TestFormatPath:
    @pytest.mark.parametrize(
        "path, text",
        [
            ((), "(top level)"),
            (("triggers", 0, "entity_id"), "triggers[0].entity_id"),
            ((0, "entity"), "[0].entity"),
            (("entities", "light.a_lamp"), "entities['light.a_lamp']"),
            (("views", 1, "cards", 2, "entity"), "views[1].cards[2].entity"),
        ],
    )
    def test_format(self, path, text):
        assert format_path(path) == text

    def test_edit_and_hit_describe(self):
        assert Edit(("entity_id",), "sensor.a", "sensor.b").describe() == (
            "entity_id: sensor.a -> sensor.b"
        )
        assert TextHit(("t",), "sensor.a", "sensor.b").describe() == (
            "t: mentions sensor.a (now sensor.b)"
        )


SCENE = {
    "id": "1",
    "name": "Evening",
    "entities": {
        "light.kitchen": {"state": "on"},
        "light.a_lamp": {"state": "on", "brightness": 120},
        "light.hall": {"state": "off"},
    },
}

AUTOMATION = {
    "id": "1700000000001",
    "alias": "Heater off",
    "triggers": [{"trigger": "state", "entity_id": "sensor.a_temp"}],
    "conditions": [{"condition": "template", "value_template": "{{ states('sensor.a_temp') }}"}],
    "actions": [{"action": "light.turn_off", "target": {"entity_id": ["light.a_lamp"]}}],
    "mode": "single",
}


class TestApplyEdits:
    def test_values(self):
        edits, _ = scan(AUTOMATION, RENAMES)
        original = copy.deepcopy(AUTOMATION)
        result = apply_edits(AUTOMATION, edits)
        assert original == AUTOMATION  # not mutated
        assert result.applied == edits
        assert result.notes == []
        expected = copy.deepcopy(AUTOMATION)
        expected["triggers"][0]["entity_id"] = "sensor.b_temp"
        expected["actions"][0]["target"]["entity_id"] = ["light.b_lamp"]
        assert result.config == expected  # everything else, templates included, unchanged

    def test_key_keeps_position(self):
        edits, _ = scan(SCENE, RENAMES)
        result = apply_edits(SCENE, edits)
        assert list(result.config["entities"]) == ["light.kitchen", "light.b_lamp", "light.hall"]
        assert result.config["entities"]["light.b_lamp"] == {"state": "on", "brightness": 120}
        assert "light.a_lamp" in SCENE["entities"]

    def test_value_under_renamed_key(self):
        config = {"entities": {"light.a_lamp": {"entity_id": "light.a_lamp"}}}
        edits, _ = scan(config, RENAMES)
        result = apply_edits(config, edits)
        assert result.config == {"entities": {"light.b_lamp": {"entity_id": "light.b_lamp"}}}
        assert revert_edits(result.config, edits).config == config

    def test_value_changed_since(self):
        edit = Edit(("entity_id",), "sensor.a_temp", "sensor.b_temp")
        result = apply_edits({"entity_id": "sensor.c_temp"}, [edit])
        assert result.config == {"entity_id": "sensor.c_temp"}
        assert result.applied == []
        assert result.notes == ["entity_id: changed since to 'sensor.c_temp'; left as is"]

    @pytest.mark.parametrize(
        "config",
        [
            {"triggers": []},
            {"triggers": [{}]},
            {"triggers": "sensor.a_temp"},
            {},
        ],
    )
    def test_value_no_longer_there(self, config):
        edit = Edit(("triggers", 0, "entity_id"), "sensor.a_temp", "sensor.b_temp")
        result = apply_edits(config, [edit])
        assert result.config == config
        assert result.notes == ["triggers[0].entity_id: no longer there; left as is"]

    def test_empty_path_never_applies(self):
        result = apply_edits("sensor.a_temp", [Edit((), "sensor.a_temp", "sensor.b_temp")])
        assert result.config == "sensor.a_temp"
        assert result.notes == ["(top level): no longer there; left as is"]

    @pytest.mark.parametrize(
        "entities",
        [
            {"light.kitchen": {}},  # removed
            {"light.a_lamp": {}, "light.b_lamp": {}},  # new key already taken
        ],
    )
    def test_key_changed_since(self, entities):
        edit = Edit(("entities", "light.a_lamp"), "light.a_lamp", "light.b_lamp", is_key=True)
        result = apply_edits({"entities": entities}, [edit])
        assert result.config == {"entities": entities}
        assert result.notes == ["entities['light.a_lamp']: changed since; left as is"]

    @pytest.mark.parametrize("first", ["sensor.a", "sensor.aa"])
    def test_chained_keys(self, first):
        # sensor.a -> sensor.aa while sensor.aa -> sensor.aaa, in either order.
        renames = {"sensor.a": "sensor.aa", "sensor.aa": "sensor.aaa"}
        second = "sensor.aa" if first == "sensor.a" else "sensor.a"
        config = {"entities": {first: {"n": 1}, second: {"n": 2}}}
        edits, _ = scan(config, renames)
        result = apply_edits(config, edits)
        assert result.notes == []
        assert result.config == {"entities": {renames[first]: {"n": 1}, renames[second]: {"n": 2}}}
        reverted = revert_edits(result.config, result.applied)
        assert reverted.notes == []
        assert reverted.config == config

    def test_chained_keys_with_a_value_below(self):
        # The value under the original sensor.aa ends up under sensor.aaa, while the old
        # sensor.a entry is now sensor.aa: undo must put the keys back first.
        renames = {"sensor.a": "sensor.aa", "sensor.aa": "sensor.aaa"}
        config = {"entities": {"sensor.a": {"n": 1}, "sensor.aa": {"ref": "sensor.aa"}}}
        edits, _ = scan(config, renames)
        result = apply_edits(config, edits)
        assert result.config == {
            "entities": {"sensor.aa": {"n": 1}, "sensor.aaa": {"ref": "sensor.aaa"}}
        }
        reverted = revert_edits(result.config, result.applied)
        assert reverted.notes == []
        assert reverted.config == config

    def test_chained_nested_keys(self):
        renames = {"sensor.a": "sensor.aa", "sensor.aa": "sensor.aaa"}
        config = {"sensor.aa": {"sensor.a": 1}, "sensor.a": {"sensor.aa": 2}}
        edits, _ = scan(config, renames)
        result = apply_edits(config, edits)
        assert result.notes == []
        assert result.config == {"sensor.aaa": {"sensor.aa": 1}, "sensor.aa": {"sensor.aaa": 2}}
        reverted = revert_edits(result.config, result.applied)
        assert reverted.notes == []
        assert reverted.config == config

    def test_swapped_keys_are_left(self):
        renames = {"light.a": "light.b", "light.b": "light.a"}
        config = {"entities": {"light.a": 1, "light.b": 2}}
        edits, _ = scan(config, renames)
        result = apply_edits(config, edits)
        assert result.config == config
        assert len(result.notes) == 2

    def test_key_parent_gone(self):
        edit = Edit(("entities", "light.a_lamp"), "light.a_lamp", "light.b_lamp", is_key=True)
        result = apply_edits({}, [edit])
        assert result.notes == ["entities['light.a_lamp']: changed since; left as is"]


class TestRevertEdits:
    def test_round_trip(self):
        edits, _ = scan(AUTOMATION, RENAMES)
        updated = apply_edits(AUTOMATION, edits).config
        result = revert_edits(updated, edits)
        assert result.config == AUTOMATION
        assert result.applied == [e.reversed() for e in edits]

    def test_scene_round_trip(self):
        edits, _ = scan(SCENE, RENAMES)
        updated = apply_edits(SCENE, edits).config
        reverted = revert_edits(updated, edits).config
        assert reverted == SCENE
        assert list(reverted["entities"]) == list(SCENE["entities"])

    def test_keeps_later_edits(self):
        edits, _ = scan(AUTOMATION, RENAMES)
        updated = apply_edits(AUTOMATION, edits).config
        updated["triggers"][0]["entity_id"] = "sensor.c_temp"
        updated["alias"] = "Renamed by user"
        result = revert_edits(updated, edits)
        assert result.config["triggers"][0]["entity_id"] == "sensor.c_temp"
        assert result.config["actions"][0]["target"]["entity_id"] == ["light.a_lamp"]
        assert result.config["alias"] == "Renamed by user"
        assert result.notes == [
            "triggers[0].entity_id: changed since to 'sensor.c_temp'; left as is"
        ]

    def test_reversed(self):
        key = Edit(("entities", "light.a"), "light.a", "light.b", is_key=True)
        assert key.reversed() == Edit(("entities", "light.b"), "light.b", "light.a", True)
        value = Edit(("entity_id",), "light.a", "light.b")
        assert value.reversed() == Edit(("entity_id",), "light.b", "light.a")


class TestPlanReferences:
    AUTOMATION_TARGET = Target("automation", "1", "automation 'Heater off' (id 1)", AUTOMATION)

    def test_editable(self):
        [plan] = plan_references([self.AUTOMATION_TARGET], RENAMES)
        assert plan.target == self.AUTOMATION_TARGET
        assert [e.path for e in plan.edits] == [
            ("triggers", 0, "entity_id"),
            ("actions", 0, "target", "entity_id", 0),
        ]
        assert plan.manual == (
            TextHit(("conditions", 0, "value_template"), "sensor.a_temp", "sensor.b_temp"),
        )

    def test_report_only(self):
        target = Target(
            "automation", None, "automation 'Garage' (defined in YAML)", AUTOMATION, False
        )
        [plan] = plan_references([target], RENAMES)
        assert plan.edits == ()
        assert [m.path for m in plan.manual] == [
            ("triggers", 0, "entity_id"),
            ("actions", 0, "target", "entity_id", 0),
            ("conditions", 0, "value_template"),
        ]

    def test_edits_that_cant_be_applied_need_a_manual_fix(self):
        # The scene already has the new ID as a key, e.g. a stale entry.
        config = {"entities": {"light.a_lamp": {"state": "on"}, "light.b_lamp": {}}}
        target = Target("scene", "1", "scene 'Evening' (id 1)", config)
        [plan] = plan_references([target], RENAMES)
        assert plan.edits == ()
        assert plan.manual == (
            Edit(("entities", "light.a_lamp"), "light.a_lamp", "light.b_lamp", is_key=True),
        )

    def test_targets_without_references_left_out(self):
        quiet = Target("script", "x", "script 'X' (script.x)", {"sequence": []})
        assert plan_references([quiet], RENAMES) == []


class TestKeepAccepted:
    def test_drops_rejected_renames(self):
        [plan] = plan_references([TestPlanReferences.AUTOMATION_TARGET], RENAMES)
        [kept] = keep_accepted([plan], {"light.a_lamp"})
        assert [e.old for e in kept.edits] == ["light.a_lamp"]
        assert kept.manual == ()

    def test_drops_targets_left_empty(self):
        [plan] = plan_references([TestPlanReferences.AUTOMATION_TARGET], RENAMES)
        assert keep_accepted([plan], set()) == []
        assert keep_accepted([plan], {"sensor.a_temp"})[0].manual == plan.manual
