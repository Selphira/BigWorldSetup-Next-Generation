"""Selection contract consumed by the core validation layer."""

from __future__ import annotations

from typing import Protocol

from core.ComponentReference import ComponentReference


class SelectionSource(Protocol):
    """Selection state that core services can read, simulate and publish.

    The concrete implementation lives in the UI layer; this protocol keeps
    ``core`` independent from it.
    """

    def get_selected_components(self) -> list[ComponentReference]:
        """Return the currently selected components."""
        ...

    def selection_snapshot(self) -> frozenset[ComponentReference]:
        """Return an immutable copy of the current selection."""
        ...

    def forced_selection(self) -> set[ComponentReference]:
        """Return the components forced by the current game."""
        ...

    def preview(self, selection: set[ComponentReference]) -> SelectionSource:
        """Return a detached source starting from ``selection`` to simulate cascades."""
        ...

    def select(self, reference: ComponentReference) -> bool:
        """Select ``reference`` with its cascade effects."""
        ...

    def unselect(self, reference: ComponentReference) -> bool:
        """Unselect ``reference`` with its cascade effects."""
        ...

    def apply_selection(self, selection: set[ComponentReference]) -> None:
        """Publish an already validated selection."""
        ...
