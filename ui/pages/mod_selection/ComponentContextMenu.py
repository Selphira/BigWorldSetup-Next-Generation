import logging
from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

from core.ComponentReference import ComponentReference, IndexManager
from core.Mod import Component, Mod, SubComponent
from core.Rules import RuleType, RuleViolation
from core.TranslationManager import tr
from core.Url import get_forum_code
from core.ValidationOrchestrator import ValidationOrchestrator
from ui.pages.mod_selection.ResolutionPreviewDialog import ResolutionPreviewDialog
from ui.pages.mod_selection.SelectionController import SelectionController
from ui.pages.mod_selection.TreeItem import TreeItem

logger = logging.getLogger(__name__)


class ComponentContextMenu:
    """Builder for component context menus."""

    def __init__(self, controller: SelectionController, orchestrator: ValidationOrchestrator):
        """Initialize menu builder.

        Args:
            controller: Selection controller for actions
        """
        self._orchestrator = orchestrator
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
        if not reference.is_mod() and self._indexes.is_selected(reference):
            keep_action = menu.addAction(tr("page.selection.violation.keep_this_component"))
            keep_action.triggered.connect(lambda: self._resolve_keep_component(reference))

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

        mod = self._indexes.resolve(reference)
        if isinstance(mod, Mod):
            self._add_links_submenu(menu, mod)

        if not all_violations:
            return

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

        if violation.rule.source_url:
            menu.addSeparator()
            show_source = menu.addAction(tr("page.selection.violation.show_source"))
            show_source.triggered.connect(
                lambda: QDesktopServices.openUrl(QUrl(violation.rule.source_url))
            )

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

    @staticmethod
    def _add_links_submenu(menu: QMenu, mod: Mod) -> None:
        """Add 'Go to' submenu with mod links, only if at least one link exists."""
        links = {
            mod.homepage: tr("widget.mod_details.link.homepage"),
            mod.readme: tr("widget.mod_details.link.readme"),
            mod.get_download_url(): tr("widget.mod_details.link.download"),
        }

        entries: list[tuple[str, str]] = [(label, url) for url, label in links.items() if url]

        if mod.forums:
            for forum_url in mod.forums:
                label = tr("widget.mod_details.link.forum")
                code = get_forum_code(forum_url)

                if code:
                    label = f"{label} ({code})"
                entries.append((label, forum_url))

        if not entries:
            return

        goto_menu = menu.addMenu(tr("page.selection.go_to"))
        for label, url in entries:
            action = goto_menu.addAction(label)
            action.triggered.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))

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

            for ref in violation.affected_components[1:]:
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

    def _resolve_keep_component(self, reference: ComponentReference) -> None:
        proposal = self._orchestrator.auto_resolve(reference)
        title = tr("page.selection.violation.keep_this_component")
        if proposal is None:
            QMessageBox.information(None, title, tr("page.selection.no_resolution"))
            return
        changes = []
        for added, references in (
            (False, proposal.original - proposal.selection),
            (True, proposal.selection - proposal.original),
        ):
            labels = sorted(
                (
                    self._change_names(ref)
                    for ref in references
                    if ref.is_component() or ref.is_sub_option()
                ),
                key=lambda names: (names[0].casefold(), names[1].casefold()),
            )
            changes.extend((mod, component, added) for mod, component in labels)
        if not changes:
            return
        _, component_name = self._change_names(reference)
        dialog = ResolutionPreviewDialog(changes, component_name, QApplication.activeWindow())
        if (
            dialog.exec() == QDialog.DialogCode.Accepted
            and not self._orchestrator.apply_resolution(proposal)
        ):
            QMessageBox.information(
                dialog.parentWidget(), title, tr("page.selection.no_resolution")
            )
        dialog.deleteLater()

    def _change_names(self, reference: ComponentReference) -> tuple[str, str]:
        component = self._indexes.resolve(reference.get_base_component_reference())
        mod = (
            component.mod
            if isinstance(component, Component)
            else self._indexes.resolve(ComponentReference.for_mod(reference.mod_id))
        )
        mod_name = mod.name if isinstance(mod, Mod) and mod.name else reference.mod_id
        name = component.get_name() if isinstance(component, Component) else ""
        path = [
            name
            or tr("page.selection.unnamed_component", key=reference.get_base_component_key()),
        ]
        if reference.is_sub_option():
            _, question, answer = reference.comp_key.split(".")
            if isinstance(component, SubComponent):
                prompt = component.get_prompt_text(question)
                if prompt:
                    path.append(prompt)
                answer = component.get_prompt_option_text(question, answer) or answer
            path.append(answer)
        return mod_name, " > ".join(path)
