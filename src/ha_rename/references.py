"""Pure logic for finding and rewriting references to renamed entity IDs.

Configs are JSON values (dicts, lists, strings, numbers...). A reference is found in
one of three ways:

- a string that is exactly a renamed entity ID: an :class:`Edit` that can be applied;
- a dict key that is exactly a renamed entity ID (as in a scene's ``entities``): also
  an :class:`Edit`, with ``is_key`` set;
- a renamed entity ID inside a longer string, such as a template: a :class:`TextHit`,
  which is only reported, never rewritten.
"""

import copy
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

Path = tuple[str | int, ...]

# Values under these keys name an action (e.g. a script's own key, "script.morning"),
# not an entity, so an exact match there is not a reference.
_ACTION_KEYS = frozenset({"action", "service"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Edit:
    """Change the value (or, with ``is_key``, the dict key) at ``path`` from old to new.

    For a key, the last element of ``path`` is the key itself.
    """

    path: Path
    old: str
    new: str
    is_key: bool = False

    def reversed(self) -> "Edit":
        path = (*self.path[:-1], self.new) if self.is_key else self.path
        return Edit(path, self.new, self.old, self.is_key)

    def describe(self) -> str:
        return f"{format_path(self.path)}: {self.old} -> {self.new}"


@dataclass(frozen=True)
class TextHit:
    """A renamed entity ID inside a longer string at ``path``."""

    path: Path
    old: str
    new: str

    def describe(self) -> str:
        return f"{format_path(self.path)}: mentions {self.old} (now {self.new})"


def format_path(path: Path) -> str:
    """``("triggers", 0, "entity_id")`` -> ``triggers[0].entity_id``."""
    if not path:
        return "(top level)"
    parts = []
    for element in path:
        if isinstance(element, int):
            parts.append(f"[{element}]")
        elif _IDENTIFIER.fullmatch(element):
            parts.append(f".{element}" if parts else element)
        else:
            parts.append(f"[{element!r}]")
    return "".join(parts)


def _token_pattern(entity_ids: Iterable[str]) -> re.Pattern[str]:
    # Longest first, so a longer ID wins over one that is its prefix.
    alternatives = "|".join(re.escape(i) for i in sorted(entity_ids, key=len, reverse=True))
    # Not preceded or followed by an ID character: "states.sensor.x.state" matches
    # sensor.x, but "binary_sensor.x" and "sensor.x_2" don't.
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alternatives})(?![A-Za-z0-9_])")


def scan(config: Any, renames: dict[str, str]) -> tuple[list[Edit], list[TextHit]]:
    """Find references to the keys of ``renames`` (old ID -> new ID) in ``config``."""
    edits: list[Edit] = []
    hits: list[TextHit] = []
    if not renames:
        return edits, hits
    pattern = _token_pattern(renames)

    def text_hits(path: Path, text: str) -> None:
        for old in dict.fromkeys(pattern.findall(text)):
            hits.append(TextHit(path, old, renames[old]))

    def walk(value: Any, path: Path, parent_key: str | int | None) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, key)  # JSON keys are always strings
                if key in renames:
                    edits.append(Edit(child_path, key, renames[key], is_key=True))
                else:
                    text_hits(child_path, key)
                walk(child, child_path, key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, (*path, i), parent_key)
        elif isinstance(value, str):
            if value in renames:
                if parent_key not in _ACTION_KEYS:
                    edits.append(Edit(path, value, renames[value]))
            else:
                text_hits(path, value)

    walk(config, (), None)
    return edits, hits


@dataclass
class EditResult:
    """The outcome of :func:`apply_edits`."""

    config: Any
    applied: list[Edit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def apply_edits(config: Any, edits: Iterable[Edit]) -> EditResult:
    """Apply ``edits`` to a copy of ``config``, each only where it still finds ``old``.

    Edits whose location no longer holds ``old`` are left out and described in
    ``notes``. A renamed key keeps its position in its dict.
    """
    result = EditResult(copy.deepcopy(config))
    edits = list(edits)
    # Paths may name a key either before or after a key edit in the same batch.
    aliases: dict[str, str] = {}
    for edit in edits:
        if edit.is_key:
            aliases[edit.old] = edit.new
            aliases[edit.new] = edit.old

    def locate(path: Path) -> tuple[Any, str | int] | None:
        """The container holding the last element of ``path``, and that element."""
        node = result.config
        for i, element in enumerate(path):
            element = _resolve(node, element, aliases)
            if element is None:
                return None
            if i == len(path) - 1:
                return node, element
            node = node[element]
        return None  # an empty path: the whole config is never a single reference

    value_edits = [e for e in edits if not e.is_key]
    # Deepest keys first, so the paths of shallower ones still resolve.
    key_edits = sorted((e for e in edits if e.is_key), key=lambda e: len(e.path), reverse=True)
    for edit in [*value_edits, *key_edits]:
        found = locate(edit.path)
        if edit.is_key:
            parent = found[0] if found else None
            if isinstance(parent, dict) and edit.old in parent and edit.new not in parent:
                items = [(edit.new if k == edit.old else k, v) for k, v in parent.items()]
                parent.clear()
                parent.update(items)
                result.applied.append(edit)
            else:
                result.notes.append(f"{format_path(edit.path)}: changed since; left as is")
        elif found is not None and found[0][found[1]] == edit.old:
            found[0][found[1]] = edit.new
            result.applied.append(edit)
        else:
            state = "no longer there"
            if found is not None:
                state = f"changed since to {found[0][found[1]]!r}"
            result.notes.append(f"{format_path(edit.path)}: {state}; left as is")
    return result


def revert_edits(config: Any, edits: Iterable[Edit]) -> EditResult:
    """Undo ``edits`` on a copy of ``config``, where they are still in place."""
    return apply_edits(config, [e.reversed() for e in edits])


def _resolve(node: Any, element: str | int, aliases: dict[str, str]) -> str | int | None:
    if isinstance(node, dict) and isinstance(element, str):
        if element in node:
            return element
        alias = aliases.get(element)
        return alias if alias in node else None
    if isinstance(node, list) and isinstance(element, int) and 0 <= element < len(node):
        return element
    return None


@dataclass(frozen=True)
class Target:
    """A place in Home Assistant that may refer to entities.

    ``key`` identifies it when saving (an automation's ``id``, a script's key, a
    dashboard's URL path...). Targets that aren't ``editable`` are only scanned, so the
    user can fix their references by hand.
    """

    kind: str
    key: str | None
    label: str
    config: Any
    editable: bool = True


@dataclass(frozen=True)
class TargetPlan:
    """The references found in one target: ``edits`` to apply, and ``manual`` ones to report."""

    target: Target
    edits: tuple[Edit, ...] = ()
    manual: tuple[Edit | TextHit, ...] = ()


def plan_references(targets: Iterable[Target], renames: dict[str, str]) -> list[TargetPlan]:
    """Scan each target for the renamed IDs, leaving out targets with no references.

    In targets that can't be edited, every reference is reported for a manual fix; in
    the others, only those inside text are.
    """
    plans = []
    for target in targets:
        edits, hits = scan(target.config, renames)
        if target.editable:
            plan = TargetPlan(target, tuple(edits), tuple(hits))
        else:
            plan = TargetPlan(target, (), (*edits, *hits))
        if plan.edits or plan.manual:
            plans.append(plan)
    return plans


def keep_accepted(plans: Iterable[TargetPlan], accepted: set[str]) -> list[TargetPlan]:
    """Only the references to the old IDs in ``accepted``, i.e. renames that were applied."""
    kept = []
    for plan in plans:
        edits = tuple(e for e in plan.edits if e.old in accepted)
        manual = tuple(m for m in plan.manual if m.old in accepted)
        if edits or manual:
            kept.append(TargetPlan(plan.target, edits, manual))
    return kept
