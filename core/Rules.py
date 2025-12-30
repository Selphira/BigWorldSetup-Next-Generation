from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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
    """

    dependency_mode: DependencyMode = DependencyMode.ANY
    implicit_order: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DependencyRule":
        """Create DependencyRule from dictionary.

        Raises ValueError if required fields are missing or invalid.
        """
        sources, targets = cls._parse_sources_and_targets(data)

        return cls(
            rule_type=RuleType.DEPENDENCY,
            severity=RuleSeverity(data.get("severity", "error")),
            sources=sources,
            targets=targets,
            dependency_mode=DependencyMode(data.get("mode", "any")),
            implicit_order=data.get("implicit_order", True),
            description=data.get("description", ""),
            source_url=data.get("source_url"),
        )


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
    """Detected rule violation with resolution suggestions.

    Attributes:
        rule: The violated rule
        affected_components: Components involved in violation
        message: Formatted error/warning message
        suggested_actions: List of possible user actions
    """

    rule: Rule
    affected_components: tuple[ComponentReference, ...]
    message: str = ""
    suggested_actions: tuple[str, ...] = field(default_factory=tuple)

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

    def _matches_reference(
        self, reference: ComponentReference, selected_set: set[ComponentReference]
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

    def _references_match(self, ref1: ComponentReference, ref2: ComponentReference) -> bool:
        """Check if two references match (handles MOD references)."""
        if ref1.is_mod() or ref2.is_mod():
            return ref1.mod_id == ref2.mod_id

        return ref1 == ref2

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
