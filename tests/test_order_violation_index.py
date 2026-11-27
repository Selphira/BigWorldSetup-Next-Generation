"""Withdrawal of published order findings from the shared index."""

from core.ComponentReference import ComponentReference, ReferenceIndexes
from core.Rules import OrderRule, RuleViolation

A = ComponentReference.from_string("a:0")
B = ComponentReference.from_string("b:0")
C = ComponentReference.from_string("c:0")


def order_violation(source: ComponentReference, target: ComponentReference) -> RuleViolation:
    rule = OrderRule.from_dict({"source": str(source), "target": str(target)})
    return RuleViolation(rule, (source, target))


def test_withdrawn_findings_leave_the_others_of_each_component() -> None:
    indexes = ReferenceIndexes()
    kept, first, second = order_violation(A, B), order_violation(A, C), order_violation(B, C)
    for violation in (kept, first, second):
        indexes.add_order_violation(violation)

    indexes.remove_order_violations([first, second])

    assert indexes.get_order_violations(A) == [kept]
    assert indexes.get_order_violations(B) == [kept]
    assert indexes.get_order_violations(C) == []


def test_an_equal_finding_published_by_another_sequence_stays() -> None:
    indexes = ReferenceIndexes()
    withdrawn, other_sequence = order_violation(A, B), order_violation(A, B)
    indexes.add_order_violation(withdrawn)
    indexes.add_order_violation(other_sequence)

    indexes.remove_order_violations([withdrawn])

    assert [id(entry) for entry in indexes.get_order_violations(A)] == [id(other_sequence)]


def test_a_list_held_by_a_reader_reflects_the_withdrawal() -> None:
    indexes = ReferenceIndexes()
    violation = order_violation(A, B)
    indexes.add_order_violation(violation)
    held = indexes.get_order_violations(A)

    indexes.remove_order_violations([violation])

    assert held == []
