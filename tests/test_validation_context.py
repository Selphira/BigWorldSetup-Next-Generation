"""Cached validations are reused only for the exact selection and context they were computed for."""

import pytest

from core.ComponentReference import ComponentReference
from core.RuleManager import RuleManager
from core.Rules import DependencyRule, OrderRule, RuleViolation


def refs(*values: str) -> list[ComponentReference]:
    return [ComponentReference.from_string(value) for value in values]


def _findings(
    violations: list[RuleViolation],
) -> set[tuple[int, tuple[ComponentReference, ...]]]:
    return {(id(violation.rule), violation.affected_components) for violation in violations}


def _order_rule(source: ComponentReference, target: ComponentReference) -> OrderRule:
    return OrderRule.from_dict(
        {"source": str(source), "target": str(target), "direction": "before"}
    )


def _published_order_violations(rules: RuleManager, *references: ComponentReference) -> set:
    return {
        (id(violation.rule), violation.affected_components)
        for reference in references
        for violation in rules.get_order_violations(reference)
    }


# ========================================
# SELECTION
# ========================================


def test_selections_with_colliding_hashes_are_validated_separately(
    rules: RuleManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ComponentReference, "__hash__", lambda reference: 0)
    a, b = refs("a:0", "b:0")
    rules._all_rules = [DependencyRule.from_dict({"source": str(a), "target": "demo:1"})]
    rules.rebuild_indexes()
    assert hash(frozenset({a})) == hash(frozenset({b}))

    assert rules.validate_selection([a])

    assert not rules.validate_selection([b])
    assert not rules.get_selection_violations(a)


def test_changed_rules_revalidate_an_identical_selection(rules: RuleManager) -> None:
    a, b = refs("a:0", "b:0")
    rules._all_rules = [DependencyRule.from_dict({"source": str(a), "target": "demo:1"})]
    rules.rebuild_indexes()
    assert rules.validate_selection([a, b])

    rules._all_rules = [DependencyRule.from_dict({"source": str(b), "target": "demo:1"})]
    rules.rebuild_indexes()

    (violation,) = rules.validate_selection([a, b])
    assert violation.affected_components[0] == b


# ========================================
# ORDER: INCREMENTAL EQUALS FULL
# ========================================


def test_changed_rules_revalidate_an_unchanged_sequence(rules: RuleManager) -> None:
    a, b, c = refs("a:0", "b:0", "demo:1")
    rules._all_rules = [_order_rule(a, b)]
    rules.rebuild_indexes()
    assert rules.validate_order("eet", 0, [b, a, c])

    rules._all_rules = [_order_rule(b, a)]
    rules.rebuild_indexes()
    incremental = rules.validate_order("eet", 0, [b, a, c], moved_components={c})

    assert _findings(incremental) == _findings(rules.validate_order("eet", 0, [b, a, c]))
    assert not incremental


def test_target_moved_to_an_earlier_sequence_is_reported(rules: RuleManager) -> None:
    a, b, c = refs("a:0", "b:0", "demo:1")
    rules._all_rules = [_order_rule(a, b)]
    rules.rebuild_indexes()
    assert not rules.validate_order("eet", 1, [a, c], later={b})

    incremental = rules.validate_order("eet", 1, [c, a], moved_components={c}, earlier={b})

    full = rules.validate_order("eet", 1, [c, a], earlier={b})
    assert _findings(incremental) == _findings(full)
    assert incremental


def test_changed_sub_answer_withdraws_its_ordering_requirement(rules: RuleManager) -> None:
    a, b, base, first, second = refs("a:0", "b:0", "demo:1", "demo:1.1.A", "demo:1.1.B")
    rules._all_rules = [DependencyRule.from_dict({"source": str(first), "target": str(a)})]
    rules.rebuild_indexes()
    order = [base, a, b]
    assert rules.validate_order("eet", 0, order, selection={base, first, a, b})

    selection = {base, second, a, b}
    incremental = rules.validate_order(
        "eet", 0, order, moved_components={b}, selection=selection
    )

    assert _findings(incremental) == _findings(
        rules.validate_order("eet", 0, order, selection=selection)
    )
    assert not incremental
    assert not _published_order_violations(rules, a, base)


# ========================================
# GAME CHANGE
# ========================================


def test_game_change_withdraws_the_published_order_violations(rules: RuleManager) -> None:
    a, b, c = refs("a:0", "b:0", "demo:1")
    rules._all_rules = [_order_rule(a, b)]
    rules.rebuild_indexes()
    assert rules.validate_order("eet", 0, [b, a])
    assert _published_order_violations(rules, a, b)

    assert not rules.validate_order("bgee", 0, [c])

    assert not _published_order_violations(rules, a, b)
