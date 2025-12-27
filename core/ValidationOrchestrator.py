from __future__ import annotations

from dataclasses import dataclass
import logging

from core.ComponentReference import ComponentReference, IndexManager
from core.RuleManager import RuleManager
from core.Rules import RuleType, RuleViolation
from ui.pages.mod_selection.ComponentSelector import SelectionStateManager

logger = logging.getLogger(__name__)


@dataclass
class ResolutionResult:
    """Result of an automatic resolution."""

    selected: list[ComponentReference]
    deselected: list[ComponentReference]
    success: bool
    message: str = ""


class ValidationOrchestrator:
    """Orchestrates validation and resolution for ComponentSelector."""

    def __init__(self, rule_manager: RuleManager):
        self._rule_manager = rule_manager
        self._selection_manager: SelectionStateManager | None = None
        self._indexes = IndexManager.get_indexes()

        self._validation_enabled = True

        logger.info("ValidationOrchestrator initialized")

    def set_selection_manager(self, manager: SelectionStateManager) -> None:
        """Inject the selection manager."""
        self._selection_manager = manager

    def get_selection_manager(self) -> SelectionStateManager:
        """Get the selection manager."""
        return self._selection_manager

    def enable_validation(self, enabled: bool) -> None:
        """Enable/disable validation (for imports)."""
        self._validation_enabled = enabled
        logger.debug(f"Validation enabled: {enabled}")

    def validate_current_selection(self) -> list[RuleViolation]:
        """Validate current selection and update indexes."""
        if not self._validation_enabled:
            logger.debug("Validation disabled, skipping")
            return []

        if not self._selection_manager:
            logger.warning("No selection manager, cannot validate")
            return []

        selected_items = self._selection_manager.get_selected_items()

        # Convert to ComponentReference, only validate true components
        selected_refs = []
        for item in selected_items:
            try:
                reference = ComponentReference.from_string(item.reference)
                if reference.is_component():
                    selected_refs.append(reference)
            except ValueError as e:
                logger.warning(f"Invalid reference {item.reference}: {e}")

        logger.debug(f"Validating {len(selected_refs)} components")

        violations = self._rule_manager.validate_selection(selected_refs)

        logger.info(f"Validation complete: {len(violations)} violations found")

        return violations

    def get_violations_for_reference(
        self, reference: ComponentReference
    ) -> list[RuleViolation]:
        """Get violations for a component."""
        violations = self._indexes.get_violations(reference)
        logger.debug(f"Found {len(violations)} violations for {reference}")
        return violations

    def has_violations(self, reference: ComponentReference) -> bool:
        """Check if a component has violations."""
        return self._indexes.has_violations(reference)

    def has_errors(self) -> bool:
        return any(
            violation.is_error
            for violations in self._indexes.violation_index.values()
            for violation in violations
        )

    def has_warnings(self) -> bool:
        return any(
            violation.is_warning
            for violations in self._indexes.violation_index.values()
            for violation in violations
        )

    def get_references_with_violations(self) -> set[ComponentReference]:
        """Return all components with violations."""
        return set(self._indexes.violation_index.keys())

    def resolve_dependencies(self, reference: ComponentReference) -> ResolutionResult:
        """Automatically resolve dependencies for a component.

        Strategy:
        1. Get DEPENDENCY violations
        2. Extract all missing targets (dependencies)
        3. Select these targets
        """
        logger.info(f"Resolving dependencies for {reference}")

        violations = self.get_violations_for_reference(reference)
        dep_violations = [v for v in violations if v.rule.rule_type == RuleType.DEPENDENCY]

        if not dep_violations:
            msg = f"✅ Aucune dépendance manquante pour {reference}"
            logger.info(msg)
            return ResolutionResult([], [], False, msg)

        # Collect all missing dependencies
        to_select: set[ComponentReference] = set()

        for violation in dep_violations:
            for affected_ref in violation.affected_components:
                # Skip source component
                if affected_ref == reference:
                    continue
                if not self._indexes.is_selected(affected_ref):
                    to_select.add(affected_ref)
                    logger.debug(f"  Missing dependency: {affected_ref}")

        if not to_select:
            msg = f"✅ Toutes les dépendances de {reference} sont déjà satisfaites"
            logger.info(msg)
            return ResolutionResult([], [], False, msg)

        # Select missing components
        added = []
        failed = []

        for target in to_select:
            if self._select_component(target):
                added.append(target)
            else:
                failed.append(target)

        self.validate_current_selection()

        message = self._format_dependency_message(reference, added, failed)
        success = len(added) > 0

        logger.info(f"Dependencies resolved: {len(added)} added, {len(failed)} failed")
        return ResolutionResult(added, [], success, message)

    def _format_dependency_message(
        self,
        source: ComponentReference,
        added: list[ComponentReference],
        failed: list[ComponentReference],
    ) -> str:
        """Format dependency resolution message."""
        parts = [f"📦 Résolution des dépendances pour {source}"]

        if added:
            parts.append(f"\n✅ Ajoutés ({len(added)}):")
            for ref in added:
                parts.append(f"  • {ref}")

        if failed:
            parts.append(f"\n❌ Échecs ({len(failed)}):")
            for ref in failed:
                parts.append(f"  • {ref}")

        if not added and not failed:
            parts.append("\n✅ Toutes les dépendances sont déjà satisfaites")

        return "\n".join(parts)

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
            for affected_ref in violation.affected_components:
                # Skip source component we want to keep
                if affected_ref == reference:
                    continue

                if self._indexes.is_selected(affected_ref):
                    to_deselect.add(affected_ref)
                    logger.debug(f"  Conflict to remove: {affected_ref}")

        if not to_deselect:
            msg = f"✅ Tous les conflits de {reference} sont déjà résolus"
            logger.info(msg)
            return ResolutionResult([], [], False, msg)

        # Deselect conflicts
        removed = []
        failed = []

        for conflict in to_deselect:
            if self._deselect_component(conflict):
                removed.append(conflict)
            else:
                failed.append(conflict)

        self.validate_current_selection()

        message = self._format_conflict_keep_message(reference, removed, failed)
        success = len(removed) > 0

        logger.info(f"Conflicts resolved (keep): {len(removed)} removed, {len(failed)} failed")
        return ResolutionResult([], removed, success, message)

    def _format_conflict_keep_message(
        self,
        source: ComponentReference,
        removed: list[ComponentReference],
        failed: list[ComponentReference],
    ) -> str:
        """Format conflict resolution message (keep)."""
        parts = [f"✅ Garder {source} - Résolution des conflits"]

        if removed:
            parts.append(f"\n❌ Retirés ({len(removed)}):")
            for ref in removed:
                parts.append(f"  • {ref}")

        if failed:
            parts.append(f"\n⚠️ Échecs ({len(failed)}):")
            for ref in failed:
                parts.append(f"  • {ref}")

        if not removed and not failed:
            parts.append("\n✅ Tous les conflits sont déjà résolus")

        return "\n".join(parts)

    def resolve_conflicts_remove(self, reference: ComponentReference) -> ResolutionResult:
        """Resolve conflicts by REMOVING this component."""
        logger.info(f"Resolving conflicts - removing {reference}")

        if not self._indexes.is_selected(reference):
            msg = f"ℹ️ {reference} n'est pas sélectionné"
            return ResolutionResult([], [], False, msg)

        if self._deselect_component(reference):
            self.validate_current_selection()

            msg = f"❌ {reference} a été retiré\n✅ Les composants en conflit ont été conservés"
            logger.info(f"Conflict resolved (remove): {reference} removed")
            return ResolutionResult([], [reference], True, msg)
        else:
            msg = f"❌ Impossible de retirer {reference}"
            logger.error(msg)
            return ResolutionResult([], [], False, msg)

    def auto_resolve(self, reference: ComponentReference) -> ResolutionResult:
        """Smart resolution: resolve dependencies then keep this component."""
        logger.info(f"Auto-resolving {reference}")

        violations = self.get_violations_for_reference(reference)

        if not violations:
            return ResolutionResult([], [], False, "✅ Aucune violation")

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
            return ResolutionResult([], [], False, "ℹ️ Aucune action nécessaire")

        message = self._format_auto_resolve_message(reference, all_added, all_removed)
        logger.info(f"Auto-resolve complete: +{len(all_added)} -{len(all_removed)}")
        return ResolutionResult(all_added, all_removed, True, message)

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

    def _select_component(self, reference: ComponentReference) -> bool:
        """Select a component via SelectionManager."""
        if not self._selection_manager:
            logger.error("No selection manager available")
            return False

        try:
            self._selection_manager.select_item(str(reference))
            logger.debug(f"Selected {reference}")
            return True
        except Exception as e:
            logger.error(f"Failed to select {reference}: {e}")
            return False

    def _deselect_component(self, reference: ComponentReference) -> bool:
        """Deselect a component via SelectionManager."""
        if not self._selection_manager:
            logger.error("No selection manager available")
            return False

        try:
            self._selection_manager.unselect_item(str(reference))
            logger.debug(f"Deselected {reference}")
            return True
        except Exception as e:
            logger.error(f"Failed to deselect {reference}: {e}")
            return False
