"""Record applied changes in an undo file, and work out how to reverse them."""

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ha_rename import __version__

FORMAT = "ha-rename-undo"
FORMAT_VERSION = 1


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


class UndoLog:
    """An undo file listing every change sent to Home Assistant.

    Each change is recorded *before* it is sent and removed again only if Home Assistant
    rejects it, so a change whose reply is lost (e.g. the connection drops) is still in
    the file. :func:`plan_undo` checks the registry, so a change that was recorded but
    never applied is simply left alone.
    """

    def __init__(self, path: Path, url: str):
        self.path = path
        self.url = url
        self.created_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.changes: list[Change] = []

    @classmethod
    def create(cls, path: Path, url: str) -> "UndoLog":
        """Create the file now, so a problem writing it shows up before anything changes."""
        log = cls(path, url)
        try:
            with path.open("x", encoding="utf-8") as f:  # "x": never overwrite an undo file
                f.write(log._dump())
        except OSError as e:
            raise UndoFileError(f"could not create undo file {path}: {e.strerror or e}") from e
        return log

    def record(self, change: Change) -> None:
        """Record ``change`` before it is sent."""
        self.changes.append(change)
        self._write(f"Stopped before changing {change.entity_id}")

    def forget(self, change: Change) -> None:
        """Remove ``change`` after Home Assistant rejected it."""
        self.changes.remove(change)
        self._write(f"It still lists {change.entity_id}, which wasn't changed")

    def _write(self, consequence: str) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(self._dump(), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            raise UndoFileError(
                f"could not update undo file {self.path}: {e.strerror or e}. {consequence}"
            ) from e

    def discard_if_empty(self) -> bool:
        """Delete the file if nothing was recorded. Returns True if it was deleted."""
        if self.changes:
            return False
        self.path.unlink(missing_ok=True)
        return True

    def _dump(self) -> str:
        data = {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "created_by": f"ha-rename {__version__}",
            "created_at": self.created_at,
            "ha_url": self.url,
            "changes": [asdict(c) for c in self.changes],
        }
        return json.dumps(data, indent=2) + "\n"


@dataclass(frozen=True)
class UndoFile:
    path: Path
    ha_url: str
    created_at: str
    changes: list[Change]


def _change_from_json(data: Any) -> Change:
    ids = [data["entity_id"], data["new_entity_id"]]
    optional = [data["name"], data["new_name"], data.get("registry_id")]
    if not all(isinstance(v, str) for v in ids) or not all(
        v is None or isinstance(v, str) for v in optional
    ):
        raise ValueError(f"invalid change {data!r}")
    return Change(*ids, *optional)


def load(path: Path) -> UndoFile:
    """Read an undo file written by :class:`UndoLog`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise UndoFileError(f"could not read undo file {path}: {e.strerror or e}") from e
    except ValueError as e:  # invalid JSON or text that isn't UTF-8
        raise UndoFileError(f"{path} is not a valid undo file: {e}") from e

    try:
        if data.get("format") != FORMAT:
            raise ValueError("not an ha-rename undo file")
        if data.get("version") != FORMAT_VERSION:
            raise ValueError(f"unsupported version {data.get('version')!r}")
        if not isinstance(data["ha_url"], str):
            raise ValueError("invalid ha_url")
        return UndoFile(
            path=path,
            ha_url=data["ha_url"],
            created_at=str(data.get("created_at", "")),
            changes=[_change_from_json(c) for c in data["changes"]],
        )
    except KeyError as e:
        raise UndoFileError(f"{path} is not a valid undo file: missing {e}") from e
    except (AttributeError, TypeError, ValueError) as e:
        raise UndoFileError(f"{path} is not a valid undo file: {e}") from e


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


def _find(recorded: Change, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The registry entry ``recorded`` changed: by registry id if known, else by entity ID."""
    if recorded.registry_id is not None:
        return next((e for e in entries if e.get("id") == recorded.registry_id), None)
    return next((e for e in entries if e["entity_id"] == recorded.new_entity_id), None)


def plan_undo(entries: list[dict[str, Any]], changes: list[Change]) -> list[UndoStep]:
    """Work out how to reverse ``changes``, newest first.

    Each change is compared with the entity's current state: only what still matches the
    recorded "after" state is put back, so edits made since are kept, and a recorded
    change that was never applied is left alone.
    """
    steps = []
    for recorded in reversed(changes):
        entry = _find(recorded, entries)
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
