from collections import defaultdict, deque
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

    def _check_dependency(
        self,
        rule: DependencyRule,
        source_ref: ComponentReference,
        selected_set: set[ComponentReference],
    ) -> RuleViolation | None:
        """Check DependencyRule: supports ALL and ANY modes."""
        satisfied: list[ComponentReference] = []
        missing: list[ComponentReference] = []

        for target in rule.targets:
            is_satisfied = target in selected_set

            if is_satisfied:
                satisfied.append(target)
            else:
                missing.append(target)

        if rule.dependency_mode == DependencyMode.ALL:
            is_violated = bool(missing)
        else:  # ANY
            is_violated = len(satisfied) == 0

        if not is_violated:
            return None

        # Build violation message
        if rule.dependency_mode == DependencyMode.ALL:
            missing_str = ", ".join(str(t) for t in missing)
            message = f"Missing required dependencies: {missing_str}"
        else:
            targets_str = ", ".join(str(t) for t in rule.targets)
            message = f"Requires at least one of: {targets_str}"

        if rule.description:
            message += f"\n{rule.description}"

        actions: list[str] = []
        if rule.dependency_mode == DependencyMode.ALL:
            for target in missing:
                actions.append(tr("rule.message_select", target=target))
        else:
            for target in rule.targets:
                actions.append(tr("rule.message_select", target=target))
        actions.append(
            tr("rule.message_deselect", mod=source_ref.mod_id, source=source_ref.comp_key)
        )

        return RuleViolation(
            rule=rule,
            affected_components=(source_ref,),
            message=message,
            suggested_actions=tuple(actions),
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
            if target in selected_set:
                conflicts.append(target)

        if not conflicts:
            return None

        conflict_names = ", ".join(str(c) for c in conflicts)
        message = tr("rule.message_incompatibility", conflict_names=conflict_names)
        if rule.description:
            message += f"\n{rule.description}"

        # Suggested actions
        actions = [
            tr("rule.message_deselect", mod=source_ref.mod_id, source=source_ref.comp_key)
        ] + [tr("rule.message_deselect", mod=c.mod_id, source=c.comp_key) for c in conflicts]

        affected = (source_ref,) + tuple(conflicts)

        return RuleViolation(
            rule=rule,
            affected_components=affected,
            message=message,
            suggested_actions=tuple(actions),
        )

    # -------------------------
    # Order generation
    # -------------------------
    def generate_order(
        self,
        selected_components: list[ComponentReference],
        base_order: list[ComponentReference] | None = None,
    ) -> list[ComponentReference]:
        """
        Generate installation order by completing base_order with dependency rules.

        Args:
            selected_components: All selected components
            base_order: Optional existing manual order to preserve (has priority)

        Returns:
            Ordered list of ComponentReference
        """
        if not selected_components:
            return []

        selected_set = set(selected_components)
        components_with_rules = self._get_components_with_rules(selected_set)

        if base_order:
            components_to_order = components_with_rules | set(
                ref for ref in base_order if ref in selected_set
            )
        else:
            components_to_order = components_with_rules

        if not components_to_order:
            return base_order if base_order else []

        graph, in_degree = self._build_dependency_graph(components_to_order)
        ideal_order = self._topological_sort(graph, in_degree, components_to_order)

        if not base_order:
            return ideal_order

        return self._merge_orders(ideal_order, base_order, components_to_order)

    def _get_components_with_rules(
        self, selected_refs: set[ComponentReference]
    ) -> set[ComponentReference]:
        """
        Find all components that have ordering rules (dependencies or explicit order).

        Uses _rules_by_source index for O(1) lookups.

        Returns:
            Set of components that appear in any rule (as source or target)
        """
        components_with_rules: set[ComponentReference] = set()

        # Use index for fast lookup - O(1) per component
        for ref in selected_refs:
            rules = self._rules_by_source.get(ref, [])

            if not rules:
                continue

            # Component has rules as source
            components_with_rules.add(ref)

            # Add all targets from these rules
            for rule in rules:
                # Only include rules that affect ordering
                if isinstance(rule, DependencyRule) and not rule.implicit_order:
                    continue

                if not isinstance(rule, (DependencyRule, OrderRule)):
                    continue

                for target_ref in rule.targets:
                    if target_ref in selected_refs:
                        components_with_rules.add(target_ref)

        return components_with_rules

    def _build_dependency_graph(
        self, components_to_order: set[ComponentReference]
    ) -> tuple[
        dict[ComponentReference, set[ComponentReference]], dict[ComponentReference, int]
    ]:
        """
        Build dependency graph from rules.

        Edge u -> v means: u must be installed BEFORE v

        Args:
            components_to_order: Only these components are included in graph

        Returns:
            (graph, in_degree) where:
            - graph[u] = set of nodes that depend on u (u -> v)
            - in_degree[v] = number of dependencies v has
        """
        graph: dict[ComponentReference, set[ComponentReference]] = defaultdict(set)
        in_degree: dict[ComponentReference, int] = {ref: 0 for ref in components_to_order}

        def add_edge(before: ComponentReference, after: ComponentReference):
            """Add edge: before -> after (before must come before after)."""
            if before == after:
                return
            if before not in components_to_order or after not in components_to_order:
                return

            if after not in graph[before]:
                graph[before].add(after)
                in_degree[after] += 1

        # Process DEPENDENCY rules: dependencies come BEFORE dependents
        for rule in self._dependency_rules:
            if not rule.implicit_order:
                continue

            # Find sources (dependents) in components_to_order
            sources = [ref for ref in components_to_order if ref in rule.sources]
            if not sources:
                continue

            # Find targets (dependencies) in components_to_order
            targets = [ref for ref in components_to_order if ref in rule.targets]
            if not targets:
                continue

            # Add edges: target -> source (dependency BEFORE dependent)
            for target in targets:
                for source in sources:
                    add_edge(target, source)

        # Process ORDER rules
        for rule in self._order_rules:
            sources = [ref for ref in components_to_order if ref in rule.sources]
            if not sources:
                continue

            targets = [ref for ref in components_to_order if ref in rule.targets]
            if not targets:
                continue

            for source in sources:
                for target in targets:
                    if rule.order_direction == OrderDirection.BEFORE:
                        # source BEFORE target
                        add_edge(source, target)
                    else:
                        # source AFTER target -> target BEFORE source
                        add_edge(target, source)

        return graph, in_degree

    def _topological_sort(
        self,
        graph: dict[ComponentReference, set[ComponentReference]],
        in_degree: dict[ComponentReference, int],
        components_to_order: set[ComponentReference],
    ) -> list[ComponentReference]:
        """
        Kahn's algorithm for topological sort.

        Returns deterministic order by sorting at each step.
        """
        # Find nodes with no dependencies
        zero_degree = deque(
            sorted(
                [ref for ref in components_to_order if in_degree[ref] == 0],
                key=lambda r: (r.mod_id, r.comp_key),
            )
        )

        result: list[ComponentReference] = []
        in_degree_copy = in_degree.copy()

        while zero_degree:
            current = zero_degree.popleft()
            result.append(current)

            # Process neighbors (nodes that depend on current)
            for neighbor in sorted(
                graph.get(current, []), key=lambda r: (r.mod_id, r.comp_key)
            ):
                in_degree_copy[neighbor] -= 1
                if in_degree_copy[neighbor] == 0:
                    zero_degree.append(neighbor)

        # Check for cycles
        if len(result) != len(components_to_order):
            logger.warning("Circular dependencies detected - adding remaining nodes")
            unprocessed = components_to_order - set(result)
            result.extend(sorted(unprocessed, key=lambda r: (r.mod_id, r.comp_key)))

        return result

    def _merge_orders(
        self,
        ideal_order: list[ComponentReference],
        base_order: list[ComponentReference],
        components_to_order: set[ComponentReference],
    ) -> list[ComponentReference]:
        """
        Merge ideal_order with base_order while preserving:
        1. Relative positions from base_order where possible
        2. Dependency constraints from ideal_order
        """
        # Find components that need to be inserted
        base_set = set(base_order)
        to_insert = [ref for ref in ideal_order if ref not in base_set]

        if not to_insert:
            return base_order

        # Build position map from ideal_order (for constraint checking)
        ideal_positions = {ref: idx for idx, ref in enumerate(ideal_order)}

        # Insert each component at the best position
        result = base_order.copy()

        for ref_to_insert in to_insert:
            best_position = self._find_best_position(ref_to_insert, result, ideal_positions)
            result.insert(best_position, ref_to_insert)

        return result

    def _find_best_position(
        self,
        ref: ComponentReference,
        current_order: list[ComponentReference],
        ideal_positions: dict[ComponentReference, int],
    ) -> int:
        """
        Find best position to insert ref into current_order.

        Rules:
        1. Must respect dependency constraints from ideal_positions
        2. Insert as close as possible to ideal position

        Returns:
            Index where ref should be inserted
        """
        if not current_order:
            return 0

        ref_ideal_pos = ideal_positions[ref]

        # Find valid range based on dependencies
        min_pos = 0  # Earliest valid position
        max_pos = len(current_order)  # Latest valid position

        for idx, existing_ref in enumerate(current_order):
            existing_ideal_pos = ideal_positions.get(existing_ref)

            if existing_ideal_pos is None:
                continue

            # If existing should come BEFORE ref in ideal order
            if existing_ideal_pos < ref_ideal_pos:
                min_pos = max(min_pos, idx + 1)

            # If existing should come AFTER ref in ideal order
            elif existing_ideal_pos > ref_ideal_pos:
                max_pos = min(max_pos, idx)

        # Insert at min_pos (respects all constraints and closest to ideal)
        return min_pos

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
    # Public utilities
    # -------------------------

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
