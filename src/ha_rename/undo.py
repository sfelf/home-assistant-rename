"""Record applied changes in an undo file, and work out how to reverse them."""

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ha_rename import __version__
from ha_rename.references import (
    EDITABLE_KINDS,
    Edit,
    format_path,
    rebase,
    revert_edits,
    scan,
)

FORMAT = "ha-rename-undo"
# Version 2 added reference updates; version 1 files only have entity changes.
FORMAT_VERSION = 2
READABLE_VERSIONS = (1, 2)


class UndoFileError(Exception):
    """The undo file couldn't be written or read."""


def _show_name(name: str | None) -> str:
    return repr(name) if name is not None else "(integration's name)"


@dataclass(frozen=True)
class Change:
    """One entity registry update: the entity ID and registry name before and after.

    The names are the registry's ``name`` field, which is None when the entity uses the
    name supplied by its integration. ``registry_id`` is the registry entry's own ``id``,
    which stays the same when the entity is renamed.
    """

    entity_id: str
    new_entity_id: str
    name: str | None
    new_name: str | None
    registry_id: str | None = None

    @property
    def subject(self) -> str:
        return self.entity_id

    @property
    def renames_entity(self) -> bool:
        return self.new_entity_id != self.entity_id

    @property
    def changes_name(self) -> bool:
        return self.new_name != self.name

    @property
    def has_changes(self) -> bool:
        return self.renames_entity or self.changes_name

    def payload(self) -> dict[str, Any]:
        """The ``config/entity_registry/update`` command for this change (without ``id``)."""
        payload: dict[str, Any] = {
            "type": "config/entity_registry/update",
            "entity_id": self.entity_id,
        }
        if self.renames_entity:
            payload["new_entity_id"] = self.new_entity_id
        if self.changes_name:
            # None clears the custom name, so the integration's name is used again.
            payload["name"] = self.new_name
        return payload

    def describe(self) -> list[str]:
        lines = [self.entity_id]
        if self.renames_entity:
            lines.append(f"  -> {self.new_entity_id}")
        if self.changes_name:
            lines.append(f"  name: {_show_name(self.name)} -> {_show_name(self.new_name)}")
        return lines


@dataclass(frozen=True)
class ReferenceChange:
    """Edits to the references in one target (an automation, a dashboard...).

    ``kind`` and ``key`` identify the target, as in :class:`ha_rename.references.Target`,
    and ``label`` describes it for the user.
    """

    kind: str
    key: str
    label: str
    edits: tuple[Edit, ...]

    @property
    def subject(self) -> str:
        return self.label

    @property
    def has_changes(self) -> bool:
        return bool(self.edits)

    def describe(self) -> list[str]:
        return [self.label, *(f"  {edit.describe()}" for edit in self.edits)]

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "label": self.label,
            "edits": [
                {"path": list(e.path), "old": e.old, "new": e.new, "is_key": e.is_key}
                for e in self.edits
            ],
        }


Recorded = Change | ReferenceChange


class UndoLog:
    """An undo file: a journal of every change sent to Home Assistant.

    The file is JSON Lines: a header line, then one line per change, appended *before*
    the change is sent: ``{"change": ...}`` for an entity, ``{"reference": ...}`` for
    the references in a target. If Home Assistant rejects a change, a
    ``{"rejected": n}`` line marks the n-th recorded change (of either kind) as not
    applied. Each change costs one appended line, however many came before it, and a
    change whose reply is lost (e.g. the connection drops) is still in the file.
    :func:`plan_undo` and :func:`plan_reference_undo` check the current state, so a
    change that was recorded but never applied is simply left alone.
    """

    def __init__(self, path: Path, url: str):
        self.path = path
        self.url = url
        self.created_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._recorded: list[Recorded] = []
        self._rejected: set[int] = set()

    @property
    def changes(self) -> list[Recorded]:
        """The recorded changes Home Assistant didn't reject."""
        return [c for i, c in enumerate(self._recorded) if i not in self._rejected]

    @classmethod
    def create(cls, path: Path, url: str) -> "UndoLog":
        """Create the file now, so a problem writing it shows up before anything changes."""
        log = cls(path, url)
        header = {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "created_by": f"ha-rename {__version__}",
            "created_at": log.created_at,
            "ha_url": url,
        }
        try:
            with path.open("x", encoding="utf-8") as f:  # "x": never overwrite an undo file
                f.write(json.dumps(header) + "\n")
        except OSError as e:
            raise UndoFileError(f"could not create undo file {path}: {e.strerror or e}") from e
        return log

    def record(self, change: Recorded) -> None:
        """Record ``change`` before it is sent."""
        if isinstance(change, Change):
            item = {"change": asdict(change)}
        else:
            item = {"reference": change.to_json()}
        self._append(item, f"Stopped before changing {change.subject}")
        self._recorded.append(change)

    def forget(self, change: Recorded) -> None:
        """Mark ``change`` as rejected by Home Assistant."""
        index = max(
            i for i, c in enumerate(self._recorded) if c == change and i not in self._rejected
        )
        self._append({"rejected": index}, f"It still lists {change.subject}, which wasn't changed")
        self._rejected.add(index)

    def _append(self, item: dict[str, Any], consequence: str) -> None:
        try:
            # No O_CREAT: if the file was removed mid-run, fail instead of starting a new
            # file without the header and earlier changes.
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            with open(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(item) + "\n")
        except OSError as e:
            raise UndoFileError(
                f"could not update undo file {self.path}: {e.strerror or e}. {consequence}"
            ) from e

    def discard_if_empty(self) -> bool:
        """Delete the file if no change was applied. Returns True if it was deleted."""
        if self.changes:
            return False
        self.path.unlink(missing_ok=True)
        return True


@dataclass(frozen=True)
class UndoFile:
    path: Path
    ha_url: str
    created_at: str
    items: list[Recorded]

    @property
    def changes(self) -> list[Change]:
        """Just the entity changes."""
        return [item for item in self.items if isinstance(item, Change)]


def _change_from_json(data: Any) -> Change:
    ids = [data["entity_id"], data["new_entity_id"]]
    optional = [data["name"], data["new_name"], data.get("registry_id")]
    if not all(isinstance(v, str) for v in ids) or not all(
        v is None or isinstance(v, str) for v in optional
    ):
        raise ValueError(f"invalid change {data!r}")
    return Change(*ids, *optional)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_path_element(value: Any) -> bool:
    return _is_str(value) or (isinstance(value, int) and not isinstance(value, bool))


def _edit_from_json(data: Any) -> Edit:
    path, old, new, is_key = data["path"], data["old"], data["new"], data["is_key"]
    if not (
        isinstance(path, list)
        and all(_is_path_element(p) for p in path)
        and _is_str(old)
        and _is_str(new)
        and isinstance(is_key, bool)
    ):
        raise ValueError(f"invalid edit {data!r}")
    return Edit(tuple(path), old, new, is_key)


def _reference_from_json(data: Any) -> ReferenceChange:
    fields = [data["kind"], data["key"], data["label"]]
    if (
        not all(_is_str(v) for v in fields)
        or data["kind"] not in EDITABLE_KINDS
        or not isinstance(data["edits"], list)
    ):
        raise ValueError(f"invalid reference {data!r}")
    return ReferenceChange(*fields, tuple(_edit_from_json(e) for e in data["edits"]))


def _is_index(value: Any, length: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < length


def _parse_header(line: str) -> dict[str, Any]:
    header = json.loads(line)
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise ValueError("not an ha-rename undo file")
    if header.get("version") not in READABLE_VERSIONS:
        raise ValueError(f"unsupported version {header.get('version')!r}")
    if not isinstance(header.get("ha_url"), str):
        raise ValueError("missing or invalid ha_url")
    return header


def load(path: Path) -> UndoFile:
    """Read an undo file written by :class:`UndoLog`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise UndoFileError(f"could not read undo file {path}: {e.strerror or e}") from e
    except ValueError as e:  # not UTF-8 text
        raise UndoFileError(f"{path} is not a valid undo file: {e}") from e

    lines = text.splitlines()
    numbered = [(n, line) for n, line in enumerate(lines, start=1) if line.strip()]
    if not numbered:
        raise UndoFileError(f"{path} is not a valid undo file: it's empty")
    try:
        header = _parse_header(numbered[0][1])
    except ValueError as e:
        raise UndoFileError(f"{path} is not a valid undo file: {e}") from e

    recorded: list[Recorded] = []
    rejected: set[int] = set()
    for n, line in numbered[1:]:
        try:
            item = json.loads(line)
        except ValueError as e:
            if n == len(lines) and not text.endswith("\n"):
                # A last line with no newline was cut short when the run was killed while
                # writing it. Changes are sent only after their whole line is written, so
                # this one was never sent. A complete line that's malformed is an error.
                break
            raise UndoFileError(f"{path} is not a valid undo file: line {n}: {e}") from e
        try:
            if isinstance(item, dict) and "change" in item:
                recorded.append(_change_from_json(item["change"]))
            elif isinstance(item, dict) and "reference" in item:
                recorded.append(_reference_from_json(item["reference"]))
            elif isinstance(item, dict) and _is_index(item.get("rejected"), len(recorded)):
                rejected.add(item["rejected"])
            else:
                raise ValueError(f"unexpected entry {item!r}")
        except KeyError as e:
            raise UndoFileError(f"{path} is not a valid undo file: line {n}: missing {e}") from e
        except (TypeError, ValueError) as e:
            raise UndoFileError(f"{path} is not a valid undo file: line {n}: {e}") from e

    return UndoFile(
        path=path,
        ha_url=header["ha_url"],
        created_at=str(header.get("created_at", "")),
        items=[c for i, c in enumerate(recorded) if i not in rejected],
    )


@dataclass(frozen=True)
class UndoStep:
    """How to reverse one recorded change, given Home Assistant's current state."""

    recorded: Change
    change: Change | None  # None when the entity can't be found
    notes: tuple[str, ...] = ()

    def describe(self) -> list[str]:
        if self.change is None:
            return [self.recorded.new_entity_id, *(f"  SKIPPED: {n}" for n in self.notes)]
        if not self.change.has_changes and not self.notes:
            return [self.change.entity_id, "  nothing to undo (already as it was before)"]
        return [*self.change.describe(), *(f"  {n}" for n in self.notes)]


def plan_undo(entries: list[dict[str, Any]], changes: list[Change]) -> list[UndoStep]:
    """Work out how to reverse ``changes``, newest first.

    Each change is compared with the entity's current state: only what still matches the
    recorded "after" state is put back, so edits made since are kept, and a recorded
    change that was never applied is left alone.
    """
    by_registry_id = {e["id"]: e for e in entries if e.get("id") is not None}
    by_entity_id = {e["entity_id"]: e for e in entries}
    steps = []
    for recorded in reversed(changes):
        # By registry id if recorded: it survives renames, unlike the entity ID.
        if recorded.registry_id is not None:
            entry = by_registry_id.get(recorded.registry_id)
        else:
            entry = by_entity_id.get(recorded.new_entity_id)
        if entry is None:
            reason = "not found in Home Assistant (renamed or removed since?)"
            if recorded.registry_id is not None:
                reason = "not found in Home Assistant (removed since?)"
            steps.append(UndoStep(recorded, None, (reason,)))
            continue

        current_id = entry["entity_id"]
        current_name = entry.get("name")
        notes = []

        restored_id = current_id
        if current_id == recorded.new_entity_id:
            restored_id = recorded.entity_id
        elif current_id != recorded.entity_id:
            notes.append(f"entity ID: changed since to {current_id}; left as is")

        restored_name = current_name
        if recorded.changes_name and current_name not in (recorded.name, recorded.new_name):
            notes.append(f"name: changed since to {_show_name(current_name)}; left as is")
        elif recorded.changes_name and current_name == recorded.new_name:
            restored_name = recorded.name

        change = Change(current_id, restored_id, current_name, restored_name, entry.get("id"))
        steps.append(UndoStep(recorded, change, tuple(notes)))
    return steps


@dataclass(frozen=True)
class ReferenceUndoStep:
    """How to reverse the edits recorded for one target, given its current config.

    ``change`` holds the edits that put the old values back (so it can itself be
    recorded and undone), and ``config`` the config to save; ``change`` is None when
    the target can't be found.
    """

    recorded: ReferenceChange
    change: ReferenceChange | None
    config: Any = None
    notes: tuple[str, ...] = ()

    def describe(self) -> list[str]:
        if self.change is None:
            return [self.recorded.label, *(f"  SKIPPED: {n}" for n in self.notes)]
        if not self.change.has_changes and not self.notes:
            return [self.recorded.label, "  nothing to undo (already as it was before)"]
        return [self.recorded.label, *self.change.describe()[1:], *(f"  {n}" for n in self.notes)]


def plan_reference_undo(
    recorded: ReferenceChange, current: Any, restored: set[str] | None = None
) -> ReferenceUndoStep:
    """Work out how to reverse ``recorded``, given the target's ``current`` config.

    Like :func:`plan_undo`, only locations that still hold the recorded new value are put
    back. ``current`` is None when the target no longer exists. With ``restored`` (the
    entity IDs that have been given back), a reference is only pointed back at an ID in
    it, so it never ends up on an entity that took that ID since. Afterwards, any
    reference to a restored entity's new ID that is still in the target (e.g. in an item
    moved since) is noted, so it can be fixed by hand.
    """
    if current is None:
        return ReferenceUndoStep(
            recorded, None, notes=("not found in Home Assistant (removed since?)",)
        )
    edits = [e for e in recorded.edits if restored is None or e.old in restored]
    notes = [
        f"{format_path(e.path)}: {e.old} wasn't given back its ID; left as is"
        for e in recorded.edits
        if e not in edits
    ]
    result = revert_edits(current, edits)
    notes += result.notes
    # Locations just put back hold an old ID that's right, even if it's also another
    # entity's new ID (a -> aa while aa -> aaa); anywhere else, it's a leftover.
    put_back = {(*e.path[:-1], e.new) if e.is_key else e.path for e in result.applied}
    leftovers, _ = scan(result.config, {e.new: e.old for e in edits})
    notes += [
        f"{format_path(e.path)}: still refers to {e.old}; left as is"
        for e in leftovers
        if e.path not in put_back
    ]
    # Recorded so this undo can itself be undone (redo).
    change = ReferenceChange(recorded.kind, recorded.key, recorded.label, rebase(result.applied))
    return ReferenceUndoStep(recorded, change, result.config, tuple(notes))
