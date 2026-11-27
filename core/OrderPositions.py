"""Where each reference is first provided in an install order."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.SelectionContext import PreparedSelection, SelectionContext

if TYPE_CHECKING:
    from core.ComponentReference import ComponentReference


@dataclass(frozen=True, slots=True)
class OrderPositions:
    """First positions of the references an install order provides.

    A component provides itself and its selected SUB answers at its position; what
    comes earlier is provided before the first position. What precedes a position
    is then answered by comparing positions, instead of building the prefix.
    """

    earlier: SelectionContext
    first_positions: Mapping[ComponentReference, int]
    first_mod_positions: Mapping[str, int]

    @classmethod
    def of(
        cls,
        install_order: Sequence[ComponentReference],
        earlier: Set[ComponentReference],
        choices: Mapping[ComponentReference, Set[ComponentReference]],
    ) -> OrderPositions:
        """Index an install order once; ``earlier`` must already hold its SUB answers."""
        first_positions: dict[ComponentReference, int] = {}
        first_mod_positions: dict[str, int] = {}
        for position, component in enumerate(install_order):
            for reference in (component, *choices.get(component, ())):
                first_positions.setdefault(reference, position)
                first_mod_positions.setdefault(reference.mod_id, position)
        return cls(SelectionContext.of(earlier), first_positions, first_mod_positions)

    def preceding(self, position: int) -> OrderPrefix:
        """Return what is provided before this position, earlier references included."""
        return OrderPrefix(self, position)


@dataclass(frozen=True, slots=True)
class OrderPrefix(PreparedSelection):
    """References provided before a position of an install order."""

    positions: OrderPositions
    bound: int

    def includes(self, reference: ComponentReference) -> bool:
        """Check if the reference is provided before the bound, or earlier."""
        if self.positions.earlier.includes(reference):
            return True
        if reference.is_mod():
            first = self.positions.first_mod_positions.get(reference.mod_id)
        else:
            first = self.positions.first_positions.get(reference)
        return first is not None and first < self.bound
