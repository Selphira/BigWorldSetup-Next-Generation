from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from constants import ICON_ERROR, ICON_INFO, ICON_WARNING
from core.ComponentReference import ComponentReference
from core.TranslationManager import tr


class RuleType(Enum):
    """Type of relationship rule."""

    DEPENDENCY = "dependency"
    INCOMPATIBILITY = "incompatibility"
    ORDER = "order"


class RuleSeverity(Enum):
    """Severity level for rule violations."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class DependencyMode(Enum):
    """How multiple dependencies should be satisfied."""

    ANY = "any"  # At least one target must be present
    ALL = "all"  # All targets must be present


class OrderDirection(Enum):
    """Direction for order rules."""

    BEFORE = "before"  # Source must be installed BEFORE targets
    AFTER = "after"  # Source must be installed AFTER targets


@dataclass(frozen=True, slots=True)
class ComponentGroup:
    """Group of components with an operator."""

    components: tuple[ComponentReference, ...]
    operator: DependencyMode = DependencyMode.ANY

    def matches(self, selected_set: set[ComponentReference]) -> bool:
        """Check if this group is satisfied by the selection."""
        if self.operator == DependencyMode.ANY:
            # At least one component must be selected
            return any(self._matches_reference(comp, selected_set) for comp in self.components)
        else:  # ALL
            # All components must be selected
            return all(self._matches_reference(comp, selected_set) for comp in self.components)

    def get_matched_components(
        self, selected_set: set[ComponentReference]
    ) -> list[ComponentReference]:
        """Get components from this group that are selected."""
        return [comp for comp in self.components if self._matches_reference(comp, selected_set)]

    def get_missing_components(
        self, selected_set: set[ComponentReference]
    ) -> list[ComponentReference]:
        """Get components from this group that are NOT selected."""
        return [
            comp for comp in self.components if not self._matches_reference(comp, selected_set)
        ]

    @staticmethod
    def _matches_reference(
        reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> bool:
        """Check if a reference matches any selected component."""
        if reference.is_mod():
            return any(selected.mod_id == reference.mod_id for selected in selected_set)
        return reference in selected_set


@dataclass(frozen=True, slots=True)
class Rule:
    """Base rule class.

    Attributes:
        rule_type: Type of rule
        severity: Rule severity
        sources: Reference/source components
        targets: List of target components
        description: Human-readable explanation
        source_url: Optional URL for documentation
    """

    rule_type: RuleType
    severity: RuleSeverity
    sources: tuple[ComponentReference, ...]
    targets: tuple[ComponentReference, ...]
    description: str = ""
    source_url: str | None = None

    @staticmethod
    def _parse_component_refs(data: Any) -> list[ComponentReference]:
        """Parse component references from various formats.

        Accepts:
        - Single string: "mod_id" or "mod_id:comp"
        - Single dict: {"mod": "mod_id", "component": "comp"}
        - List of strings or dicts

        Returns list of ComponentReference objects.
        Raises ValueError if data is invalid.
        """

        if not isinstance(data, list):
            data = [data]

        if not data:
            raise ValueError("Component reference list cannot be empty")

        refs = []
        for item in data:
            if isinstance(item, str):
                refs.append(ComponentReference.from_string(item))
            elif isinstance(item, dict):
                if "mod" not in item:
                    raise ValueError(f"Component reference dict missing 'mod' key: {item}")
                refs.append(ComponentReference(item["mod"], item.get("component")))
            else:
                raise ValueError(f"Invalid component reference type: {type(item)}")

        return refs

    @classmethod
    def _parse_sources_and_targets(
        cls, data: dict[str, Any]
    ) -> tuple[tuple[ComponentReference, ...], tuple[ComponentReference, ...]]:
        """Parse and validate sources and targets from rule data.

        Returns tuple of (sources, targets).
        Raises ValueError if required fields are missing or invalid.
        """
        if "source" not in data:
            raise ValueError("Missing required field: 'source'")

        if "target" not in data:
            raise ValueError("Missing required field: 'target'")

        sources = cls._parse_component_refs(data["source"])
        targets = cls._parse_component_refs(data["target"])

        return tuple(sources), tuple(targets)


@dataclass(frozen=True, slots=True)
class DependencyRule(Rule):
    """Dependency rule with mode and implicit ordering.

    Dependencies automatically create ordering constraints:
    - ALL mode: source requires ALL targets → all targets BEFORE source
    - ANY mode: source requires ANY target → at least one target BEFORE source

    Supports advanced group syntax:
    - source_groups: Multiple groups of sources (AND between groups, operator within)
    - target_groups: Multiple groups of targets (AND between groups, operator within)
    """

    dependency_mode: DependencyMode = DependencyMode.ANY
    implicit_order: bool = True
    source_groups: tuple[ComponentGroup, ...] | None = None
    target_groups: tuple[ComponentGroup, ...] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DependencyRule":
        """Create DependencyRule from dictionary.

        Supports three syntaxes:
        1. Standard: {"source": [...], "target": [...], "mode": "any/all"}
        2. Source groups: {"source_groups": [[...], [...]], "target": [...], "mode": "any/all"}
        3. Target groups: {"source": [...], "target_groups": [[...], [...]], "mode": "any/all"}
        4. Both groups: {"source_groups": [...], "target_groups": [...], "mode": "any/all"}

        Raises ValueError if required fields are missing or invalid.
        """
        has_source_groups = "source_groups" in data
        has_target_groups = "target_groups" in data
        has_standard_source = "source" in data
        has_standard_target = "target" in data

        # Must have at least source and target definition
        if not (has_source_groups or has_standard_source):
            raise ValueError("Missing required field: 'source' or 'source_groups'")
        if not (has_target_groups or has_standard_target):
            raise ValueError("Missing required field: 'target' or 'target_groups'")

        # Cannot mix standard and groups for same side
        if has_source_groups and has_standard_source:
            raise ValueError("Cannot specify both 'source' and 'source_groups'")
        if has_target_groups and has_standard_target:
            raise ValueError("Cannot specify both 'target' and 'target_groups'")

        dependency_mode = DependencyMode(data.get("mode", "any"))
        severity = RuleSeverity(data.get("severity", "error"))
        implicit_order = data.get("implicit_order", True)
        description = data.get("description", "")
        source_url = data.get("source_url")

        if has_source_groups:
            source_groups = cls._parse_component_groups(data["source_groups"])
            # For indexing, we need a flat list of all source components
            sources = tuple(comp for group in source_groups for comp in group.components)
        else:
            source_groups = None
            sources = tuple(cls._parse_component_refs(data["source"]))

        if has_target_groups:
            target_groups = cls._parse_component_groups(data["target_groups"])
            # For indexing, we need a flat list of all target components
            targets = tuple(comp for group in target_groups for comp in group.components)
        else:
            target_groups = None
            targets = tuple(cls._parse_component_refs(data["target"]))

        return cls(
            rule_type=RuleType.DEPENDENCY,
            severity=severity,
            sources=sources,
            targets=targets,
            dependency_mode=dependency_mode,
            implicit_order=implicit_order,
            description=description,
            source_url=source_url,
            source_groups=source_groups,
            target_groups=target_groups,
        )

    @classmethod
    def _parse_component_groups(cls, groups_data: list[list]) -> tuple[ComponentGroup, ...]:
        """Parse component groups from JSON data."""
        if not isinstance(groups_data, list) or not groups_data:
            raise ValueError("Component groups must be a non-empty list")

        operator = DependencyMode("any")
        groups = []

        for idx, group_data in enumerate(groups_data):
            if not isinstance(group_data, list) or not group_data:
                raise ValueError(f"Group {idx} must be a non-empty list")

            components = tuple(cls._parse_component_refs(group_data))
            groups.append(ComponentGroup(components=components, operator=operator))

        return tuple(groups)

    def uses_groups(self) -> bool:
        """Check if this rule uses group syntax."""
        return self.source_groups is not None or self.target_groups is not None


@dataclass(frozen=True, slots=True)
class IncompatibilityRule(Rule):
    """Incompatibility rule - mutual exclusion."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IncompatibilityRule":
        """Create IncompatibilityRule from dictionary.

        Raises ValueError if required fields are missing or invalid.
        """
        sources, targets = cls._parse_sources_and_targets(data)

        return cls(
            rule_type=RuleType.INCOMPATIBILITY,
            severity=RuleSeverity(data.get("severity", "error")),
            sources=sources,
            targets=targets,
            description=data.get("description", ""),
            source_url=data.get("source_url"),
        )


@dataclass(frozen=True, slots=True)
class OrderRule(Rule):
    """Explicit order rule with direction."""

    order_direction: OrderDirection = OrderDirection.BEFORE

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrderRule":
        """Create OrderRule from dictionary.

        Raises ValueError if required fields are missing or invalid.
        """
        sources, targets = cls._parse_sources_and_targets(data)

        return cls(
            rule_type=RuleType.ORDER,
            severity=RuleSeverity(data.get("severity", "error")),
            sources=sources,
            targets=targets,
            order_direction=OrderDirection(data.get("direction", "before")),
            description=data.get("description", ""),
            source_url=data.get("source_url"),
        )


@dataclass(frozen=True, slots=True)
class RuleViolation:
    """Detected rule violation.

    Attributes:
        rule: The violated rule
        affected_components: Components involved in violation
    """

    rule: Rule
    affected_components: tuple[ComponentReference, ...]

    def get_message(
        self, for_reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> str:
        """Get formatted message adapted to the given reference context.

        Args:
            for_reference: The reference from which we're viewing the violation
            selected_set: Current set of selected components (for accurate state)

        Returns:
            Formatted message string
        """

        if self.rule.rule_type == RuleType.DEPENDENCY:
            message = self._format_dependency_message(for_reference, selected_set)
        elif self.rule.rule_type == RuleType.INCOMPATIBILITY:
            message = self._format_incompatibility_message(for_reference, selected_set)
        else:
            message = "Unknown violation"

        if self.rule.description:
            message += f"\n{self.rule.description}"

        return message

    def get_order_message(
        self, for_reference: ComponentReference, current_order: list[ComponentReference]
    ) -> str:
        """Get formatted order violation message.

        Args:
            for_reference: The reference from which we're viewing the violation
            current_order: Current installation order

        Returns:
            Formatted message listing only components that violate the order
        """
        if self.rule.rule_type == RuleType.ORDER:
            message = self._format_order_message(for_reference, current_order)
        elif self.rule.rule_type == RuleType.DEPENDENCY:
            message = self._format_order_dependency_message(for_reference, current_order)
        else:
            message = ""

        if self.rule.description:
            message += f"\n    {self.rule.description}"

        return message

    def _format_order_message(
        self, for_reference: ComponentReference, current_order: list[ComponentReference]
    ) -> str:
        """Format ORDER rule message with only violating components."""
        is_source = self._reference_in_list(for_reference, list(self.rule.sources))

        if cast(OrderRule, self.rule).order_direction == OrderDirection.BEFORE:
            constraint_key = "before" if is_source else "after"
            other_refs = self.rule.targets if is_source else self.rule.sources
        else:  # AFTER
            constraint_key = "after" if is_source else "before"
            other_refs = self.rule.targets if is_source else self.rule.sources

        violating_refs = self._get_violating_refs(
            for_reference, list(other_refs), constraint_key, current_order
        )

        if not violating_refs:
            return ""

        violating_names = ", ".join(str(ref) for ref in violating_refs)
        return tr(f"rule.message_order_{constraint_key}", components=violating_names)

    def _format_order_dependency_message(
        self, for_reference: ComponentReference, current_order: list[ComponentReference]
    ) -> str:
        """Format DEPENDENCY rule as order message with only violating components.

        Dependencies imply: targets (dependencies) must be BEFORE sources (dependents)
        """
        is_source = self._reference_in_list(for_reference, list(self.rule.sources))

        constraint_key = "after" if is_source else "before"
        other_refs = self.rule.targets if is_source else self.rule.sources

        violating_refs = self._get_violating_refs(
            for_reference, list(other_refs), constraint_key, current_order
        )

        if not violating_refs:
            return ""

        violating_names = ", ".join(str(ref) for ref in violating_refs)
        return tr(f"rule.message_order_{constraint_key}", components=violating_names)

    def _format_dependency_message(
        self, for_reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> str:
        """Format dependency violation message with current selection state."""
        is_source = self._reference_in_list(for_reference, list(self.rule.sources))
        missing: list[ComponentReference] = []

        if is_source:
            for target in self.rule.targets:
                if not self._matches_reference(target, selected_set):
                    missing.append(target)
            if missing:
                missing_str = ", ".join(str(t) for t in missing)
                return tr("rule.message_dependency_missing", missing=missing_str)
            else:
                return tr("rule.message_dependency_all_satisfied")
        else:
            for source in self.rule.sources:
                if self._matches_reference(source, selected_set):
                    missing.append(source)
            sources_str = ", ".join(str(s) for s in missing)
            return tr("rule.message_dependency_required_by", sources=sources_str)

    def _format_incompatibility_message(
        self, for_reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> str:
        """Format incompatibility violation message with current selection state."""
        if self._reference_in_list(for_reference, list(self.rule.sources)):
            conflicts = [
                ref for ref in self.rule.targets if self._matches_reference(ref, selected_set)
            ]
        else:
            conflicts = [
                ref for ref in self.rule.sources if self._matches_reference(ref, selected_set)
            ]

        if not conflicts:
            return tr("rule.message_incompatibility_resolved")

        conflict_names = ", ".join(str(ref) for ref in conflicts)
        return tr("rule.message_incompatibility", conflict_names=conflict_names)

    @staticmethod
    def _matches_reference(
        reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> bool:
        """Check if a reference matches any selected component.

        Duplicates RuleManager logic for consistency.
        """
        if reference.is_mod():
            return any(selected.mod_id == reference.mod_id for selected in selected_set)

        return reference in selected_set

    def _reference_in_list(
        self, reference: ComponentReference, ref_list: list[ComponentReference]
    ) -> bool:
        """Check if reference matches any reference in list (handles MOD references)."""
        for ref in ref_list:
            if self._references_match(reference, ref):
                return True
        return False

    @staticmethod
    def _references_match(ref1: ComponentReference, ref2: ComponentReference) -> bool:
        """Check if two references match (handles MOD references)."""
        if ref1.is_mod() or ref2.is_mod():
            return ref1.mod_id == ref2.mod_id

        return ref1 == ref2

    def _get_violating_refs(
        self,
        for_reference: ComponentReference,
        other_refs: list[ComponentReference],
        constraint_type: str,  # "before" or "after"
        current_order: list[ComponentReference],
    ) -> list[ComponentReference]:
        """Get list of references that violate the order constraint."""
        ref_position = self._get_reference_position(for_reference, current_order)
        if ref_position is None:
            return []

        violating = []

        for other_ref in other_refs:
            other_position = self._get_reference_position(other_ref, current_order)

            if other_position is None:
                continue

            if constraint_type == "before":
                if ref_position > other_position:
                    violating.append(other_ref)
            else:  # "after"
                if ref_position < other_position:
                    violating.append(other_ref)

        return violating

    @staticmethod
    def _get_reference_position(
        reference: ComponentReference, order_list: list[ComponentReference]
    ) -> int | None:
        """Get position of reference in order list."""
        if reference.is_mod():
            for i, ref in enumerate(order_list):
                if ref.mod_id == reference.mod_id:
                    return i
            return None

        try:
            return order_list.index(reference)
        except ValueError:
            return None

    @property
    def severity(self) -> RuleSeverity:
        """Get severity from rule."""
        return self.rule.severity

    @property
    def is_error(self) -> bool:
        """Check if this is an error-level violation."""
        return self.severity == RuleSeverity.ERROR

    @property
    def is_warning(self) -> bool:
        """Check if this is a warning-level violation."""
        return self.severity == RuleSeverity.WARNING

    @property
    def icon(self) -> str:
        """Get icon for display."""
        return ICON_ERROR if self.is_error else ICON_WARNING if self.is_warning else ICON_INFO


@dataclass
class ValidationCache:
    """Cache for validation results to improve performance."""

    violations_by_component: dict[ComponentReference, list[RuleViolation]] = field(
        default_factory=dict
    )
    selection_hash: int | None = None

    def clear(self) -> None:
        """Clear all cached data."""
        self.violations_by_component.clear()
        self.selection_hash = None

    def get_violations(self, reference: ComponentReference) -> list[RuleViolation]:
        """Get cached violations for a component."""
        return self.violations_by_component.get(reference, [])

    def has_violations(self, reference: ComponentReference) -> bool:
        """Check if component has any violations."""
        return bool(self.get_violations(reference))

    def get_icon(self, reference: ComponentReference) -> str:
        """Get warning icon if component has violations."""
        violations = self.get_violations(reference)
        if not violations:
            return ""

        # Return icon based on highest severity
        if any(v.is_error for v in violations):
            return ICON_ERROR
        return ICON_WARNING
