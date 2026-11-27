from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, cast

from constants import ICON_ERROR, ICON_INFO, ICON_WARNING
from core.ComponentReference import ComponentReference
from core.SelectionContext import Selection
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

    def matches(self, selected_set: Selection) -> bool:
        """Check if this group is satisfied by the selection."""
        if self.operator == DependencyMode.ANY:
            # At least one component must be selected
            return any(comp.is_selected_in(selected_set) for comp in self.components)
        # All components must be selected
        return all(comp.is_selected_in(selected_set) for comp in self.components)

    def get_matched_components(self, selected_set: Selection) -> list[ComponentReference]:
        """Get components from this group that are selected."""
        return [comp for comp in self.components if comp.is_selected_in(selected_set)]

    def get_missing_components(self, selected_set: Selection) -> list[ComponentReference]:
        """Get components from this group that are NOT selected."""
        return [comp for comp in self.components if not comp.is_selected_in(selected_set)]


@dataclass(frozen=True, slots=True)
class Rule:
    rule_type: RuleType
    severity: RuleSeverity
    sources: tuple[ComponentReference, ...]
    targets: tuple[ComponentReference, ...]
    description: str = ""
    source_url: str | None = None

    @staticmethod
    def _parse_component_refs(data: Any) -> tuple[ComponentReference, ...]:
        if not isinstance(data, list):
            data = [data]

        if not data:
            raise ValueError("Component reference list cannot be empty")

        return tuple(ComponentReference.from_string(item) for item in data)

    @classmethod
    def _parse_groups(cls, data: list) -> tuple[ComponentGroup, ...]:
        if not isinstance(data, list) or not data:
            raise ValueError("Component groups must be a non-empty list")

        groups: list[ComponentGroup] = []

        for idx, item in enumerate(data):
            if isinstance(item, dict):
                components = item.get("components", [])
                operator = DependencyMode(item.get("operator", "any"))
            elif isinstance(item, list):
                components = item
                operator = DependencyMode.ANY
            else:
                raise ValueError(f"Group {idx} must be list or dict")

            if not components:
                raise ValueError(f"Group {idx} must not be empty")

            groups.append(
                ComponentGroup(
                    components=cls._parse_component_refs(components),
                    operator=operator,
                )
            )

        return tuple(groups)

    @classmethod
    def _parse_side(
        cls, data: dict, name: str
    ) -> tuple[
        tuple[ComponentReference, ...],
        tuple[ComponentGroup, ...] | None,
    ]:
        std = data.get(name)
        groups = data.get(f"{name}_groups")

        if groups:
            parsed_groups = cls._parse_groups(groups)
            flat = tuple(c for g in parsed_groups for c in g.components)
            return flat, parsed_groups

        return cls._parse_component_refs(std), None


@dataclass(frozen=True, slots=True)
class DependencyRule(Rule):
    """Dependency of sources on targets.

    An optional dependency only states that the sources adapt to the targets when they
    are present: an absent target is normal, but a selected one must come first.
    """

    dependency_mode: DependencyMode = DependencyMode.ANY
    implicit_order: bool = True
    optional: bool = False
    source_groups: tuple[ComponentGroup, ...] | None = None
    target_groups: tuple[ComponentGroup, ...] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DependencyRule":
        sources, source_groups = cls._parse_side(data, "source")
        targets, target_groups = cls._parse_side(data, "target")

        return cls(
            rule_type=RuleType.DEPENDENCY,
            severity=RuleSeverity(data.get("severity", "error")),
            sources=sources,
            targets=targets,
            dependency_mode=DependencyMode(data.get("mode", "any")),
            implicit_order=data.get("implicit_order", True),
            optional=data.get("optional", False),
            description=data.get("description", ""),
            source_url=data.get("source_url"),
            source_groups=source_groups,
            target_groups=target_groups,
        )

    def uses_groups(self) -> bool:
        return bool(self.source_groups or self.target_groups)


@dataclass(frozen=True, slots=True)
class IncompatibilityRule(Rule):
    source_groups: tuple[ComponentGroup, ...] | None = None
    target_groups: tuple[ComponentGroup, ...] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IncompatibilityRule":
        sources, source_groups = cls._parse_side(data, "source")
        targets, target_groups = cls._parse_side(data, "target")

        return cls(
            rule_type=RuleType.INCOMPATIBILITY,
            severity=RuleSeverity(data.get("severity", "error")),
            sources=sources,
            targets=targets,
            description=data.get("description", ""),
            source_url=data.get("source_url"),
            source_groups=source_groups,
            target_groups=target_groups,
        )

    def uses_groups(self) -> bool:
        return bool(self.source_groups or self.target_groups)


@dataclass(frozen=True, slots=True)
class OrderRule(Rule):
    """Explicit order rule with direction."""

    order_direction: OrderDirection = OrderDirection.BEFORE

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrderRule":
        """Create OrderRule from dictionary.

        Raises ValueError if required fields are missing or invalid.
        """
        sources, _ = cls._parse_side(data, "source")
        targets, _ = cls._parse_side(data, "target")

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

        message = RuleViolation._insert_soft_breaks(message)

        if self.rule.description:
            message += f"\n{self.rule.description}"

        if self.is_broad_rule():
            message += "\n" + tr("page.order.violation.broad_rule_tooltip")

        escaped = message.replace("\n", "<br>")
        return f"<div>{self.icon} {escaped}</div>"

    def is_broad_rule(self) -> bool:
        """True if the rule targets a whole mod rather than specific components."""
        return any(ref.is_mod() for ref in (*self.rule.sources, *self.rule.targets))

    @staticmethod
    def _insert_soft_breaks(text: str, max_len: int = 30) -> str:
        """Insert soft-break hints in long unbreakable words."""
        return re.sub(
            r"\S{" + str(max_len) + r",}",
            lambda m: "&shy;".join(
                m.group(0)[i : i + max_len] for i in range(0, len(m.group(0)), max_len)
            ),
            text,
        )

    def _format_order_message(
        self,
        for_reference: ComponentReference,
        current_order: list[ComponentReference],
    ) -> str:
        """Format ORDER rule message with only violating components."""
        ref_pos = self._get_reference_position(for_reference, current_order)
        if ref_pos is None:
            return ""

        is_source = for_reference.matches(self.affected_components[0])
        direction = cast(OrderRule, self.rule).order_direction
        other_side = self.rule.targets if is_source else self.rule.sources
        positions = {ref: i for i, ref in enumerate(current_order)}

        def is_violation(other_pos: int) -> bool:
            if direction == OrderDirection.BEFORE:
                return (ref_pos > other_pos) if is_source else (other_pos > ref_pos)
            return (ref_pos < other_pos) if is_source else (other_pos < ref_pos)

        violating = [
            ref
            for ref in current_order
            if ref != for_reference
            and any(ref.matches(rule_ref) for rule_ref in other_side)
            and is_violation(positions[ref])
        ]

        if not violating:
            return ""

        by_mod: dict[str, list[str]] = {}
        for ref in violating:
            if not ref.is_mod():
                by_mod.setdefault(ref.mod_id, []).append(ref.comp_key)
            else:
                by_mod.setdefault(ref.mod_id, [])

        grouped = ", ".join(
            f"{mod}:{','.join(keys)}" if keys else mod for mod, keys in by_mod.items()
        )

        constraint_key = (
            "before" if (direction == OrderDirection.BEFORE) == is_source else "after"
        )

        return tr(f"rule.message_order_{constraint_key}", components=grouped)

    def _format_order_dependency_message(
        self, for_reference: ComponentReference, current_order: list[ComponentReference]
    ) -> str:
        """Format DEPENDENCY rule as order message with only violating components.

        Dependencies imply: targets (dependencies) must be BEFORE sources (dependents)
        """
        source = self.affected_components[0]
        if for_reference == source:
            return tr("rule.message_order_after", components=self._missing_expression())
        return tr("rule.message_order_before", components=str(source))

    def _missing_expression(self) -> str:
        """Format the unsatisfied part of an AND of component groups."""
        missing = set(self.affected_components[1:])
        rule = self.rule
        groups = rule.target_groups or (ComponentGroup(rule.targets, rule.dependency_mode),)
        expressions = []
        for group in groups:
            terms = [str(ref) for ref in group.components if ref in missing]
            if not terms:
                continue
            operator = " ∨ " if group.operator == DependencyMode.ANY else " ∧ "
            expression = operator.join(terms)
            expressions.append(f"({expression})" if len(terms) > 1 else expression)
        return " ∧ ".join(expressions)

    def _format_dependency_message(
        self, for_reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> str:
        """Format dependency violation message with current selection state."""
        is_source = any(for_reference.matches(source) for source in self.rule.sources)
        missing: list[ComponentReference] = []

        if is_source:
            missing = list(self.affected_components[1:])
            if missing:
                missing_str = self._missing_expression()
                return tr("rule.message_dependency_missing", missing=missing_str)
            return tr("rule.message_dependency_all_satisfied")
        for source in self.rule.sources:
            if source.is_selected_in(selected_set):
                missing.append(source)
        sources_str = ", ".join(str(s) for s in missing)
        return tr("rule.message_dependency_required_by", sources=sources_str)

    def _format_incompatibility_message(
        self, for_reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> str:
        """Format incompatibility violation message with current selection state."""
        if any(for_reference.matches(source) for source in self.rule.sources):
            conflicts = [ref for ref in self.rule.targets if ref.is_selected_in(selected_set)]
        else:
            conflicts = [ref for ref in self.rule.sources if ref.is_selected_in(selected_set)]

        if not conflicts:
            return tr("rule.message_incompatibility_resolved")

        conflict_names = ", ".join(str(ref) for ref in conflicts)
        return tr("rule.message_incompatibility", conflict_names=conflict_names)

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
