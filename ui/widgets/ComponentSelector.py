"""
ComponentSelector - Hierarchical widget for mod and component selection.

This module provides a tree-based component selector with filtering capabilities,
supporting different component types (STD, MUC, SUB) with their specific behaviors.
"""
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Any, cast

from PySide6.QtCore import QSortFilterProxyModel, QModelIndex, Qt
from PySide6.QtGui import QStandardItemModel, QStandardItem, QColor, QCursor
from PySide6.QtWidgets import QTreeView

from constants import (
    ROLE_MOD, ROLE_COMPONENT, ROLE_AUTHOR, ROLE_OPTION_KEY, ROLE_PROMPT_KEY, ROLE_IS_DEFAULT, ROLE_RADIO,
    COLOR_STATUS_NONE, COLOR_STATUS_COMPLETE, COLOR_STATUS_PARTIAL
)
from core.ModManager import ModManager
from core.TranslationManager import tr
from core.enums.CategoryEnum import CategoryEnum

logger = logging.getLogger(__name__)


# ============================================================================
# Enums and Data Classes
# ============================================================================

class ItemType(Enum):
    """Types of tree items."""
    MOD = auto()
    COMPONENT_STD = auto()
    COMPONENT_MUC = auto()
    COMPONENT_SUB = auto()
    MUC_OPTION = auto()
    SUB_PROMPT = auto()
    SUB_PROMPT_OPTION = auto()


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
        return bool(
            self.text or self.game or self.category
            or self.authors or self.languages
        )

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
        mod = index.data(ROLE_MOD)
        if not mod:
            return False
        return mod.supports_game(self._criteria.game)

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

        # Configuration
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

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
        """
        if not self.has_active_filters():
            return True

        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)

        # Check if this item matches
        if self._filter_engine.matches_item(index):
            return True

        # Check if any child matches (recursive)
        if self._has_matching_child(index):
            return True

        # Check if any parent matches
        if self._has_matching_parent(source_parent):
            return True

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

    def _has_matching_parent(self, index: QModelIndex) -> bool:
        """Check if any parent matches filters."""
        current = index
        while current.isValid():
            if self._filter_engine.matches_item(current):
                return True
            current = current.parent()
        return False


# ============================================================================
# Base Tree Item with Type Information
# ============================================================================

class BaseTreeItem(QStandardItem):
    """Base class for all tree items with type information."""

    def __init__(self, item_type: ItemType, text: str = ""):
        super().__init__(text)
        self._item_type = item_type

    def get_item_type(self) -> ItemType:
        """Get the type of this item."""
        return self._item_type

    def get_selected_component(self) -> Optional[Any]:
        """Get selected component data. Override in subclasses."""
        return None


# ============================================================================
# Specific Item Types
# ============================================================================

class ModTreeItem(BaseTreeItem):
    """Tree item representing a mod."""

    def __init__(self, mod):
        super().__init__(ItemType.MOD, mod.name)
        self.setFlags(
            self.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate |
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.setCheckState(Qt.CheckState.Unchecked)
        self.setData(mod, ROLE_MOD)


class StdTreeItem(BaseTreeItem):
    """Tree item for standard (STD) components."""

    def __init__(self, mod, component):
        super().__init__(ItemType.COMPONENT_STD)
        self.setFlags(
            self.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.setCheckState(Qt.CheckState.Unchecked)
        self.setData(mod, ROLE_MOD)
        self.setData(component, ROLE_COMPONENT)

    def get_selected_component(self) -> Optional[str]:
        """Return component key if checked."""
        if self.checkState() == Qt.CheckState.Checked:
            component = self.data(ROLE_COMPONENT)
            return component.key
        return None


class MucTreeItem(BaseTreeItem):
    """Tree item for mutually exclusive choice (MUC) components."""

    def __init__(self, mod, component):
        super().__init__(ItemType.COMPONENT_MUC)
        self.setFlags(
            self.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.setCheckState(Qt.CheckState.Unchecked)
        self.setData(mod, ROLE_MOD)
        self.setData(component, ROLE_COMPONENT)

    def get_selected_component(self) -> Optional[str]:
        """Return selected option key."""
        for row in range(self.rowCount()):
            option_item = self.child(row, 0)
            if option_item.checkState() == Qt.CheckState.Checked:
                return option_item.data(ROLE_OPTION_KEY)
        return None


class SubTreeItem(BaseTreeItem):
    """Tree item for sub-components with prompts."""

    def __init__(self, mod, component):
        super().__init__(ItemType.COMPONENT_SUB)
        self.setFlags(
            self.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.setCheckState(Qt.CheckState.Unchecked)
        self.setData(mod, ROLE_MOD)
        self.setData(component, ROLE_COMPONENT)

    def get_selected_component(self) -> Optional[dict[str, Any]]:
        """Return selected prompts dictionary."""
        if self.checkState() != Qt.CheckState.Checked:
            return None

        component = self.data(ROLE_COMPONENT)
        prompts = {}

        for row in range(self.rowCount()):
            prompt_item = self.child(row, 0)
            if prompt_item.checkState() != Qt.CheckState.Checked:
                continue

            prompt_key = prompt_item.data(ROLE_PROMPT_KEY).key

            # Find selected option in this prompt
            for option_row in range(prompt_item.rowCount()):
                option_item = prompt_item.child(option_row, 0)
                if option_item.checkState() == Qt.CheckState.Checked:
                    prompts[prompt_key] = option_item.data(ROLE_OPTION_KEY)
                    break

        return {
            "key": component.key,
            "prompts": prompts
        } if prompts else None


class MucOptionTreeItem(BaseTreeItem):
    """Tree item for MUC component options."""

    def __init__(self, mod, component, option_key: str, is_default: bool):
        super().__init__(ItemType.MUC_OPTION)
        self.setFlags(
            self.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.setCheckState(Qt.CheckState.Unchecked)
        self.setData(mod, ROLE_MOD)
        self.setData(component, ROLE_COMPONENT)
        self.setData(option_key, ROLE_OPTION_KEY)
        self.setData(is_default, ROLE_IS_DEFAULT)


class PromptTreeItem(BaseTreeItem):
    """Tree item for SUB component prompts."""

    def __init__(self, mod, component, prompt):
        super().__init__(ItemType.SUB_PROMPT)
        self.setFlags(
            self.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.setCheckState(Qt.CheckState.Unchecked)
        self.setData(mod, ROLE_MOD)
        self.setData(component, ROLE_COMPONENT)
        self.setData(prompt, ROLE_PROMPT_KEY)


class PromptOptionTreeItem(BaseTreeItem):
    """Tree item for SUB prompt options."""

    def __init__(self, mod, component, prompt, option_key: str, is_default: bool):
        super().__init__(ItemType.SUB_PROMPT_OPTION)
        self.setFlags(
            self.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.setCheckState(Qt.CheckState.Unchecked)
        self.setData(mod, ROLE_MOD)
        self.setData(component, ROLE_COMPONENT)
        self.setData(prompt, ROLE_PROMPT_KEY)
        self.setData(option_key, ROLE_OPTION_KEY)
        self.setData(is_default, ROLE_IS_DEFAULT)


# ============================================================================
# Radio Tree Model
# ============================================================================

class RadioTreeModel(QStandardItemModel):
    """Model with automatic radio button behavior for mutually exclusive groups."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._radio_parents: list[QStandardItem] = []
        self._updating = False
        self.itemChanged.connect(self._on_item_changed)

    def set_radio_mode(self, parent_item: QStandardItem) -> None:
        """Enable radio mode for children of a parent."""
        if parent_item in self._radio_parents:
            return

        self._radio_parents.append(parent_item)

        # Mark all children as radio items
        for row in range(parent_item.rowCount()):
            child = parent_item.child(row, 0)
            child.setData(True, ROLE_RADIO)

    def _on_item_changed(self, item: QStandardItem) -> None:
        """Handle radio behavior when item changes."""
        if self._updating:
            return

        parent = item.parent()
        if not parent or parent not in self._radio_parents:
            return

        if item.checkState() != Qt.CheckState.Checked:
            return

        self._updating = True
        try:
            # Uncheck all siblings
            for row in range(parent.rowCount()):
                sibling = parent.child(row, 0)
                if sibling != item:
                    self.setData(
                        sibling.index(),
                        Qt.CheckState.Unchecked,
                        Qt.ItemDataRole.CheckStateRole
                    )
        finally:
            self._updating = False


# ============================================================================
# Selection State Manager
# ============================================================================

class SelectionStateManager:
    """Manages selection state and update logic separately from UI."""

    def __init__(self, proxy_model: HierarchicalFilterProxyModel):
        self._proxy_model = proxy_model
        self._model = self._proxy_model.sourceModel()
        self._updating = False

    def handle_item_change(self, item: BaseTreeItem) -> Optional[ModTreeItem]:
        """Handle item change and return the affected mod item."""
        if self._updating:
            return None

        self._updating = True
        try:
            return self._handle_item_by_type(item)
        finally:
            self._updating = False

    def _handle_item_by_type(self, item: BaseTreeItem) -> Optional[ModTreeItem]:
        """Route handling based on item type."""
        item_type = item.get_item_type()

        handlers = {
            ItemType.MOD: self._handle_mod_change,
            ItemType.COMPONENT_STD: self._handle_std_component,
            ItemType.COMPONENT_MUC: self._handle_muc_component,
            ItemType.COMPONENT_SUB: self._handle_sub_component,
            ItemType.MUC_OPTION: self._handle_muc_option,
            ItemType.SUB_PROMPT: self._handle_sub_prompt,
            ItemType.SUB_PROMPT_OPTION: self._handle_sub_prompt_option,
        }

        handler = handlers.get(item_type)
        if handler:
            return handler(item)

        return None

    def _handle_mod_change(self, item: ModTreeItem) -> ModTreeItem:
        """Handle mod item check state change."""
        check_state = item.checkState()

        for row in range(item.rowCount()):
            child = item.child(row, 0)

            # Only treat visible children (those who pass the filter)
            child_index = self._model.indexFromItem(child)
            proxy_index = self._proxy_model.mapFromSource(child_index)

            if not proxy_index.isValid():
                # The child is filtered, do not modify it.
                continue

            # Don't cascade blindly - handle each child according to its type
            if isinstance(child, BaseTreeItem):
                if check_state == Qt.CheckState.Checked:
                    self._check_component(child)
                else:
                    self._uncheck_component(child)

        return item

    def _check_component(self, component: BaseTreeItem) -> None:
        """Check a component according to its type."""
        item_type = component.get_item_type()

        # Set component as checked
        self._model.setData(
            component.index(),
            Qt.CheckState.Checked,
            Qt.ItemDataRole.CheckStateRole
        )

        # Handle children based on type
        if item_type == ItemType.COMPONENT_MUC:
            # MUC: ensure exactly one option is checked
            self._ensure_one_child_checked(component)

        elif item_type == ItemType.COMPONENT_SUB:
            # SUB: check all prompts and ensure each has one option
            for row in range(component.rowCount()):
                prompt_item = component.child(row, 0)
                self._model.setData(
                    prompt_item.index(),
                    Qt.CheckState.Checked,
                    Qt.ItemDataRole.CheckStateRole
                )
                self._ensure_one_child_checked(prompt_item)

        # STD components have no children, nothing to do

    def _uncheck_component(self, component: BaseTreeItem) -> None:
        """Uncheck a component according to its type."""
        self._model.setData(
            component.index(),
            Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole
        )

        # Uncheck all children recursively
        if component.rowCount() > 0:
            self._uncheck_all_children_recursive(component)

    def _handle_std_component(self, item: StdTreeItem) -> Optional[ModTreeItem]:
        """Handle standard component change."""
        return self._update_parent_chain(item)

    def _handle_muc_component(self, item: MucTreeItem) -> Optional[ModTreeItem]:
        """Handle MUC component change."""
        check_state = item.checkState()

        if check_state == Qt.CheckState.Checked:
            # Ensure one option is checked
            self._ensure_one_child_checked(item)
        else:
            # Uncheck all options
            self._uncheck_all_children(item)

        return self._update_parent_chain(item)

    def _handle_sub_component(self, item: SubTreeItem) -> Optional[ModTreeItem]:
        """Handle SUB component change."""
        check_state = item.checkState()

        if check_state == Qt.CheckState.Checked:
            # Check all prompts and ensure each has one option selected
            for row in range(item.rowCount()):
                prompt_item = item.child(row, 0)
                prompt_item.setCheckState(Qt.CheckState.Checked)
                self._ensure_one_child_checked(prompt_item)
        else:
            # Uncheck all prompts and their options
            self._uncheck_all_children_recursive(item)

        return self._update_parent_chain(item)

    def _handle_muc_option(self, item: MucOptionTreeItem) -> Optional[ModTreeItem]:
        """Handle MUC option change."""
        parent = item.parent()
        if parent:
            self.update_check_state(parent)
            return self._update_parent_chain(parent)
        return None

    def _handle_sub_prompt(self, item: PromptTreeItem) -> Optional[ModTreeItem]:
        """Handle SUB prompt change."""
        sub_parent = self._find_parent_by_type(item, ItemType.COMPONENT_SUB)
        if not sub_parent:
            return None

        # Prevent unchecking if SUB is active
        if sub_parent.checkState() == Qt.CheckState.Checked:
            if item.checkState() == Qt.CheckState.Unchecked:
                item.setCheckState(Qt.CheckState.Checked)
                return None

        # If checking, ensure one option is selected
        if item.checkState() == Qt.CheckState.Checked:
            self._ensure_one_child_checked(item)
            sub_parent.setCheckState(Qt.CheckState.Checked)

        return self._update_parent_chain(sub_parent)

    def _handle_sub_prompt_option(self, item: PromptOptionTreeItem) -> Optional[ModTreeItem]:
        """Handle SUB prompt option change."""
        prompt_parent = item.parent()
        if not prompt_parent:
            return None

        # Prevent unchecking the only active option
        if item.checkState() == Qt.CheckState.Unchecked:
            if not self._has_checked_sibling(item):
                item.setCheckState(Qt.CheckState.Checked)
                return None

        # Trigger SUB component logic
        sub_parent = self._find_parent_by_type(item, ItemType.COMPONENT_SUB)
        if sub_parent:
            prompt_parent.setCheckState(Qt.CheckState.Checked)
            sub_parent.setCheckState(Qt.CheckState.Checked)
            return self._update_parent_chain(sub_parent)

        return None

    # ========================================
    # Helper Methods
    # ========================================

    def _ensure_one_child_checked(self, parent: QStandardItem) -> None:
        """Ensure at least one child is checked."""
        has_checked = any(
            parent.child(row, 0).checkState() == Qt.CheckState.Checked
            for row in range(parent.rowCount())
        )

        if not has_checked:
            default_idx = self._get_default_child_index(parent)
            parent.child(default_idx, 0).setCheckState(Qt.CheckState.Checked)

    def _uncheck_all_children(self, parent: QStandardItem) -> None:
        """Uncheck all direct children."""
        for row in range(parent.rowCount()):
            parent.child(row, 0).setCheckState(Qt.CheckState.Unchecked)

    def _uncheck_all_children_recursive(self, parent: QStandardItem) -> None:
        """Recursively uncheck all children."""
        for row in range(parent.rowCount()):
            child = parent.child(row, 0)
            child.setCheckState(Qt.CheckState.Unchecked)
            if child.rowCount() > 0:
                self._uncheck_all_children_recursive(child)

    def update_check_state(self, parent: QStandardItem) -> None:
        """Update parent check state based on children."""
        if parent.rowCount() == 0:
            return

        checked_count = sum(
            1 for row in range(parent.rowCount())
            if parent.child(row, 0).checkState() == Qt.CheckState.Checked
        )

        total = parent.rowCount()

        if checked_count == 0:
            parent.setCheckState(Qt.CheckState.Unchecked)
        elif checked_count == total or not isinstance(parent, ModTreeItem):
            parent.setCheckState(Qt.CheckState.Checked)
        else:
            parent.setCheckState(Qt.CheckState.PartiallyChecked)

    def _update_parent_chain(self, item: QStandardItem) -> Optional[ModTreeItem]:
        """Update check state up the parent chain."""
        current = item
        mod_item = None

        while current:
            if isinstance(current, ModTreeItem):
                mod_item = current
                break

            parent = current.parent()
            if parent:
                self.update_check_state(parent)
            current = parent

        return mod_item

    def _get_default_child_index(self, parent: QStandardItem) -> int:
        """Get index of default child.

        Returns:
            Index of child marked as default, or 0 if none found.
        """
        for row in range(parent.rowCount()):
            child = parent.child(row, 0)
            if child and child.data(ROLE_IS_DEFAULT):
                return row

        # No default found, return first item
        return 0

    def _has_checked_sibling(self, item: QStandardItem) -> bool:
        """Check if item has any checked siblings."""
        parent = item.parent()
        if not parent:
            return False

        for row in range(parent.rowCount()):
            sibling = parent.child(row, 0)
            if sibling != item and sibling.checkState() == Qt.CheckState.Checked:
                return True

        return False

    def _find_parent_by_type(self, item: QStandardItem, item_type: ItemType) -> Optional[BaseTreeItem]:
        """Find parent of specific type."""
        current = item.parent()
        while current:
            if isinstance(current, BaseTreeItem) and current.get_item_type() == item_type:
                return current
            current = current.parent()
        return None


# ============================================================================
# Main Component Selector Widget
# ============================================================================

class ComponentSelector(QTreeView):
    """Hierarchical widget for mod and component selection."""

    def __init__(self, mod_manager: ModManager, parent=None):
        super().__init__(parent)
        self._mod_manager = mod_manager
        self._selection_manager = None

        # Setup
        self._setup_model()
        self._setup_ui()
        self._load_data()
        self.setColumnWidth(0, 400)
        self._configure_header()

        logger.info("ComponentSelector initialized")

    # ========================================
    # Initialization
    # ========================================

    def _setup_model(self) -> None:
        """Configure model and proxy."""
        self._model = RadioTreeModel()

        self._proxy_model = HierarchicalFilterProxyModel()
        self._proxy_model.setSourceModel(self._model)
        self._proxy_model.setFilterKeyColumn(-1)
        self._proxy_model.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy_model.sort(0, Qt.SortOrder.AscendingOrder)

        self._selection_manager = SelectionStateManager(self._proxy_model)

        self.setModel(self._proxy_model)
        self._model.itemChanged.connect(self._on_item_changed)

    def _setup_ui(self) -> None:
        """Configure UI."""
        self.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.setExpandsOnDoubleClick(True)
        # self.clicked.connect(self._on_item_clicked)

    def _configure_header(self) -> None:
        """Configure tree view header."""
        header = self.header()
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setSectionsMovable(False)
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

    # ========================================
    # Data Loading
    # ========================================

    def _load_data(self) -> None:
        """Load mods and components into tree."""
        self._model.clear()

        for mod_id, mod in self._mod_manager.get_all_mods().items():
            mod_item = ModTreeItem(mod)
            status_item = self._create_status_item()

            # Add components
            for component in mod.get_all_components():
                comp_item = self._create_component_item(mod, component)
                mod_item.appendRow([comp_item, QStandardItem("")])

            self._update_mod_status(mod_item)
            self._model.appendRow([mod_item, status_item])

    def _create_component_item(self, mod, component) -> BaseTreeItem:
        """Create appropriate item type for component."""
        if component.is_muc():
            item = MucTreeItem(mod, component)
            self._add_muc_options(item, mod, component)
            self._model.set_radio_mode(item)
        elif component.is_sub():
            item = SubTreeItem(mod, component)
            self._add_sub_prompts(item, mod, component)
        else:
            item = StdTreeItem(mod, component)

        return item

    def _add_muc_options(self, parent: MucTreeItem, mod, component) -> None:
        """Add options for MUC component.

        Selects the default option if specified, otherwise selects first option.
        """
        options = list(component.get_all_options())

        # Normalize default value to string for comparison
        default_value = None
        if component.default is not None:
            default_value = str(component.default)

        # Check if default exists in options
        default_exists = False
        if default_value:
            default_exists = any(str(opt) == default_value for opt in options)
            if not default_exists:
                logger.warning(
                    f"Default value '{default_value}' not found in options "
                    f"for MUC component {component.key}. Using first option."
                )

        for idx, option_key in enumerate(options):
            option_key_str = str(option_key)

            # Determine if this is the default
            if default_value and default_exists:
                is_default = (option_key_str == default_value)
            else:
                # No valid default → use first element
                is_default = (idx == 0)

            option_item = MucOptionTreeItem(
                mod, component, option_key, is_default
            )
            parent.appendRow([option_item, QStandardItem("")])

            if is_default:
                logger.debug(
                    f"MUC default option: {option_key_str} "
                    f"for component {component.key}"
                )

    def _add_sub_prompts(self, parent: SubTreeItem, mod, component) -> None:
        """Add prompts for SUB component."""
        for prompt_key in component.get_all_prompts():
            prompt = component.get_prompt(prompt_key)
            prompt_item = PromptTreeItem(mod, component, prompt)

            # Add options to prompt
            for option_key in prompt.options:
                option_item = PromptOptionTreeItem(
                    mod, component, prompt, option_key,
                    option_key == prompt.default
                )
                prompt_item.appendRow([option_item, QStandardItem("")])

            self._model.set_radio_mode(prompt_item)
            parent.appendRow([prompt_item, QStandardItem("")])

    def _create_status_item(self) -> QStandardItem:
        """Create status column item."""
        item = QStandardItem(tr("widget.component_selector.selection.none"))
        item.setForeground(QColor(COLOR_STATUS_NONE))
        return item

    # ========================================
    # Event Handlers
    # ========================================

    def _on_item_changed(self, item: QStandardItem) -> None:
        """Handle item check state change."""
        if not isinstance(item, BaseTreeItem):
            return

        self._model.blockSignals(True)
        try:
            mod_item = self._selection_manager.handle_item_change(item)
            if mod_item:
                self._update_mod_status(mod_item)
        finally:
            self._model.blockSignals(False)

        self.style().unpolish(self)
        self.style().polish(self)

        logger.debug(f"Selection changed: {self.get_selected_items()}")

    def _on_item_clicked(self, index: QModelIndex) -> None:
        """
        Handle item click to toggle expansion without interfering with checkbox.
        
        Expands or collapses tree branches on click, but only if the click is
        outside the checkbox area. This allows natural checkbox toggling while
        providing easy branch expansion.
        
        Args:
            index: Model index of the clicked item
        """
        source_index = self._proxy_model.mapToSource(index)
        item = self._model.itemFromIndex(source_index)

        # Ignore items without children (cannot be expanded)
        if not item or item.rowCount() == 0:
            return

        # Get visual position of the clicked row
        rect = self.visualRect(index)
        click_pos = self.viewport().mapFromGlobal(QCursor.pos())

        # Define checkbox interaction zone
        checkbox_zone = rect.left() + 25

        # Si on clique après la case, on replie/déplie
        if click_pos.x() > checkbox_zone:
            is_expanded = self.isExpanded(index)
            self.setExpanded(index, not is_expanded)

    # ========================================
    # Status Updates
    # ========================================

    def _update_mod_status(self, mod_item: ModTreeItem) -> None:
        """Update status column for mod."""
        total = mod_item.rowCount()
        if total == 0:
            return

        checked_count = sum(
            1 for row in range(total)
            if mod_item.child(row, 0).checkState() == Qt.CheckState.Checked
        )

        # Determine status
        if checked_count == 0:
            status_text = tr("widget.component_selector.selection.none")
            color = QColor(COLOR_STATUS_NONE)
        elif checked_count == total:
            status_text = tr(
                "widget.component_selector.selection.complete",
                count=checked_count,
                total=total
            )
            color = QColor(COLOR_STATUS_COMPLETE)
        else:
            status_text = tr(
                "widget.component_selector.selection.partial",
                count=checked_count,
                total=total
            )
            color = QColor(COLOR_STATUS_PARTIAL)

        # Update status item
        status_item = self._get_status_item(mod_item)
        if status_item:
            status_item.setText(status_text)
            status_item.setForeground(color)
            pass

    def _get_status_item(self, mod_item: ModTreeItem) -> Optional[QStandardItem]:
        """Get status item (column 1) for mod."""
        parent = mod_item.parent()
        if parent:
            return parent.child(mod_item.row(), 1)
        return self._model.item(mod_item.row(), 1)

    # ========================================
    # Filtering API
    # ========================================

    def apply_filters(
            self,
            text: str = "",
            game: str = "",
            category: str = "",
            authors: Optional[set[str]] = None,
            languages: Optional[set[str]] = None
    ) -> None:
        """Apply all filters at once for better performance."""
        criteria = FilterCriteria(
            text=text,
            game=game,
            category=category,
            authors=authors or set(),
            languages=languages or set()
        )
        self._proxy_model.set_filter_criteria(criteria)

    def clear_filters(self) -> None:
        """Clear all filters."""
        self._proxy_model.clear_all_filters()

    def expand_filtered_results(self) -> None:
        """Expand all nodes matching filters."""
        if self._proxy_model.has_active_filters():
            self.expandAll()
        else:
            self.collapseAll()

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
            languages=criteria.languages.copy()
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

    def _item_or_children_match(
            self,
            item: QStandardItem,
            engine: FilterEngine
    ) -> bool:
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
    # Selection API
    # ========================================

    def get_selected_items(self) -> dict[str, list[Any]]:
        """Get all selected components organized by mod ID."""
        selected = {}

        root = self._model.invisibleRootItem()
        for row in range(root.rowCount()):
            mod_item = root.child(row, 0)
            if not isinstance(mod_item, ModTreeItem):
                continue

            mod = mod_item.data(ROLE_MOD)
            components = []

            for comp_row in range(mod_item.rowCount()):
                comp_item = mod_item.child(comp_row, 0)
                if isinstance(comp_item, BaseTreeItem):
                    selected_comp = comp_item.get_selected_component()
                    if selected_comp:
                        components.append(selected_comp)

            if components:
                selected[mod.id] = components

        return selected

    def has_selection(self) -> bool:
        """Check if at least one component is selected."""
        return len(self.get_selected_items()) > 0

    def restore_selection(self, selected_items: dict[str, list[Any]]) -> None:
        """Restore component selections from saved state.

        Signals are blocked during the restore to
        avoid unwanted cascades.

        Args:
            selected_items: Dict mapping mod IDs to lists of selected components
        """
        if not selected_items:
            return

        logger.info(f"Restoring selection for {len(selected_items)} mods")

        self._updating = True
        self._model.blockSignals(True)

        try:
            root = self._model.invisibleRootItem()

            for i in range(root.rowCount()):
                mod_item = root.child(i, 0)
                mod = mod_item.data(ROLE_MOD)

                if mod.id not in selected_items:
                    continue
                mod_item.setCheckState(Qt.CheckState.Checked)

                # saved_components is a LIST, not a dict
                saved_components = selected_items[mod.id]

                for j in range(mod_item.rowCount()):
                    comp_item = mod_item.child(j, 0)
                    component = comp_item.data(ROLE_COMPONENT)

                    # Find if this component is in the saved selection
                    matched_selection = self._find_matching_selection(
                        cast(BaseTreeItem, comp_item), component, saved_components
                    )

                    if matched_selection is None:
                        continue

                    # ========== STD COMPONENT ==========
                    if isinstance(comp_item, StdTreeItem):
                        comp_item.setCheckState(Qt.CheckState.Checked)

                    # ========== MUC COMPONENT ==========
                    elif isinstance(comp_item, MucTreeItem):
                        # Check the MUC component itself
                        comp_item.setCheckState(Qt.CheckState.Checked)

                        # matched_selection is the option key
                        selected_option_key = str(matched_selection)

                        for k in range(comp_item.rowCount()):
                            opt = comp_item.child(k, 0)
                            option_key = str(opt.data(ROLE_OPTION_KEY))

                            if option_key == selected_option_key:
                                opt.setCheckState(Qt.CheckState.Checked)
                            else:
                                opt.setCheckState(Qt.CheckState.Unchecked)

                    # ========== SUB COMPONENT ==========
                    elif isinstance(comp_item, SubTreeItem):
                        # matched_selection is a dict with "key" and "prompts"
                        comp_item.setCheckState(Qt.CheckState.Checked)

                        saved_prompts = matched_selection.get("prompts", {})

                        for p in range(comp_item.rowCount()):
                            prompt_item = comp_item.child(p, 0)
                            prompt = prompt_item.data(ROLE_PROMPT_KEY)

                            if prompt.key not in saved_prompts:
                                continue

                            # Check the prompt
                            prompt_item.setCheckState(Qt.CheckState.Checked)

                            selected_option_key = str(saved_prompts[prompt.key])

                            # Select the matching option
                            for q in range(prompt_item.rowCount()):
                                opt = prompt_item.child(q, 0)
                                option_key = str(opt.data(ROLE_OPTION_KEY))

                                if option_key == selected_option_key:
                                    opt.setCheckState(Qt.CheckState.Checked)
                                else:
                                    opt.setCheckState(Qt.CheckState.Unchecked)
                self._selection_manager.update_check_state(mod_item)
                self._update_mod_status(cast(ModTreeItem, mod_item))

        finally:
            self._model.blockSignals(False)
            self._updating = False
            self.style().unpolish(self)
            self.style().polish(self)

        logger.info("Selection restored successfully")

    def _find_matching_selection(
            self,
            comp_item: BaseTreeItem,
            component,
            saved_components: list[Any]
    ) -> Optional[Any]:
        """Find the saved selection data for a component.

        Args:
            comp_item: Component tree item
            component: Component data object
            saved_components: List of saved selections for this mod

        Returns:
            Matching selection data, or None if not found
            - For STD: component.key (str)
            - For MUC: selected option key (str)
            - For SUB: dict with "key" and "prompts"
        """
        for saved in saved_components:
            # STD: saved is a simple string matching component.key
            if isinstance(comp_item, StdTreeItem):
                if saved == component.key:
                    return saved

            # MUC: saved is a string (the selected option key)
            # We need to check if this string is one of the MUC's options
            elif isinstance(comp_item, MucTreeItem):
                if isinstance(saved, str):
                    # Check if this option exists in the MUC
                    for k in range(comp_item.rowCount()):
                        opt = comp_item.child(k, 0)
                        if str(opt.data(ROLE_OPTION_KEY)) == str(saved):
                            return saved

            # SUB: saved is a dict with "key" field
            elif isinstance(comp_item, SubTreeItem):
                if isinstance(saved, dict) and saved.get("key") == component.key:
                    return saved

        return None

    # ========================================
    # Translation Support
    # ========================================

    def retranslate_ui(self) -> None:
        """Update UI text after language change."""
        self._model.setHorizontalHeaderLabels([
            tr("widget.component_selector.header.type"),
            tr("widget.component_selector.header.selection")
        ])

        # Block signals during update
        self._model.blockSignals(True)
        try:
            self._retranslate_tree_items()
        finally:
            self._model.blockSignals(False)

    def _retranslate_tree_items(self) -> None:
        """Recursively retranslate all tree items."""

        def retranslate_item(item: QStandardItem) -> None:
            if not isinstance(item, BaseTreeItem):
                return

            mod = item.data(ROLE_MOD)
            component = item.data(ROLE_COMPONENT)

            # Refresh mod data to get new translations
            if mod:
                mod = self._mod_manager.get_mod_by_id(mod.id)
                item.setData(mod, ROLE_MOD)

            # Update text based on item type
            item_type = item.get_item_type()

            if item_type == ItemType.MOD:
                self._update_mod_status(cast(ModTreeItem, item))

            elif item_type in (ItemType.COMPONENT_STD, ItemType.COMPONENT_SUB):
                if component:
                    text = f"[{component.key}] {mod.get_component_text(component.key)}"
                    item.setText(text)

            elif item_type == ItemType.COMPONENT_MUC:
                if component:
                    item.setText(mod.get_component_text(component.key))

            elif item_type == ItemType.MUC_OPTION:
                option_key = item.data(ROLE_OPTION_KEY)
                if option_key:
                    text = f"[{option_key}] {mod.get_component_text(option_key)}"
                    item.setText(text)

            elif item_type == ItemType.SUB_PROMPT:
                prompt = item.data(ROLE_PROMPT_KEY)
                if prompt and component:
                    text = f"[{component.key}.{prompt.key}] {component.get_prompt_text(prompt.key)}"
                    item.setText(text)

            elif item_type == ItemType.SUB_PROMPT_OPTION:
                prompt = item.data(ROLE_PROMPT_KEY)
                option_key = item.data(ROLE_OPTION_KEY)
                if prompt and option_key and component:
                    text = f"[{component.key}.{prompt.key}.{option_key}] " \
                           f"{component.get_prompt_option_text(prompt.key, option_key)}"
                    item.setText(text)

            # Recursively process children
            for row in range(item.rowCount()):
                child = item.child(row, 0)
                if child:
                    retranslate_item(child)

        # Start from root
        root = self._model.invisibleRootItem()
        for row in range(root.rowCount()):
            item = root.child(row, 0)
            if item:
                retranslate_item(item)
