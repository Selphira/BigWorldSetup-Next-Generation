from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from constants import ICON_WARNING, ICON_ERROR, ICON_INFO


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
class ComponentRef:
    """Reference to a specific component or entire mod.

    Attributes:
        mod_id: Mod identifier
        comp_key: Component key (None = entire mod)
    """
    mod_id: str
    comp_key: str | None = None

    def is_mod_level(self) -> bool:
        """Check if this references an entire mod."""
        return self.comp_key is None

    def is_any_component(self) -> bool:
        """Check if this matches any component of the mod."""
        return self.comp_key == "*"

    def matches(self, mod_id: str, comp_key: str | None = None) -> bool:
        """Check if this reference matches given mod/component."""
        if self.mod_id != mod_id:
            return False
        if self.is_mod_level():
            return True
        if self.is_any_component():
            return comp_key is not None
        return self.comp_key == comp_key

    def __str__(self) -> str:
        if self.is_mod_level():
            return self.mod_id
        if self.is_any_component():
            return f"{self.mod_id}:*"
        return f"{self.mod_id}:{self.comp_key}"

    @classmethod
    def from_string(cls, ref: str) -> "ComponentRef":
        """Parse component reference from string format.

        Formats:
        - "mod_id" -> mod-level reference
        - "mod_id:*" -> any component of mod
        - "mod_id:comp_key" -> specific component
        """
        if ":" in ref:
            mod_id, comp_key = ref.split(":", 1)
            return cls(mod_id, comp_key if comp_key != "*" else "*")
        return cls(ref, None)


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
    sources: tuple[ComponentRef, ...]
    targets: tuple[ComponentRef, ...]
    description: str = ""
    source_url: str | None = None

    def applies_to(self, mod_id: str, comp_key: str | None = None) -> bool:
        """Check if this rule applies to given component."""
        return any(source.matches(mod_id, comp_key) for source in self.sources)

    def involves(self, mod_id: str, comp_key: str | None = None) -> bool:
        """Check if component is involved in this rule."""
        if self.applies_to(mod_id, comp_key):
            return True
        return any(target.matches(mod_id, comp_key) for target in self.targets)

    @staticmethod
    def _parse_component_refs(data: Any) -> list[ComponentRef]:
        """Parse component references from various formats.

        Accepts:
        - Single string: "mod_id" or "mod_id:comp"
        - Single dict: {"mod": "mod_id", "component": "comp"}
        - List of strings or dicts

        Returns list of ComponentRef objects.
        Raises ValueError if data is invalid.
        """

        if not isinstance(data, list):
            data = [data.lower()]

        if not data:
            raise ValueError("Component reference list cannot be empty")

        refs = []
        for item in data:
            if isinstance(item, str):
                refs.append(ComponentRef.from_string(item.lower()))
            elif isinstance(item, dict):
                if "mod" not in item:
                    raise ValueError(f"Component reference dict missing 'mod' key: {item}")
                refs.append(ComponentRef(item["mod"].lower(), item.get("component")))
            else:
                raise ValueError(f"Invalid component reference type: {type(item)}")

        return refs

    @classmethod
    def _parse_sources_and_targets(cls, data: dict[str, Any]) -> tuple[
        tuple[ComponentRef, ...], tuple[ComponentRef, ...]]:
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
            source_url=data.get("source_url")
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
            source_url=data.get("source_url")
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
            source_url=data.get("source_url")
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
    affected_components: tuple[tuple[str, str], ...]
    message: str
    suggested_actions: tuple[str, ...] = field(default_factory=tuple)

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
    violations_by_component: dict[str, list[RuleViolation]] = field(default_factory=dict)
    selection_hash: int | None = None

    def clear(self) -> None:
        """Clear all cached data."""
        self.violations_by_component.clear()
        self.selection_hash = None

    def get_violations(self, mod_id: str, comp_key: str) -> list[RuleViolation]:
        """Get cached violations for a component."""
        key = f"{mod_id}:{comp_key}"
        return self.violations_by_component.get(key, [])

    def has_violations(self, mod_id: str, comp_key: str) -> bool:
        """Check if component has any violations."""
        return bool(self.get_violations(mod_id, comp_key))

    def get_icon(self, mod_id: str, comp_key: str) -> str:
        """Get warning icon if component has violations."""
        violations = self.get_violations(mod_id, comp_key)
        if not violations:
            return ""

        # Return icon based on highest severity
        if any(v.is_error for v in violations):
            return ICON_ERROR
        return ICON_WARNING
