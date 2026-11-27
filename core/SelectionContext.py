"""Selection prepared once for the evaluation of every rule against it."""

from __future__ import annotations

from collections.abc import Iterable, Set
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.ComponentReference import ComponentReference


@dataclass(frozen=True, slots=True)
class SelectionContext:
    """Selected references and the mods they belong to.

    A mod reference is satisfied by any selected reference of that mod: knowing the
    mods present turns that test into a lookup instead of a scan of the selection.
    """

    references: frozenset[ComponentReference]
    mods_present: frozenset[str]

    @classmethod
    def of(cls, selection: Iterable[ComponentReference]) -> SelectionContext:
        """Build the context of a selection in one pass."""
        references = frozenset(selection)
        return cls(references, frozenset(reference.mod_id for reference in references))

    def includes(self, reference: ComponentReference) -> bool:
        """Check if a reference is satisfied by this selection."""
        if reference.is_mod():
            return reference.mod_id in self.mods_present
        return reference in self.references


# What a condition is evaluated against: a prepared context, or a plain set of references
type Selection = SelectionContext | Set[ComponentReference]
