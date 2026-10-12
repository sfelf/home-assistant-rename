import json
from dataclasses import asdict

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
                "version": 1,
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
            ('{"format": "ha-rename-undo", "version": 2}', "unsupported version 2"),
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
