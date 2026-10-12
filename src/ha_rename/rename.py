"""Pure logic for working out which entities to rename and how."""

import re
from dataclasses import dataclass
from typing import Any

from ha_rename.undo import Change

# Home Assistant object IDs (the part after "<domain>.") are lowercase snake case.
_OBJECT_ID_CHARS = re.compile(r"[a-z0-9_]*")


def validate_id_prefix(prefix: str, *, allow_empty: bool) -> str:
    """Return ``prefix`` if it is a valid object ID prefix, else raise ValueError."""
    if not prefix and not allow_empty:
        raise ValueError("prefix must not be empty")
    if not _OBJECT_ID_CHARS.fullmatch(prefix):
        raise ValueError(
            f"{prefix!r} is not a valid entity ID prefix "
            "(use only lowercase letters, digits and underscores)"
        )
    return prefix


def check_prefix_separators(old_prefix: str, new_prefix: str) -> None:
    """Raise ValueError if only one of the prefixes ends with an underscore.

    Prefixes are swapped as plain text, so a mismatch runs words together
    (``skyvane_station`` + ``temperature``) or doubles the separator. An empty
    ``new_prefix`` (just remove the old one) is always allowed.
    """
    if not new_prefix or old_prefix.endswith("_") == new_prefix.endswith("_"):
        return
    if old_prefix.endswith("_"):
        raise ValueError(
            f"--old-id-prefix {old_prefix!r} ends with '_' but --new-id-prefix {new_prefix!r} "
            f"doesn't, so new IDs would run together (e.g. '{new_prefix}temperature'); "
            f"use --new-id-prefix {new_prefix + '_'!r}"
        )
    raise ValueError(
        f"--new-id-prefix {new_prefix!r} ends with '_' but --old-id-prefix {old_prefix!r} "
        f"doesn't, so new IDs would get a double underscore (e.g. '{new_prefix}_temperature'); "
        f"use --old-id-prefix {old_prefix + '_'!r} or --new-id-prefix {new_prefix.rstrip('_')!r}"
    )


@dataclass(frozen=True)
class Rename:
    """A planned change to one entity registry entry.

    ``current_name`` is the name as shown (the registry's custom name, else the
    integration's); ``registry_name`` is the registry's custom ``name`` field itself, and
    ``registry_id`` the registry entry's ``id``.
    """

    entity_id: str
    new_entity_id: str
    current_name: str
    new_name: str | None
    uses_device_name: bool = False
    registry_name: str | None = None
    registry_id: str | None = None

    def to_change(self) -> Change:
        return Change(
            entity_id=self.entity_id,
            new_entity_id=self.new_entity_id,
            name=self.registry_name,
            new_name=self.new_name if self.new_name is not None else self.registry_name,
            registry_id=self.registry_id,
        )

    @property
    def has_changes(self) -> bool:
        return self.to_change().has_changes

    def update_payload(self) -> dict[str, Any]:
        """The ``config/entity_registry/update`` command for this rename (without ``id``)."""
        return self.to_change().payload()

    def describe(self) -> list[str]:
        """Human-readable lines describing this rename."""
        lines = [self.entity_id, f"  -> {self.new_entity_id}"]
        if self.new_name is not None:
            lines.append(f"  name: {self.current_name!r} -> {self.new_name!r}")
        elif self.uses_device_name:
            lines.append("  name: uses device name as prefix (rename the device instead)")
        return lines


def strip_name_prefix(name: str, prefix: str) -> str | None:
    """Remove ``prefix`` from ``name``, returning None if it doesn't apply.

    Leading whitespace left after removing the prefix is trimmed, so the prefixes
    ``"Backyard Station"`` and ``"Backyard Station "`` behave the same. A name that would
    become empty is left alone.
    """
    if not prefix or not name.startswith(prefix):
        return None
    stripped = name[len(prefix) :].lstrip()
    return stripped or None


def plan_renames(
    entries: list[dict[str, Any]],
    old_id_prefix: str,
    new_id_prefix: str,
    name_prefix: str,
) -> list[Rename]:
    """Work out the renames for every registry entry whose object ID starts with ``old_id_prefix``.

    Entries are matched on the part of the entity ID after ``<domain>.``.
    """
    renames = []
    for entry in entries:
        domain, object_id = entry["entity_id"].split(".", 1)
        if not object_id.startswith(old_id_prefix):
            continue

        new_entity_id = f"{domain}.{new_id_prefix}{object_id[len(old_id_prefix) :]}"
        current_name = entry.get("name") or entry.get("original_name") or ""
        renames.append(
            Rename(
                entity_id=entry["entity_id"],
                new_entity_id=new_entity_id,
                current_name=current_name,
                new_name=strip_name_prefix(current_name, name_prefix),
                uses_device_name=bool(entry.get("has_entity_name")),
                registry_name=entry.get("name"),
                registry_id=entry.get("id"),
            )
        )
    return renames
