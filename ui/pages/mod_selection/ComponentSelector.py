"""
ComponentSelector - Hierarchical widget for mod and component selection.

This module provides a tree-based component selector with filtering capabilities,
supporting different component types (STD, MUC, SUB) with their specific behaviors.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import cast

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QHeaderView, QStyledItemDelegate, QTreeView

from constants import (
    COLOR_STATUS_COMPLETE,
    COLOR_STATUS_NONE,
    COLOR_STATUS_PARTIAL,
    ICON_ERROR,
    ICON_WARNING,
    ROLE_AUTHOR,
    ROLE_COMPONENT,
    ROLE_MOD,
    ROLE_OPTION_KEY,
    ROLE_PROMPT_KEY,
)
from core.ComponentReference import ComponentReference, IndexManager
from core.enums.CategoryEnum import CategoryEnum
from core.ModManager import ModManager
from core.TranslationManager import tr
from ui.pages.mod_selection.SelectionController import SelectionController
from ui.pages.mod_selection.TreeItem import TreeItem

logger = logging.getLogger(__name__)


# ============================================================================
# Enums and Data Classes
# ============================================================================


@dataclass(frozen=True)
class VisibilityStats:
    """Statistics about visible and checked children items.

    Attributes:
        total_visible: Total number of visible children
        checked_count: Number of visible children that are checked
    """

    total_visible: int
    checked_count: int

    @property
    def has_visible_children(self) -> bool:
        """Check if there are any visible children."""
        return self.total_visible > 0

    @property
    def all_checked(self) -> bool:
        """Check if all visible children are checked."""
        return 0 < self.total_visible == self.checked_count

    @property
    def none_checked(self) -> bool:
        """Check if no visible children are checked."""
        return self.checked_count == 0

    @property
    def partially_checked(self) -> bool:
        """Check if some but not all visible children are checked."""
        return 0 < self.checked_count < self.total_visible


@dataclass(frozen=True)
class StatusConfig:
    """Configuration for status display.

    Attributes:
        text_key: Translation key for the status text
        color: Color to use for the status
        check_state: Check state to apply to the parent item
    """

    text_key: str
    color: str
    check_state: Qt.CheckState


@dataclass
class FilterCriteria:
    """Encapsulates all filter criteria."""

    text: str = ""
    game: str = ""
    category: str = ""
    authors: set[str] = None
    languages: set[str] = None

    def __post_init__(self):
        """Initialize sets if None."""
        if self.authors is None:
            self.authors = set()
        if self.languages is None:
            self.languages = set()

    def has_active_filters(self) -> bool:
        """Check if any filter is active."""
        return bool(self.text or self.game or self.category or self.authors or self.languages)

    def clear(self) -> None:
        """Clear all filters."""
        self.text = ""
        self.game = ""
        self.category = ""
        self.authors.clear()
        self.languages.clear()


# ============================================================================
# Filter Engine
# ============================================================================


class FilterEngine:
    """Handles all filtering logic separately from UI concerns."""

    def __init__(self):
        self._criteria = FilterCriteria()

    def set_criteria(self, criteria: FilterCriteria) -> None:
        """Set filter criteria."""
        self._criteria = criteria

    def get_criteria(self) -> FilterCriteria:
        """Get current filter criteria."""
        return self._criteria

    def matches_item(self, index: QModelIndex) -> bool:
        """Check if an item matches all active filters."""
        if not self._criteria.has_active_filters():
            return True

        # Text filter
        if self._criteria.text and not self._matches_text(index):
            return False

        # Games filter
        if self._criteria.game and not self._matches_game(index):
            return False

        # Categories filter
        if self._criteria.category and not self._matches_category(index):
            return False

        # Authors filter
        if self._criteria.authors and not self._matches_authors(index):
            return False

        # Languages filter
        if self._criteria.languages and not self._matches_languages(index):
            return False

        return True

    def _matches_text(self, index: QModelIndex) -> bool:
        """Check if item matches text filter."""
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return False
        return self._criteria.text.lower() in text.lower()

    def _matches_game(self, index: QModelIndex) -> bool:
        """Check if item matches game filter."""
        component = index.data(ROLE_COMPONENT)
        if component:
            return component.supports_game(self._criteria.game)

        mod = index.data(ROLE_MOD)
        if mod:
            return mod.supports_game(self._criteria.game)

        return False

    def _matches_category(self, index: QModelIndex) -> bool:
        """Check if item matches categories filter."""
        mod = index.data(ROLE_MOD)

        if not mod:
            return False

        return self._criteria.category in mod.categories

    def _matches_authors(self, index: QModelIndex) -> bool:
        """Check if item matches authors filter."""
        item_author = index.data(ROLE_AUTHOR)
        return item_author in self._criteria.authors

    def _matches_languages(self, index: QModelIndex) -> bool:
        """Check if item matches languages filter."""
        mod = index.data(ROLE_MOD)
        if not mod:
            return False
        return mod.supports_language(self._criteria.languages)


# ============================================================================
# Hierarchical Filter Proxy Model
# ============================================================================


class HierarchicalFilterProxyModel(QSortFilterProxyModel):
    """Proxy model with intelligent hierarchical filtering.

    Shows complete branches if any element in the branch matches active filters.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter_engine = FilterEngine()
        self._indexes = IndexManager.get_indexes()
        self._show_violations_only = False

        # Configuration
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_show_violations_only(self, show: bool) -> None:
        if self._show_violations_only == show:
            return

        self._show_violations_only = show
        self.invalidateFilter()
        logger.debug(f"Show violations only: {show}")

    def get_show_violations_only(self) -> bool:
        return self._show_violations_only

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Compare two items for sorting.

        Only sorts first level (mods), not children.
        """
        # Don't sort if we're in children (preserve insertion order)
        if left.parent().isValid():
            return left.row() < right.row()

        # Sort mods normally
        return super().lessThan(left, right)

    # ========================================
    # Filter Configuration
    # ========================================

    def set_filter_criteria(self, criteria: FilterCriteria) -> None:
        """Set all filter criteria at once."""
        self._filter_engine.set_criteria(criteria)
        self.invalidateFilter()

    def get_filter_criteria(self) -> FilterCriteria:
        """Get current filter criteria."""
        return self._filter_engine.get_criteria()

    def clear_all_filters(self) -> None:
        """Reset all filters."""
        self._filter_engine.get_criteria().clear()
        self.invalidateFilter()

    def has_active_filters(self) -> bool:
        """Check if at least one filter is active."""
        return self._filter_engine.get_criteria().has_active_filters()

    # ========================================
    # Filtering Logic
    # ========================================

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """Determine if a row should be displayed.

        A row is shown if:
        - It matches filters
        - OR one of its children matches filters
        - OR one of its parents matches filters
        - AND (if violations filter active) it or its children have violations
        """
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)

        # Standard filter first
        standard_filter_passes = self._check_standard_filters(index, source_model)

        if not standard_filter_passes:
            return False

        if not self._show_violations_only:
            return True

        return self._has_violations_recursive(index, source_model)

    def _check_standard_filters(self, index: QModelIndex, source_model) -> bool:
        if not self.has_active_filters():
            return True

        # Check if this item matches
        if self._filter_engine.matches_item(index):
            return True

        # Check if any child matches (recursive)
        if self._has_matching_child(index):
            return True

        return False

    def _has_violations_recursive(self, index: QModelIndex, source_model) -> bool:
        item = source_model.itemFromIndex(index)
        if not isinstance(item, TreeItem):
            return False

        try:
            reference = item.reference

            if reference.is_mod():
                for row in range(item.rowCount()):
                    child = item.child(row, 0)
                    if not isinstance(child, TreeItem):
                        continue

                    try:
                        if self._indexes.has_violations(child.reference):
                            return True
                    except ValueError:
                        continue

                return False
            else:
                return self._indexes.has_violations(reference)

        except ValueError:
            return False

    def _has_matching_child(self, parent_index: QModelIndex) -> bool:
        """Check if any child matches filters (recursive)."""
        source_model = self.sourceModel()
        row_count = source_model.rowCount(parent_index)

        for row in range(row_count):
            child_index = source_model.index(row, 0, parent_index)

            if self._filter_engine.matches_item(child_index):
                return True

            if self._has_matching_child(child_index):
                return True

        return False


class StatusColumnDelegate(QStyledItemDelegate):
    """Delegate for status column with violation icons."""

    ICON_SPACING = 4

    def __init__(self, indexes, parent=None):
        super().__init__(parent)
        self._indexes = indexes

    def paint(self, painter, option, index):
        super().paint(painter, option, index)

        # Map to source model if needed
        source_index = index
        if hasattr(index.model(), "mapToSource"):
            source_index = index.model().mapToSource(index)

        # Get item from first column (name)
        item = source_index.model().itemFromIndex(source_index.siblingAtColumn(0))
        if not isinstance(item, TreeItem):
            return

        try:
            icons_to_draw = self._get_icons_for_reference(item.reference, item)

            if not icons_to_draw:
                return

            self._draw_icons(painter, option.rect, icons_to_draw)

        except (ValueError, AttributeError) as e:
            logger.debug(f"Could not render status: {e}")

    def _get_icons_for_reference(
        self, reference: ComponentReference, item: TreeItem
    ) -> list[tuple[str, QColor]]:
        """Get icons to display for a reference.

        Returns:
            List of tuples (icon_text, color)
        """
        icons = []

        # For mod items, aggregate violations from children
        if reference.is_mod():
            has_child_error = False
            has_child_warning = False

            for row in range(item.rowCount()):
                child = item.child(row, 0)
                if not isinstance(child, TreeItem):
                    continue

                try:
                    child_violations = self._indexes.get_violations(child.reference)

                    if any(v.is_error for v in child_violations):
                        has_child_error = True
                    if any(v.is_warning for v in child_violations):
                        has_child_warning = True

                except ValueError:
                    continue

            if has_child_error:
                icons.append((ICON_ERROR, QColor("#d32f2f")))
            if has_child_warning:
                icons.append((ICON_WARNING, QColor("#f57c00")))
        else:
            # For components, display their own violations
            violations = self._indexes.get_violations(reference)

            if not violations:
                return icons

            has_error = any(v.is_error for v in violations)
            has_warning = any(v.is_warning for v in violations)

            if has_error:
                icons.append((ICON_ERROR, QColor("#d32f2f")))
            if has_warning:
                icons.append((ICON_WARNING, QColor("#f57c00")))

        return icons

    def _draw_icons(self, painter, rect, icons: list[tuple[str, QColor]]) -> None:
        """Draw icons in the rect."""
        if not icons:
            return

        painter.save()

        font = painter.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        painter.setFont(font)

        fm = QFontMetrics(font)

        # Calculate positions (horizontally centered)
        total_width = sum(fm.horizontalAdvance(icon[0]) for icon in icons)
        total_width += self.ICON_SPACING * (len(icons) - 1)

        x = rect.x() + (rect.width() - total_width) // 2
        y = rect.center().y() + fm.height() // 3

        for icon_text, color in icons:
            painter.setPen(color)
            painter.drawText(x, y, icon_text)
            x += fm.horizontalAdvance(icon_text) + self.ICON_SPACING

        painter.restore()


# ============================================================================
# Main Component Selector Widget
# ============================================================================


class ComponentSelector(QTreeView):
    """Hierarchical widget for mod and component selection."""

    item_clicked_signal = Signal(ComponentReference)

    def __init__(self, mod_manager: ModManager, controller: SelectionController, parent=None):
        super().__init__(parent)

        self._mod_manager = mod_manager
        self._controller = controller
        self._indexes = IndexManager.get_indexes()

        self._setup_model()
        self._setup_ui()
        self._load_data()
        self._configure_table()
        self._connect_signals()

        logger.info("ComponentSelector initialized")

    # ========================================
    # Initialization
    # ========================================

    def _setup_model(self) -> None:
        """Setup model and proxy."""
        self._model = QStandardItemModel()

        self._proxy_model = HierarchicalFilterProxyModel()
        self._proxy_model.setSourceModel(self._model)
        self._proxy_model.setFilterKeyColumn(-1)
        self._proxy_model.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy_model.sort(0, Qt.SortOrder.AscendingOrder)

        self.setModel(self._proxy_model)

    def _setup_ui(self) -> None:
        """Configure UI."""
        self.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.setExpandsOnDoubleClick(False)

    def _configure_table(self) -> None:
        """Configure tree view header."""
        header = self.header()
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setSectionsMovable(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.setColumnWidth(1, 80)  # Selection
        self.setColumnWidth(2, 50)  # Status

        self._status_delegate = StatusColumnDelegate(self._indexes, self)
        self.setItemDelegateForColumn(2, self._status_delegate)

    def _connect_signals(self) -> None:
        """Connect to controller signals."""
        self.clicked.connect(self._on_item_clicked)
        self._controller.selection_changed.connect(self._on_controller_selection_changed)
        self._controller.selections_bulk_changed.connect(self._on_controller_bulk_changed)

    # ========================================
    # Data Loading
    # ========================================

    def _load_data(self) -> None:
        """Load mods and components into tree."""
        self._model.clear()

        for mod in self._mod_manager.get_all_mods().values():
            self._add_mod_to_tree(mod)

    def _add_mod_to_tree(self, mod) -> None:
        """Add mod and its components to tree."""
        mod_item = TreeItem.create_mod(mod)
        status_item = QStandardItem("")
        selection_item = self._create_selection_item()

        self._indexes.register_tree_item(mod_item.reference, mod_item)

        # Add components
        for component in mod.get_components():
            self._add_component_to_mod(mod_item, mod, component)

        self._model.appendRow([mod_item, selection_item, status_item])

    def _add_component_to_mod(self, mod_item: TreeItem, mod, component) -> None:
        """Add component to mod item."""
        comp_item = TreeItem.create_component(mod, component)
        comp_selection_item = QStandardItem("")
        comp_status_item = QStandardItem("")

        self._indexes.register_tree_item(comp_item.reference, comp_item)

        if component.is_muc():
            self._add_muc_options(comp_item, mod, component)
        elif component.is_sub():
            self._add_sub_prompts(comp_item, mod, component)

        mod_item.appendRow([comp_item, comp_selection_item, comp_status_item])

    def _add_muc_options(self, parent: TreeItem, mod, component) -> None:
        """Add MUC options."""
        default_value = str(component.default) if component.default is not None else None
        components = component.components
        for idx, component in enumerate(components.values()):
            option_key = component.key
            is_default = str(option_key) == default_value if default_value else idx == 0

            option_item = TreeItem.create_muc_option(mod, component, option_key, is_default)
            status_item = QStandardItem("")
            selection_item = QStandardItem("")

            self._indexes.register_tree_item(option_item.reference, option_item)

            parent.appendRow([option_item, status_item, selection_item])

    def _add_sub_prompts(self, parent: TreeItem, mod, component) -> None:
        """Add SUB prompts."""
        for prompt_key in component.prompts.keys():
            prompt = component.get_prompt(prompt_key)
            prompt_item = TreeItem.create_sub_prompt(mod, component, prompt)
            prompt_status_item = QStandardItem("")
            prompt_selection_item = QStandardItem("")

            self._indexes.register_tree_item(prompt_item.reference, prompt_item)

            for option_key in prompt.options:
                option_item = TreeItem.create_sub_option(
                    mod, component, prompt, option_key, option_key == prompt.default
                )
                option_status_item = QStandardItem("")
                option_selection_item = QStandardItem("")

                self._indexes.register_tree_item(option_item.reference, option_item)

                prompt_item.appendRow([option_item, option_status_item, option_selection_item])

            parent.appendRow([prompt_item, prompt_status_item, prompt_selection_item])

    def _create_selection_item(self) -> QStandardItem:
        """Create status column item."""
        item = QStandardItem(tr("widget.component_selector.selection.none"))
        item.setForeground(QColor(COLOR_STATUS_NONE))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    # ========================================
    # Event Handlers (Delegate to Controller)
    # ========================================

    def _on_item_clicked(self, index: QModelIndex) -> None:
        """Handle click - distinguish checkbox vs expand."""
        source_index = self._proxy_model.mapToSource(index)
        item = self._model.itemFromIndex(source_index)

        source_model = index.model().sourceModel()
        root_index = source_index.sibling(source_index.row(), 0)
        root_item = source_model.itemFromIndex(root_index)

        if isinstance(item, TreeItem):
            rect = self.visualRect(index)
            click_pos = self.viewport().mapFromGlobal(QCursor.pos())
            checkbox_zone = rect.left() + 35

            if click_pos.x() <= checkbox_zone:
                was_checked = item.checkState() != Qt.CheckState.Checked
                success = self._controller.toggle(item.reference)

                if not success:
                    self._model.blockSignals(True)
                    try:
                        item.setCheckState(
                            Qt.CheckState.Checked if was_checked else Qt.CheckState.Unchecked
                        )
                    finally:
                        self._model.blockSignals(False)

                    self.style().unpolish(self)
                    self.style().polish(self)
                return

        else:
            item = root_item
            index = self._proxy_model.mapFromSource(root_index)

        if item.rowCount() > 0:
            self.setExpanded(index, not self.isExpanded(index))

        self.item_clicked_signal.emit(item.reference)

    # ========================================
    # Event Handlers
    # ========================================

    def _on_controller_selection_changed(
        self, reference: ComponentReference, is_selected: bool
    ) -> None:
        """Update UI when controller changes selection."""
        item = self._indexes.get_tree_item(reference)
        if not isinstance(item, TreeItem):
            return

        check_state = Qt.CheckState.Checked if is_selected else Qt.CheckState.Unchecked

        if item.checkState() != check_state:
            self._model.blockSignals(True)
            item.setCheckState(check_state)
            self._model.blockSignals(False)
            self.style().unpolish(self)
            self.style().polish(self)

        self._update_parent_mod_status(item)

    def _on_controller_bulk_changed(
        self, selected: list[ComponentReference], unselected: list[ComponentReference]
    ) -> None:
        """Update UI for bulk changes."""
        self._model.blockSignals(True)

        try:
            for reference in selected:
                item = self._indexes.get_tree_item(reference)
                if isinstance(item, TreeItem):
                    item.setCheckState(Qt.CheckState.Checked)

            for reference in unselected:
                item = self._indexes.get_tree_item(reference)
                if isinstance(item, TreeItem):
                    item.setCheckState(Qt.CheckState.Unchecked)

            self._update_all_mod_statuses()

        finally:
            self._model.blockSignals(False)
            self.viewport().update()

    # ========================================
    # Status Updates
    # ========================================

    def _update_parent_mod_status(self, item: TreeItem) -> None:
        """Update status for parent mod."""
        current = item
        while current:
            if current.reference.is_mod():
                self._update_mod_status(cast(TreeItem, current))
                break
            current = current.parent()

    def _update_mod_status(self, mod_item: TreeItem) -> None:
        """Update mod selection status."""
        total_visible = 0
        checked_count = 0

        for row in range(mod_item.rowCount()):
            child = mod_item.child(row, 0)
            if not child:
                continue

            # Check if visible through proxy
            child_index = self._model.indexFromItem(child)
            proxy_index = self._proxy_model.mapFromSource(child_index)

            if not proxy_index.isValid():
                continue

            total_visible += 1
            if child.checkState() == Qt.CheckState.Checked:
                checked_count += 1

        parent = mod_item.parent()
        status_item = (
            parent.child(mod_item.row(), 1) if parent else self._model.item(mod_item.row(), 1)
        )

        if not status_item:
            return

        if total_visible == 0:
            text = tr("widget.component_selector.selection.none")
            color = COLOR_STATUS_NONE
            mod_item.setCheckState(Qt.CheckState.Unchecked)
        elif checked_count == 0:
            text = tr("widget.component_selector.selection.none", total=total_visible)
            color = COLOR_STATUS_NONE
            mod_item.setCheckState(Qt.CheckState.Unchecked)
        elif checked_count == total_visible:
            text = tr(
                "widget.component_selector.selection.complete",
                count=checked_count,
                total=total_visible,
            )
            color = COLOR_STATUS_COMPLETE
            mod_item.setCheckState(Qt.CheckState.Checked)
        else:
            text = tr(
                "widget.component_selector.selection.partial",
                count=checked_count,
                total=total_visible,
            )
            color = COLOR_STATUS_PARTIAL
            mod_item.setCheckState(Qt.CheckState.PartiallyChecked)

        status_item.setText(text)
        status_item.setForeground(QColor(color))

    def _update_all_mod_statuses(self) -> None:
        """Update status for all mods."""
        root = self._model.invisibleRootItem()
        for row in range(root.rowCount()):
            mod_item = root.child(row, 0)
            if isinstance(mod_item, TreeItem) and mod_item.reference.is_mod():
                self._update_mod_status(mod_item)

    # ========================================
    # Statistics
    # ========================================

    def get_filtered_mod_count(self) -> int:
        """Get number of visible mods after filtering."""
        return self._proxy_model.rowCount()

    def get_filtered_mod_count_by_category(self) -> dict[str, int]:
        """Get mod count per category with current filters applied.

        A mod is counted in each of its categories if it or any of its
        children match the active filters (excluding category filter).
        A mod is also counted in a category if any of its components has that category.
        """
        counts: dict[str, int] = {}
        criteria = self._proxy_model.get_filter_criteria()

        # Create criteria without category filter
        filter_no_category = FilterCriteria(
            text=criteria.text,
            game=criteria.game,
            category="",  # Exclude category
            authors=criteria.authors.copy(),
            languages=criteria.languages.copy(),
        )

        mods = set()
        engine = FilterEngine()
        engine.set_criteria(filter_no_category)

        # Iterate through all mods
        root = self._model.invisibleRootItem()
        for row in range(root.rowCount()):
            mod_item = root.child(row, 0)
            if not mod_item:
                continue

            # Check if mod or any child matches
            if self._item_or_children_match(mod_item, engine):
                mod = mod_item.data(ROLE_MOD)
                if mod:
                    # Increment the counters for all categories found
                    for category in mod.categories:
                        counts[category] = counts.get(category, 0) + 1

                    mods.add(mod)

        counts[CategoryEnum.ALL.value] = len(mods)

        return counts

    def _item_or_children_match(self, item: QStandardItem, engine: FilterEngine) -> bool:
        """Check if item or any of its children match filter criteria."""
        # Check item itself
        if engine.matches_item(item.index()):
            return True

        # Check children recursively
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            if child and self._item_or_children_match(child, engine):
                return True

        return False

    # ========================================
    # Public API
    # ========================================

    def apply_filters(
        self,
        text: str = "",
        game: str = "",
        category: str = "",
        authors: set[str] | None = None,
        languages: set[str] | None = None,
    ) -> None:
        """Apply all filters at once for better performance."""
        criteria = FilterCriteria(
            text=text,
            game=game,
            category=category,
            authors=authors or set(),
            languages=languages or set(),
        )
        self._proxy_model.set_filter_criteria(criteria)

    def clear_filters(self) -> None:
        """Clear all filters."""
        self._proxy_model.clear_all_filters()

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        self._model.setHorizontalHeaderLabels(
            [
                tr("widget.component_selector.header.type"),
                tr("widget.component_selector.header.selection"),
                tr("widget.component_selector.header.status"),
            ]
        )

        self._retranslate_items()
        self._update_all_mod_statuses()

    def _retranslate_items(self) -> None:
        """Retranslate all items using indexes instead of tree traversal."""

        for reference, item in self._indexes.tree_item_index.items():
            if not isinstance(item, TreeItem):
                continue

            # Refresh mod data
            mod = item.data(ROLE_MOD)
            component = item.data(ROLE_COMPONENT)

            if mod:
                mod = self._mod_manager.get_mod_by_id(mod.id)
                item.setData(mod, ROLE_MOD)

            if reference.is_mod():
                self._update_mod_status(item)

            elif component:
                if component.is_standard():
                    text = f"[{component.key}] {mod.get_component_text(component.key)}"
                    item.setText(text)

                elif component.is_muc():
                    item.setText(mod.get_component_text(component.key))

                elif reference.is_sub():
                    prompt = item.data(ROLE_PROMPT_KEY)
                    if prompt:
                        text = f"[{component.key}.{prompt.key}] {component.get_prompt_text(prompt.key)}"
                        item.setText(text)

                elif reference.is_sub_option():
                    prompt = item.data(ROLE_PROMPT_KEY)
                    option_key = item.data(ROLE_OPTION_KEY)
                    if prompt and option_key:
                        text = (
                            f"[{component.key}.{prompt.key}.{option_key}] "
                            f"{component.get_prompt_option_text(prompt.key, option_key)}"
                        )
                        item.setText(text)
