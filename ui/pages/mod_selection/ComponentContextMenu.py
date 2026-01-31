import logging
from typing import Callable

from PySide6.QtWidgets import QMenu

from core.ComponentReference import ComponentReference, IndexManager
from core.Mod import Mod
from core.Rules import RuleType, RuleViolation
from core.TranslationManager import tr
from ui.pages.mod_selection.SelectionController import SelectionController
from ui.pages.mod_selection.TreeItem import TreeItem

logger = logging.getLogger(__name__)


class ComponentContextMenu:
    """Builder for component context menus."""

    def __init__(self, controller: SelectionController):
        """Initialize menu builder.

        Args:
            controller: Selection controller for actions
        """
        self._controller = controller
        self._indexes = IndexManager.get_indexes()

    # ========================================
    # Public API
    # ========================================

    def show_menu(self, item: TreeItem, position, specific_violation=None) -> None:
        """Show context menu for an item.

        Args:
            item: Tree item to show menu for
            position: Global position for menu
            specific_violation: Optional specific violation to focus on.
                              If provided, only actions for this violation are shown.
        """
        reference = item.reference

        # Determine violations to use
        if specific_violation:
            violations = [specific_violation]
        else:
            if reference.is_muc():
                violations = self._get_muc_violations(reference)
            else:
                violations = self._indexes.get_selection_violations(reference)

        # Allow menu without violations for mod rows
        if not violations and not reference.is_mod():
            return

        menu = QMenu()

        # Build menu based on context
        if specific_violation:
            self._build_violation_menu(menu, reference, specific_violation)
        elif reference.is_mod():
            self._build_mod_menu(menu, reference)
        else:  # Standard, MUC or SUB component
            self._build_component_menu(menu, reference, violations)

        if not menu.isEmpty():
            menu.exec(position)

    # ========================================
    # Menu Builders by Type
    # ========================================

    def _build_mod_menu(self, menu: QMenu, reference: ComponentReference):
        """Build context menu for mod row."""
        component_refs, all_violations = self._get_all_violations_for_descendants(reference)

        if not all_violations:
            return

        """TODO
        keep_action = menu.addAction(tr("page.selection.violation.keep_this_mod_components"))
        keep_action.triggered.connect(
            lambda: self._resolve_keep_mod(reference, component_refs, all_violations)
        )
        """

        remove_action = menu.addAction(
            tr("page.selection.violation.unselect_this_mod_components")
        )
        remove_action.triggered.connect(lambda: self._controller.unselect(reference))

        self._add_dependencies_section(menu, all_violations)
        self._add_dependent_components_section(menu, all_violations)
        self._add_mod_conflicts_section(menu, all_violations, component_refs)

    def _build_component_menu(
        self, menu: QMenu, reference: ComponentReference, violations: list
    ):
        """Build context menu for component (Standard, MUC, SUB)."""
        if not violations:
            return

        if self._indexes.is_selected(reference):
            """ TODO
            keep_action = menu.addAction(tr("page.selection.violation.keep_this_component"))
            keep_action.triggered.connect(
                lambda: self._resolve_keep_component(reference, violations)
            )
            """

            remove_action = menu.addAction(
                tr("page.selection.violation.unselect_this_component")
            )
            remove_action.triggered.connect(lambda: self._controller.unselect(reference))

            self._add_dependencies_section(menu, violations)
            self._add_conflicts_section(menu, violations, reference)
        else:
            select_action = menu.addAction(tr("page.selection.violation.select_this_component"))
            select_action.triggered.connect(lambda: self._controller.select(reference))
            menu.addSeparator()
            self._add_dependent_components_section(menu, violations)

    def _build_violation_menu(self, menu: QMenu, reference: ComponentReference, violation):
        """Build context menu for a specific violation."""

        if violation.rule.rule_type == RuleType.DEPENDENCY:
            if self._indexes.is_selected(reference):
                self._add_dependencies_section(menu, [violation])
                menu.addSeparator()
                remove_action = menu.addAction(
                    tr("page.selection.violation.unselect_this_component")
                )
                remove_action.triggered.connect(lambda: self._controller.unselect(reference))
            else:
                select_action = menu.addAction(
                    tr("page.selection.violation.select_this_component")
                )
                select_action.triggered.connect(lambda: self._controller.select(reference))
                menu.addSeparator()
                self._add_dependent_components_section(menu, [violation])

        elif violation.rule.rule_type == RuleType.INCOMPATIBILITY:
            self._add_conflicts_section(menu, [violation], reference)
            menu.addSeparator()
            remove_action = menu.addAction(
                tr("page.selection.violation.unselect_this_component")
            )
            remove_action.triggered.connect(lambda: self._controller.unselect(reference))

    # ========================================
    # Section Builders
    # ========================================

    def _add_dependencies_section(self, menu: QMenu, violations: list):
        """Add missing dependency actions to menu."""
        actions = self._collect_dependency_actions(violations)
        self._add_action_list(
            menu, actions, "page.selection.violation.select_component", self._controller.select
        )

    def _add_dependent_components_section(self, menu: QMenu, violations: list):
        """Add actions for components that depend on this one."""
        component_actions, mod_actions = self._collect_dependent_components_actions(violations)

        self._add_action_list(
            menu,
            component_actions,
            "page.selection.violation.unselect_component",
            self._controller.unselect,
        )
        self._add_action_list(
            menu,
            mod_actions,
            "page.selection.violation.unselect_mod",
            self._controller.unselect,
        )

    def _add_conflicts_section(
        self, menu: QMenu, violations: list, reference: ComponentReference
    ):
        """Add conflict resolution actions to menu."""
        actions = self._collect_conflict_actions(violations, reference)
        self._add_action_list(
            menu,
            actions,
            "page.selection.violation.unselect_component",
            self._controller.unselect,
        )

    def _add_mod_conflicts_section(
        self, menu: QMenu, violations: list, mod_component_refs: list[ComponentReference]
    ):
        """Add aggregated mod conflicts (excluding internal conflicts)."""
        actions = self._collect_mod_conflict_actions(violations, mod_component_refs)
        self._add_action_list(
            menu,
            actions,
            "page.selection.violation.unselect_component",
            self._controller.unselect,
        )

    # ========================================
    # Action Collectors
    # ========================================

    def _get_all_selected_descendants(
        self, reference: ComponentReference
    ) -> list[ComponentReference]:
        """Get all selected descendant references recursively.

        Args:
            reference: Starting reference

        Returns:
            List of all selected descendant references
        """
        selected = []

        if reference in self._indexes.selection_index:
            if reference.is_component() or reference.is_sub_option():
                selected.append(reference)
                return selected

        children = self._indexes.get_children(reference)
        for child_ref in children:
            selected.extend(self._get_all_selected_descendants(child_ref))

        return selected

    def _get_all_violations_for_descendants(
        self, reference: ComponentReference
    ) -> tuple[list[ComponentReference], list]:
        """Get all selected descendants and their violations.

        Args:
            reference: Starting reference (mod or component)

        Returns:
            Tuple of (selected_refs, all_violations)
        """
        selected_refs = self._get_all_selected_descendants(reference)
        all_violations = []

        for ref in selected_refs:
            violations = self._indexes.get_selection_violations(ref)
            all_violations.extend(violations)

        return selected_refs, all_violations

    def _collect_dependency_actions(self, violations: list) -> list[tuple]:
        """Collect unique missing dependency actions."""
        unique_deps = {}

        for violation in violations:
            if violation.rule.rule_type != RuleType.DEPENDENCY:
                continue

            for ref in violation.rule.targets:
                if ref in self._indexes.selection_index:
                    continue

                component = self._indexes.resolve(ref)
                if component:
                    unique_deps[ref] = component

        return list(unique_deps.items())

    def _collect_dependent_components_actions(
        self, violations: list[RuleViolation]
    ) -> tuple[list[tuple], list[tuple]]:
        """Collect components that depend on this one."""
        mod_actions = {}
        comp_actions = {}

        for violation in violations:
            if violation.rule.rule_type != RuleType.DEPENDENCY:
                continue

            for ref in violation.rule.sources:
                if ref.is_mod():
                    if not any(
                        selected.mod_id == ref.mod_id
                        for selected in self._indexes.selection_index
                    ):
                        continue
                elif ref not in self._indexes.selection_index:
                    continue

                component = self._indexes.resolve(ref)
                if component:
                    if ref.is_mod():
                        mod_actions[ref] = component
                    else:
                        comp_actions[ref] = component

        return list(comp_actions.items()), list(mod_actions.items())

    def _collect_conflict_actions(
        self, violations: list, reference: ComponentReference
    ) -> list[tuple]:
        """Collect unique conflict actions for a component."""
        unique_conflicts = {}

        for violation in violations:
            if violation.rule.rule_type != RuleType.INCOMPATIBILITY:
                continue

            # Get conflicting side
            conflict_refs = (
                violation.rule.targets
                if reference in violation.rule.sources
                else violation.rule.sources
            )

            for ref in conflict_refs:
                if ref not in self._indexes.selection_index:
                    continue

                component = self._indexes.resolve(ref)
                if component:
                    unique_conflicts[ref] = component

        return list(unique_conflicts.items())

    def _collect_mod_conflict_actions(
        self, violations: list, mod_component_refs: list[ComponentReference]
    ) -> list[tuple]:
        """Collect unique conflict actions for mod (excluding internal conflicts)."""
        mod_ref_set = set(mod_component_refs)
        unique_conflicts = {}

        for violation in violations:
            if violation.rule.rule_type != RuleType.INCOMPATIBILITY:
                continue

            # Find which component from this mod is involved
            source_ref = next(
                (ref for ref in violation.rule.sources if ref in mod_ref_set), None
            )
            if not source_ref:
                continue

            references = set(violation.rule.sources) | set(violation.rule.targets)

            for ref in references:
                if ref not in self._indexes.selection_index:
                    continue

                component = self._indexes.resolve(ref)
                if component:
                    unique_conflicts[ref] = component

        return list(unique_conflicts.items())

    # ========================================
    # Descendant Processing
    # ========================================

    def _get_muc_violations(self, muc_reference: ComponentReference) -> list:
        """Get violations from the selected MUC option using indexes.

        Args:
            muc_reference: Reference to the MUC component

        Returns:
            List of violations from the selected option, empty if none selected
        """
        children = self._indexes.get_children(muc_reference)

        for child_ref in children:
            if child_ref in self._indexes.selection_index:
                return self._indexes.get_selection_violations(child_ref)

        return []

    # ========================================
    # Helper Methods
    # ========================================

    @staticmethod
    def _add_action_list(
        menu: QMenu,
        actions: list[tuple],
        text_key: str,
        callback: Callable,
    ) -> None:
        """Add a list of actions to menu with separator."""
        if not actions:
            return

        menu.addSeparator()
        for reference, action in actions:
            if isinstance(action, Mod):
                action = menu.addAction(
                    tr("page.selection.violation.unselect_mod", mod=action.name)
                )
            else:
                action = menu.addAction(
                    tr(
                        text_key,
                        mod=action.mod.name,
                        comp_key=action.key,
                        component=action.text,
                    )
                )
            action.triggered.connect(lambda _, ref=reference: callback(ref))

    # ========================================
    # Resolution Actions
    # ========================================

    def _resolve_keep_component(self, reference: ComponentReference, violations: list):
        """Resolve violations by keeping this component.

        Strategy:
        1. Select missing dependencies
        2. Unselect conflicting components
        """
        to_select = []
        to_unselect = []

        for violation in violations:
            if violation.rule.rule_type == RuleType.DEPENDENCY:
                # Add missing dependencies
                for ref in violation.rule.targets:
                    if ref not in self._indexes.selection_index:
                        to_select.append(ref)

            elif violation.rule.rule_type == RuleType.INCOMPATIBILITY:
                # Remove conflicts
                conflict_refs = (
                    violation.rule.targets
                    if reference in violation.rule.sources
                    else violation.rule.sources
                )
                for ref in conflict_refs:
                    if ref in self._indexes.selection_index:
                        to_unselect.append(ref)

        # Apply changes
        if to_select:
            self._controller.select_bulk(to_select)
        if to_unselect:
            self._controller.unselect_bulk(to_unselect)

        logger.info(
            f"Auto-resolved {reference}: +{len(to_select)} deps, -{len(to_unselect)} conflicts"
        )

    def _resolve_keep_mod(
        self,
        mod_ref: ComponentReference,
        component_refs: list[ComponentReference],
        violations: list,
    ):
        """Resolve violations by keeping all mod components.

        Strategy:
        1. Select all missing dependencies
        2. Unselect external conflicting components
        3. For internal conflicts, keep first occurrence in component list
        """
        mod_ref_set = set(component_refs)
        to_select = []
        to_unselect = []
        internal_conflicts = set()

        for violation in violations:
            if violation.rule.rule_type == RuleType.DEPENDENCY:
                # Add missing dependencies
                for ref in violation.rule.targets:
                    if ref not in self._indexes.selection_index:
                        to_select.append(ref)

            elif violation.rule.rule_type == RuleType.INCOMPATIBILITY:
                # Check if internal or external conflict
                sources_in_mod = [r for r in violation.rule.sources if r in mod_ref_set]
                targets_in_mod = [r for r in violation.rule.targets if r in mod_ref_set]

                if sources_in_mod and targets_in_mod:
                    # Internal conflict: keep first in list
                    all_involved = sources_in_mod + targets_in_mod
                    first_idx = min(component_refs.index(r) for r in all_involved)
                    first_ref = component_refs[first_idx]

                    for ref in all_involved:
                        if ref != first_ref:
                            internal_conflicts.add(ref)
                else:
                    # External conflict: remove external component
                    if sources_in_mod:
                        for ref in violation.rule.targets:
                            if ref in self._indexes.selection_index:
                                to_unselect.append(ref)
                    else:
                        for ref in violation.rule.sources:
                            if ref in self._indexes.selection_index:
                                to_unselect.append(ref)

        # Apply changes
        if to_select:
            self._controller.select_bulk(to_select)
        if to_unselect:
            self._controller.unselect_bulk(to_unselect)
        if internal_conflicts:
            self._controller.unselect_bulk(list(internal_conflicts))

        logger.info(
            f"Auto-resolved mod {mod_ref}: "
            f"+{len(to_select)} deps, "
            f"-{len(to_unselect)} external conflicts, "
            f"-{len(internal_conflicts)} internal conflicts"
        )
