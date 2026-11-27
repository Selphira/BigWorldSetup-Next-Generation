"""
Unified reference system for mods and components.

This module provides the central reference type used throughout the application
for identifying mods, components, and options in a type-safe manner.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import logging
from typing import Any

from core.Mod import Component, Mod
from core.SelectionContext import PreparedSelection, Selection

logger = logging.getLogger(__name__)

# ============================================================================
# Component Reference
# ============================================================================


@dataclass(frozen=True, slots=True)
class ComponentReference:
    """Immutable reference to a mod, component, or option.

    This is the single source of truth for component identification.

    Format: "{mod_id}:{comp_key}"

    Examples:
        - Mod: "bp-bgt-worldmap"
        - Component: "bp-bgt-worldmap:3"
        - MUC option: "bp-bgt-worldmap:1" (where 1 is option of choice_0)
        - SUB option: "bp-bgt-worldmap:0.1.2"
    """

    mod_id: str
    comp_key: str

    def __post_init__(self):
        """Validate and normalize reference."""
        object.__setattr__(self, "mod_id", self.mod_id.lower())

    # ========================================
    # Factory Methods
    # ========================================

    @classmethod
    def from_string(cls, reference_str: str) -> ComponentReference:
        """Create reference from string.

        Args:
            reference_str: String in format "mod_id:comp_key"

        Returns:
            ComponentReference instance

        Raises:
            ValueError: If format is invalid
        """
        if not isinstance(reference_str, str) or not reference_str:
            raise ValueError(f"Invalid reference format: {reference_str}")

        if ":" not in reference_str:
            reference_str = f"{reference_str}:*"

        mod_id, comp_key = reference_str.split(":", 1)

        if not mod_id or not comp_key:
            raise ValueError(f"Invalid reference format: {reference_str}")

        return cls(mod_id, comp_key)

    @classmethod
    def for_mod(cls, mod_id: str) -> ComponentReference:
        """Create mod reference."""
        return cls(mod_id, "*")

    @classmethod
    def for_component(cls, mod_id: str, comp_key: str) -> ComponentReference:
        """Create component reference."""
        return cls(mod_id, comp_key)

    # ========================================
    # String Representation
    # ========================================

    def __str__(self) -> str:
        """String representation in standard format."""
        return f"{self.mod_id}:{self.comp_key}"

    def __repr__(self) -> str:
        """Debug representation."""
        return f"ComponentReference('{self.mod_id}:{self.comp_key}')"

    def __hash__(self) -> int:
        """Make hashable for use in sets/dicts."""
        return hash((self.mod_id, self.comp_key))

    # ========================================
    # Type Detection
    # ========================================

    def is_mod(self) -> bool:
        """Check if this is a mod reference."""
        return self.comp_key == "*"

    def is_component(self) -> bool:
        """Check if this is a standard component."""
        return not self.is_mod() and not self.is_muc() and self.comp_key.count(".") == 0

    def is_muc(self) -> bool:
        """Check if this is a MUC option."""
        return self.comp_key.startswith("choice_")

    def is_sub(self) -> bool:
        """Check if this is a SUB."""
        return self.comp_key.count(".") == 1

    def is_sub_option(self) -> bool:
        """Check if this is a SUB option."""
        return self.comp_key.count(".") == 2

    # ========================================
    # Matching
    # ========================================

    def matches(self, other: ComponentReference) -> bool:
        """Check if two references designate the same thing.

        A mod reference on either side matches every reference of that mod.
        """
        if self.is_mod() or other.is_mod():
            return self.mod_id == other.mod_id
        return self == other

    def covers(self, other: ComponentReference) -> bool:
        """Check if this reference includes ``other``.

        Unlike ``matches``, only this side acts as a wildcard: a mod reference
        covers its components, but a component never covers its mod.
        """
        if self.is_mod():
            return self.mod_id == other.mod_id
        return self == other

    def is_selected_in(self, selection: Selection) -> bool:
        """Check if this reference is satisfied by the selection.

        A mod reference is satisfied by any selected reference of that mod: a
        prepared context answers it without scanning the selection.
        """
        if isinstance(selection, PreparedSelection):
            return selection.includes(self)
        if self.is_mod():
            return any(selected.mod_id == self.mod_id for selected in selection)
        return self in selection

    # ========================================
    # Hierarchy Navigation
    # ========================================

    def get_base_component_key(self) -> str:
        """Get base component key.

        Examples:
            "10" -> "10"
            "10.1" -> "10"
            "10.1.2" -> "10"
        """
        return self.comp_key.split(".", 1)[0]

    def get_base_component_reference(self) -> ComponentReference:
        """Get reference to base component.

        Examples:
            "mod:10" -> "mod:10"
            "mod:10.1.2" -> "mod:10"
        """
        base_key = self.get_base_component_key()
        return ComponentReference(self.mod_id, base_key)

    # ========================================
    # Conversion Utilities
    # ========================================

    @classmethod
    def from_string_list(cls, string_list: list[str]) -> list[ComponentReference]:
        """Convert a list of string references to ComponentReference list.

        Invalid references are logged and skipped.

        Args:
            string_list: List of strings in "mod_id:comp_key" format

        Returns:
            List of ComponentReference objects (may be shorter if some were invalid)
        """
        references = []
        for ref_str in string_list:
            try:
                references.append(cls.from_string(ref_str))
            except ValueError as e:
                logger.warning(f"Invalid component reference '{ref_str}': {e}")
        return references

    @staticmethod
    def to_string_list(references: list[ComponentReference]) -> list[str]:
        """Convert a list of ComponentReference to string list.

        Args:
            references: List of ComponentReference objects

        Returns:
            List of strings in "mod_id:comp_key" format
        """
        return [str(ref) for ref in references]


# ============================================================================
# Reference Indexes
# ============================================================================


@dataclass
class ReferenceIndexes:
    """Centralized index system for O(1) lookups.

    Manages six critical indexes:
    1. Mod/Component resolution (reference -> object)
    2. Tree items (reference -> UI item)
    3. Selection state (set of selected references)
    4. Violations (reference -> list of violations)
    5. Parent relationships (child -> parent reference)
    6. Children relationships (parent -> list of child references)
    """

    # Mod/Component objects (populated by ModManager)
    mod_component_index: dict[ComponentReference, Mod | Component] = field(default_factory=dict)

    # UI Tree items (populated by ComponentSelector)
    tree_item_index: dict[ComponentReference, Any] = field(default_factory=dict)

    # Selection state
    selection_index: set[ComponentReference] = field(default_factory=set)

    # Violations/conflicts
    selection_violation_index: dict[ComponentReference, list[Any]] = field(default_factory=dict)

    # Violations/order
    order_violation_index: dict[ComponentReference, list[Any]] = field(default_factory=dict)

    # Parent relationships (for MUC options and SUB components)
    parent_index: dict[ComponentReference, ComponentReference] = field(default_factory=dict)

    # Children relationships (for MUC and SUB components)
    children_index: dict[ComponentReference, list[ComponentReference]] = field(
        default_factory=dict
    )

    # ========================================
    # Mod/Component Index
    # ========================================

    def register_mod(self, mod: Mod) -> ComponentReference:
        """Register a mod in the index."""
        reference = ComponentReference.for_mod(mod.id)
        self.mod_component_index[reference] = mod
        return reference

    def register_component(self, component: Component) -> ComponentReference:
        """Register a component in the index."""
        reference = ComponentReference.for_component(component.mod.id, component.key)
        self.mod_component_index[reference] = component
        return reference

    def resolve(self, reference: ComponentReference) -> Mod | Component | None:
        """Resolve reference to actual object.

        For options (MUC/SUB), returns the parent component.
        """
        obj = self.mod_component_index.get(reference)
        if obj:
            return obj

        # For SUB options, get parent component
        if reference.is_sub_option():
            parent_ref = reference.get_base_component_reference()
            return self.mod_component_index.get(parent_ref)

        return None

    # ========================================
    # Tree Item Index
    # ========================================

    def register_tree_item(self, reference: ComponentReference, item: Any) -> None:
        """Register a tree item."""
        self.tree_item_index[reference] = item

    def get_tree_item(self, reference: ComponentReference) -> Any:
        """Get tree item by reference."""
        return self.tree_item_index.get(reference)

    # ========================================
    # Selection Index
    # ========================================

    def select(self, reference: ComponentReference) -> None:
        """Mark reference as selected."""
        self.selection_index.add(reference)

    def unselect(self, reference: ComponentReference) -> None:
        """Mark reference as unselected."""
        self.selection_index.discard(reference)

    def is_selected(self, reference: ComponentReference) -> bool:
        """Check if reference is selected."""
        return reference in self.selection_index

    def get_selected_components(self) -> list[ComponentReference]:
        """Get only installable components.

        Returns base components that are actually installable.
        """
        return [
            reference
            for reference in self.selection_index
            if not reference.is_mod() and not reference.is_muc() and not reference.is_sub()
        ]

    def clear_selection(self) -> None:
        """Clear all selections."""
        self.selection_index.clear()

    # ========================================
    # Violation Index - Selection
    # ========================================

    def add_selection_violation(self, violation: Any) -> None:
        """Add a violation to all affected components."""
        for reference in violation.affected_components:
            if reference not in self.selection_violation_index:
                self.selection_violation_index[reference] = []
            self.selection_violation_index[reference].append(violation)

    def get_selection_violations(self, reference: ComponentReference) -> list[Any]:
        """Get violations for a reference."""
        return self.selection_violation_index.get(reference, [])

    def has_selection_violations(self, reference: ComponentReference) -> bool:
        """Check if reference has violations."""
        return bool(self.selection_violation_index.get(reference))

    def clear_selection_violations(self) -> None:
        """Clear all violations."""
        self.selection_violation_index.clear()

    # ========================================
    # Violation Index - Order
    # ========================================

    def add_order_violation(self, violation: Any) -> None:
        """Add an order violation to all affected components."""
        for reference in violation.affected_components:
            if reference not in self.order_violation_index:
                self.order_violation_index[reference] = []
            self.order_violation_index[reference].append(violation)

    def remove_order_violations(self, violations: Iterable[Any]) -> None:
        """Remove these violations from every affected component, each list filtered once.

        Violations are matched by identity: an equal finding published by another
        sequence stays. Lists are updated in place, as readers may hold them.
        """
        withdrawn = list(violations)
        withdrawn_ids = {id(violation) for violation in withdrawn}
        references = {
            reference for violation in withdrawn for reference in violation.affected_components
        }
        for reference in references:
            entries = self.order_violation_index.get(reference)
            if entries:
                entries[:] = [entry for entry in entries if id(entry) not in withdrawn_ids]

    def get_order_violations(self, reference: ComponentReference) -> list[Any]:
        """Get order violations for a reference."""
        return self.order_violation_index.get(reference, [])

    def clear_order_violations(self) -> None:
        """Clear all order violations."""
        self.order_violation_index.clear()

    # ========================================
    # Parent/Child Index (for MUC and SUB)
    # ========================================

    def register_parent_child(
        self, parent: ComponentReference, children: list[ComponentReference]
    ) -> None:
        """Register parent-child relationship.

        Used for:
        - MOD components: parent = mod, children = component keys
        - MUC components: parent = choice_X, children = option keys
        - SUB components: parent = comp_X, children = prompt keys

        Args:
            parent: Parent component reference
            children: List of child references
        """
        # Store children list for parent
        self.children_index[parent] = children

        # Store parent for each child
        for child in children:
            self.parent_index[child] = parent

    def get_parent(self, reference: ComponentReference) -> ComponentReference | None:
        """Get parent reference for a child.

        Args:
            reference: Child reference (option or prompt)

        Returns:
            Parent reference or None if not found

        Examples:
            get_parent("bp-bgt-worldmap:2") -> "bp-bgt-worldmap:choice_0"
            get_parent("bp-bgt-worldmap:0.2.1") -> "bp-bgt-worldmap:0.2"
            get_parent("bp-bgt-worldmap:0.2") -> "bp-bgt-worldmap:0"
            get_parent("bp-bgt-worldmap:choice_1") -> "bp-bgt-worldmap:*"
        """
        return self.parent_index.get(reference)

    def get_children(self, reference: ComponentReference) -> list[ComponentReference]:
        """Get children references for a parent.

        Args:
            reference: Parent reference (MUC or SUB component)

        Returns:
            List of child references (empty if none)

        Examples:
            get_children("bp-bgt-worldmap") -> ["bp-bgt-worldmap:0", "bp-bgt-worldmap:choice_0", "bp-bgt-worldmap:3", "bp-bgt-worldmap:choice_1]
            get_children("bp-bgt-worldmap:0") -> ["bp-bgt-worldmap:0.1", "bp-bgt-worldmap:0.2"]
            get_children("bp-bgt-worldmap:3") -> []
            get_children("bp-bgt-worldmap:choice_0") -> ["bp-bgt-worldmap:1", "bp-bgt-worldmap:2"]
        """
        return self.children_index.get(reference, [])

    def get_siblings(self, reference: ComponentReference) -> list[ComponentReference]:
        """Get sibling references (other children of same parent).

        Args:
            reference: Reference to get siblings for

        Returns:
            List of sibling references (excluding self)
        """
        parent = self.get_parent(reference)
        if not parent:
            return []

        siblings = self.get_children(parent)
        return [sibling for sibling in siblings if sibling != reference]

    # ========================================
    # Utilities
    # ========================================

    def replace_catalogue(self, catalogue: ReferenceIndexes) -> None:
        """Replace the mods, components and their links by those of another index.

        Tree items, selection and violations are kept: they belong to the
        interface and to the user, not to the loaded catalogue.
        """
        for current, replacement in (
            (self.mod_component_index, catalogue.mod_component_index),
            (self.parent_index, catalogue.parent_index),
            (self.children_index, catalogue.children_index),
        ):
            current.clear()
            current.update(replacement)

    def add_catalogue(self, catalogue: ReferenceIndexes) -> None:
        """Add the mods, components and links of another index, replacing equal references."""
        self.mod_component_index.update(catalogue.mod_component_index)
        self.parent_index.update(catalogue.parent_index)
        self.children_index.update(catalogue.children_index)

    def remove_mod(self, mod_id: str) -> None:
        """Remove a mod, its components and their links.

        Like `replace_catalogue`, tree items, selection and violations are kept.
        """
        mod_id = mod_id.lower()
        for index in (self.mod_component_index, self.parent_index, self.children_index):
            for reference in [reference for reference in index if reference.mod_id == mod_id]:
                del index[reference]

    def clear_all(self) -> None:
        """Clear all indexes."""
        self.mod_component_index.clear()
        self.tree_item_index.clear()
        self.selection_index.clear()
        self.selection_violation_index.clear()
        self.order_violation_index.clear()
        self.parent_index.clear()
        self.children_index.clear()


# ============================================================================
# Global Index Manager (Singleton)
# ============================================================================


class IndexManager:
    """Global singleton manager for all reference indexes.

    Ensures there's only ONE set of indexes throughout the application.
    Access via: IndexManager.get_indexes()
    """

    _instance: IndexManager | None = None
    _indexes: ReferenceIndexes | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._indexes = ReferenceIndexes()
        return cls._instance

    @classmethod
    def get_indexes(cls) -> ReferenceIndexes:
        """Get the global indexes instance."""
        if cls._indexes is None:
            cls._indexes = ReferenceIndexes()
        return cls._indexes

    @classmethod
    def reset(cls) -> None:
        """Reset all indexes (for testing or full reload)."""
        if cls._indexes:
            cls._indexes.clear_all()
            logger.debug("All indexes cleared")
