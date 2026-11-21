"""Order violations grouped per rule from the point of view of one component."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from core.ComponentReference import ComponentReference
from core.Rules import OrderDirection, OrderRule, RuleViolation


@dataclass(slots=True)
class OrderConflict:
    """Components a reference must follow or precede because of one rule.

    Attributes:
        violation: First violation met for the rule, used for severity and messages
        reference: Component from which the conflict is viewed
        earlier: Components that must be installed before ``reference``
        later: Components that must be installed after ``reference``
    """

    violation: RuleViolation
    reference: ComponentReference
    earlier: list[ComponentReference] = field(default_factory=list)
    later: list[ComponentReference] = field(default_factory=list)

    def add(self, violation: RuleViolation) -> None:
        """Place the other component of ``violation`` on the required side."""
        source, target = violation.affected_components[:2]
        is_source = self.reference == source
        other = target if is_source else source

        # Dependency rules imply that the source is installed after its target.
        direction = (
            violation.rule.order_direction
            if isinstance(violation.rule, OrderRule)
            else OrderDirection.AFTER
        )
        source_comes_first = direction == OrderDirection.BEFORE
        side = self.later if is_source == source_comes_first else self.earlier

        if other not in side:
            side.append(other)


def group_order_conflicts(
    references: Iterable[ComponentReference],
    violations_of: Callable[[ComponentReference], Iterable[RuleViolation]],
    ignored: Mapping[ComponentReference, set[int]],
) -> list[OrderConflict]:
    """Group the order violations of each reference by rule.

    ``ignored`` maps a reference to the ``id`` of the rules the user chose to
    ignore for it. Rules are compared by identity, as the ignored ids are.
    """
    conflicts: dict[tuple[int, int], OrderConflict] = {}

    for reference in references:
        ignored_ids = ignored.get(reference, set())
        for violation in violations_of(reference):
            rule_id = id(violation.rule)
            if rule_id in ignored_ids:
                continue

            key = (rule_id, id(reference))
            conflict = conflicts.setdefault(key, OrderConflict(violation, reference))
            conflict.add(violation)

    return list(conflicts.values())
