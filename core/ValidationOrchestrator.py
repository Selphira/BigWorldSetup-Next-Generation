from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
import logging

from core.ComponentReference import ComponentPool, ComponentReference, IndexManager
from core.RuleManager import RuleManager
from core.Rules import Rule, RuleType, RuleViolation
from core.SelectionCascade import SelectionCascade, SelectionConstraints
from core.SelectionSource import SelectionSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolutionProposal:
    reference: ComponentReference
    original: frozenset[ComponentReference]
    selection: frozenset[ComponentReference]


@dataclass(frozen=True)
class ResolutionRequest:
    """Frozen inputs of a resolution search: it reads neither widgets nor the selection source."""

    reference: ComponentReference
    original: frozenset[ComponentReference]
    protected: frozenset[ComponentReference]
    constraints: SelectionConstraints
    pool: ComponentPool


class ResolutionCancelled(Exception):
    """The search was interrupted before finding a proposal or proving there is none."""


class ValidationOrchestrator:
    """Orchestrates validation and resolution."""

    def __init__(self, rule_manager: RuleManager, selection_source: SelectionSource):
        self._rule_manager = rule_manager
        self._selection_source = selection_source
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

        selected_refs = self._selection_source.get_selected_components()

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
        """Search actual selection transitions, preferring existing choices."""
        request = self.prepare_resolution(reference)
        if request is None:
            return None
        return self.search_resolution(request)

    def prepare_resolution(self, reference: ComponentReference) -> ResolutionRequest | None:
        """Freeze what a search needs; to call on the thread that owns the selection."""
        original = self._selection_source.selection_snapshot()
        if reference not in original:
            return None
        return ResolutionRequest(
            reference=reference,
            original=original,
            protected=frozenset(
                (self._selection_source.forced_selection() & original) | {reference}
            ),
            constraints=self._selection_source.selection_constraints(),
            pool=self._indexes.component_pool(),
        )

    def search_resolution(
        self,
        request: ResolutionRequest,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> ResolutionProposal | None:
        """Explore transitions by fewest removals, then fewest additions, then discovery order.

        Can run off the interface thread: it only reads the request, the catalogue
        and the rules. ``None`` means that no transition keeps the reference.

        Raises:
            ResolutionCancelled: ``is_cancelled`` became true before the end.
        """
        original = request.original
        baseline = self._unrelated_violations(original, request.reference)
        serial = count()
        pending = [(0, 0, next(serial), original)]
        seen = {original}
        while pending:
            if is_cancelled():
                raise ResolutionCancelled(request.reference)

            _, _, _, candidate = heappop(pending)
            violations = [
                v
                for v in self._rule_manager.selection_violations(set(candidate))
                if (v.rule, v.affected_components[0]) not in baseline
            ]
            if not violations:
                return ResolutionProposal(request.reference, original, candidate)

            violation = violations[0]
            adding = violation.rule.rule_type == RuleType.DEPENDENCY
            for ref in self._transition_options(violation, candidate, adding, request.pool):
                cascade = SelectionCascade(self._indexes, set(candidate), request.constraints)
                if adding:
                    cascade.select(ref)
                else:
                    cascade.unselect(ref)
                state = frozenset(cascade.selection)
                if not request.protected <= state or state in seen:
                    continue
                seen.add(state)
                heappush(
                    pending, (len(original - state), len(state - original), next(serial), state)
                )
        return None

    @staticmethod
    def _transition_options(
        violation: RuleViolation,
        candidate: frozenset[ComponentReference],
        adding: bool,
        pool: ComponentPool,
    ) -> list[ComponentReference]:
        """References whose selection (dependency) or removal (conflict) may fix ``violation``."""
        patterns = (
            violation.affected_components[1:] if adding else violation.affected_components
        )
        options = set()
        for pattern in patterns:
            if pattern.is_mod():
                options.update(pool.components_of(pattern.mod_id))
            elif pattern in pool:
                options.add(pattern)
        options = options - candidate if adding else options & candidate
        return sorted(options, key=str)

    def apply_resolution(self, proposal: ResolutionProposal) -> bool:
        """Reject stale proposals and revalidate the published selection."""
        if self._selection_source.selection_snapshot() != proposal.original:
            return False
        baseline = self._unrelated_violations(proposal.original, proposal.reference)
        if any(
            (v.rule, v.affected_components[0]) not in baseline
            for v in self._rule_manager.selection_violations(set(proposal.selection))
        ):
            return False
        self._selection_source.apply_selection(set(proposal.selection))
        self.validate_current_selection()
        return True
