from __future__ import annotations

from dataclasses import dataclass
import logging

from core.ComponentReference import ComponentReference, IndexManager
from core.RuleManager import RuleManager
from core.Rules import RuleType, RuleViolation
from ui.pages.mod_selection.SelectionController import SelectionController

logger = logging.getLogger(__name__)


@dataclass
class ResolutionResult:
    """Result of an automatic resolution."""

    selected: list[ComponentReference]
    deselected: list[ComponentReference]
    success: bool
    message: str = ""


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
            for violations in self._indexes.violation_index.values()
            for violation in violations
        )

    def has_warnings(self) -> bool:
        """Check if any warnings exist."""
        return any(
            violation.is_warning
            for violations in self._indexes.violation_index.values()
            for violation in violations
        )

    def get_violations_for_reference(
        self, reference: ComponentReference
    ) -> list[RuleViolation]:
        """Get violations for a component."""
        return self._indexes.get_violations(reference)

    # ========================================
    # Resolution Strategies
    # ========================================

    def resolve_dependencies(self, reference: ComponentReference) -> ResolutionResult:
        """Resolve missing dependencies by selecting them."""
        logger.info(f"Resolving dependencies for {reference}")

        violations = self.get_violations_for_reference(reference)
        dep_violations = [v for v in violations if v.rule.rule_type == RuleType.DEPENDENCY]

        if not dep_violations:
            msg = f"✅ Aucune dépendance manquante pour {reference}"
            logger.info(msg)
            return ResolutionResult([], [], False, msg)

        # Collect missing dependencies
        to_select: set[ComponentReference] = set()

        for violation in dep_violations:
            for target in violation.rule.targets:
                if not self._controller.is_selected(target):
                    to_select.add(target)

        if not to_select:
            msg = f"✅ Toutes les dépendances de {reference} sont déjà satisfaites"
            logger.info(msg)
            return ResolutionResult([], [], False, msg)

        added = []
        failed = []

        for target in to_select:
            if self._controller.select(target, emit_signal=False):
                added.append(target)
            else:
                failed.append(target)

        if added:
            self._controller.validation_needed.emit()

        message = self._format_dependency_message(reference, added, failed)
        success = len(added) > 0

        logger.info(f"Dependencies resolved: {len(added)} added, {len(failed)} failed")
        return ResolutionResult(added, [], success, message)

    def resolve_conflicts_keep(self, reference: ComponentReference) -> ResolutionResult:
        """Resolve conflicts by KEEPING this component (remove conflicts)."""
        logger.info(f"Resolving conflicts - keeping {reference}")

        violations = self.get_violations_for_reference(reference)
        incompat_violations = [
            v for v in violations if v.rule.rule_type == RuleType.INCOMPATIBILITY
        ]

        if not incompat_violations:
            msg = f"✅ Aucun conflit pour {reference}"
            logger.info(msg)
            return ResolutionResult([], [], False, msg)

        # Collect conflicting components to remove
        to_deselect: set[ComponentReference] = set()

        for violation in incompat_violations:
            for target in violation.rule.targets:
                if target != reference and self._controller.is_selected(target):
                    to_deselect.add(target)

        if not to_deselect:
            msg = f"✅ Tous les conflits de {reference} sont déjà résolus"
            logger.info(msg)
            return ResolutionResult([], [], False, msg)

        removed = []
        failed = []

        for conflict in to_deselect:
            if self._controller.unselect(conflict, emit_signal=False):
                removed.append(conflict)
            else:
                failed.append(conflict)

        if removed:
            self._controller.validation_needed.emit()

        message = self._format_conflict_keep_message(reference, removed, failed)
        success = len(removed) > 0

        logger.info(f"Conflicts resolved (keep): {len(removed)} removed")
        return ResolutionResult([], removed, success, message)

    def resolve_conflicts_remove(self, reference: ComponentReference) -> ResolutionResult:
        """Resolve conflicts by REMOVING this component."""
        logger.info(f"Resolving conflicts - removing {reference}")

        if not self._controller.is_selected(reference):
            msg = f"ℹ️ {reference} n'est pas sélectionné"
            return ResolutionResult([], [], False, msg)

        if self._controller.unselect(reference):
            msg = f"❌ {reference} a été retiré\n✅ Les composants en conflit ont été conservés"
            logger.info(f"Conflict resolved (remove): {reference} removed")
            return ResolutionResult([], [reference], True, msg)
        else:
            msg = f"❌ Impossible de retirer {reference}"
            logger.error(msg)
            return ResolutionResult([], [], False, msg)

    def auto_resolve(self, reference: ComponentReference) -> ResolutionResult:
        """Smart auto-resolution: dependencies first, then conflicts.

        Strategy:
        1. Resolve dependencies (add missing)
        2. Resolve conflicts (keep this component)
        """
        logger.info(f"Auto-resolving {reference}")

        violations = self.get_violations_for_reference(reference)

        if not violations:
            return ResolutionResult([], [], False, "✅ Aucun problème")

        all_added = []
        all_removed = []

        # 1. Resolve dependencies first
        dep_result = self.resolve_dependencies(reference)
        if dep_result.success:
            all_added.extend(dep_result.selected)

        # 2. Resolve conflicts (keep this component)
        conflict_result = self.resolve_conflicts_keep(reference)
        if conflict_result.success:
            all_removed.extend(conflict_result.deselected)

        if not all_added and not all_removed:
            return ResolutionResult([], [], False, "ℹ️ No action needed")

        message = self._format_auto_resolve_message(reference, all_added, all_removed)
        logger.info(f"Auto-resolve complete: +{len(all_added)} -{len(all_removed)}")
        return ResolutionResult(all_added, all_removed, True, message)

    # ========================================
    # Message Formatting
    # ========================================

    def _format_dependency_message(
        self,
        source: ComponentReference,
        added: list[ComponentReference],
        failed: list[ComponentReference],
    ) -> str:
        """Format dependency resolution message."""
        parts = [f"📦 Dependencies for {source}"]

        if added:
            parts.append(f"\n✅ Added ({len(added)}):")
            for ref in added:
                parts.append(f"  • {ref}")

        if failed:
            parts.append(f"\n❌ Failed ({len(failed)}):")
            for ref in failed:
                parts.append(f"  • {ref}")

        return "\n".join(parts)

    def _format_conflict_keep_message(
        self,
        source: ComponentReference,
        removed: list[ComponentReference],
        failed: list[ComponentReference],
    ) -> str:
        """Format conflict resolution message (keep)."""
        parts = [f"✅ Keep {source} - Resolve conflicts"]

        if removed:
            parts.append(f"\n❌ Removed ({len(removed)}):")
            for ref in removed:
                parts.append(f"  • {ref}")

        if failed:
            parts.append(f"\n⚠️ Failed ({len(failed)}):")
            for ref in failed:
                parts.append(f"  • {ref}")

        return "\n".join(parts)

    def _format_auto_resolve_message(
        self,
        source: ComponentReference,
        added: list[ComponentReference],
        removed: list[ComponentReference],
    ) -> str:
        """Format auto-resolution message."""
        parts = [f"🔧 Résolution automatique pour {source}"]

        if added:
            parts.append(f"\n➕ Dépendances ajoutées ({len(added)}):")
            for ref in added:
                parts.append(f"  • {ref}")

        if removed:
            parts.append(f"\n❌ Conflits retirés ({len(removed)}):")
            for ref in removed:
                parts.append(f"  • {ref}")

        return "\n".join(parts)
