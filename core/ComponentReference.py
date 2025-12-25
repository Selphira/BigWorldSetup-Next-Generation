"""
Unified reference system for mods and components.

This module provides the central reference type used throughout the application
for identifying mods, components, and options in a type-safe manner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
import logging
from typing import Any

from core.Mod import Component, Mod

logger = logging.getLogger(__name__)

# ============================================================================
# Reference Type Enum
# ============================================================================


class ReferenceType(Enum):
    MOD = auto()  # mod_id:*
    COMPONENT = auto()  # mod_id:comp_key
    MUC = auto()  # mod_id:choice_X
    SUB_PROMPT = auto()  # mod_id:X.Y
    SUB_OPTION = auto()  # mod_id:X.Y.Z


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
        if not reference_str:
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

    @property
    def reference_type(self) -> ReferenceType:
        """Detect reference type."""
        if self.is_mod():
            return ReferenceType.MOD

        if self.is_muc():
            return ReferenceType.MUC

        if self.is_sub():
            return ReferenceType.SUB_PROMPT

        if self.is_sub_option():
            return ReferenceType.SUB_OPTION

        return ReferenceType.COMPONENT

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
    violation_index: dict[ComponentReference, list[Any]] = field(default_factory=dict)

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

    def resolve_by_string(self, reference_str: str) -> Mod | Component | None:
        """Resolve reference string to object."""
        try:
            reference = ComponentReference.from_string(reference_str)
            return self.resolve(reference)
        except ValueError:
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

    def remove_tree_item(self, reference: ComponentReference) -> None:
        """Remove tree item from index."""
        self.tree_item_index.pop(reference, None)

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

    def get_selected_references(self) -> set[ComponentReference]:
        """Get all selected references."""
        return self.selection_index.copy()

    def get_selected_components(self) -> list[ComponentReference]:
        """Get only installable components.

        Returns base components that are actually installable.
        """
        return [ref for ref in self.selection_index if ref.is_component()]

    def clear_selection(self) -> None:
        """Clear all selections."""
        self.selection_index.clear()

    # ========================================
    # Violation Index
    # ========================================

    def add_violation(self, reference: ComponentReference, violation: Any) -> None:
        """Add a violation to all affected components."""
        for mod_id, comp_key in violation.affected_components:
            reference = ComponentReference.for_component(mod_id, comp_key)
            if reference not in self.violation_index:
                self.violation_index[reference] = []
            self.violation_index[reference].append(violation)

    def get_violations(self, reference: ComponentReference) -> list[Any]:
        """Get violations for a reference."""
        return self.violation_index.get(reference, [])

    def has_violations(self, reference: ComponentReference) -> bool:
        """Check if reference has violations."""
        return bool(self.violation_index.get(reference))

    def clear_violations(self) -> None:
        """Clear all violations."""
        self.violation_index.clear()

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

    def has_children(self, reference: ComponentReference) -> bool:
        """Check if reference has children."""
        return reference in self.children_index

    def is_child(self, reference: ComponentReference) -> bool:
        """Check if reference is a child (has a parent)."""
        return reference in self.parent_index

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

    def get_references_by_mod(self, mod_id: str) -> list[ComponentReference]:
        """Get all references for a specific mod."""
        mod_id_lower = mod_id.lower()
        return [ref for ref in self.mod_component_index.keys() if ref.mod_id == mod_id_lower]

    def clear_all(self) -> None:
        """Clear all indexes."""
        self.mod_component_index.clear()
        self.tree_item_index.clear()
        self.selection_index.clear()
        self.violation_index.clear()
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
