"""Seeded generator of small catalogues, rules and sequences for the order oracle (plan 5.1).

A graph is a set of mods with standard, SUB and MUC components, rules mixing every
shape the catalogue uses (ALL/ANY, groups, optional dependencies, explicit and implicit
order, mod wildcards, absent targets), and the selection split into sequences. Rules are
drawn at random, so dependency and order cycles occur on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any

from core.ComponentReference import ComponentReference
from core.Mod import Mod
from core.models.PauseEntry import PAUSE_PREFIX
from core.Rules import DependencyRule, IncompatibilityRule, OrderRule, Rule

GRAPH_SIZES = (5, 20, 80)
GRAPH_GAME = "eet"
ABSENT_MOD = "ghost"

# Shares of the installable components and of the rules, by kind
SUB_SHARE = 0.2
MUC_SHARE = 0.1
SELECTED_SHARE = 0.85
RULES_PER_COMPONENT = 1.2
DEPENDENCY_SHARE = 0.55
INCOMPATIBILITY_SHARE = 0.15


@dataclass(frozen=True)
class OrderGraph:
    """Catalogue, rules and planned sequences of one generated case."""

    seed: int
    mod_data: tuple[dict[str, Any], ...]
    rules: tuple[Rule, ...]
    sequences: tuple[tuple[ComponentReference, ...], ...]
    # Index of the sequence that is scheduled and validated; the others precede or follow it
    seq_idx: int
    selection: frozenset[ComponentReference]
    forced: frozenset[ComponentReference]
    # Installation outcome replayed by blocked_positions
    unavailable: frozenset[tuple[int, int]]
    completed_through: tuple[int, int]

    @property
    def editorial(self) -> list[ComponentReference]:
        return list(self.sequences[self.seq_idx])

    @property
    def earlier(self) -> set[ComponentReference]:
        return {ref for sequence in self.sequences[: self.seq_idx] for ref in sequence}

    @property
    def later(self) -> set[ComponentReference]:
        return {ref for sequence in self.sequences[self.seq_idx + 1 :] for ref in sequence}

    def mods(self) -> list[Mod]:
        return [Mod(data) for data in self.mod_data]


def generate_graphs(count: int, first_seed: int = 0) -> list[OrderGraph]:
    """Graphs of every size in turn, each one reproducible from its seed."""
    return [
        GraphGenerator(seed, GRAPH_SIZES[seed % len(GRAPH_SIZES)]).generate()
        for seed in range(first_seed, first_seed + count)
    ]


class GraphGenerator:
    """Draws one graph of ``size`` installable components from a seed."""

    def __init__(self, seed: int, size: int) -> None:
        self.seed = seed
        self.size = size
        self.random = random.Random(seed)

        self.mod_data: list[dict[str, Any]] = []
        self.installable: list[ComponentReference] = []
        # SUB component -> question -> answers, and MUC choice -> options
        self.answers: dict[
            ComponentReference, dict[ComponentReference, list[ComponentReference]]
        ] = {}
        self.muc_options: dict[ComponentReference, list[ComponentReference]] = {}
        self.selection: set[ComponentReference] = set()

    def generate(self) -> OrderGraph:
        self._draw_catalogue()
        self._draw_selection()
        rules = [self._draw_rule() for _ in range(round(self.size * RULES_PER_COMPONENT))]
        sequences = self._draw_sequences()
        positions = [
            (seq, pos) for seq, order in enumerate(sequences) for pos in range(len(order))
        ]

        return OrderGraph(
            seed=self.seed,
            mod_data=tuple(self.mod_data),
            rules=tuple(rules),
            sequences=tuple(tuple(order) for order in sequences),
            seq_idx=self.random.randrange(len(sequences)),
            selection=frozenset(self.selection),
            forced=frozenset(self._sample(self._planned(), self.random.randint(0, 2))),
            unavailable=frozenset(self._sample(positions, self.random.randint(0, 3))),
            completed_through=self.random.choice([(-1, -1), *positions[: len(positions) // 2]]),
        )

    # ========================================
    # CATALOGUE AND SELECTION
    # ========================================

    def _draw_catalogue(self) -> None:
        mod_count = max(2, self.size // 4)
        components: list[dict[str, Any]] = [{} for _ in range(mod_count)]
        drawn = 0
        while drawn < self.size:
            mod_idx = self.random.randrange(mod_count)
            drawn += self._draw_component(f"m{mod_idx}", components[mod_idx])

        for mod_idx, mod_components in enumerate(components):
            self.mod_data.append(
                {
                    "id": f"m{mod_idx}",
                    "name": f"m{mod_idx}",
                    "links": {},
                    "tp2": f"m{mod_idx}",
                    "games": [GRAPH_GAME],
                    "components": mod_components,
                }
            )

    def _draw_component(self, mod_id: str, components: dict[str, Any]) -> int:
        """Add one component to a mod; return how many installable components it brings."""
        key = str(len(components) * 10)
        kind = self.random.random()

        if kind < MUC_SHARE:
            options = [f"{key}{suffix}" for suffix in (1, 2)]
            components[f"choice_{key}"] = {"type": "muc", "components": options}
            refs = [ComponentReference.for_component(mod_id, option) for option in options]
            self.muc_options[ComponentReference.for_component(mod_id, f"choice_{key}")] = refs
            self.installable.extend(refs)
            return len(refs)

        reference = ComponentReference.for_component(mod_id, key)
        if kind < MUC_SHARE + SUB_SHARE:
            prompts = {
                str(question): {"options": ["a", "b"], "default": "a"}
                for question in range(1, self.random.randint(1, 2) + 1)
            }
            components[key] = {"type": "sub", "prompts": prompts}
            self.answers[reference] = {
                ComponentReference.for_component(mod_id, f"{key}.{question}"): [
                    ComponentReference.for_component(mod_id, f"{key}.{question}.{answer}")
                    for answer in prompt["options"]
                ]
                for question, prompt in prompts.items()
            }
        else:
            components[key] = {"type": "std"}
        self.installable.append(reference)
        return 1

    def _draw_selection(self) -> None:
        """Select components as the selection page does: parents and one answer per question."""
        exclusive = {option for options in self.muc_options.values() for option in options}
        for reference in self.installable:
            if reference not in exclusive and self.random.random() < SELECTED_SHARE:
                self._select(reference)
        for choice, options in self.muc_options.items():
            if self.random.random() < SELECTED_SHARE:
                self._select(self.random.choice(options))
                self.selection.add(choice)

    def _select(self, reference: ComponentReference) -> None:
        self.selection.update((reference, ComponentReference.for_mod(reference.mod_id)))
        for question, answers in self.answers.get(reference, {}).items():
            self.selection.update((question, self.random.choice(answers)))

    def _planned(self) -> list[ComponentReference]:
        return [ref for ref in self.installable if ref in self.selection]

    # ========================================
    # RULES
    # ========================================

    def _draw_rule(self) -> Rule:
        kind = self.random.random()
        if kind < DEPENDENCY_SHARE:
            return self._draw_dependency()
        if kind < DEPENDENCY_SHARE + INCOMPATIBILITY_SHARE:
            return IncompatibilityRule.from_dict(self._draw_sides())
        return OrderRule.from_dict(
            {
                "source": self._refs(self.random.randint(1, 2)),
                "target": self._refs(self.random.randint(1, 3)),
                "direction": self.random.choice(["before", "after"]),
            }
        )

    def _draw_dependency(self) -> DependencyRule:
        data = self._draw_sides()
        data["mode"] = self.random.choice(["any", "all"])
        data["severity"] = self.random.choice(["error", "error", "warning"])
        data["optional"] = self.random.random() < 0.2
        data["implicit_order"] = self.random.random() < 0.85
        return DependencyRule.from_dict(data)

    def _draw_sides(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.random.random() < 0.15:
            data["source_groups"] = self._groups()
        else:
            data["source"] = self._refs(self.random.choice([1, 1, 1, 2]))
        if self.random.random() < 0.2:
            data["target_groups"] = self._groups()
        else:
            data["target"] = self._refs(self.random.randint(1, 3))
        return data

    def _groups(self) -> list[dict[str, Any]]:
        return [
            {
                "components": self._refs(self.random.randint(1, 3)),
                "operator": self.random.choice(["any", "all"]),
            }
            for _ in range(self.random.randint(1, 2))
        ]

    def _refs(self, count: int) -> list[str]:
        return list(dict.fromkeys(str(self._ref()) for _ in range(count)))

    def _ref(self) -> ComponentReference:
        """A component, a SUB answer, a mod wildcard or an absent reference."""
        draw = self.random.random()
        if draw < 0.12:
            return ComponentReference.for_mod(self.random.choice(self.installable).mod_id)
        if draw < 0.22 and self.answers:
            questions = self.random.choice(list(self.answers.values()))
            return self.random.choice(self.random.choice(list(questions.values())))
        if draw < 0.25:
            return self.random.choice(
                [ComponentReference.for_mod(ABSENT_MOD), ComponentReference(ABSENT_MOD, "0")]
            )
        return self.random.choice(self.installable)

    # ========================================
    # SEQUENCES
    # ========================================

    def _draw_sequences(self) -> list[list[ComponentReference]]:
        """Split the planned components into 1 to 3 sequences, in a shuffled editorial order."""
        planned = self._planned()
        self.random.shuffle(planned)
        cuts = sorted(self._sample(range(1, len(planned)), self.random.randint(0, 2)))
        bounds = [0, *cuts, len(planned)]
        sequences = [
            planned[start:end]
            for start, end in zip(bounds, bounds[1:], strict=False)
            if end > start
        ]
        sequences = sequences or [[]]

        # Editorial orders may hold a pause, and a component listed twice
        target = self.random.choice(sequences)
        if target and self.random.random() < 0.1:
            target.insert(self.random.randrange(len(target)), self.random.choice(target))
        if self.random.random() < 0.1:
            target.insert(0, ComponentReference(PAUSE_PREFIX, "1"))
        return sequences

    def _sample[T](self, population: list[T] | range, size: int) -> list[T]:
        return self.random.sample(list(population), min(size, len(population)))
