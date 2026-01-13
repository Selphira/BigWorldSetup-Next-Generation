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
    COLOR_ERROR,
    COLOR_STATUS_COMPLETE,
    COLOR_STATUS_NONE,
    COLOR_STATUS_PARTIAL,
    COLOR_WARNING,
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
from ui.pages.mod_selection.ComponentContextMenu import ComponentContextMenu
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

    __slots__ = ("_text", "_game", "_category", "_authors", "_languages")

    def __init__(self):
        self._text = ""
        self._game = ""
        self._category = ""
        self._authors: set[str] = set()
        self._languages: set[str] = set()

    def set_criteria(self, criteria: FilterCriteria) -> None:
        """Set filter criteria."""
        self._text = criteria.text.lower() if criteria.text else ""
        self._game = criteria.game
        self._category = criteria.category
        self._authors = criteria.authors
        self._languages = criteria.languages

    def get_criteria(self) -> FilterCriteria:
        """Get current filter criteria."""
        return FilterCriteria(
            text=self._text,
            game=self._game,
            category=self._category,
            authors=self._authors.copy(),
            languages=self._languages.copy(),
        )

    def has_active_filters(self) -> bool:
        """Check if any filter is active."""
        return bool(
            self._text or self._game or self._category or self._authors or self._languages
        )

    def matches_item(self, index: QModelIndex) -> bool:
        """Check if an item matches all active filters."""
        if not self.has_active_filters():
            return True

        # Text filter
        if self._text:
            text = index.data(Qt.ItemDataRole.DisplayRole)
            if not text or self._text not in text.lower():
                return False

        # Games filter
        if self._game and not self._matches_game(index):
            return False

        # Categories filter
        if self._category:
            mod = index.data(ROLE_MOD)
            if not mod or self._category not in mod.categories:
                return False

        # Authors filter
        if self._authors:
            author = index.data(ROLE_AUTHOR)
            if author not in self._authors:
                return False

        # Languages filter
        if self._languages:
            mod = index.data(ROLE_MOD)
            if not mod or not mod.supports_language(self._languages):
                return False

        return True

    def _matches_game(self, index: QModelIndex) -> bool:
        """Check if item matches game filter."""
        component = index.data(ROLE_COMPONENT)
        if component:
            return component.supports_game(self._game)

        mod = index.data(ROLE_MOD)
        if mod:
            return mod.supports_game(self._game)

        return False


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
        self._show_selection_only = False
        self._matching_cache: dict[int, bool] = {}

        # Configuration
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    # ========================================
    # Public API
    # ========================================

    def set_show_violations_only(self, show: bool) -> None:
        """Toggle violations filter."""
        if self._show_violations_only == show:
            return
        self._show_violations_only = show
        self._clear_cache()
        self.invalidateFilter()

    def set_show_selection_only(self, show: bool) -> None:
        """Toggle selection filter."""
        if self._show_selection_only == show:
            return
        self._show_selection_only = show
        self._clear_cache()
        self.invalidateFilter()

    def get_show_violations_only(self) -> bool:
        """Get violations filter state."""
        return self._show_violations_only

    def set_filter_criteria(self, criteria: FilterCriteria) -> None:
        """Set all filter criteria at once."""
        self._filter_engine.set_criteria(criteria)
        self._clear_cache()
        self.invalidateFilter()

    def get_filter_criteria(self) -> FilterCriteria:
        """Get current filter criteria."""
        return self._filter_engine.get_criteria()

    def clear_all_filters(self) -> None:
        """Reset all filters."""
        self._filter_engine.get_criteria().clear()
        self._filter_engine.set_criteria(FilterCriteria())
        self._show_violations_only = False
        self._show_selection_only = False
        self._clear_cache()
        self.invalidateFilter()

    def has_active_filters(self) -> bool:
        """Check if at least one filter is active."""
        return (
            self._filter_engine.has_active_filters()
            or self._show_violations_only
            or self._show_selection_only
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Compare two items for sorting.

        Only sorts first level (mods), not children.
        """
        # Don't sort if we're in children (preserve insertion order)
        if left.parent().isValid():
            return left.row() < right.row()

        return super().lessThan(left, right)

    # ========================================
    # Filtering Logic
    # ========================================

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """Determine if a row should be displayed."""
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)

        if not self._check_stateless_filters(index, source_model):
            return False

        return self._check_stateful_filters(index, source_model)

    def _check_stateless_filters(self, index: QModelIndex, source_model) -> bool:
        """Check stateless filters with caching."""
        if not self._filter_engine.has_active_filters():
            return True

        if self._filter_engine.matches_item(index):
            return True

        # Check children
        cache_key = self._get_cache_key(index)

        if cache_key in self._matching_cache:
            return self._matching_cache[cache_key]

        has_match = self._has_matching_child(index, source_model)
        self._matching_cache[cache_key] = has_match

        return has_match

    def _check_stateful_filters(self, index: QModelIndex, source_model) -> bool:
        """Check stateful filters (selection, violations)."""
        if not self._show_violations_only and not self._show_selection_only:
            return True

        item = source_model.itemFromIndex(index)
        if not item:
            return True

        if self._show_selection_only:
            if item.checkState() == Qt.CheckState.Unchecked:
                return False

        if self._show_violations_only:
            return self._has_violations_recursive(index, source_model)

        return True

    def _has_matching_child(self, parent_index: QModelIndex, source_model) -> bool:
        """Check if any child matches filters (recursive)."""
        row_count = source_model.rowCount(parent_index)

        for row in range(row_count):
            child_index = source_model.index(row, 0, parent_index)

            if self._filter_engine.matches_item(child_index):
                return True

            if self._has_matching_child(child_index, source_model):
                return True

        return False

    def _has_violations_recursive(self, index: QModelIndex, source_model) -> bool:
        """Check if item or children have violations."""
        item = source_model.itemFromIndex(index)
        if not hasattr(item, "reference"):
            return False

        try:
            reference = item.reference

            if reference.is_mod():
                for row in range(item.rowCount()):
                    child = item.child(row, 0)
                    if not hasattr(child, "reference"):
                        continue

                    try:
                        if self._indexes.has_selection_violations(child.reference):
                            return True
                    except ValueError:
                        continue

                return False
            else:
                return self._indexes.has_selection_violations(reference)

        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _get_cache_key(index: QModelIndex) -> int:
        """Generate cache key for an index."""
        return hash((index.row(), index.internalId()))

    def _clear_cache(self) -> None:
        """Clear matching cache."""
        self._matching_cache.clear()


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
            icons_to_draw = self._get_icons_for_reference(item.reference)
            self._draw_icons(painter, option.rect, icons_to_draw)

        except (ValueError, AttributeError) as e:
            logger.debug(f"Could not render status: {e}")

    def _get_icons_for_reference(
        self, reference: ComponentReference
    ) -> list[tuple[str, QColor]]:
        """Get icons to display for a reference.

        Returns:
            List of tuples (icon_text, color)
        """
        has_error, has_warning = self._check_violations_recursive(reference)

        icons = []
        if has_error:
            icons.append((ICON_ERROR, QColor(COLOR_ERROR)))
        if has_warning:
            icons.append((ICON_WARNING, QColor(COLOR_WARNING)))

        return icons

    def _check_violations_recursive(self, reference: ComponentReference) -> tuple[bool, bool]:
        """Recursively check violations for reference and all descendants.

        Returns:
            (has_error, has_warning) tuple
        """
        has_error = False
        has_warning = False

        # Check own violations using existing index
        own_violations = self._indexes.get_selection_violations(reference)
        if own_violations:
            has_error = any(v.is_error for v in own_violations)
            has_warning = any(v.is_warning for v in own_violations)

        # Check all children recursively
        children = self._indexes.get_children(reference)
        for child in children:
            child_error, child_warning = self._check_violations_recursive(child)
            has_error = has_error or child_error
            has_warning = has_warning or child_warning

            # Early exit optimization
            if has_error and has_warning:
                break

        return has_error, has_warning

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
        self._context_menu: ComponentContextMenu | None = None
        self._indexes = IndexManager.get_indexes()

        self._setup_model()
        self._setup_ui()
        self._load_data()
        self._configure_table()
        self._connect_signals()

        logger.info("ComponentSelector initialized")

    def set_context_menu(self, context_menu: ComponentContextMenu):
        """Set the context menu builder to use."""
        self._context_menu = context_menu

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
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _connect_signals(self) -> None:
        """Connect to controller signals."""
        self.clicked.connect(self._on_item_clicked)
        self.customContextMenuRequested.connect(self._show_row_context_menu)
        self._controller.selection_changed.connect(self._on_controller_selection_changed)
        self._controller.selections_bulk_changed.connect(self._on_controller_bulk_changed)

    # ========================================
    # Data Loading
    # ========================================

    def reload(self) -> None:
        self._load_data()
        self._configure_table()
        self.retranslate_ui()

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
    # Event Handlers
    # ========================================

    def _show_row_context_menu(self, position):
        """Display context menu for a row (all violations)."""
        index = self.indexAt(position)
        if not index.isValid():
            return

        source_index = self._proxy_model.mapToSource(index)
        item = self._model.itemFromIndex(source_index.siblingAtColumn(0))

        if isinstance(item, TreeItem):
            global_pos = self.viewport().mapToGlobal(position)
            self._context_menu.show_menu(item, global_pos)

    def _on_item_clicked(self, index: QModelIndex) -> None:
        """Handle click - distinguish checkbox vs expand."""
        if not index.isValid():
            return

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
                old_state = item.checkState()
                success = self._controller.toggle(item.reference)

                if not success:
                    self._model.blockSignals(True)
                    try:
                        item.setCheckState(old_state)
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
            for reference in unselected:
                item = self._indexes.get_tree_item(reference)
                if isinstance(item, TreeItem):
                    item.setCheckState(Qt.CheckState.Unchecked)

            for reference in selected:
                item = self._indexes.get_tree_item(reference)
                if isinstance(item, TreeItem):
                    item.setCheckState(Qt.CheckState.Checked)

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
                else:
                    text = f"[{component.key}] {mod.get_component_text(component.key)}"
                    item.setText(text)
