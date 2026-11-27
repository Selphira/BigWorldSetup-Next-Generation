"""Selection validation of ``RuleManager`` before the shared selection context (plan 4.1).

Copied as pure functions fed by the rule index of a ``RuleManager``. Conditions are
evaluated against plain sets, with the former scan of the selection for mod wildcards,
so that the oracle does not depend on the code it checks. The public conditions are
shared with the order oracle (``order_v0``).
"""

from __future__ import annotations

from core.ComponentReference import ComponentReference
from core.RuleManager import RuleManager
from core.Rules import (
    ComponentGroup,
    DependencyMode,
    DependencyRule,
    IncompatibilityRule,
    Rule,
    RuleViolation,
)


def selection_violations(
    rule_manager: RuleManager, selection: set[ComponentReference]
) -> list[RuleViolation]:
    """Evaluate a candidate selection as ``RuleManager.selection_violations`` did."""
    selected = {ref for ref in selection if ref.is_component() or ref.is_sub_option()}
    violations = []
    for ref in sorted(selected, key=str):
        for rule in rule_manager.get_rules_for_component(ref):
            violation = _check_rule(rule, ref, selected)
            if violation:
                violations.append(violation)
    return violations


# ========================================
# RULE CHECKS
# ========================================


def _check_rule(
    rule: Rule, source_ref: ComponentReference, selected_set: set[ComponentReference]
) -> RuleViolation | None:
    if isinstance(rule, DependencyRule):
        return _check_dependency(rule, source_ref, selected_set)
    if isinstance(rule, IncompatibilityRule):
        return _check_incompatibility(rule, source_ref, selected_set)
    return None


def _check_dependency(
    rule: DependencyRule, source_ref: ComponentReference, selected_set: set[ComponentReference]
) -> RuleViolation | None:
    if rule.optional:
        return None
    if not sources_satisfied(rule, source_ref, selected_set):
        return None
    if _targets_satisfied(rule, selected_set):
        return None

    missing = _targets_missing(rule, selected_set)
    return RuleViolation(rule=rule, affected_components=(source_ref,) + tuple(missing))


def _check_incompatibility(
    rule: IncompatibilityRule,
    source_ref: ComponentReference,
    selected_set: set[ComponentReference],
) -> RuleViolation | None:
    if not sources_satisfied(rule, source_ref, selected_set):
        return None
    if not _targets_satisfied(rule, selected_set):
        return None

    conflicts = _targets_matching(rule, selected_set)
    return RuleViolation(rule=rule, affected_components=(source_ref,) + tuple(conflicts))


# ========================================
# CONDITIONS
# ========================================


def sources_satisfied(
    rule: DependencyRule | IncompatibilityRule,
    source_ref: ComponentReference,
    selected_set: set[ComponentReference],
) -> bool:
    if not any(covers(source, source_ref) for source in rule.sources):
        return False
    if rule.source_groups:
        return all(group_matches(group, selected_set) for group in rule.source_groups)
    return True


def target_groups(rule: DependencyRule | IncompatibilityRule) -> tuple[ComponentGroup, ...]:
    """Standard targets behave as one group: ALL/ANY for a dependency, ANY otherwise."""
    if rule.target_groups:
        return rule.target_groups
    mode = rule.dependency_mode if isinstance(rule, DependencyRule) else DependencyMode.ANY
    return (ComponentGroup(rule.targets, mode),)


def _targets_satisfied(
    rule: DependencyRule | IncompatibilityRule, selected_set: set[ComponentReference]
) -> bool:
    return all(group_matches(group, selected_set) for group in target_groups(rule))


def _targets_missing(
    rule: DependencyRule | IncompatibilityRule, selected_set: set[ComponentReference]
) -> list[ComponentReference]:
    return [
        comp
        for group in target_groups(rule)
        if not group_matches(group, selected_set)
        for comp in group.components
        if not is_selected_in(comp, selected_set)
    ]


def _targets_matching(
    rule: DependencyRule | IncompatibilityRule, selected_set: set[ComponentReference]
) -> list[ComponentReference]:
    return [
        comp
        for group in target_groups(rule)
        if group_matches(group, selected_set)
        for comp in group.components
        if is_selected_in(comp, selected_set)
    ]


def group_matches(group: ComponentGroup, selected_set: set[ComponentReference]) -> bool:
    matches = (is_selected_in(comp, selected_set) for comp in group.components)
    if group.operator == DependencyMode.ANY:
        return any(matches)
    return all(matches)


def covers(reference: ComponentReference, other: ComponentReference) -> bool:
    if reference.is_mod():
        return reference.mod_id == other.mod_id
    return reference == other


def is_selected_in(reference: ComponentReference, selection: set[ComponentReference]) -> bool:
    if reference.is_mod():
        return any(selected.mod_id == reference.mod_id for selected in selection)
    return reference in selection
