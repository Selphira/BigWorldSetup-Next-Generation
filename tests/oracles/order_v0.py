"""Order scheduling, order validation and resolution before the lot 5 rewrites (plan 5.1).

Copied as pure functions fed by the rule index of a ``RuleManager``: only its lookups are
read, never its evaluators. Conditions are evaluated against plain sets with the former
scan of the selection for mod wildcards (shared with ``rules_v0``), so that the oracle does
not depend on the code it checks. ``validate_order`` is the full validation, without the
incremental state of the manager.
"""

from __future__ import annotations

from collections import defaultdict
from graphlib import CycleError, TopologicalSorter
from heapq import heappop, heappush
from itertools import count

from core.ComponentReference import ComponentReference, IndexManager
from core.RuleManager import RuleManager, SuggestedOrder
from core.Rules import (
    ComponentGroup,
    DependencyRule,
    OrderDirection,
    OrderRule,
    Rule,
    RuleType,
    RuleViolation,
)
from core.SelectionSource import SelectionSource
from core.ValidationOrchestrator import ResolutionProposal
from tests.oracles import rules_v0
from tests.oracles.rules_v0 import (
    group_matches,
    is_selected_in,
    sources_satisfied,
    target_groups,
)

type Options = dict[ComponentReference, set[ComponentReference]]


# ========================================
# PREFERRED ORDER
# ========================================


def preferred_order(
    rule_manager: RuleManager,
    editorial: list[ComponentReference],
    earlier: set[ComponentReference],
    selection: set[ComponentReference],
) -> SuggestedOrder:
    """Schedule as ``RuleManager.preferred_order`` did: the first ready editorial entry."""
    pending = list(dict.fromkeys(editorial))
    active = set(pending)
    before: dict[ComponentReference, set[ComponentReference]] = defaultdict(set)
    for rule in {
        rule
        for ref in active
        for rule in rule_manager.get_rules_for_component(ref)
        if isinstance(rule, OrderRule)
    }:
        sources, targets = _resolved(rule_manager, rule)
        for source in sources & active:
            for target in targets & active:
                if source != target:
                    first, second = (
                        (source, target)
                        if rule.order_direction == OrderDirection.BEFORE
                        else (target, source)
                    )
                    before[second].add(first)

    choices = selected_options(selection)
    available = earlier | {option for base in earlier for option in choices.get(base, ())}
    ordered = []
    placed = set()
    while pending:
        ready = next(
            (
                ref
                for ref in pending
                if before[ref] <= placed
                and all(
                    _order_satisfied(rule, selection, available)
                    for rule in ordering_requirements(
                        rule_manager, ref, selection, choices.get(ref, set())
                    )
                    if rule.implicit_order
                )
            ),
            None,
        )
        if ready is None:
            break
        ordered.append(ready)
        pending.remove(ready)
        placed.add(ready)
        available.add(ready)
        available.update(choices.get(ready, ()))

    cycle = ()
    if pending:
        remaining = set(pending)
        for ref in pending:
            for rule in ordering_requirements(
                rule_manager, ref, selection, choices.get(ref, set())
            ):
                if not rule.implicit_order or _order_satisfied(rule, selection, available):
                    continue
                for missing in _order_missing(rule, selection, available):
                    before[ref].update(
                        candidate
                        for candidate in remaining
                        if is_selected_in(missing, {candidate} | choices.get(candidate, set()))
                    )
        try:
            TopologicalSorter(before).prepare()
        except CycleError as error:
            cycle = tuple(error.args[1])
    return SuggestedOrder(ordered + pending, cycle)


# ========================================
# ORDER VALIDATION
# ========================================


def validate_order(
    rule_manager: RuleManager,
    install_order: list[ComponentReference],
    earlier: set[ComponentReference] | None = None,
    later: set[ComponentReference] | None = None,
    selection: set[ComponentReference] | None = None,
) -> list[RuleViolation]:
    """Validate a whole sequence as a full ``RuleManager.validate_order`` did."""
    earlier = earlier or set()
    later = later or set()
    selection = selection if selection is not None else earlier | set(install_order)
    choices = selected_options(selection)
    earlier = earlier | {option for base in earlier for option in choices.get(base, ())}
    positions = {ref: idx for idx, ref in enumerate(install_order)}
    active = set(install_order)

    affected_rules = set()
    for ref in install_order:
        affected_rules.update(rule_manager.get_rules_for_component(ref))
        for option in choices.get(ref, ()):
            affected_rules.update(rule_manager.get_rules_for_component(option))

    violations = []
    for rule in affected_rules:
        all_sources, all_targets = _resolved(rule_manager, rule)
        sources = active & all_sources
        targets = all_targets & active

        if isinstance(rule, DependencyRule) and rule.implicit_order:
            sources.update(
                option.get_base_component_reference()
                for option in all_sources & selection
                if option.is_sub_option() and option.get_base_component_reference() in active
            )
            for src in sources:
                if rule not in ordering_requirements(
                    rule_manager, src, selection, choices.get(src, set())
                ):
                    continue
                preceding = earlier | set(install_order[: positions[src]])
                preceding.update(
                    option for base in tuple(preceding) for option in choices.get(base, ())
                )
                if _order_satisfied(rule, selection, preceding):
                    continue
                missing = tuple(_order_missing(rule, selection, preceding))
                violations.append(RuleViolation(rule=rule, affected_components=(src, *missing)))
            continue
        if not isinstance(rule, OrderRule):
            continue

        direction = rule.order_direction
        outside = earlier if direction == OrderDirection.BEFORE else later
        for source in sources:
            for target in all_targets & outside:
                if source != target:
                    violations.append(RuleViolation(rule, (source, target)))

        for src in sources:
            for tgt in targets:
                if src != tgt and _misplaced(positions[src], positions[tgt], direction):
                    violations.append(RuleViolation(rule=rule, affected_components=(src, tgt)))
    return violations


def _misplaced(source_pos: int, target_pos: int, direction: OrderDirection) -> bool:
    if direction == OrderDirection.BEFORE:
        return source_pos > target_pos
    return source_pos < target_pos


# ========================================
# BLOCKED POSITIONS
# ========================================


def blocked_positions(
    rule_manager: RuleManager,
    sequences: list[list[ComponentReference]],
    unavailable: set[tuple[int, int]],
    completed_through: tuple[int, int],
    selection: set[ComponentReference],
) -> set[tuple[int, int]]:
    """Project losses in plan order as ``RuleManager.blocked_positions`` did."""
    expected: set[ComponentReference] = set()
    available: set[ComponentReference] = set()
    provided = selected_options(selection)
    blocked = set()
    for seq_idx, sequence in enumerate(sequences):
        for idx, ref in enumerate(sequence):
            position = (seq_idx, idx)
            identities = {ref} | provided.get(ref, set())
            if position in unavailable:
                expected.update(identities)
                continue
            if position > completed_through and _lost_requirement(
                rule_manager, ref, selection, expected, available, provided.get(ref, set())
            ):
                blocked.add(position)
            else:
                available.update(identities)
            expected.update(identities)
    return blocked


def _lost_requirement(
    rule_manager: RuleManager,
    reference: ComponentReference,
    selection: set[ComponentReference],
    expected: set[ComponentReference],
    available: set[ComponentReference],
    options: set[ComponentReference],
) -> bool:
    """Whether a mandatory prerequisite lost its last preceding provider."""
    dependents = rule_manager._dependents_by_target
    relevant = set()
    for lost in expected - available:
        relevant.update(dependents.get(lost, ()))
        relevant.update(dependents.get(ComponentReference.for_mod(lost.mod_id), ()))

    mandatory = {
        rule
        for rule in ordering_requirements(rule_manager, reference, selection, options)
        if not rule.optional
    }
    return any(
        _targets_satisfied(rule, expected) and not _targets_satisfied(rule, available)
        for rule in mandatory & relevant
    )


# ========================================
# AUTOMATIC RESOLUTION
# ========================================


def auto_resolve(
    rule_manager: RuleManager,
    selection_source: SelectionSource,
    reference: ComponentReference,
) -> ResolutionProposal | None:
    """Search selection transitions as ``ValidationOrchestrator.auto_resolve`` did."""
    indexes = IndexManager.get_indexes()
    original = selection_source.selection_snapshot()
    if reference not in original:
        return None
    baseline = _unrelated_violations(rule_manager, original, reference)
    serial = count()
    pending = [(0, 0, next(serial), original)]
    seen = {original}
    protected = (selection_source.forced_selection() & original) | {reference}
    pool = indexes.mod_component_index.keys() | indexes.parent_index.keys()
    while pending:
        _, _, _, candidate = heappop(pending)
        violations = [
            v
            for v in rules_v0.selection_violations(rule_manager, set(candidate))
            if (v.rule, v.affected_components[0]) not in baseline
        ]
        if not violations:
            return ResolutionProposal(reference, original, candidate)

        violation = violations[0]
        adding = violation.rule.rule_type == RuleType.DEPENDENCY
        patterns = (
            violation.affected_components[1:] if adding else violation.affected_components
        )
        options = set()
        for pattern in patterns:
            if pattern.is_mod():
                options.update(
                    ref for ref in pool if ref.is_component() and ref.mod_id == pattern.mod_id
                )
            elif pattern in pool:
                options.add(pattern)
        options = options - candidate if adding else options & candidate
        for ref in sorted(options, key=str):
            simulation = selection_source.preview(set(candidate))
            if adding:
                simulation.select(ref)
            else:
                simulation.unselect(ref)
            state = simulation.selection_snapshot()
            if not protected <= state or state in seen:
                continue
            seen.add(state)
            heappush(
                pending, (len(original - state), len(state - original), next(serial), state)
            )
    return None


def _unrelated_violations(
    rule_manager: RuleManager,
    selection: frozenset[ComponentReference],
    reference: ComponentReference,
) -> set[tuple[Rule, ComponentReference]]:
    return {
        (v.rule, v.affected_components[0])
        for v in rules_v0.selection_violations(rule_manager, set(selection))
        if reference not in v.affected_components
    }


# ========================================
# REQUIREMENTS AND CONDITIONS
# ========================================


def selected_options(selection: set[ComponentReference]) -> Options:
    """Selected SUB answers of each installable component."""
    provided: Options = defaultdict(set)
    for choice in selection:
        if choice.is_sub_option():
            provided[choice.get_base_component_reference()].add(choice)
    return provided


def ordering_requirements(
    rule_manager: RuleManager,
    reference: ComponentReference,
    selection: set[ComponentReference],
    options: set[ComponentReference],
) -> set[DependencyRule]:
    """Dependencies of a component or of its selected SUB answers, active in the selection."""
    return {
        rule
        for source in {reference} | options
        for rule in _requirements(rule_manager, source)
        if sources_satisfied(rule, source, selection)
    }


def _requirements(
    rule_manager: RuleManager, reference: ComponentReference
) -> tuple[DependencyRule, ...]:
    by_source = rule_manager._dependencies_by_source
    wildcard = ComponentReference.for_mod(reference.mod_id)
    return tuple(dict.fromkeys(by_source.get(reference, []) + by_source.get(wildcard, [])))


def _resolved(
    rule_manager: RuleManager, rule: Rule
) -> tuple[frozenset[ComponentReference], frozenset[ComponentReference]]:
    """Sources and targets of a rule, mod wildcards replaced by the catalogue components."""
    return rule_manager._resolved_wildcards_cache[id(rule)]


def _targets_satisfied(rule: DependencyRule, available: set[ComponentReference]) -> bool:
    return all(group_matches(group, available) for group in target_groups(rule))


def _order_groups(
    rule: DependencyRule, selection: set[ComponentReference]
) -> tuple[ComponentGroup, ...]:
    """Targets that must precede: an optional dependency keeps only its selected targets."""
    groups = target_groups(rule)
    if not rule.optional:
        return groups
    return tuple(
        ComponentGroup(selected, group.operator)
        for group in groups
        if (selected := tuple(c for c in group.components if is_selected_in(c, selection)))
    )


def _order_satisfied(
    rule: DependencyRule, selection: set[ComponentReference], preceding: set[ComponentReference]
) -> bool:
    return all(group_matches(group, preceding) for group in _order_groups(rule, selection))


def _order_missing(
    rule: DependencyRule, selection: set[ComponentReference], preceding: set[ComponentReference]
) -> list[ComponentReference]:
    return [
        comp
        for group in _order_groups(rule, selection)
        if not group_matches(group, preceding)
        for comp in group.components
        if not is_selected_in(comp, preceding)
    ]
