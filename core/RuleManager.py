import json
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable

from core.Rules import (
    Rule, DependencyRule, IncompatibilityRule, OrderRule,
    RuleType, RuleViolation, ComponentRef,
    DependencyMode, ValidationCache, OrderDirection
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
        self._all_rules: list[Rule] = []
        self._rules_by_source: dict[str, list[Rule]] = defaultdict(list)
        self._rules_by_type: dict[RuleType, list[Rule]] = defaultdict(list)
        self._components_by_mod: dict[str, set[str]] = defaultdict(set)

        # Cache
        self._cache = ValidationCache()

        if rules_dir and rules_dir.exists():
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
        self._load_rules_file(rules_dir / "dependencies.json", DependencyRule, self._dependency_rules)
        self._load_rules_file(rules_dir / "incompatibilities.json", IncompatibilityRule, self._incompatibility_rules)
        self._load_rules_file(rules_dir / "order.json", OrderRule, self._order_rules)

        logger.info(
            "Loaded rules: %d dependencies, %d incompatibilities, %d order rules",
            len(self._dependency_rules), len(self._incompatibility_rules), len(self._order_rules)
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

        for idx, rule_data in enumerate(rules_data):
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
                    exc_info=True
                )

        if loaded_count > 0:
            logger.info(f"Loaded {loaded_count} rule(s) from {path.name}")
        if error_count > 0:
            logger.warning(f"Skipped {error_count} invalid rule(s) from {path.name}")

    def _add_rule_to_indexes(self, rule: Rule) -> None:
        """Add rule to all indexes."""
        self._all_rules.append(rule)
        self._rules_by_type[rule.rule_type].append(rule)
        # Index by each source
        for source in rule.sources:
            self._rules_by_source[str(source)].append(rule)
            if source.comp_key and source.comp_key != '*':
                self._components_by_mod[source.mod_id.lower()].add(source.comp_key)

        for target in rule.targets:
            if target.comp_key and target.comp_key != '*':
                self._components_by_mod[target.mod_id.lower()].add(target.comp_key)

    def _get_known_components(self, mod_id: str) -> set[str]:
        """Get all known component keys for a mod from indexed rules."""
        return self._components_by_mod.get(mod_id.lower(), set())

    # -------------------------
    # Helpers - normalization / matching
    # -------------------------
    @staticmethod
    def _normalize_selected(selected_components: dict) -> dict[str, list[str]]:
        """
        Normalize selected_components into a dict[mod_id] -> list[comp_key].
        Accepts values as strings or dicts with "key".
        """
        normalized: dict[str, list[str]] = defaultdict(list)
        for mod, comps in (selected_components or {}).items():
            for comp in comps:
                if isinstance(comp, dict):
                    comp_key = comp.get("key")
                else:
                    comp_key = comp
                normalized[mod].append(comp_key)
        return dict(normalized)

    @staticmethod
    def _selected_items_set(normalized_selected: dict[str, list[str]]) -> set[tuple[str, str]]:
        """Return a set of (mod, comp) tuples for quick membership checks."""
        return {
            (mod, comp)
            for mod, comps in normalized_selected.items()
            for comp in comps
        }

    def _rules_for_component(self, mod_id: str, comp_key: str | None) -> list[Rule]:
        """Return rules that explicitly target the component or the mod."""
        comp_ref = str(ComponentRef(mod_id, comp_key))
        rules = list(self._rules_by_source.get(comp_ref, []))
        # mod-level rules apply to components
        if comp_key != "*":
            any_ref = str(ComponentRef(mod_id, "*"))
            rules.extend(self._rules_by_source.get(any_ref, []))
        return rules

    @staticmethod
    def _components_matching_ref(target_ref: ComponentRef, all_components: Iterable[tuple[str, str]]) -> list[
        tuple[str, str]]:
        """
        Return list of components (mod, comp) from all_components that match target_ref.
        target_ref may be mod-level, wildcard, or specific component.
        """
        res: list[tuple[str, str]] = []
        for mod, comp in all_components:
            if target_ref.matches(mod, comp):
                res.append((mod, comp))
        return res

    # -------------------------
    # Selection validation
    # -------------------------

    def validate_selection(self, selected_components: dict, use_cache: bool = True) -> list[RuleViolation]:
        """Validate component selection against dependency and incompatibility rules.

        Args:
            selected_components: Dict mapping mod_id to list of selected components
            use_cache: Whether to use cached results if available

        Returns:
            List of RuleViolation objects describing any violations
        """
        normalized = self._normalize_selected(selected_components)
        items = [(mod, comp) for mod, comps in normalized.items() for comp in comps]
        selection_hash = hash(frozenset(items))

        if use_cache and self._cache.selection_hash == selection_hash:
            return self._get_all_cached_violations()

        self._cache.clear()
        self._cache.selection_hash = selection_hash

        violations: list[RuleViolation] = []

        for mod, comps in normalized.items():
            for comp in comps:
                rules = self._rules_for_component(mod, comp)

                for rule in rules:
                    if rule.rule_type == RuleType.ORDER:
                        continue  # Order rules validated separately

                    violation = self._check_rule(rule, mod, comp, normalized)
                    if violation:
                        violations.append(violation)
                        self._cache_violation(violation)

        return violations

    def _get_all_cached_violations(self) -> list[RuleViolation]:
        """Extract all violations from cache as flat list."""
        return [
            violation
            for violations in self._cache.violations_by_component.values()
            for violation in violations
        ]

    def _cache_violation(self, violation: RuleViolation) -> None:
        """Cache a violation for all affected components."""
        for mod, comp in violation.affected_components:
            key = f"{mod}:{comp}"
            self._cache.violations_by_component.setdefault(key, []).append(violation)

    # -------------------------
    # Rule checking
    # -------------------------
    def _check_rule(self, rule: Rule, source_mod: str, source_comp: str,
                    selected: dict[str, list[str]]) -> RuleViolation | None:
        if isinstance(rule, DependencyRule):
            return self._check_dependency(rule, source_mod, source_comp, selected)
        if isinstance(rule, IncompatibilityRule):
            return self._check_incompatibility(rule, source_mod, source_comp, selected)
        return None

    def _check_dependency(self, rule: DependencyRule, source_mod: str, source_comp: str,
                          selected: dict[str, list[str]]) -> RuleViolation | None:
        """
        Check DependencyRule: supports ALL and ANY modes.
        """
        satisfied: list[ComponentRef] = []
        missing: list[ComponentRef] = []

        for target in rule.targets:
            is_satisfied = False

            if target.is_any_component():
                if target.mod_id in selected and len(selected[target.mod_id]) > 0:
                    is_satisfied = True
            else:
                selected_set = self._selected_items_set(selected)
                # exact component match
                if (target.mod_id, target.comp_key) in selected_set:
                    is_satisfied = True

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
        actions.append(tr("rule.message_deselect", mod=source_mod, source=source_comp))

        return RuleViolation(
            rule=rule,
            affected_components=((source_mod, source_comp),),
            message=message,
            suggested_actions=tuple(actions)
        )

    def _check_incompatibility(self, rule: IncompatibilityRule, source_mod: str, source_comp: str,
                               selected: dict[str, list[str]]) -> RuleViolation | None:
        """
        Check incompatibility: collect conflicting selected components.
        """
        conflicts: list[tuple[str, str]] = []
        selected_dict = selected  # already normalized

        for target in rule.targets:
            # mod-level or any-component
            if target.is_any_component():
                if target.mod_id in selected_dict:
                    for comp in selected_dict[target.mod_id]:
                        conflicts.append((target.mod_id, comp))
            else:
                # exact component
                if target.mod_id in selected_dict:
                    for comp in selected_dict[target.mod_id]:
                        if comp == target.comp_key:
                            conflicts.append((target.mod_id, comp))

        if not conflicts:
            return None

        conflict_names = ", ".join(f"{mod}:{comp}" for mod, comp in conflicts)
        message = tr("rule.message_incompatibility", conflict_names=conflict_names)
        if rule.description:
            message += f"\n{rule.description}"

        # Suggested actions
        actions = [tr("rule.message_deselect", mod=source_mod, source=source_comp)] + [
            tr("rule.message_deselect", mod=mod, source=comp) for mod, comp in conflicts]

        affected = ((source_mod, source_comp),) + tuple(conflicts)

        return RuleViolation(
            rule=rule,
            affected_components=affected,
            message=message,
            suggested_actions=tuple(actions)
        )

    # -------------------------
    # Order generation
    # -------------------------
    def generate_order(self, selected_components: dict, base_order: list[tuple[str, str]] | None = None) -> list[
        tuple[str, str]]:
        """
        Generate installation order from dependencies + explicit order rules.

        Dependencies create implicit order constraints:
        - Dependencies must be installed BEFORE dependents

        Args:
            selected_components: Components to order
            base_order: Optional base order to preserve for components without rules

        Returns:
            Ordered list of (mod_id, comp_key) tuples
        """
        normalized = self._normalize_selected(selected_components)
        all_components: set[tuple[str, str]] = self._selected_items_set(normalized)

        # Start from base_order where relevant
        ordered: list[tuple[str, str]] = []
        remaining: set[tuple[str, str]] = set(all_components)
        if base_order:
            for mod, comp in base_order:
                if (mod, comp) in all_components:
                    ordered.append((mod, comp))
                    remaining.discard((mod, comp))

        if not remaining:
            return ordered

        # Build graph edges (u -> v means u must come before v)
        graph: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        in_degree: dict[tuple[str, str], int] = defaultdict(int)

        for comp in remaining:
            in_degree[comp] = 0

        # helper to add edge
        def _add_edge(u: tuple[str, str], v: tuple[str, str]):
            if u == v:
                return
            if v not in graph[u]:
                graph[u].add(v)
                in_degree[v] = in_degree.get(v, 0) + 1

        # Add dependency-based edges: targets BEFORE sources
        for rule in self._dependency_rules:
            # sources = components matching any of rule.sources within remaining
            sources = []
            for source_ref in rule.sources:
                sources.extend([c for c in remaining if source_ref.matches(c[0], c[1])])

            if not sources:
                continue

            # gather targets (matching within remaining)
            targets: list[tuple[str, str]] = []
            for tref in rule.targets:
                targets.extend([c for c in remaining if tref.matches(c[0], c[1])])

            # For ordering we conservatively add edges from each target -> source
            for src in sources:
                for tgt in targets:
                    _add_edge(tgt, src)

        # Add explicit order rules
        for rule in self._order_rules:
            sources = []
            for source_ref in rule.sources:
                sources.extend([c for c in remaining if source_ref.matches(c[0], c[1])])

            if not sources:
                continue

            targets: list[tuple[str, str]] = []
            for tref in rule.targets:
                targets.extend([c for c in remaining if tref.matches(c[0], c[1])])

            if rule.order_direction == OrderDirection.BEFORE:
                for src in sources:
                    for tgt in targets:
                        _add_edge(src, tgt)
            else:  # AFTER -> targets BEFORE source
                for src in sources:
                    for tgt in targets:
                        _add_edge(tgt, src)

        # Kahn's algorithm for topological sort (deterministic by sorting queue)
        zero_q = deque(sorted([n for n in remaining if in_degree.get(n, 0) == 0], key=lambda x: (x[0], x[1])))
        sorted_remaining: list[tuple[str, str]] = []

        while zero_q:
            cur = zero_q.popleft()
            sorted_remaining.append(cur)
            for neigh in sorted(graph.get(cur, [])):
                in_degree[neigh] -= 1
                if in_degree[neigh] == 0:
                    zero_q.append(neigh)

        # If cycle detected, append remaining deterministically
        if len(sorted_remaining) != len(remaining):
            logger.warning("Circular dependencies detected in rules")
            unprocessed = remaining - set(sorted_remaining)
            sorted_remaining.extend(sorted(unprocessed, key=lambda x: (x[0], x[1])))

        return ordered + sorted_remaining

    # -------------------------
    # Order validation
    # -------------------------
    def validate_order(self, install_order: list[tuple[str, str]]) -> list[RuleViolation]:
        """
        Validate order against dependency rules (implicit) + explicit order rules.
        Populates cache with violations by component as before.
        """
        normalized_order, reverse_map = self._normalize_order(install_order)

        violations: list[RuleViolation] = []
        self._cache.clear()

        # Check DEPENDENCY rules (implicit ordering)
        for rule in self._dependency_rules:
            if rule.implicit_order:
                violations.extend(self._validate_dependency_order(rule, normalized_order, reverse_map))

        # Check explicit ORDER rules
        for rule in self._order_rules:
            violations.extend(self._validate_explicit_order(rule, normalized_order, reverse_map))

        return violations

    def _normalize_order(self, install_order: list[tuple[str, str]]):
        """
        Returns:
            normalized_order: list[(lower_mod, comp)]
            reverse_map: dict[(lower_mod, comp)] -> (orig_mod, comp)
        """
        normalized = []
        reverse = {}

        for mod, comp in install_order:
            mod_l = mod.lower()
            normalized.append((mod_l, comp))
            reverse[(mod_l, comp)] = (mod, comp)

        return normalized, reverse

    def _positions_map(self, install_order: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
        """Return mapping component -> index for fast lookups."""
        return {comp: idx for idx, comp in enumerate(install_order)}

    def _validate_dependency_order(self, rule: DependencyRule, install_order: list[tuple[str, str]], reverse_map) -> \
            list[RuleViolation]:
        """Validate that dependencies come before dependents."""
        violations: list[RuleViolation] = []
        pos = self._positions_map(install_order)

        # find sources (dependents) positions - check all sources
        source_positions = []
        for source_ref in rule.sources:
            source_positions.extend([
                (idx, m, c) for idx, (m, c) in enumerate(install_order)
                if source_ref.matches(m, c)
            ])

        if not source_positions:
            return violations

        # For each source, find target occurrences and ensure they come before source
        for source_idx, source_mod_l, source_comp in source_positions:
            for tref in rule.targets:
                # find targets
                for t_mod_l, t_comp in install_order:
                    if tref.matches(t_mod_l, t_comp):
                        dep_idx = pos[(t_mod_l, t_comp)]
                        if dep_idx > source_idx:
                            # restore original casing
                            source_mod, source_comp_o = reverse_map[(source_mod_l, source_comp)]
                            dep_mod, dep_comp_o = reverse_map[(t_mod_l, t_comp)]

                            message = tr(
                                "rule.message_dependency",
                                source_mod=source_mod,
                                source_comp=source_comp_o,
                                dep_mod=dep_mod,
                                dep_comp=dep_comp_o
                            )

                            if rule.description:
                                message += f"\n{rule.description}"

                            suggested_action = tr(
                                "rule.message_dependency_suggestion",
                                source_mod=source_mod,
                                source_comp=source_comp_o,
                                dep_mod=dep_mod,
                                dep_comp=dep_comp_o
                            )

                            violation = RuleViolation(
                                rule=rule,
                                affected_components=((source_mod, source_comp_o), (dep_mod, dep_comp_o)),
                                message=message,
                                suggested_actions=(suggested_action,)
                            )
                            violations.append(violation)
                            self._cache_violation(violation)

        return violations

    def _validate_explicit_order(self, rule: OrderRule, install_order: list[tuple[str, str]], reverse_map) -> list[
        RuleViolation]:
        """Validate explicit order rules."""
        violations: list[RuleViolation] = []

        # find source positions - check all sources
        source_positions = []
        for source_ref in rule.sources:
            source_positions.extend([
                (idx, m, c) for idx, (m, c) in enumerate(install_order)
                if source_ref.matches(m, c)
            ])

        if not source_positions:
            return violations

        for source_idx, source_mod, source_comp in source_positions:
            for tref in rule.targets:
                for target_idx, (target_mod, target_comp) in enumerate(install_order):
                    if (target_mod, target_comp) == (source_mod, source_comp):
                        continue
                    if not tref.matches(target_mod, target_comp):
                        continue

                    violation_detected = False
                    message = ""
                    action = ""

                    if rule.order_direction == OrderDirection.BEFORE:
                        if source_idx > target_idx:
                            violation_detected = True
                            # restore casing
                            source_mod, source_comp = reverse_map[(source_mod, source_comp)]
                            target_mod, target_comp = reverse_map[(target_mod, target_comp)]
                            message = tr("rule.message_order_before",
                                         source_mod=source_mod, source_comp=source_comp,
                                         target_mod=target_mod, target_comp=target_comp)
                            action = tr("rule.message_order_move_before",
                                        source_mod=source_mod, source_comp=source_comp,
                                        target_mod=target_mod, target_comp=target_comp)

                    else:  # AFTER
                        if source_idx < target_idx:
                            violation_detected = True
                            source_mod, source_comp = reverse_map[(source_mod, source_comp)]
                            target_mod, target_comp = reverse_map[(target_mod, target_comp)]
                            message = tr("rule.message_order_after",
                                         source_mod=source_mod, source_comp=source_comp,
                                         target_mod=target_mod, target_comp=target_comp)
                            action = tr("rule.message_order_move_after",
                                        source_mod=source_mod, source_comp=source_comp,
                                        target_mod=target_mod, target_comp=target_comp)

                    if violation_detected:
                        if rule.description:
                            message += f"\n{rule.description}"

                        violation = RuleViolation(
                            rule=rule,
                            affected_components=((source_mod, source_comp), (target_mod, target_comp)),
                            message=message,
                            suggested_actions=(action,)
                        )
                        violations.append(violation)
                        self._cache_violation(violation)

        return violations

    # -------------------------
    # Public utilities
    # -------------------------

    def get_violations_for_component(self, mod_id: str, comp_key: str) -> list[RuleViolation]:
        """Get cached violations for a specific component.

        Args:
            mod_id: Mod identifier
            comp_key: Component key

        Returns:
            List of violations affecting this component
        """
        return self._cache.get_violations(mod_id, comp_key)

    def get_requirements(self, mod_id: str, comp_key: str, recursive: bool = False) -> set[tuple[str, str]]:
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

    def has_error_violations(self, selected_components: dict) -> bool:
        """Check if selection has any error-level violations.

        Args:
            selected_components: Components to check

        Returns:
            True if any error-level violations exist
        """
        violations = self.validate_selection(selected_components, use_cache=True)
        return any(v.is_error for v in violations)
