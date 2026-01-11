from collections import defaultdict
import json
import logging
from pathlib import Path
from typing import Callable, Protocol

from core.ComponentReference import ComponentReference, IndexManager
from core.Rules import (
    ComponentGroup,
    DependencyMode,
    DependencyRule,
    IncompatibilityRule,
    OrderDirection,
    OrderRule,
    Rule,
    RuleType,
    RuleViolation,
)

logger = logging.getLogger(__name__)


class ConditionEvaluator(Protocol):
    """Protocol for evaluating source/target conditions."""

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        """Check if condition is satisfied."""
        ...

    def get_missing(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get missing components."""
        ...


class StandardCondition:
    """Evaluates standard source/target."""

    __slots__ = ("components", "mode", "_matcher")

    def __init__(
        self,
        components: tuple[ComponentReference, ...],
        mode: DependencyMode,
        matcher: Callable[[ComponentReference, set[ComponentReference]], bool],
    ):
        self.components = components
        self.mode = mode
        self._matcher = matcher

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        """Check if condition is met."""
        matches = [self._matcher(comp, selected_set) for comp in self.components]

        if self.mode == DependencyMode.ALL:
            return all(matches)
        return any(matches)

    def get_missing(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get components that don't match."""
        return [comp for comp in self.components if not self._matcher(comp, selected_set)]


class GroupCondition:
    """Evaluates component groups (all groups must be satisfied)."""

    __slots__ = ("groups",)

    def __init__(self, groups: tuple[ComponentGroup, ...]):
        self.groups = groups

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        """All groups must be satisfied."""
        return all(group.matches(selected_set) for group in self.groups)

    def get_missing(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get missing components from unsatisfied groups."""
        missing = []
        for group in self.groups:
            if not group.matches(selected_set):
                missing.extend(group.get_missing_components(selected_set))
        return missing


class TrivialCondition:
    """Trivial condition evaluator (for optimization)."""

    __slots__ = ("_result",)

    def __init__(self, result: bool):
        self._result = result

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        return self._result

    @staticmethod
    def get_missing(selected_set: set[ComponentReference]) -> list[ComponentReference]:
        return []


# ===================================================================
# Rule Manager with optimized checking
# ===================================================================


class RuleManager:
    """Manages rules."""

    def __init__(self, rules_dir: Path):
        """Initialize rule manager."""
        self._dependency_rules: list[DependencyRule] = []
        self._incompatibility_rules: list[IncompatibilityRule] = []
        self._order_rules: list[OrderRule] = []

        # Indexes
        self._indexes = IndexManager.get_indexes()
        self._all_rules: list[Rule] = []
        self._rules_by_source: dict[ComponentReference, list[Rule]] = defaultdict(list)
        self._rules_by_type: dict[RuleType, list[Rule]] = defaultdict(list)
        self._components_by_mod: dict[str, set[str]] = defaultdict(set)

        self._last_selection_hash: int | None = None

        self.load_rules(rules_dir)

    # -------------------------
    # Loading helpers
    # -------------------------
    def load_rules(self, rules_dir: Path) -> None:
        """Load standard rule files from a directory (dependencies, incompatibilities, order)."""
        # Reset
        self._dependency_rules.clear()
        self._incompatibility_rules.clear()
        self._order_rules.clear()
        self._all_rules.clear()
        self._rules_by_source.clear()
        self._rules_by_type.clear()
        self._components_by_mod.clear()

        self._load_rules_file(
            rules_dir / "dependencies.json", DependencyRule, self._dependency_rules
        )
        self._load_rules_file(
            rules_dir / "incompatibilities.json",
            IncompatibilityRule,
            self._incompatibility_rules,
        )
        self._load_rules_file(rules_dir / "order.json", OrderRule, self._order_rules)

        group_rules = sum(1 for rule in self._dependency_rules if rule.uses_groups())

        logger.info(
            "Loaded rules: %d dependencies (%d with groups), %d incompatibilities, %d order rules",
            len(self._dependency_rules),
            group_rules,
            len(self._incompatibility_rules),
            len(self._order_rules),
        )

    def _load_rules_file(self, path: Path, cls, target_list: list) -> None:
        """Generic loader for a rule JSON file."""
        if not path.exists():
            return

        loaded_count = 0
        error_count = 0

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to parse JSON file {path}: {e}")
            return

        rules_data = data.get("rules", [])

        for rule_data in rules_data:
            try:
                rule = cls.from_dict(rule_data)
                target_list.append(rule)
                self._add_rule_to_indexes(rule)
                loaded_count += 1
            except (ValueError, KeyError, TypeError) as e:
                error_count += 1
                logger.error(
                    f"Failed to load rule '{rule_data}' from {path.name}: {e}. Rule skipped."
                )
            except Exception as e:
                error_count += 1
                logger.error(
                    f"Unexpected error loading rule '{rule_data}' from {path.name}: {e}. Rule skipped.",
                    exc_info=True,
                )

        if loaded_count > 0:
            logger.info(f"Loaded {loaded_count} rule(s) from {path.name}")

        if error_count > 0:
            logger.warning(f"Skipped {error_count} invalid rule(s) from {path.name}")

    def _add_rule_to_indexes(self, rule: Rule) -> None:
        """Add rule to all indexes."""
        self._all_rules.append(rule)
        self._rules_by_type[rule.rule_type].append(rule)

        for source_ref in rule.sources:
            self._rules_by_source[source_ref].append(rule)
            if not source_ref.is_mod():
                self._components_by_mod[source_ref.mod_id].add(source_ref.comp_key)

        for target_ref in rule.targets:
            if not target_ref.is_mod():
                self._components_by_mod[target_ref.mod_id].add(target_ref.comp_key)

    def _get_known_components(self, mod_id: str) -> set[str]:
        """Get all known component keys for a mod from indexed rules."""
        return self._components_by_mod.get(mod_id.lower(), set())

    # -------------------------
    # Selection validation
    # -------------------------

    def validate_selection(
        self, selected_components: list[ComponentReference]
    ) -> list[RuleViolation]:
        """Validate component selection against dependency and incompatibility rules."""
        references = [reference for reference in selected_components if not reference.is_mod()]
        selection_hash = hash(frozenset(references))

        if self._last_selection_hash == selection_hash:
            return self._get_all_cached_selection_violations()

        self._indexes.clear_selection_violations()
        self._last_selection_hash = selection_hash

        violations: list[RuleViolation] = []
        selected_set = set(references)

        for reference in references:
            rules = self._rules_by_source.get(reference, [])

            for rule in rules:
                if rule.rule_type == RuleType.ORDER:
                    continue  # Order rules validated separately

                violation = self._check_rule(rule, reference, selected_set)
                if violation:
                    violations.append(violation)
                    self._indexes.add_selection_violation(violation)

        for rule in self._all_rules:
            if rule.rule_type == RuleType.ORDER:
                continue

            for source in rule.sources:
                if source.is_mod() and self._matches_reference(source, selected_set):
                    # Find a matching component to use as source_ref
                    for reference in selected_set:
                        if reference.mod_id == source.mod_id:
                            violation = self._check_rule(rule, reference, selected_set)
                            if violation:
                                violations.append(violation)
                                self._indexes.add_selection_violation(violation)
                            break
                    break

        return violations

    def _get_all_cached_selection_violations(self) -> list[RuleViolation]:
        """Extract all violations from cache as flat list."""
        all_violations = []
        seen_violations = set()

        for violations in self._indexes.selection_violation_index.values():
            for violation in violations:
                # Use id() to avoid duplicates (same violation object)
                if id(violation) not in seen_violations:
                    all_violations.append(violation)
                    seen_violations.add(id(violation))

        return all_violations

    # -------------------------
    # Rule checking
    # -------------------------

    def _check_rule(
        self, rule: Rule, source_ref: ComponentReference, selected_set: set[ComponentReference]
    ) -> RuleViolation | None:
        if isinstance(rule, DependencyRule):
            return self._check_dependency(rule, source_ref, selected_set)
        if isinstance(rule, IncompatibilityRule):
            return self._check_incompatibility(rule, source_ref, selected_set)
        return None

    @staticmethod
    def _matches_reference(
        reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> bool:
        """Check if a reference matches any selected component."""
        if reference.is_mod():
            return any(selected.mod_id == reference.mod_id for selected in selected_set)
        return reference in selected_set

    def _check_dependency(
        self,
        rule: DependencyRule,
        source_ref: ComponentReference,
        selected_set: set[ComponentReference],
    ) -> RuleViolation | None:
        """Check DependencyRule: supports ALL and ANY modes."""
        source_evaluator = self._create_source_evaluator(rule, source_ref)

        if not source_evaluator.is_satisfied(selected_set):
            return None

        target_evaluator = self._create_target_evaluator(rule)

        if target_evaluator.is_satisfied(selected_set):
            return None

        missing = target_evaluator.get_missing(selected_set)
        affected = (source_ref,) + tuple(missing)

        return RuleViolation(rule=rule, affected_components=affected)

    @staticmethod
    def _create_source_evaluator(
        rule: DependencyRule, source_ref: ComponentReference
    ) -> ConditionEvaluator:
        """Factory method: create appropriate source evaluator."""
        if rule.source_groups:
            return GroupCondition(rule.source_groups)
        else:
            # Standard source condition: just check if source_ref is in rule.sources
            # For standard rules, source is already satisfied (we're here because it's selected)
            return TrivialCondition(source_ref in rule.sources)

    def _create_target_evaluator(self, rule: DependencyRule) -> ConditionEvaluator:
        """Factory method: create appropriate target evaluator."""
        if rule.target_groups:
            return GroupCondition(rule.target_groups)
        else:
            return StandardCondition(
                components=rule.targets,
                mode=rule.dependency_mode,
                matcher=self._matches_reference,
            )

    def _check_incompatibility(
        self,
        rule: IncompatibilityRule,
        source_ref: ComponentReference,
        selected_set: set[ComponentReference],
    ) -> RuleViolation | None:
        """Check incompatibility: collect conflicting selected components."""
        conflicts = [
            target for target in rule.targets if self._matches_reference(target, selected_set)
        ]

        if not conflicts:
            return None

        affected = (source_ref,) + tuple(conflicts)
        return RuleViolation(rule=rule, affected_components=affected)

    # -------------------------
    # Order validation
    # -------------------------
    def validate_order(
        self, sequences: dict[int, list[ComponentReference]]
    ) -> dict[int, list[RuleViolation]]:
        """Validate order for multiple sequences at once."""
        self._indexes.clear_order_violations()
        all_violations = {}

        for seq_idx, install_order in sequences.items():
            violations = []

            if not install_order:
                all_violations[seq_idx] = violations
                continue

            # Build position map
            positions = {ref: idx for idx, ref in enumerate(install_order)}

            # Check DEPENDENCY rules (implicit ordering)
            for rule in self._dependency_rules:
                if rule.implicit_order:
                    violations.extend(
                        self._validate_dependency_order(rule, install_order, positions)
                    )

            # Check explicit ORDER rules
            for rule in self._order_rules:
                violations.extend(self._validate_explicit_order(rule, install_order, positions))

            all_violations[seq_idx] = violations

        return all_violations

    def _validate_dependency_order(
        self,
        rule: DependencyRule,
        install_order: list[ComponentReference],
        positions: dict[ComponentReference, int],
    ) -> list[RuleViolation]:
        """Validate that dependencies come before dependents."""
        violations: list[RuleViolation] = []

        # find sources (dependents) positions - check all sources
        source_positions = [
            (positions[ref], ref) for ref in install_order if ref in rule.sources
        ]

        if not source_positions:
            return violations

        # For each source, ensure all targets come before it
        for source_idx, source_ref in source_positions:
            for target_ref in rule.targets:
                if target_ref not in positions:
                    continue

                target_idx = positions[target_ref]
                if target_idx > source_idx:
                    violation = RuleViolation(
                        rule=rule,
                        affected_components=(source_ref, target_ref),
                    )
                    violations.append(violation)
                    self._indexes.add_order_violation(violation)

        return violations

    def _validate_explicit_order(
        self,
        rule: OrderRule,
        install_order: list[ComponentReference],
        positions: dict[ComponentReference, int],
    ) -> list[RuleViolation]:
        """Validate explicit order rules."""
        violations: list[RuleViolation] = []

        # Find source positions
        source_positions = [
            (positions[ref], ref) for ref in install_order if ref in rule.sources
        ]

        if not source_positions:
            return violations

        for source_idx, source_ref in source_positions:
            for target_ref in rule.targets:
                if target_ref not in positions or target_ref == source_ref:
                    continue

                target_idx = positions[target_ref]
                violation_detected = False

                if rule.order_direction == OrderDirection.BEFORE:
                    if source_idx > target_idx:
                        violation_detected = True
                else:  # AFTER
                    if source_idx < target_idx:
                        violation_detected = True

                if violation_detected:
                    violation = RuleViolation(
                        rule=rule,
                        affected_components=(source_ref, target_ref),
                    )
                    violations.append(violation)
                    self._indexes.add_order_violation(violation)

        return violations

    # -------------------------
    # Public API
    # -------------------------

    def get_rules_for_component(self, reference: ComponentReference) -> list[Rule]:
        """Get all rules where the component is a source."""
        return self._rules_by_source.get(reference, [])

    def get_dependency_rules(self) -> tuple[DependencyRule, ...]:
        """Get all dependency rules (read-only)."""
        return tuple(self._dependency_rules)

    def get_order_rules(self) -> tuple[OrderRule, ...]:
        """Get all explicit order rules (read-only)."""
        return tuple(self._order_rules)

    def get_incompatibility_rules(self) -> tuple[IncompatibilityRule, ...]:
        """Get all incompatibility rules (read-only)."""
        return tuple(self._incompatibility_rules)

    def get_selection_violations(self, reference: ComponentReference) -> list[RuleViolation]:
        """Get cached selection violations for a specific component."""
        return self._indexes.get_selection_violations(reference)

    def get_order_violations(self, reference: ComponentReference) -> list[RuleViolation]:
        """Get cached order violations for a specific component."""
        return self._indexes.get_order_violations(reference)

    def get_requirements(
        self, mod_id: str, comp_key: str, recursive: bool = False
    ) -> set[tuple[str, str]]:
        """Get all components required by a specific component."""
        requirements: set[tuple[str, str]] = set()
        visiting: set[tuple[str, str]] = set()

        def _collect_requirements(mod_id: str, comp_key: str):
            current = (mod_id, comp_key)

            if current in visiting:
                return

            visiting.add(current)
            reference = ComponentReference.from_string(f"{mod_id}:{comp_key}")
            rules = self.get_rules_for_component(reference)

            for rule in rules:
                if not isinstance(rule, DependencyRule):
                    continue

                for target in rule.targets:
                    requirements.add((target.mod_id, target.comp_key))

                    if recursive:
                        if target.comp_key == "*":
                            known_comps = self._get_known_components(target.mod_id)
                            for comp in known_comps:
                                _collect_requirements(target.mod_id, comp)
                        else:
                            _collect_requirements(target.mod_id, target.comp_key)

        _collect_requirements(mod_id, comp_key)
        return requirements
