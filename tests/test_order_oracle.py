"""Order scheduling, order validation and resolution against the former implementation (plan 5.1).

Lot 5 rewrites these algorithms for speed: they must keep the same order, the same
unscheduled components and cycle, the same order violations (compared as a multiset,
their order follows the iteration of a set of rules) and the same resolution proposal.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from core.ComponentReference import ComponentReference, IndexManager, ReferenceIndexes
from core.models.PauseEntry import PauseEntry
from core.ModManager import ModManager
from core.RuleManager import RuleManager
from core.Rules import DependencyRule, IncompatibilityRule, Rule, RuleViolation
from core.ValidationOrchestrator import ValidationOrchestrator
from tests.conftest import ModCatalogue, ShippedCatalogue
from tests.oracles import order_v0
from tests.oracles.graphs import GRAPH_GAME, OrderGraph, generate_graphs
from ui.pages.mod_selection.SelectionController import SelectionController
from util.bench_core import (
    RESOLVE_REFERENCE_COUNT,
    RESOLVE_SEED,
    RESOLVE_SELECTION_SIZE,
    SequenceSelection,
)

GRAPH_COUNT = 501
# References in violation whose resolution is compared, per selection
RESOLVED_REFERENCES = 2
# The search has no limit: this graph explores enough selections to take about forty
# seconds with both implementations, too long for the suite
UNBOUNDED_RESOLUTION_SEEDS = frozenset({293})

SHIPPED_GAME = "eet"
SHIPPED_SEEDS = range(4)
SHIPPED_MOD_SHARES = (0.04, 0.08)

type Findings = Counter[tuple[Rule, tuple[ComponentReference, ...]]]


def findings(violations: list[RuleViolation]) -> Findings:
    """Violations as a multiset: equal rules are compared by value, duplicates are kept."""
    return Counter((violation.rule, violation.affected_components) for violation in violations)


# ========================================
# INPUTS
# ========================================


@dataclass(frozen=True)
class OrderCase:
    """Rules and the planned sequences a comparison runs on."""

    name: str
    rules: RuleManager
    sequences: list[list[ComponentReference]]
    seq_idx: int
    selection: set[ComponentReference]
    unavailable: set[tuple[int, int]]
    completed_through: tuple[int, int]

    @property
    def editorial(self) -> list[ComponentReference]:
        return self.sequences[self.seq_idx]

    @property
    def earlier(self) -> set[ComponentReference]:
        return {ref for sequence in self.sequences[: self.seq_idx] for ref in sequence}

    @property
    def later(self) -> set[ComponentReference]:
        return {ref for sequence in self.sequences[self.seq_idx + 1 :] for ref in sequence}


@pytest.fixture(scope="module")
def graphs() -> list[OrderGraph]:
    return generate_graphs(GRAPH_COUNT)


@pytest.fixture
def graph_cases(graphs: list[OrderGraph], tmp_path: Path) -> list[tuple[OrderGraph, OrderCase]]:
    """Each generated graph with its rules indexed against its own catalogue."""
    return [(graph, graph_case(graph, tmp_path / str(graph.seed))) for graph in graphs]


def graph_case(graph: OrderGraph, work_dir: Path) -> OrderCase:
    rules = RuleManager(
        ModCatalogue(graph.mods()),
        rules_dir=work_dir / "rules",
        cache_dir=work_dir / "cache",
        mods_dir=work_dir / "mods",
        custom_mods_dir=work_dir / "custom",
    )
    rules._all_rules = list(graph.rules)
    rules.rebuild_indexes()
    return OrderCase(
        name=f"graph {graph.seed}",
        rules=rules,
        sequences=[list(sequence) for sequence in graph.sequences],
        seq_idx=graph.seq_idx,
        selection=set(graph.selection),
        unavailable=set(graph.unavailable),
        completed_through=graph.completed_through,
    )


def publish_graph_catalogue(graph: OrderGraph) -> None:
    """Register the graph components as the loaded catalogue, for the selection cascades."""
    catalogue = ReferenceIndexes()
    for data in graph.mod_data:
        ModManager._register_components(data, {}, catalogue)
    IndexManager.get_indexes().replace_catalogue(catalogue)


def shipped_cases(catalogue: ShippedCatalogue) -> list[OrderCase]:
    """Sequences of the shipped game restricted to a sample of its mods.

    The whole selection takes about ten seconds to schedule: samples keep every rule shape
    of the catalogue at a few hundred components.
    """
    game = catalogue.games.get(SHIPPED_GAME)
    assert game is not None
    orders = [
        [
            reference
            for reference in ComponentReference.from_string_list(list(sequence.order))
            if not PauseEntry.is_pause(str(reference))
        ]
        for sequence in game.sequences
    ]
    mod_ids = sorted({reference.mod_id for order in orders for reference in order})

    cases = []
    for seed in SHIPPED_SEEDS:
        draw = random.Random(seed)
        share = SHIPPED_MOD_SHARES[seed % len(SHIPPED_MOD_SHARES)]
        sampled = set(draw.sample(mod_ids, round(len(mod_ids) * share)))
        sequences = [[ref for ref in order if ref.mod_id in sampled] for order in orders]
        selection = {ref for sequence in sequences for ref in sequence}
        selection |= default_answers(catalogue.rules, selection)
        positions = [
            (seq, pos) for seq, order in enumerate(sequences) for pos in range(len(order))
        ]
        cases.append(
            OrderCase(
                name=f"{SHIPPED_GAME} sample {seed}",
                rules=catalogue.rules,
                sequences=sequences,
                seq_idx=max(range(len(sequences)), key=lambda idx: len(sequences[idx])),
                selection=selection,
                unavailable=set(draw.sample(positions, min(5, len(positions)))),
                completed_through=(-1, -1),
            )
        )
    return cases


def default_answers(
    rules: RuleManager, selection: set[ComponentReference]
) -> set[ComponentReference]:
    """First answer of every question of the selected SUB components."""
    answers = set()
    for reference in selection:
        mod = rules._mod_manager.get_mod_by_id(reference.mod_id)
        component = mod.get_component(reference.comp_key) if mod else None
        for question, prompt in getattr(component, "prompts", {}).items():
            if prompt.options:
                answers.add(
                    ComponentReference.for_component(
                        reference.mod_id, f"{reference.comp_key}.{question}.{prompt.options[0]}"
                    )
                )
    return answers


# ========================================
# COMPARISONS
# ========================================


def assert_same_preferred_order(case: OrderCase) -> None:
    expected = order_v0.preferred_order(
        case.rules, case.editorial, case.earlier, case.selection
    )

    actual = case.rules.preferred_order(case.editorial, case.earlier, case.selection)

    assert actual == expected, case.name


def assert_same_order_validation(case: OrderCase, order: list[ComponentReference]) -> None:
    expected = order_v0.validate_order(
        case.rules, order, case.earlier, case.later, case.selection
    )

    # Without any moved component, a validation is always a full one
    actual = case.rules.validate_order(
        GRAPH_GAME, case.seq_idx, order, None, case.earlier, case.later, case.selection
    )

    assert findings(actual) == findings(expected), case.name


def assert_same_validation_after_a_move(case: OrderCase, draw: random.Random) -> None:
    """The incremental validation of a move finds what a full one would."""
    order = list(case.editorial)
    movable = [ref for ref in order if not PauseEntry.is_pause(str(ref))]
    if not movable:
        return
    case.rules.validate_order(
        GRAPH_GAME, case.seq_idx, order, None, case.earlier, case.later, case.selection
    )
    moved = draw.choice(movable)
    order.remove(moved)
    order.insert(draw.randint(0, len(order)), moved)

    expected = order_v0.validate_order(
        case.rules, order, case.earlier, case.later, case.selection
    )

    actual = case.rules.validate_order(
        GRAPH_GAME, case.seq_idx, order, {moved}, case.earlier, case.later, case.selection
    )

    assert findings(actual) == findings(expected), f"{case.name}, {moved} moved"


def assert_same_blocked_positions(case: OrderCase) -> None:
    arguments = (case.sequences, case.unavailable, case.completed_through, case.selection)

    expected = order_v0.blocked_positions(case.rules, *arguments)

    assert case.rules.blocked_positions(*arguments) == expected, case.name


def assert_same_resolutions(
    case: OrderCase, controller: SelectionController, reference_count: int
) -> int:
    """Compare the proposals for the first sources in violation; return how many were found."""
    indexes = IndexManager.get_indexes()
    indexes.selection_index.clear()
    indexes.selection_index.update(case.selection)
    orchestrator = ValidationOrchestrator(case.rules, controller)
    references = list(
        dict.fromkeys(
            violation.affected_components[0]
            for violation in case.rules.selection_violations(case.selection)
        )
    )[:reference_count]

    for reference in references:
        expected = order_v0.auto_resolve(case.rules, controller, reference)

        actual = orchestrator.auto_resolve(reference)

        assert actual == expected, f"{case.name}, resolution of {reference}"
        assert indexes.selection_index == case.selection, "a resolution changed the selection"
    return len(references)


# ========================================
# GENERATED GRAPHS
# ========================================


def test_generated_graphs_cover_cycles_and_unscheduled_components(
    graph_cases: list[tuple[OrderGraph, OrderCase]],
) -> None:
    suggestions = [
        order_v0.preferred_order(case.rules, case.editorial, case.earlier, case.selection)
        for _, case in graph_cases
    ]
    unscheduled = [
        suggestion
        for suggestion, (_, case) in zip(suggestions, graph_cases, strict=True)
        if suggestion.components != list(dict.fromkeys(case.editorial))
    ]

    # The comparisons below are only meaningful if the graphs reach every outcome
    assert sum(bool(suggestion.cycle) for suggestion in suggestions) >= 20
    assert len(unscheduled) >= 100


def test_generated_preferred_orders_match_the_former_scheduling(
    graph_cases: list[tuple[OrderGraph, OrderCase]],
) -> None:
    for _, case in graph_cases:
        assert_same_preferred_order(case)


def test_generated_order_validations_match_the_former_validation(
    graph_cases: list[tuple[OrderGraph, OrderCase]],
) -> None:
    for _, case in graph_cases:
        preferred = order_v0.preferred_order(
            case.rules, case.editorial, case.earlier, case.selection
        )
        assert_same_order_validation(case, case.editorial)
        assert_same_order_validation(case, preferred.components)


def test_generated_moves_are_validated_as_a_full_validation_would(
    graph_cases: list[tuple[OrderGraph, OrderCase]],
) -> None:
    for graph, case in graph_cases:
        assert_same_validation_after_a_move(case, random.Random(graph.seed))


def test_generated_blocked_positions_match_the_former_projection(
    graph_cases: list[tuple[OrderGraph, OrderCase]],
) -> None:
    for _, case in graph_cases:
        assert_same_blocked_positions(case)


def test_generated_resolutions_match_the_former_search(
    graph_cases: list[tuple[OrderGraph, OrderCase]],
) -> None:
    resolved = 0
    for graph, case in graph_cases:
        if graph.seed in UNBOUNDED_RESOLUTION_SEEDS:
            continue
        publish_graph_catalogue(graph)
        controller = SelectionController(case.rules)
        forced = [str(reference) for reference in graph.forced]
        controller._game = SimpleNamespace(
            id=GRAPH_GAME, get_forced_components=lambda forced=forced: forced
        )
        resolved += assert_same_resolutions(case, controller, RESOLVED_REFERENCES)

    assert resolved >= GRAPH_COUNT // 2


# ========================================
# SHIPPED CATALOGUE
# ========================================


def test_shipped_samples_match_the_former_order_and_validation(
    shipped_catalogue: ShippedCatalogue,
) -> None:
    for case in shipped_cases(shipped_catalogue):
        assert_same_preferred_order(case)
        assert_same_order_validation(case, case.editorial)
        assert_same_validation_after_a_move(case, random.Random(case.name))
        assert_same_blocked_positions(case)


def test_shipped_resolutions_of_the_benchmark_match_the_former_search(
    shipped_catalogue: ShippedCatalogue,
) -> None:
    # Whole mods give searches without end: the measured sample keeps them bounded
    game = shipped_catalogue.games.get(SHIPPED_GAME)
    assert game is not None
    candidates = sorted(SequenceSelection.largest(game).selection, key=str)
    sampled = set(random.Random(RESOLVE_SEED).sample(candidates, RESOLVE_SELECTION_SIZE))
    case = OrderCase(
        f"{SHIPPED_GAME} benchmark", shipped_catalogue.rules, [], 0, sampled, set(), (-1, -1)
    )
    shipped_catalogue.publish_references()
    controller = SelectionController(case.rules)
    controller._game = game

    assert assert_same_resolutions(case, controller, RESOLVE_REFERENCE_COUNT) == (
        RESOLVE_REFERENCE_COUNT
    )


# ========================================
# REGRESSION CASES (B05, B06, B21)
# ========================================


def refs(*values: str) -> list[ComponentReference]:
    return [ComponentReference.from_string(value) for value in values]


def use_rules(rules: RuleManager, *definitions: Rule) -> None:
    rules._all_rules = list(definitions)
    rules.rebuild_indexes()


def test_b05_blocked_positions_match_the_former_projection(rules: RuleManager) -> None:
    a, b, c, d = refs("a:0", "b:0", "demo:1", "demo:2")
    use_rules(
        rules,
        DependencyRule.from_dict({"source": "b:0", "target": ["a:*", "demo:2"]}),
        DependencyRule.from_dict({"source": "demo:1", "target": "b:0"}),
    )
    selection = {a, b, c, d}

    for sequences, completed_through in (
        ([[a], [b, c, d]], (0, 0)),
        ([[a, d], [b, c]], (0, 1)),
        ([[a], [a, b, c]], (0, 0)),
    ):
        case = OrderCase("B05", rules, sequences, 0, selection, {(0, 0)}, completed_through)
        assert_same_blocked_positions(case)


def test_b06_cycle_and_providers_match_the_former_order(rules: RuleManager) -> None:
    a, b, c = refs("a:0", "b:0", "demo:1")
    use_rules(
        rules,
        *(
            DependencyRule.from_dict({"source": str(source), "target": str(target)})
            for source, target in [(a, b), (b, a)]
        ),
    )
    cycle = OrderCase("B06 cycle", rules, [[a, b]], 0, {a, b}, set(), (-1, -1))
    assert_same_preferred_order(cycle)
    assert_same_order_validation(cycle, cycle.editorial)

    use_rules(rules, DependencyRule.from_dict({"source": "a:0", "target": ["b:*", "demo:1"]}))
    for sequences, seq_idx in (([[a, c, b]], 0), ([[c], [a, b]], 1), ([[a, b]], 0)):
        selection = {ref for sequence in sequences for ref in sequence}
        case = OrderCase("B06 providers", rules, sequences, seq_idx, selection, set(), (-1, -1))
        assert_same_preferred_order(case)
        assert_same_order_validation(case, case.editorial)


def test_b21_resolution_matches_the_former_search(
    rules: RuleManager, catalogue: ModCatalogue
) -> None:
    a, b, c, d = refs("a:0", "b:0", "demo:1", "demo:2")
    indexes = IndexManager.get_indexes()
    for ref in (a, b, c, d):
        indexes.register_component(
            catalogue.get_mod_by_id(ref.mod_id).get_component(ref.comp_key)
        )
    use_rules(
        rules,
        DependencyRule.from_dict({"source": str(a), "target": [str(b), str(c)]}),
        IncompatibilityRule.from_dict({"source": str(b), "target": str(d)}),
    )
    case = OrderCase("B21", rules, [[a, d]], 0, {a, d}, set(), (-1, -1))

    assert assert_same_resolutions(case, SelectionController(rules), 1) == 1
