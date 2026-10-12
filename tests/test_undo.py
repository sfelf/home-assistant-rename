import json

import pytest

from ha_rename import __version__, undo
from ha_rename.undo import Change, UndoLog, UndoStep, load, plan_undo
from tests.conftest import entity

URL = "ws://ha.test/api/websocket"
RENAME = Change("sensor.old_temp", "sensor.new_temp", None, "Temp")


class TestChange:
    def test_payload_renames_and_sets_name(self):
        assert RENAME.payload() == {
            "type": "config/entity_registry/update",
            "entity_id": "sensor.old_temp",
            "new_entity_id": "sensor.new_temp",
            "name": "Temp",
        }

    def test_payload_can_clear_name(self):
        change = Change("sensor.new_temp", "sensor.old_temp", "Temp", None)
        assert change.payload()["name"] is None

    def test_payload_id_only(self):
        change = Change("sensor.a", "sensor.b", "Same", "Same")
        assert change.payload() == {
            "type": "config/entity_registry/update",
            "entity_id": "sensor.a",
            "new_entity_id": "sensor.b",
        }

    def test_payload_name_only(self):
        assert Change("sensor.a", "sensor.a", "X", "Y").payload() == {
            "type": "config/entity_registry/update",
            "entity_id": "sensor.a",
            "name": "Y",
        }

    def test_no_changes(self):
        assert not Change("sensor.a", "sensor.a", "X", "X").has_changes

    def test_describe(self):
        assert RENAME.describe() == [
            "sensor.old_temp",
            "  -> sensor.new_temp",
            "  name: (integration's name) -> 'Temp'",
        ]

    def test_describe_name_only(self):
        assert Change("sensor.a", "sensor.a", "X", None).describe() == [
            "sensor.a",
            "  name: 'X' -> (integration's name)",
        ]


class TestUndoLog:
    def test_create_writes_empty_log(self, tmp_path):
        path = tmp_path / "undo.json"
        UndoLog.create(path, URL)
        data = json.loads(path.read_text())
        assert data["format"] == "ha-rename-undo"
        assert data["version"] == 1
        assert data["created_by"] == f"ha-rename {__version__}"
        assert data["ha_url"] == URL
        assert data["changes"] == []

    def test_record_updates_file_after_each_change(self, tmp_path):
        path = tmp_path / "undo.json"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        assert json.loads(path.read_text())["changes"] == [
            {
                "entity_id": "sensor.old_temp",
                "new_entity_id": "sensor.new_temp",
                "name": None,
                "new_name": "Temp",
                "registry_id": None,
            }
        ]
        assert not (tmp_path / "undo.json.tmp").exists()

    def test_forget_removes_rejected_change(self, tmp_path):
        path = tmp_path / "undo.json"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        log.forget(RENAME)
        assert json.loads(path.read_text())["changes"] == []

    def test_never_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "undo.json"
        path.write_text("keep me")
        with pytest.raises(undo.UndoFileError, match="could not create undo file"):
            UndoLog.create(path, URL)
        assert path.read_text() == "keep me"

    def test_create_in_missing_directory(self, tmp_path):
        with pytest.raises(undo.UndoFileError, match="could not create undo file"):
            UndoLog.create(tmp_path / "missing" / "undo.json", URL)

    def fail_writes(self, monkeypatch):
        def fail(*args):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("os.replace", fail)

    def test_record_failure_stops_before_the_change(self, tmp_path, monkeypatch):
        log = UndoLog.create(tmp_path / "undo.json", URL)
        self.fail_writes(monkeypatch)
        with pytest.raises(undo.UndoFileError) as exc:
            log.record(RENAME)
        assert "No space left on device" in str(exc.value)
        assert "Stopped before changing sensor.old_temp" in str(exc.value)

    def test_forget_failure_explains_the_extra_entry(self, tmp_path, monkeypatch):
        log = UndoLog.create(tmp_path / "undo.json", URL)
        log.record(RENAME)
        self.fail_writes(monkeypatch)
        with pytest.raises(undo.UndoFileError, match="still lists sensor.old_temp"):
            log.forget(RENAME)

    def test_discard_if_empty(self, tmp_path):
        path = tmp_path / "undo.json"
        log = UndoLog.create(path, URL)
        assert log.discard_if_empty()
        assert not path.exists()

    def test_keeps_file_with_changes(self, tmp_path):
        path = tmp_path / "undo.json"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        assert not log.discard_if_empty()
        assert path.exists()


class TestLoad:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "undo.json"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        loaded = load(path)
        assert loaded.path == path
        assert loaded.ha_url == URL
        assert loaded.created_at == log.created_at
        assert loaded.changes == [RENAME]

    def write(self, tmp_path, data):
        path = tmp_path / "undo.json"
        path.write_text(json.dumps(data) if not isinstance(data, str) else data)
        return path

    def valid(self, **overrides):
        data = {
            "format": "ha-rename-undo",
            "version": 1,
            "ha_url": URL,
            "changes": [
                {"entity_id": "a.b", "new_entity_id": "a.c", "name": None, "new_name": "C"}
            ],
        }
        return {**data, **overrides}

    def test_missing_file(self, tmp_path):
        with pytest.raises(undo.UndoFileError, match="could not read undo file"):
            load(tmp_path / "nope.json")

    @pytest.mark.parametrize(
        "data, message",
        [
            ("{not json", "not a valid undo file"),
            ([], "not a valid undo file"),
            ({"format": "other"}, "not an ha-rename undo file"),
            ({"format": "ha-rename-undo", "version": 2}, "unsupported version 2"),
            ({"format": "ha-rename-undo", "version": 1}, "missing 'ha_url'"),
        ],
    )
    def test_invalid_files(self, tmp_path, data, message):
        with pytest.raises(undo.UndoFileError, match=message):
            load(self.write(tmp_path, data))

    def test_not_utf8(self, tmp_path):
        path = tmp_path / "undo.json"
        path.write_bytes(b"\xff\xfe not text")
        with pytest.raises(undo.UndoFileError, match="not a valid undo file"):
            load(path)

    def test_registry_id_is_optional(self, tmp_path):
        [change] = load(self.write(tmp_path, self.valid())).changes
        assert change.registry_id is None

    def test_invalid_url(self, tmp_path):
        with pytest.raises(undo.UndoFileError, match="invalid ha_url"):
            load(self.write(tmp_path, self.valid(ha_url=5)))

    @pytest.mark.parametrize(
        "change",
        [
            {"entity_id": "a.b", "new_entity_id": 5, "name": None, "new_name": None},
            {"entity_id": "a.b", "new_entity_id": "a.c", "name": 5, "new_name": None},
            {
                "entity_id": "a.b",
                "new_entity_id": "a.c",
                "name": None,
                "new_name": None,
                "registry_id": 5,
            },
        ],
    )
    def test_invalid_change(self, tmp_path, change):
        with pytest.raises(undo.UndoFileError, match="invalid change"):
            load(self.write(tmp_path, self.valid(changes=[change])))

    def test_change_missing_field(self, tmp_path):
        change = {"entity_id": "a.b", "new_entity_id": "a.c", "name": None}
        with pytest.raises(undo.UndoFileError, match="missing 'new_name'"):
            load(self.write(tmp_path, self.valid(changes=[change])))


class TestPlanUndo:
    def test_reverses_id_and_name(self):
        entries = [entity("sensor.new_temp", name="Temp", original_name="Old Temp")]
        [step] = plan_undo(entries, [RENAME])
        assert step == UndoStep(
            RENAME,
            Change("sensor.new_temp", "sensor.old_temp", "Temp", None, "reg-sensor.new_temp"),
        )
        assert step.describe() == [
            "sensor.new_temp",
            "  -> sensor.old_temp",
            "  name: 'Temp' -> (integration's name)",
        ]

    def test_newest_change_first(self):
        first = Change("a.one", "a.two", None, None)
        second = Change("a.two", "a.three", None, None)
        steps = plan_undo([entity("a.three")], [first, second])
        assert [s.recorded for s in steps] == [second, first]
        assert steps[0].change.new_entity_id == "a.two"
        assert steps[1].change is None  # a.two doesn't exist yet when planning

    def test_missing_entity_is_skipped(self):
        [step] = plan_undo([], [RENAME])
        assert step.change is None
        assert step.describe() == [
            "sensor.new_temp",
            "  SKIPPED: not found in Home Assistant (renamed or removed since?)",
        ]

    def test_finds_entity_by_registry_id_after_rename(self):
        recorded = Change("sensor.old_temp", "sensor.new_temp", None, "Temp", "reg-1")
        entries = [entity("sensor.mine", name="Temp", id="reg-1")]
        [step] = plan_undo(entries, [recorded])
        assert step.change == Change("sensor.mine", "sensor.mine", "Temp", None, "reg-1")
        assert step.notes == ("entity ID: changed since to sensor.mine; left as is",)

    def test_entity_with_same_id_but_different_registry_id_is_not_touched(self):
        recorded = Change("sensor.old_temp", "sensor.new_temp", None, "Temp", "reg-1")
        entries = [entity("sensor.new_temp", name="Temp", id="reg-2")]
        [step] = plan_undo(entries, [recorded])
        assert step.change is None
        assert step.describe()[-1] == "  SKIPPED: not found in Home Assistant (removed since?)"

    def test_change_that_was_never_applied_is_left_alone(self):
        recorded = Change("sensor.old_temp", "sensor.new_temp", None, "Temp", "reg-1")
        entries = [entity("sensor.old_temp", id="reg-1")]
        [step] = plan_undo(entries, [recorded])
        assert not step.change.has_changes
        assert step.notes == ()
        assert step.describe() == [
            "sensor.old_temp",
            "  nothing to undo (already as it was before)",
        ]

    def test_name_changed_since_is_left_alone(self):
        entries = [entity("sensor.new_temp", name="My own name")]
        [step] = plan_undo(entries, [RENAME])
        assert step.change.new_name == "My own name"
        assert step.describe()[-1] == "  name: changed since to 'My own name'; left as is"

    def test_name_already_restored_needs_no_note(self):
        [step] = plan_undo([entity("sensor.new_temp", name=None)], [RENAME])
        assert step.change.new_name is None
        assert step.notes == ()

    def test_id_only_change_keeps_current_name(self):
        recorded = Change("sensor.old", "sensor.new", "Same", "Same")
        [step] = plan_undo([entity("sensor.new", name="Edited")], [recorded])
        assert step.change == Change(
            "sensor.new", "sensor.old", "Edited", "Edited", "reg-sensor.new"
        )
        assert step.notes == ()
