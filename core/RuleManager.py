from collections import defaultdict
import json
import logging
from pathlib import Path

from core.ComponentReference import ComponentReference, IndexManager
from core.Rules import (
    DependencyMode,
    DependencyRule,
    IncompatibilityRule,
    OrderDirection,
    OrderRule,
    Rule,
    RuleType,
    RuleViolation,
)
from core.TranslationManager import tr

logger = logging.getLogger(__name__)


class RuleManager:
    """Manages rules from separate files with implicit dependency ordering."""

    def __init__(self, rules_dir: Path | None = None):
        """
        Initialize rule manager.

        Args:
            rules_dir: Directory containing rule JSON files
        """
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

        # Generic loader usage
        self._load_rules_file(
            rules_dir / "dependencies.json", DependencyRule, self._dependency_rules
        )
        self._load_rules_file(
            rules_dir / "incompatibilities.json",
            IncompatibilityRule,
            self._incompatibility_rules,
        )
        self._load_rules_file(rules_dir / "order.json", OrderRule, self._order_rules)

        logger.info(
            "Loaded rules: %d dependencies, %d incompatibilities, %d order rules",
            len(self._dependency_rules),
            len(self._incompatibility_rules),
            len(self._order_rules),
        )

    def _load_rules_file(self, path: Path, cls, target_list: list) -> None:
        """Generic loader for a rule JSON file into target_list."""
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
        """Validate component selection against dependency and incompatibility rules.

        Args:
            selected_components: Dict mapping mod_id to list of selected components

        Returns:
            List of RuleViolation objects describing any violations
        """
        references = [reference for reference in selected_components if not reference.is_mod()]

        selection_hash = hash(frozenset(references))

        if self._last_selection_hash == selection_hash:
            return self._get_all_cached_violations()

        self._indexes.clear_violations()
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
                    self._indexes.add_violation(violation)

        for rule in self._all_rules:
            if rule.rule_type == RuleType.ORDER:
                continue

            for source in rule.sources:
                if source.is_mod():
                    if self._matches_reference(source, selected_set):
                        # At least one component from this mod is selected
                        # Check the rule for one of the matching components
                        mod_id = source.mod_id
                        for reference in selected_set:
                            if reference.mod_id == mod_id:
                                violation = self._check_rule(rule, reference, selected_set)
                                if violation:
                                    violations.append(violation)
                                    self._indexes.add_violation(violation)

                    break

        return violations

    def _get_all_cached_violations(self) -> list[RuleViolation]:
        """Extract all violations from cache as flat list."""
        all_violations = []
        seen_violations = set()

        for violations in self._indexes.violation_index.values():
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
        """Check if a reference matches any selected component.

        Args:
            reference: Reference to check
            selected_set: Set of selected component references

        Returns:
            True if reference matches at least one selected component
        """
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
        satisfied_count = 0
        missing: list[ComponentReference] = []

        for target in rule.targets:
            if self._matches_reference(target, selected_set):
                satisfied_count += 1
            else:
                missing.append(target)

        if rule.dependency_mode == DependencyMode.ALL:
            is_violated = bool(missing)
        else:  # ANY
            is_violated = satisfied_count == 0

        if not is_violated:
            return None

        affected = (source_ref,) + tuple(missing)

        return RuleViolation(
            rule=rule,
            affected_components=affected,
            suggested_actions=tuple(),
        )

    def _check_incompatibility(
        self,
        rule: IncompatibilityRule,
        source_ref: ComponentReference,
        selected_set: set[ComponentReference],
    ) -> RuleViolation | None:
        """Check incompatibility: collect conflicting selected components."""
        conflicts: list[ComponentReference] = []

        for target in rule.targets:
            if self._matches_reference(target, selected_set):
                conflicts.append(target)

        if not conflicts:
            return None

        affected = (source_ref,) + tuple(conflicts)

        return RuleViolation(
            rule=rule,
            affected_components=affected,
        )

    # -------------------------
    # Order validation
    # -------------------------
    def validate_order(self, install_order: list[ComponentReference]) -> list[RuleViolation]:
        """Validate order against dependency rules (implicit) + explicit order rules."""
        violations: list[RuleViolation] = []
        self._indexes.clear_violations()

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

        return violations

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
                    message = tr(
                        "rule.message_dependency",
                        source_mod=source_ref.mod_id,
                        source_comp=source_ref.comp_key,
                        dep_mod=target_ref.mod_id,
                        dep_comp=target_ref.comp_key,
                    )

                    if rule.description:
                        message += f"\n{rule.description}"

                    suggested_action = tr(
                        "rule.message_dependency_suggestion",
                        source_mod=source_ref.mod_id,
                        source_comp=source_ref.comp_key,
                        dep_mod=target_ref.mod_id,
                        dep_comp=target_ref.comp_key,
                    )

                    violation = RuleViolation(
                        rule=rule,
                        affected_components=(
                            source_ref,
                            target_ref,
                        ),
                        message=message,
                        suggested_actions=(suggested_action,),
                    )
                    violations.append(violation)
                    self._indexes.add_violation(violation)

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
                message = ""
                action = ""

                if rule.order_direction == OrderDirection.BEFORE:
                    if source_idx > target_idx:
                        violation_detected = True
                        message = tr(
                            "rule.message_order_before",
                            source_mod=source_ref.mod_id,
                            source_comp=source_ref.comp_key,
                            target_mod=target_ref.mod_id,
                            target_comp=target_ref.comp_key,
                        )
                        action = tr(
                            "rule.message_order_move_before",
                            source_mod=source_ref.mod_id,
                            source_comp=source_ref.comp_key,
                            target_mod=target_ref.mod_id,
                            target_comp=target_ref.comp_key,
                        )

                else:  # AFTER
                    if source_idx < target_idx:
                        violation_detected = True
                        message = tr(
                            "rule.message_order_after",
                            source_mod=source_ref.mod_id,
                            source_comp=source_ref.comp_key,
                            target_mod=target_ref.mod_id,
                            target_comp=target_ref.comp_key,
                        )
                        action = tr(
                            "rule.message_order_move_after",
                            source_mod=source_ref.mod_id,
                            source_comp=source_ref.comp_key,
                            target_mod=target_ref.mod_id,
                            target_comp=target_ref.comp_key,
                        )

                if violation_detected:
                    if rule.description:
                        message += f"\n{rule.description}"

                    violation = RuleViolation(
                        rule=rule,
                        affected_components=(
                            source_ref,
                            target_ref,
                        ),
                        message=message,
                        suggested_actions=(action,),
                    )
                    violations.append(violation)
                    self._indexes.add_violation(violation)

        return violations

    # -------------------------
    # Public API
    # -------------------------

    def get_rules_for_component(self, reference: ComponentReference) -> list[Rule]:
        """Get all rules where the component is a source.

        Args:
            reference: Component reference

        Returns:
            List of rules where component appears as source
        """
        return self._rules_by_source.get(reference, [])

    def get_dependency_rules(self) -> tuple[DependencyRule, ...]:
        """Get all dependency rules (read-only).

        Returns:
            Tuple of dependency rules (immutable)
        """
        return tuple(self._dependency_rules)

    def get_order_rules(self) -> tuple[OrderRule, ...]:
        """Get all explicit order rules (read-only).

        Returns:
            Tuple of order rules (immutable)
        """
        return tuple(self._order_rules)

    def get_incompatibility_rules(self) -> tuple[IncompatibilityRule, ...]:
        """Get all incompatibility rules (read-only).

        Returns:
            Tuple of incompatibility rules (immutable)
        """
        return tuple(self._incompatibility_rules)

    def get_violations_for_component(
        self, reference: ComponentReference
    ) -> list[RuleViolation]:
        """Get cached violations for a specific component.

        Args:
            reference: Component reference

        Returns:
            List of violations affecting this component
        """
        return self._indexes.get_violations(reference)

    def get_requirements(
        self, mod_id: str, comp_key: str, recursive: bool = False
    ) -> set[tuple[str, str]]:
        """Get all components required by a specific component.

        Args:
            mod_id: Mod identifier of the source component
            comp_key: Component key of the source component
            recursive: If True, include transitive dependencies

        Returns:
            List of (mod_id, comp_key) tuples representing required components
        """
        requirements: set[tuple[str, str]] = set()
        visiting: set[tuple[str, str]] = set()

        def _collect_requirements(mod_id: str, comp_key: str):
            current = (mod_id, comp_key)

            # Protection contre les cycles
            if current in visiting:
                return

            visiting.add(current)
            rules = self._rules_for_component(mod_id, comp_key)

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

    def has_error_violations(self, selected_components: list[str]) -> bool:
        """Check if selection has any error-level violations.

        Args:
            selected_components: Components to check

        Returns:
            True if any error-level violations exist
        """
        violations = self.validate_selection(selected_components, use_cache=True)
        return any(v.is_error for v in violations)
