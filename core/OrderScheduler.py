"""Incremental scheduling of a preferred install order.

What an entry waits for only grows as entries are placed: taking the first ready entry
in editorial order is a Kahn sort whose queue is ordered by editorial position.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
import heapq

from core.ComponentReference import ComponentReference
from core.Rules import ComponentGroup, DependencyMode


class _GroupProgress:
    """Atoms a group of one entry still waits for.

    An ANY group is met by its first provided atom, an ALL group by its last distinct one.
    Atoms provided once met take the count below zero and are ignored.
    """

    __slots__ = ("entry", "remaining")

    def __init__(self, entry: int, remaining: int) -> None:
        self.entry = entry
        self.remaining = remaining


class OrderScheduler:
    """Places editorial entries once their predecessors and implicit requirements are met.

    An entry waits for its explicit predecessors and for every group of its implicit
    requirements. A group is made of atoms: exact references, or mod references met by
    any reference of the mod. A placed entry provides itself, its selected SUB answers
    and its mod; each atom is provided, and its waiting groups updated, only once.
    """

    def __init__(
        self,
        entries: Sequence[ComponentReference],
        predecessors: Mapping[ComponentReference, Set[ComponentReference]],
        requirements: Mapping[ComponentReference, Iterable[ComponentGroup]],
        choices: Mapping[ComponentReference, Set[ComponentReference]],
    ) -> None:
        """Index what each entry waits for; predecessors must be entries themselves."""
        self._entries = entries
        self._choices = choices
        self._waiting: list[int] = [0] * len(entries)
        self._successors: dict[ComponentReference, list[int]] = defaultdict(list)
        self._watchers: dict[ComponentReference, list[_GroupProgress]] = defaultdict(list)
        self._provided: set[ComponentReference] = set()
        self._ready: list[int] = []

        for entry_index, entry in enumerate(entries):
            for predecessor in predecessors.get(entry, ()):
                self._successors[predecessor].append(entry_index)
                self._waiting[entry_index] += 1
            for group in requirements.get(entry, ()):
                self._watch(entry_index, group)
            if not self._waiting[entry_index]:
                self._ready.append(entry_index)

    def schedule(self, earlier: Iterable[ComponentReference]) -> list[ComponentReference]:
        """Place every entry that becomes ready, earliest editorial position first.

        Args:
            earlier: References provided before the first placement, SUB answers included.

        Returns:
            The placed entries in order; the others wait on a cycle or an absent target.
        """
        for reference in earlier:
            self._provide(reference)

        heapq.heapify(self._ready)
        ordered = []
        while self._ready:
            entry = self._entries[heapq.heappop(self._ready)]
            ordered.append(entry)
            self._place(entry)
        return ordered

    # ========================================
    # Readiness
    # ========================================

    def _watch(self, entry_index: int, group: ComponentGroup) -> None:
        """Make the entry wait for a group, unless it is met without any atom."""
        atoms = dict.fromkeys(group.components)
        if group.operator == DependencyMode.ALL:
            if not atoms:
                return
            progress = _GroupProgress(entry_index, len(atoms))
        else:
            # An ANY group without atoms is never met: the entry waits for it forever
            progress = _GroupProgress(entry_index, 1)

        self._waiting[entry_index] += 1
        for atom in atoms:
            self._watchers[atom].append(progress)

    def _place(self, entry: ComponentReference) -> None:
        for successor in self._successors.get(entry, ()):
            self._release(successor)
        self._provide(entry)
        for option in self._choices.get(entry, ()):
            self._provide(option)

    def _provide(self, reference: ComponentReference) -> None:
        """Meet the atoms of a reference: itself and the mod it belongs to."""
        for atom in (reference, ComponentReference.for_mod(reference.mod_id)):
            if atom in self._provided:
                continue
            self._provided.add(atom)
            for progress in self._watchers.pop(atom, ()):
                progress.remaining -= 1
                if progress.remaining == 0:
                    self._release(progress.entry)

    def _release(self, entry_index: int) -> None:
        self._waiting[entry_index] -= 1
        if not self._waiting[entry_index]:
            heapq.heappush(self._ready, entry_index)


@dataclass(frozen=True, slots=True)
class PendingProviders:
    """Entries left unplaced, indexed by the references they would provide.

    Each list keeps the iteration order of the pending set it was built from, so that
    a cycle diagnostic adds candidates in the same order as a scan of that set.
    """

    by_reference: Mapping[ComponentReference, list[ComponentReference]]
    by_mod: Mapping[str, list[ComponentReference]]

    @classmethod
    def of(
        cls,
        pending: Iterable[ComponentReference],
        choices: Mapping[ComponentReference, Set[ComponentReference]],
    ) -> PendingProviders:
        """Index each pending entry under itself, its selected SUB answers and their mods."""
        by_reference: dict[ComponentReference, list[ComponentReference]] = defaultdict(list)
        by_mod: dict[str, list[ComponentReference]] = defaultdict(list)
        for candidate in pending:
            identities = {candidate} | choices.get(candidate, set())
            for identity in identities:
                by_reference[identity].append(candidate)
            for mod_id in {identity.mod_id for identity in identities}:
                by_mod[mod_id].append(candidate)
        return cls(by_reference, by_mod)

    def providing(self, reference: ComponentReference) -> list[ComponentReference]:
        """Pending entries that would satisfy a reference, a mod reference included."""
        if reference.is_mod():
            return self.by_mod.get(reference.mod_id, [])
        return self.by_reference.get(reference, [])
