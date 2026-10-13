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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

Path = tuple[str | int, ...]

# Values under these keys name an action (e.g. a script's own key, "script.morning"),
# not an entity, so an exact match there is not a reference.
_ACTION_KEYS = frozenset({"action", "service"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MISSING = object()
# The kinds of target that can be edited, and so appear in undo files.
EDITABLE_KINDS = frozenset({"automation", "script", "scene", "dashboard", "person", "energy"})


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


def apply_edits(config: Any, edits: Iterable[Edit], *, keys_first: bool = False) -> EditResult:
    """Apply ``edits`` to a copy of ``config``, each only where it still finds ``old``.

    Edits whose location no longer holds ``old`` are left out and described in
    ``notes``. A renamed key keeps its position in its dict.

    Paths name keys as they were before any key edit. So values go first, then keys
    deepest first, leaving every parent path as it was; with ``keys_first`` (undoing),
    keys go shallowest first, restoring each parent path before the keys and values
    below it.
    """
    result = EditResult(copy.deepcopy(config))
    edits = list(edits)
    # Undoing: paths below a key that couldn't be put back would land in whatever entry
    # has that key now, so they're left alone.
    blocked: list[Path] = []

    def is_blocked(edit: Edit) -> bool:
        if any(edit.path[: len(b)] == b for b in blocked):
            note = "under a key that wasn't put back; left as is"
            result.notes.append(f"{format_path(edit.path)}: {note}")
            return True
        return False

    def node_at(path: Path) -> Any:
        """The value at ``path``, or ``_MISSING``."""
        node = result.config
        for element in path:
            if not _has(node, element):
                return _MISSING
            node = node[element]
        return node

    def apply_value(edit: Edit) -> None:
        container = node_at(edit.path[:-1]) if edit.path else _MISSING
        found = bool(edit.path) and _has(container, edit.path[-1])
        if found and container[edit.path[-1]] == edit.old:
            container[edit.path[-1]] = edit.new
            result.applied.append(edit)
            return
        state = "no longer there"
        if found:
            state = f"changed since to {container[edit.path[-1]]!r}"
        result.notes.append(f"{format_path(edit.path)}: {state}; left as is")

    # Keys made by this call: such an entry is never renamed again, as it isn't the one a
    # pending edit was recorded for (a -> aa when aa -> aaa was put back over it).
    made: set[Path] = set()

    def apply_key(edit: Edit) -> bool:
        parent = node_at(edit.path[:-1])
        if not (isinstance(parent, dict) and edit.old in parent and edit.new not in parent):
            return False
        if (*edit.path[:-1], edit.old) in made:
            return False
        items = [(edit.new if k == edit.old else k, v) for k, v in parent.items()]
        parent.clear()
        parent.update(items)
        made.add((*edit.path[:-1], edit.new))
        result.applied.append(edit)
        return True

    def apply_keys() -> None:
        depths = sorted({len(e.path) for e in edits if e.is_key}, reverse=not keys_first)
        for depth in depths:
            # Retried while any applies: one may free the key another is renamed to
            # (a -> aa while aa -> aaa), in whichever order they come.
            pending = [e for e in edits if e.is_key and len(e.path) == depth and not is_blocked(e)]
            while pending:
                remaining = [edit for edit in pending if not apply_key(edit)]
                if len(remaining) == len(pending):
                    break
                pending = remaining
            for edit in pending:
                result.notes.append(f"{format_path(edit.path)}: changed since; left as is")
                if keys_first:
                    blocked.append((*edit.path[:-1], edit.new))

    if keys_first:
        apply_keys()
    for edit in edits:
        if not edit.is_key and not is_blocked(edit):
            apply_value(edit)
    if not keys_first:
        apply_keys()
    return result


def revert_edits(config: Any, edits: Iterable[Edit]) -> EditResult:
    """Undo ``edits`` on a copy of ``config``, where they are still in place."""
    return apply_edits(config, [e.reversed() for e in edits], keys_first=True)


def rebase(applied: Iterable[Edit]) -> tuple[Edit, ...]:
    """Edits applied by :func:`revert_edits`, with paths as they were before the revert.

    Paths name keys as they were before the edits they belong to. :func:`revert_edits`
    takes and returns paths from before the original edits; recorded as edits of their
    own (to be reverted again, e.g. to redo), they must name the keys as the revert found
    them, i.e. the original edits' new keys.
    """
    applied = list(applied)
    # Where a key was put back, as a path from before the original edits -> its key then.
    keys = {(*e.path[:-1], e.new): e.old for e in applied if e.is_key}

    def translate(path: Path) -> Path:
        return tuple(keys.get(path[: i + 1], element) for i, element in enumerate(path))

    return tuple(
        Edit((*translate(e.path[:-1]), e.old), e.old, e.new, True)
        if e.is_key
        else Edit(translate(e.path), e.old, e.new)
        for e in applied
    )


def _has(node: Any, element: str | int) -> bool:
    if isinstance(node, dict):
        return element in node
    return isinstance(node, list) and isinstance(element, int) and 0 <= element < len(node)


@dataclass(frozen=True)
class Fix:
    """Where in Home Assistant a target's references are fixed by hand.

    ``page`` is a path relative to Home Assistant's base URL, or None if the target has
    no page. When the path ends with the target's entity ID, ``entity_id`` is that ID,
    so the link can follow the target's own rename. ``in_yaml`` means the fix is made in
    the YAML file that defines the target, and its page (if any) is read-only.
    ``integration`` names the integration that manages the target.
    """

    page: str | None = None
    entity_id: str | None = None
    in_yaml: bool = False
    integration: str | None = None

    def describe(self, base_url: str | None, renames: Mapping[str, str]) -> list[str]:
        """The lines saying where to fix it; ``renames`` maps old to new entity IDs.

        Without a ``base_url`` (Home Assistant's HTTP address), there's no link.
        """
        lines = []
        if self.integration is not None:
            lines.append(f"managed by the {self.integration} integration")
        if self.in_yaml:
            lines.append("fix in the YAML file that defines it")
        if self.page is not None and base_url is not None:
            page = self.page
            if self.entity_id in renames:
                page = page.removesuffix(self.entity_id) + renames[self.entity_id]
            lines.append(f"{'view' if self.in_yaml else 'open'}: {base_url}/{page}")
        return lines


@dataclass(frozen=True)
class Target:
    """A place in Home Assistant that may refer to entities.

    ``key`` identifies it when saving (an automation's ``id``, a script's key, a
    dashboard's URL path...). Targets that aren't ``editable`` are only scanned, so the
    user can fix their references by hand; ``fix`` says where.
    """

    kind: str
    key: str | None
    label: str
    config: Any
    editable: bool = True
    fix: Fix = Fix()


@dataclass(frozen=True)
class TargetPlan:
    """The references found in one target: ``edits`` to apply, and ``manual`` ones to report."""

    target: Target
    edits: tuple[Edit, ...] = ()
    manual: tuple[Edit | TextHit, ...] = ()


def plan_references(targets: Iterable[Target], renames: dict[str, str]) -> list[TargetPlan]:
    """Scan each target for the renamed IDs, leaving out targets with no references.

    In targets that can't be edited, every reference is reported for a manual fix; in
    the others, those inside text are, and so are edits that can't be applied (such as
    renaming a scene's entity key to one it already has).
    """
    plans = []
    for target in targets:
        edits, hits = scan(target.config, renames)
        if target.editable:
            applied = apply_edits(target.config, edits).applied
            rejected = [e for e in edits if e not in applied]
            plan = TargetPlan(target, tuple(applied), (*rejected, *hits))
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
