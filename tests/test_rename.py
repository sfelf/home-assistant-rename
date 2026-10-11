import pytest

from ha_rename.rename import (
    Rename,
    check_prefix_separators,
    plan_renames,
    strip_name_prefix,
    validate_id_prefix,
)
from tests.conftest import SAMPLE_ENTITIES, entity

OLD = "skyvane_skyvane_station_backyard_station_"
NEW = "skyvane_station_"


class TestValidateIdPrefix:
    @pytest.mark.parametrize("prefix", ["sky_", "a1_b2", "x"])
    def test_accepts_valid(self, prefix):
        assert validate_id_prefix(prefix, allow_empty=False) == prefix

    def test_empty_allowed_when_requested(self):
        assert validate_id_prefix("", allow_empty=True) == ""

    def test_empty_rejected_by_default(self):
        with pytest.raises(ValueError, match="must not be empty"):
            validate_id_prefix("", allow_empty=False)

    @pytest.mark.parametrize("prefix", ["Sky_", "sky-vane", "sky vane", "sensor.sky", "skä"])
    def test_rejects_invalid_characters(self, prefix):
        with pytest.raises(ValueError, match="not a valid entity ID prefix"):
            validate_id_prefix(prefix, allow_empty=True)


class TestCheckPrefixSeparators:
    @pytest.mark.parametrize("old, new", [
        ("sky_", "vane_"),
        ("kitchen", "lounge"),
        ("sky_", ""),
        ("kitchen", ""),
    ])
    def test_allowed(self, old, new):
        check_prefix_separators(old, new)

    def test_new_prefix_missing_underscore(self):
        with pytest.raises(ValueError, match="would run together") as exc:
            check_prefix_separators("sky_old_", "skyvane_station")
        assert "'skyvane_station_'" in str(exc.value)

    def test_old_prefix_missing_underscore(self):
        with pytest.raises(ValueError, match="double underscore") as exc:
            check_prefix_separators("sky_old", "skyvane_station_")
        assert "'sky_old_'" in str(exc.value)
        assert "'skyvane_station'" in str(exc.value)


class TestStripNamePrefix:
    def test_strips_prefix_and_following_space(self):
        assert strip_name_prefix("Backyard Station Temperature", "Backyard Station") == "Temperature"

    def test_prefix_with_trailing_space(self):
        assert strip_name_prefix("Backyard Station Temperature", "Backyard Station ") == "Temperature"

    def test_no_match(self):
        assert strip_name_prefix("Temperature", "Backyard Station") is None

    def test_case_sensitive(self):
        assert strip_name_prefix("backyard station Temperature", "Backyard Station") is None

    def test_empty_prefix_never_matches(self):
        assert strip_name_prefix("Temperature", "") is None

    def test_would_leave_empty_name(self):
        assert strip_name_prefix("Backyard Station ", "Backyard Station") is None


class TestPlanRenames:
    def test_sample(self):
        renames = plan_renames(SAMPLE_ENTITIES, OLD, NEW, "Backyard Station")
        assert renames == [
            Rename("sensor.skyvane_skyvane_station_backyard_station_temperature", "sensor.skyvane_station_temperature",
                   "Backyard Station Temperature", "Temperature"),
            Rename("switch.skyvane_skyvane_station_backyard_station_heater", "switch.skyvane_station_heater",
                   "Backyard Station Heater", "Heater"),
            Rename("sensor.skyvane_skyvane_station_backyard_station_humidity", "sensor.skyvane_station_humidity",
                   "Humidity", None, uses_device_name=True),
        ]

    def test_user_name_takes_precedence_over_original_name(self):
        entries = [entity(f"sensor.{OLD}x", name="Backyard Station Custom", original_name="Backyard Station X")]
        [rename] = plan_renames(entries, OLD, NEW, "Backyard Station")
        assert rename.current_name == "Backyard Station Custom"
        assert rename.new_name == "Custom"

    def test_missing_names(self):
        [rename] = plan_renames([entity(f"sensor.{OLD}x")], OLD, NEW, "Backyard Station")
        assert rename.current_name == ""
        assert rename.new_name is None

    def test_prefix_only_matches_start_of_object_id(self):
        entries = [entity(f"sensor.other_{OLD}x"), entity(f"{OLD[:-1]}.x")]
        assert plan_renames(entries, OLD, NEW, "Backyard Station") == []

    def test_empty_new_prefix_removes_old_prefix(self):
        [rename] = plan_renames([entity(f"sensor.{OLD}temperature")], OLD, "", "X")
        assert rename.new_entity_id == "sensor.temperature"

    def test_no_entries(self):
        assert plan_renames([], OLD, NEW, "X") == []


class TestRename:
    def test_payload_with_id_and_name(self):
        r = Rename("sensor.a_x", "sensor.b_x", "Old X", "X")
        assert r.has_changes
        assert r.update_payload() == {
            "type": "config/entity_registry/update",
            "entity_id": "sensor.a_x",
            "new_entity_id": "sensor.b_x",
            "name": "X",
        }

    def test_payload_name_only(self):
        r = Rename("sensor.a_x", "sensor.a_x", "Old X", "X")
        assert r.has_changes
        assert r.update_payload() == {
            "type": "config/entity_registry/update",
            "entity_id": "sensor.a_x",
            "name": "X",
        }

    def test_no_changes(self):
        r = Rename("sensor.a_x", "sensor.a_x", "X", None)
        assert not r.has_changes
        assert "new_entity_id" not in r.update_payload()
        assert "name" not in r.update_payload()

    def test_describe_with_new_name(self):
        assert Rename("sensor.a_x", "sensor.b_x", "Old X", "X").describe() == [
            "sensor.a_x",
            "  -> sensor.b_x",
            "  name: 'Old X' -> 'X'",
        ]

    def test_describe_uses_device_name(self):
        lines = Rename("sensor.a_x", "sensor.b_x", "X", None, uses_device_name=True).describe()
        assert lines[-1] == "  name: uses device name as prefix (rename the device instead)"

    def test_describe_id_only(self):
        assert Rename("sensor.a_x", "sensor.b_x", "X", None).describe() == ["sensor.a_x", "  -> sensor.b_x"]
