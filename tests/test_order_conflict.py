"""Order conflicts as seen from each component of a violated rule."""

from collections.abc import Callable

import pytest

from core.ComponentReference import ComponentReference
from core.OrderConflict import group_order_conflicts
from core.Rules import DependencyRule, OrderRule, RuleViolation

A = ComponentReference.from_string("a:0")
B = ComponentReference.from_string("b:0")
C = ComponentReference.from_string("c:0")


def order_violation(
    source: ComponentReference, target: ComponentReference, direction: str
) -> RuleViolation:
    rule = OrderRule.from_dict(
        {"source": str(source), "target": str(target), "direction": direction}
    )
    return RuleViolation(rule, (source, target))


def violations_by_reference(
    *violations: RuleViolation,
) -> Callable[[ComponentReference], list[RuleViolation]]:
    def violations_of(reference: ComponentReference) -> list[RuleViolation]:
        return [v for v in violations if reference in v.affected_components]

    return violations_of


@pytest.mark.parametrize(
    ("direction", "viewed_from", "earlier", "later"),
    [
        ("before", A, [], [B]),
        ("before", B, [A], []),
        ("after", A, [B], []),
        ("after", B, [], [A]),
    ],
)
def test_order_rule_places_other_component_on_required_side(
    direction: str,
    viewed_from: ComponentReference,
    earlier: list[ComponentReference],
    later: list[ComponentReference],
) -> None:
    violations_of = violations_by_reference(order_violation(A, B, direction))

    [conflict] = group_order_conflicts([viewed_from], violations_of, {})

    assert conflict.reference == viewed_from
    assert conflict.earlier == earlier
    assert conflict.later == later


def test_dependency_requires_its_target_to_be_installed_first() -> None:
    rule = DependencyRule.from_dict({"source": str(A), "target": str(B)})
    violations_of = violations_by_reference(RuleViolation(rule, (A, B)))

    [conflict] = group_order_conflicts([A], violations_of, {})

    assert conflict.earlier == [B]
    assert not conflict.later


def test_violations_of_one_rule_merge_into_a_single_conflict() -> None:
    first = order_violation(A, B, "before")
    second = RuleViolation(first.rule, (A, C))
    duplicate = RuleViolation(first.rule, (A, B))

    conflicts = group_order_conflicts([A], lambda _ref: [first, second, duplicate], {})

    assert len(conflicts) == 1
    assert conflicts[0].later == [B, C]


def test_ignored_rule_is_left_out_only_for_its_reference() -> None:
    violation = order_violation(A, B, "before")
    violations_of = violations_by_reference(violation)

    conflicts = group_order_conflicts([A, B], violations_of, {A: {id(violation.rule)}})

    assert [conflict.reference for conflict in conflicts] == [B]
