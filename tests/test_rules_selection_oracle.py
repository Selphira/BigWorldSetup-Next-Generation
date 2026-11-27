"""Selection validation against the former implementation (plan 4.1).

The shared selection context must not change a single violation, nor their order.
"""

from collections.abc import Iterator
import random

import pytest

from core.ComponentReference import ComponentReference
from core.RuleManager import RuleManager
from core.Rules import IncompatibilityRule, RuleViolation
from core.SelectionContext import SelectionContext
from tests.oracles import rules_v0

RANDOM_SELECTION_SEEDS = range(20)
RANDOM_SELECTION_SIZES = (50, 200, 600, 1500, 3000)


@pytest.fixture
def catalogue_references(shipped_rules: RuleManager) -> list[ComponentReference]:
    """Every component, MUC option, SUB and SUB answer of the catalogue, in a stable order."""
    mods = shipped_rules._mod_manager.get_all_mods().values()
    references = {
        reference
        for mod in mods
        for reference in ComponentReference.from_string_list(list(mod.get_component_refs()))
    }
    return sorted(references, key=str)


def random_selections(
    references: list[ComponentReference],
) -> Iterator[set[ComponentReference]]:
    for seed in RANDOM_SELECTION_SEEDS:
        size = RANDOM_SELECTION_SIZES[seed % len(RANDOM_SELECTION_SIZES)]
        yield set(random.Random(seed).sample(references, min(size, len(references))))


def comparable(
    violations: list[RuleViolation],
) -> list[tuple[int, tuple[ComponentReference, ...]]]:
    """Identity of the rule and affected components: equal but distinct rules stay apart."""
    return [(id(violation.rule), violation.affected_components) for violation in violations]


def test_whole_catalogue_selection_matches_the_former_validation(
    shipped_rules: RuleManager, catalogue_references: list[ComponentReference]
) -> None:
    selection = set(catalogue_references)

    expected = rules_v0.selection_violations(shipped_rules, selection)

    assert expected, (
        "the shipped catalogue should produce violations when everything is selected"
    )
    assert comparable(shipped_rules.selection_violations(selection)) == comparable(expected)


def test_random_selections_match_the_former_validation(
    shipped_rules: RuleManager, catalogue_references: list[ComponentReference]
) -> None:
    for selection in random_selections(catalogue_references):
        expected = rules_v0.selection_violations(shipped_rules, selection)

        assert comparable(shipped_rules.selection_violations(selection)) == comparable(expected)


def test_each_selected_source_reports_its_own_violation(rules: RuleManager) -> None:
    a, b, answer = (ComponentReference.from_string(value) for value in ("a:0", "b:0", "demo:1"))
    demo = ComponentReference.for_mod("demo")
    rule = IncompatibilityRule.from_dict({"source": ["a", "b"], "target": "demo"})
    rules._all_rules = [rule]
    rules.rebuild_indexes()

    violations = rules.selection_violations({a, b, answer})

    # The target condition is shared by both sources, each one still gets its violation
    assert [violation.affected_components for violation in violations] == [(a, demo), (b, demo)]


def test_mod_wildcard_is_satisfied_by_a_selected_sub_answer() -> None:
    answer = ComponentReference.from_string("demo:1.1.2")
    wildcard = ComponentReference.for_mod("demo")
    other_mod = ComponentReference.for_mod("a")
    context = SelectionContext.of({answer})

    assert wildcard.is_selected_in(context) == wildcard.is_selected_in({answer}) is True
    assert other_mod.is_selected_in(context) == other_mod.is_selected_in({answer}) is False
