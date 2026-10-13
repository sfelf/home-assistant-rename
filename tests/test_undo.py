import json
from dataclasses import asdict

import pytest

from ha_rename import __version__, undo
from ha_rename.references import Edit
from ha_rename.undo import (
    Change,
    ReferenceChange,
    UndoLog,
    UndoStep,
    load,
    plan_reference_undo,
    plan_undo,
)
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


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


RENAME_JSON = {
    "entity_id": "sensor.old_temp",
    "new_entity_id": "sensor.new_temp",
    "name": None,
    "new_name": "Temp",
    "registry_id": None,
}


class TestUndoLog:
    def test_create_writes_header(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        assert lines(path) == [
            {
                "format": "ha-rename-undo",
                "version": 2,
                "created_by": f"ha-rename {__version__}",
                "created_at": log.created_at,
                "ha_url": URL,
            }
        ]

    def test_record_appends_one_line_per_change(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        other = Change("sensor.a", "sensor.b", None, None)
        log.record(RENAME)
        before = path.read_text()
        log.record(other)
        after = path.read_text()
        assert after.startswith(before)  # appended, never rewritten
        assert lines(path)[1:] == [{"change": RENAME_JSON}, {"change": {**asdict(other)}}]
        assert log.changes == [RENAME, other]

    def test_forget_marks_change_rejected(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        log.forget(RENAME)
        assert lines(path)[1:] == [{"change": RENAME_JSON}, {"rejected": 0}]
        assert log.changes == []
        assert load(path).changes == []

    def test_never_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        path.write_text("keep me")
        with pytest.raises(undo.UndoFileError, match="could not create undo file"):
            UndoLog.create(path, URL)
        assert path.read_text() == "keep me"

    def test_create_in_missing_directory(self, tmp_path):
        with pytest.raises(undo.UndoFileError, match="could not create undo file"):
            UndoLog.create(tmp_path / "missing" / "undo.jsonl", URL)

    def fail_appends(self, log):
        log.path.chmod(0o444)  # read-only, so appending fails

    def test_record_failure_stops_before_the_change(self, tmp_path):
        log = UndoLog.create(tmp_path / "undo.jsonl", URL)
        self.fail_appends(log)
        with pytest.raises(undo.UndoFileError) as exc:
            log.record(RENAME)
        assert "could not update undo file" in str(exc.value)
        assert "Stopped before changing sensor.old_temp" in str(exc.value)
        assert log.changes == []

    def test_removed_file_is_not_recreated(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        path.unlink()
        other = Change("sensor.a", "sensor.b", None, None)
        with pytest.raises(undo.UndoFileError, match="Stopped before changing sensor.a"):
            log.record(other)
        assert not path.exists()

    def test_forget_failure_explains_the_extra_entry(self, tmp_path):
        log = UndoLog.create(tmp_path / "undo.jsonl", URL)
        log.record(RENAME)
        self.fail_appends(log)
        with pytest.raises(undo.UndoFileError, match="still lists sensor.old_temp"):
            log.forget(RENAME)

    def test_discard_if_empty(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        assert log.discard_if_empty()
        assert not path.exists()

    def test_discards_when_every_change_was_rejected(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        log.forget(RENAME)
        assert log.discard_if_empty()
        assert not path.exists()

    def test_keeps_file_with_changes(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        assert not log.discard_if_empty()
        assert path.exists()


HEADER = {"format": "ha-rename-undo", "version": 1, "ha_url": URL}
CHANGE = {"entity_id": "a.b", "new_entity_id": "a.c", "name": None, "new_name": "C"}


class TestLoad:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        kept = Change("sensor.a", "sensor.b", None, None)
        log.record(RENAME)
        log.record(kept)
        log.forget(RENAME)
        loaded = load(path)
        assert loaded.path == path
        assert loaded.ha_url == URL
        assert loaded.created_at == log.created_at
        assert loaded.changes == [kept]

    def write(self, tmp_path, *items, raw=None):
        path = tmp_path / "undo.jsonl"
        text = raw if raw is not None else "".join(json.dumps(i) + "\n" for i in items)
        path.write_text(text)
        return path

    def test_registry_id_is_optional(self, tmp_path):
        [change] = load(self.write(tmp_path, HEADER, {"change": CHANGE})).changes
        assert change == Change("a.b", "a.c", None, "C", None)

    def test_blank_lines_are_ignored(self, tmp_path):
        raw = json.dumps(HEADER) + "\n\n" + json.dumps({"change": CHANGE}) + "\n\n"
        assert len(load(self.write(tmp_path, raw=raw)).changes) == 1

    def test_half_written_last_line_is_ignored(self, tmp_path):
        raw = json.dumps(HEADER) + "\n" + json.dumps({"change": CHANGE}) + '\n{"change": {"ent'
        assert len(load(self.write(tmp_path, raw=raw)).changes) == 1

    def test_complete_but_malformed_last_line_is_an_error(self, tmp_path):
        raw = json.dumps(HEADER) + "\n" + json.dumps({"change": CHANGE}) + '\n{"change": {"ent\n'
        with pytest.raises(undo.UndoFileError, match="line 3"):
            load(self.write(tmp_path, raw=raw))

    def test_corrupt_line_in_the_middle_is_an_error(self, tmp_path):
        raw = json.dumps(HEADER) + "\nnot json\n" + json.dumps({"change": CHANGE}) + "\n"
        with pytest.raises(undo.UndoFileError, match="line 2"):
            load(self.write(tmp_path, raw=raw))

    def test_missing_file(self, tmp_path):
        with pytest.raises(undo.UndoFileError, match="could not read undo file"):
            load(tmp_path / "nope.jsonl")

    def test_not_utf8(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        path.write_bytes(b"\xff\xfe not text")
        with pytest.raises(undo.UndoFileError, match="not a valid undo file"):
            load(path)

    @pytest.mark.parametrize(
        "raw, message",
        [
            ("", "it's empty"),
            ("{not json", "not a valid undo file"),
            ("[]", "not an ha-rename undo file"),
            ('{"format": "other"}', "not an ha-rename undo file"),
            ('{"format": "ha-rename-undo", "version": 3}', "unsupported version 3"),
            ('{"format": "ha-rename-undo", "version": 1}', "missing or invalid ha_url"),
            ('{"format": "ha-rename-undo", "version": 1, "ha_url": 5}', "invalid ha_url"),
        ],
    )
    def test_invalid_headers(self, tmp_path, raw, message):
        with pytest.raises(undo.UndoFileError, match=message):
            load(self.write(tmp_path, raw=raw))

    @pytest.mark.parametrize(
        "item, message",
        [
            ({"change": {**CHANGE, "new_entity_id": 5}}, "line 2: invalid change"),
            ({"change": {**CHANGE, "name": 5}}, "line 2: invalid change"),
            ({"change": {**CHANGE, "registry_id": 5}}, "line 2: invalid change"),
            (
                {"change": {"entity_id": "a.b", "new_entity_id": "a.c", "name": None}},
                "line 2: missing 'new_name'",
            ),
            ({"change": "nope"}, "line 2"),
            ({"rejected": 0}, "line 2: unexpected entry"),
            ({"something": "else"}, "line 2: unexpected entry"),
            ([1, 2], "line 2: unexpected entry"),
        ],
    )
    def test_invalid_entries(self, tmp_path, item, message):
        # Each bad entry is followed by a valid line, so it isn't taken for a cut-off write.
        with pytest.raises(undo.UndoFileError, match=message):
            load(self.write(tmp_path, HEADER, item, {"change": CHANGE}))

    @pytest.mark.parametrize("value", [1, -1, True, "0"])
    def test_invalid_rejected_index(self, tmp_path, value):
        path = self.write(
            tmp_path, HEADER, {"change": CHANGE}, {"rejected": value}, {"change": CHANGE}
        )
        with pytest.raises(undo.UndoFileError, match="line 3: unexpected entry"):
            load(path)


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


EDIT = Edit(("triggers", 0, "entity_id"), "sensor.old_temp", "sensor.new_temp")
KEY_EDIT = Edit(("entities", "light.old"), "light.old", "light.new", is_key=True)
REFERENCE = ReferenceChange("automation", "17", "automation 'Heater off' (id 17)", (EDIT,))
REFERENCE_JSON = {
    "kind": "automation",
    "key": "17",
    "label": "automation 'Heater off' (id 17)",
    "edits": [
        {
            "path": ["triggers", 0, "entity_id"],
            "old": "sensor.old_temp",
            "new": "sensor.new_temp",
            "is_key": False,
        }
    ],
}


class TestReferenceChange:
    def test_describe(self):
        change = ReferenceChange("scene", "1", "scene 'Evening' (id 1)", (EDIT, KEY_EDIT))
        assert change.describe() == [
            "scene 'Evening' (id 1)",
            "  triggers[0].entity_id: sensor.old_temp -> sensor.new_temp",
            "  entities['light.old']: light.old -> light.new",
        ]

    def test_has_changes(self):
        assert REFERENCE.has_changes
        assert not ReferenceChange("automation", "17", "x", ()).has_changes

    def test_subject(self):
        assert REFERENCE.subject == "automation 'Heater off' (id 17)"
        assert RENAME.subject == "sensor.old_temp"


class TestReferenceRecords:
    def test_record_and_load(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        key_change = ReferenceChange("scene", "1", "scene 'Evening' (id 1)", (KEY_EDIT,))
        log.record(RENAME)
        log.record(REFERENCE)
        log.record(key_change)
        assert lines(path)[2] == {"reference": REFERENCE_JSON}
        assert log.changes == [RENAME, REFERENCE, key_change]
        loaded = load(path)
        assert loaded.items == [RENAME, REFERENCE, key_change]
        assert loaded.changes == [RENAME]

    def test_rejected_counts_both_kinds(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        log.record(RENAME)
        log.record(REFERENCE)
        log.forget(REFERENCE)
        assert lines(path)[3] == {"rejected": 1}
        assert log.changes == [RENAME]
        assert load(path).items == [RENAME]

    def test_only_references_keep_the_file(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        log.record(REFERENCE)
        assert not log.discard_if_empty()
        assert path.exists()

    def test_write_failure_names_the_target(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        log = UndoLog.create(path, URL)
        path.unlink()
        with pytest.raises(
            undo.UndoFileError, match="Stopped before changing automation 'Heater off'"
        ):
            log.record(REFERENCE)

    def test_version_1_file(self, tmp_path):
        # Written by v0.2.0: entity changes only.
        path = tmp_path / "undo.jsonl"
        path.write_text(
            json.dumps({**HEADER, "created_by": "ha-rename 0.2.0", "created_at": "2026-10-11"})
            + "\n"
            + json.dumps({"change": RENAME_JSON})
            + "\n"
        )
        loaded = load(path)
        assert loaded.items == loaded.changes == [RENAME]

    def test_half_written_reference_is_ignored(self, tmp_path):
        path = tmp_path / "undo.jsonl"
        raw = json.dumps(HEADER) + "\n" + json.dumps({"change": CHANGE}) + '\n{"reference": {"ki'
        path.write_text(raw)
        assert len(load(path).items) == 1

    @pytest.mark.parametrize(
        "reference, message",
        [
            ({**REFERENCE_JSON, "kind": 5}, "invalid reference"),
            ({**REFERENCE_JSON, "key": None}, "invalid reference"),
            ({**REFERENCE_JSON, "edits": "nope"}, "invalid reference"),
            (
                {
                    **REFERENCE_JSON,
                    "edits": [{"path": "x", "old": "a", "new": "b", "is_key": False}],
                },
                "invalid edit",
            ),
            (
                {
                    **REFERENCE_JSON,
                    "edits": [{"path": [True], "old": "a", "new": "b", "is_key": False}],
                },
                "invalid edit",
            ),
            (
                {**REFERENCE_JSON, "edits": [{"path": [], "old": 1, "new": "b", "is_key": False}]},
                "invalid edit",
            ),
            (
                {
                    **REFERENCE_JSON,
                    "edits": [{"path": [], "old": "a", "new": None, "is_key": False}],
                },
                "invalid edit",
            ),
            (
                {**REFERENCE_JSON, "edits": [{"path": [], "old": "a", "new": "b", "is_key": 0}]},
                "invalid edit",
            ),
            (
                {**REFERENCE_JSON, "edits": [{"path": [], "old": "a", "new": "b"}]},
                "missing 'is_key'",
            ),
            ({"kind": "automation", "key": "1", "edits": []}, "missing 'label'"),
        ],
    )
    def test_invalid_references(self, tmp_path, reference, message):
        path = tmp_path / "undo.jsonl"
        path.write_text(
            "".join(
                json.dumps(i) + "\n" for i in [HEADER, {"reference": reference}, {"change": CHANGE}]
            )
        )
        with pytest.raises(undo.UndoFileError, match=f"line 2: {message}"):
            load(path)


class TestPlanReferenceUndo:
    AFTER = {"triggers": [{"entity_id": "sensor.new_temp"}], "alias": "Heater off"}

    def test_reverts(self):
        step = plan_reference_undo(REFERENCE, self.AFTER)
        assert step.config == {
            "triggers": [{"entity_id": "sensor.old_temp"}],
            "alias": "Heater off",
        }
        assert step.change == ReferenceChange(
            "automation", "17", "automation 'Heater off' (id 17)", (EDIT.reversed(),)
        )
        assert step.notes == ()
        assert step.describe() == [
            "automation 'Heater off' (id 17)",
            "  triggers[0].entity_id: sensor.new_temp -> sensor.old_temp",
        ]

    def test_edited_since(self):
        current = {"triggers": [{"entity_id": "sensor.other"}]}
        step = plan_reference_undo(REFERENCE, current)
        assert step.config == current
        assert not step.change.has_changes
        assert step.describe() == [
            "automation 'Heater off' (id 17)",
            "  triggers[0].entity_id: changed since to 'sensor.other'; left as is",
        ]

    def test_already_reverted(self):
        step = plan_reference_undo(REFERENCE, {"triggers": [{"entity_id": "sensor.old_temp"}]})
        assert step.describe() == [
            "automation 'Heater off' (id 17)",
            "  triggers[0].entity_id: changed since to 'sensor.old_temp'; left as is",
        ]

    def test_nothing_recorded(self):
        empty = ReferenceChange("automation", "17", "automation 'Heater off' (id 17)", ())
        step = plan_reference_undo(empty, self.AFTER)
        assert step.describe() == [
            "automation 'Heater off' (id 17)",
            "  nothing to undo (already as it was before)",
        ]

    def test_only_restored_ids(self):
        step = plan_reference_undo(REFERENCE, self.AFTER, restored={"sensor.other"})
        assert step.config == self.AFTER
        assert not step.change.has_changes
        assert step.notes == (
            "triggers[0].entity_id: sensor.old_temp wasn't given back its ID; left as is",
        )

    def test_restored_id(self):
        step = plan_reference_undo(REFERENCE, self.AFTER, restored={"sensor.old_temp"})
        assert step.config["triggers"] == [{"entity_id": "sensor.old_temp"}]

    def test_reports_references_left_behind(self):
        current = {"triggers": [{"entity_id": "sensor.new_temp"}, {"entity_id": "sensor.new_temp"}]}
        step = plan_reference_undo(REFERENCE, current)
        assert step.config["triggers"][0] == {"entity_id": "sensor.old_temp"}
        assert step.notes == ("triggers[1].entity_id: still refers to sensor.new_temp; left as is",)

    def test_removed(self):
        step = plan_reference_undo(REFERENCE, None)
        assert step.change is None
        assert step.describe() == [
            "automation 'Heater off' (id 17)",
            "  SKIPPED: not found in Home Assistant (removed since?)",
        ]
