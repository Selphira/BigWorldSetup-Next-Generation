from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
import logging

from core.ComponentReference import ComponentReference, IndexManager
from core.RuleManager import RuleManager
from core.Rules import Rule, RuleType, RuleViolation
from ui.pages.mod_selection.SelectionController import SelectionController

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolutionProposal:
    reference: ComponentReference
    original: frozenset[ComponentReference]
    selection: frozenset[ComponentReference]


class ValidationOrchestrator:
    """Orchestrates validation and resolution."""

    def __init__(self, rule_manager: RuleManager, selection_controller: SelectionController):
        self._rule_manager = rule_manager
        self._controller = selection_controller
        self._indexes = IndexManager.get_indexes()

        self._validation_enabled = True

        logger.info("ValidationOrchestrator initialized")

    # ========================================
    # Validation
    # ========================================

    def enable_validation(self, enabled: bool) -> None:
        """Enable/disable validation (for imports)."""
        self._validation_enabled = enabled
        logger.debug(f"Validation enabled: {enabled}")

    def validate_current_selection(self) -> list[RuleViolation]:
        """Validate current selection and update violation index."""
        if not self._validation_enabled:
            logger.debug("Validation disabled, skipping")
            return []

        selected_refs = self._controller.get_selected_components()

        logger.debug(f"Validating {len(selected_refs)} components")

        violations = self._rule_manager.validate_selection(selected_refs)

        logger.info(f"Validation complete: {len(violations)} violations found")

        return violations

    def has_errors(self) -> bool:
        """Check if any errors exist."""
        return any(
            violation.is_error
            for violations in self._indexes.selection_violation_index.values()
            for violation in violations
        )

    def has_warnings(self) -> bool:
        """Check if any warnings exist."""
        return any(
            violation.is_warning
            for violations in self._indexes.selection_violation_index.values()
            for violation in violations
        )

    # ========================================
    # Resolution Strategies
    # ========================================

    def _unrelated_violations(
        self,
        selection: frozenset[ComponentReference],
        reference: ComponentReference,
    ) -> set[tuple[Rule, ComponentReference]]:
        return {
            (v.rule, v.affected_components[0])
            for v in self._rule_manager.selection_violations(set(selection))
            if reference not in v.affected_components
        }

    def auto_resolve(self, reference: ComponentReference) -> ResolutionProposal | None:
        """Search actual controller transitions, preferring existing choices."""
        original = self._controller.selection_snapshot()
        if reference not in original:
            return None
        baseline = self._unrelated_violations(original, reference)
        serial = count()
        pending = [(0, 0, next(serial), original)]
        seen = {original}
        protected = (self._controller.forced_selection() & original) | {reference}
        pool = self._indexes.mod_component_index.keys() | self._indexes.parent_index.keys()
        while pending:
            _, _, _, candidate = heappop(pending)
            violations = [
                v
                for v in self._rule_manager.selection_violations(set(candidate))
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
                        ref
                        for ref in pool
                        if ref.is_component() and ref.mod_id == pattern.mod_id
                    )
                elif pattern in pool:
                    options.add(pattern)
            options = options - candidate if adding else options & candidate
            for ref in sorted(options, key=str):
                simulation = self._controller.preview(set(candidate))
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

    def apply_resolution(self, proposal: ResolutionProposal) -> bool:
        """Reject stale proposals and revalidate the published selection."""
        if self._controller.selection_snapshot() != proposal.original:
            return False
        baseline = self._unrelated_violations(proposal.original, proposal.reference)
        if any(
            (v.rule, v.affected_components[0]) not in baseline
            for v in self._rule_manager.selection_violations(set(proposal.selection))
        ):
            return False
        self._controller.apply_selection(set(proposal.selection))
        self.validate_current_selection()
        return True
