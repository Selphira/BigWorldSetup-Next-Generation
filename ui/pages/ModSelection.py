"""
ModSelection - Page for selecting mods and components with filtering capabilities.

This module provides the main selection interface with category navigation,
search functionality, and hierarchical component selection.
"""

import logging
from typing import cast

from PySide6.QtCore import QEvent, QModelIndex, Qt, QTimer
from PySide6.QtGui import QAction, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from constants import (
    COLOR_BACKGROUND_HIGHLIGHT,
    COLOR_TEXT_HIGHLIGHT,
    FLAGS_DIR,
    MARGIN_SMALL,
    MARGIN_STANDARD,
    MAX_SEARCH_LENGTH,
    MIN_SEARCH_LENGTH,
    ROLE_MOD,
    SEARCH_DEBOUNCE_DELAY,
    SPACING_LARGE,
    SPACING_MEDIUM,
    SPACING_SMALL,
)
from core.ComponentReference import ComponentReference
from core.enums.CategoryEnum import CategoryEnum
from core.ModManager import ModManager
from core.RuleManager import RuleManager
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.ValidationOrchestrator import ValidationOrchestrator
from core.WeiDULogParser import WeiDULogParser
from ui.pages.BasePage import BasePage, ButtonConfig
from ui.pages.mod_selection.ComponentSelector import BaseTreeItem, ComponentSelector
from ui.pages.mod_selection.ViolationPanel import ViolationPanel
from ui.widgets.CategoryButton import CategoryButton
from ui.widgets.ModDetailsPanel import ModDetailsPanel
from ui.widgets.MultiSelectComboBox import MultiSelectComboBox

logger = logging.getLogger(__name__)


# ============================================================================
# Highlight Delegate
# ============================================================================


class HighlightDelegate(QStyledItemDelegate):
    """Delegate that highlights search text in tree view items.

    Features:
    - Highlights matching text with yellow background
    - Shows tooltip for truncated text
    """

    # Rendering constants
    TEXT_HORIZONTAL_OFFSET = 27  # Space for checkbox/icon

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_text = ""

    def set_search_text(self, text: str) -> None:
        """Set text to highlight."""
        self._search_text = text.lower().strip()

    def get_search_text(self) -> str:
        """Get current search text."""
        return self._search_text

    # ========================================
    # Tooltip Support
    # ========================================

    def helpEvent(
        self, event: QEvent, view: QWidget, option: QStyleOptionViewItem, index: QModelIndex
    ) -> bool:
        """Show tooltip if text is truncated."""
        if event.type() != QEvent.Type.ToolTip or not index.isValid():
            return False

        text = index.data()
        if not text:
            return False

        # Check if text is truncated
        if option.fontMetrics.horizontalAdvance(text) > option.rect.width():
            QToolTip.showText(event.globalPos(), text, view)
            return True

        return False

    # ========================================
    # Custom Painting
    # ========================================

    def paint(self, painter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Paint item with search text highlighting."""
        # Remove focus outline
        option.state &= ~QStyle.StateFlag.State_HasFocus

        # No search active - use default rendering
        if not self._search_text:
            super().paint(painter, option, index)
            return

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            super().paint(painter, option, index)
            return

        # Find search text position
        search_pos = text.lower().find(self._search_text)

        # Search text not found - use default rendering
        if search_pos == -1:
            super().paint(painter, option, index)
            return

        # Render with highlighting
        self._paint_with_highlight(painter, option, index, text, search_pos)

    def _paint_with_highlight(
        self,
        painter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
        text: str,
        search_pos: int,
    ) -> None:
        """Paint item with highlighted search text."""
        style = option.widget.style() if option.widget else QStyle()

        # Draw background, checkbox, icon (but not text)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.features = opt.features & ~QStyleOptionViewItem.ViewItemFeature.HasDisplay
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, option.widget)

        # Get text area rectangle
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, option, option.widget
        )

        # Create HTML with highlighting
        html = self._create_highlighted_html(text, search_pos, len(self._search_text), option)

        # Render HTML text
        self._render_html_text(painter, text_rect, html, option)

    def _create_highlighted_html(
        self, text: str, match_start: int, match_length: int, option: QStyleOptionViewItem
    ) -> str:
        """Create HTML with highlighted match."""
        before = text[:match_start]
        match = text[match_start : match_start + match_length]
        after = text[match_start + match_length :]

        # Get text color based on selection state
        if option.state & QStyle.StateFlag.State_Selected:
            text_color = option.palette.highlightedText().color().name()
        else:
            text_color = option.palette.text().color().name()

        return (
            f'<span style="color: {text_color};">{before}</span>'
            f'<span style="background-color: {COLOR_BACKGROUND_HIGHLIGHT}; '
            f'color: {COLOR_TEXT_HIGHLIGHT}; font-weight: bold;">{match}</span>'
            f'<span style="color: {text_color};">{after}</span>'
        )

    def _render_html_text(
        self, painter, text_rect, html: str, option: QStyleOptionViewItem
    ) -> None:
        """Render HTML text in the text rectangle."""
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(html)
        doc.setTextWidth(text_rect.width())
        doc.setDocumentMargin(0)

        painter.save()

        # Position in text area
        painter.translate(text_rect.topLeft())

        # Vertical centering
        font_metrics = option.fontMetrics
        available_height = text_rect.height()
        font_height = font_metrics.height()
        vertical_offset = (
            (available_height - font_height) / 2 + font_metrics.ascent() - font_metrics.ascent()
        )

        painter.translate(self.TEXT_HORIZONTAL_OFFSET, vertical_offset)

        # Clip to prevent overflow
        painter.setClipRect(0, 0, text_rect.width(), text_rect.height())

        doc.drawContents(painter)

        painter.restore()


# ============================================================================
# Mod Selection Page
# ============================================================================


class ModSelectionPage(BasePage):
    """Main page for mod and component selection.

    Features:
    - Category-based filtering
    - Text search with highlighting
    - Hierarchical component selection
    - Real-time statistics
    - Collapsible mod details panel
    """

    # Layout constants
    LEFT_PANEL_WIDTH = 300

    def __init__(self, state_manager: StateManager) -> None:
        super().__init__(state_manager)

        self._mod_manager: ModManager = self.state_manager.get_mod_manager()
        self._rule_manager: RuleManager = self.state_manager.get_rule_manager()
        self._category_buttons: dict[CategoryEnum, CategoryButton] = {}
        self._current_category = CategoryEnum.ALL
        self._weidu_parser: WeiDULogParser = WeiDULogParser()
        self._orchestrator = ValidationOrchestrator(self._rule_manager)

        # Search debouncing
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search_filter)

        self._chk_ignore_warnings: QCheckBox | None = None
        self._chk_ignore_errors: QCheckBox | None = None
        self._chk_show_violations: QCheckBox | None = None
        self._violation_panel: ViolationPanel | None = None

        # Additional buttons
        self._btn_import: QToolButton | None = None
        self._btn_export: QPushButton | None = None
        self._btn_deselect_all: QPushButton | None = None

        # Build UI
        self._create_widgets()
        self._create_additional_buttons()

        self._orchestrator.set_selection_manager(self._component_selector._selection_manager)
        self._component_selector.set_orchestrator(self._orchestrator)
        self._violation_panel.set_orchestrator(self._orchestrator)

        self._component_selector._model.itemChanged.connect(self._on_selection_validation)
        self._component_selector.selectionModel().currentChanged.connect(
            self._on_component_selection_changed
        )

        self._validation_timer = QTimer()
        self._validation_timer.setSingleShot(True)
        self._validation_timer.timeout.connect(self._trigger_validation)

        self._update_statistics()
        logger.info("ModSelectionPage initialized with validation")

    def _on_selection_validation(self) -> None:
        self._validation_timer.stop()
        self._validation_timer.start(100)  # 100ms debounce

    def _trigger_validation(self) -> None:
        if not self._orchestrator:
            return

        logger.debug("=== TRIGGERING VALIDATION ===")

        violations = self._orchestrator.validate_current_selection()

        logger.info(f"Validation complete: {len(violations)} violations found")

        for v in violations:
            logger.debug(f"  - {v.rule.rule_type.value}: {v.affected_components}")

        self._update_selected_component_violations()
        self._component_selector._proxy_model.invalidateFilter()
        self._component_selector.viewport().update()
        self.notify_navigation_changed()

        logger.debug("=== VALIDATION COMPLETE ===")

    def _on_component_selection_changed(self, current, previous) -> None:
        self._update_selected_component_violations()
        self._on_component_changed(current)

    def _update_selected_component_violations(self) -> None:
        # Get current selection
        current_index = self._component_selector.currentIndex()

        if not current_index.isValid():
            self._violation_panel.update_for_reference(None)
            return

        # Get item
        source_index = self._component_selector._proxy_model.mapToSource(current_index)
        item = self._component_selector._model.itemFromIndex(source_index.siblingAtColumn(0))

        if not isinstance(item, BaseTreeItem):
            self._violation_panel.update_for_reference(None)
            return

        try:
            reference = ComponentReference.from_string(item.reference)
            self._violation_panel.update_for_reference(reference)
            logger.debug(f"Updated violation panel for {reference}")
        except ValueError as e:
            logger.warning(f"Invalid reference: {e}")
            self._violation_panel.update_for_reference(None)

    def _on_ignore_warnings_changed(self, state) -> None:
        ignore = state == Qt.CheckState.Checked.value
        self.notify_navigation_changed()
        logger.debug(f"Ignore warnings: {ignore}")

    def _on_ignore_errors_changed(self, state) -> None:
        ignore = state == Qt.CheckState.Checked.value
        self.notify_navigation_changed()
        logger.debug(f"Ignore errors: {ignore}")

    # ========================================
    # Widget Creation
    # ========================================

    def _create_widgets(self) -> None:
        """Create page UI layout."""
        layout = QHBoxLayout(self)
        layout.setSpacing(SPACING_LARGE)
        layout.setContentsMargins(
            MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD
        )

        layout.addWidget(self._create_main_splitter(), stretch=1)

    def _create_main_splitter(self) -> QSplitter:
        """Create main splitter with table and operations panels."""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._create_left_panel())
        splitter.addWidget(self._create_right_splitter())
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 2)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        return splitter

    def _create_left_panel(self) -> QWidget:
        """Create left panel with category buttons."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(0, 0, 10, 0)

        layout.addWidget(self._create_category_panel())
        layout.addWidget(self._create_components_panel())

        return panel

    def _create_category_panel(self) -> QFrame:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setFixedWidth(self.LEFT_PANEL_WIDTH)

        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(0, 0, 10, 0)

        # Title
        self._left_title = self._create_section_title()
        layout.addWidget(self._left_title)

        # Scrollable category list
        scroll = self._create_category_scroll()
        layout.addWidget(scroll)

        return panel

    def _create_category_scroll(self) -> QScrollArea:
        """Create scrollable category list."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 0, 0)

        all_button = self._create_category_button(CategoryEnum.ALL, selected=True)
        layout.addWidget(all_button)

        # Other categories
        for category in CategoryEnum.list_without_all():
            button = self._create_category_button(category)
            layout.addWidget(button)

        scroll.setWidget(scroll_content)
        return scroll

    def _create_category_button(
        self, category: CategoryEnum, selected: bool = False
    ) -> CategoryButton:
        """Create and register a category button."""
        button = CategoryButton(category, 0, selected)
        button.clicked.connect(self._on_category_clicked)
        self._category_buttons[category] = button
        return button

    def _create_components_panel(self) -> QFrame:
        """Create center panel with filters and selector."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_MEDIUM)

        # Search filter
        filters_widget = self._create_filters_section()
        layout.addWidget(filters_widget)

        # Component selector with highlight delegate
        self._component_selector = ComponentSelector(self._mod_manager, self)
        self._highlight_delegate = HighlightDelegate(self._component_selector)
        self._component_selector.setItemDelegate(self._highlight_delegate)
        self._component_selector._model.itemChanged.connect(self._on_selection_changed)
        self._component_selector.selectionModel().currentChanged.connect(
            self._on_component_changed
        )

        layout.addWidget(self._component_selector)

        return panel

    def _create_right_splitter(self) -> QSplitter:
        """Create main splitter with table and operations panels."""
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._create_mod_details_panel())
        splitter.addWidget(self._create_violation_panel())
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 2)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        return splitter

    def _create_mod_details_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(MARGIN_SMALL, 0, 0, 0)

        checkbox_widget = self._create_checkboxs_widget()
        checkbox_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        layout.addWidget(checkbox_widget)

        self._mod_details_title = self._create_section_title()
        layout.addWidget(self._mod_details_title)

        self._details_panel = ModDetailsPanel(self._mod_manager)
        layout.addWidget(self._details_panel)

        return panel

    def _create_violation_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(MARGIN_SMALL, 0, 0, 0)

        self._violation_title = self._create_section_title()
        layout.addWidget(self._violation_title)

        self._violation_panel = ViolationPanel()
        layout.addWidget(self._violation_panel, stretch=1)

        return panel

    def _create_checkboxs_widget(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)
        layout.addStretch()

        self._chk_ignore_warnings = QCheckBox()
        self._chk_ignore_warnings.stateChanged.connect(self._on_ignore_warnings_changed)
        layout.addWidget(self._chk_ignore_warnings)

        self._chk_ignore_errors = QCheckBox()
        self._chk_ignore_errors.stateChanged.connect(self._on_ignore_errors_changed)
        layout.addWidget(self._chk_ignore_errors)

        return container

    def _create_additional_buttons(self):
        """Create additional buttons."""
        # Import selection
        self._btn_import = QToolButton()
        self._btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_import.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        menu = QMenu(self._btn_import)

        self._action_import_file = QAction("", self._btn_import)
        self._action_import_file.triggered.connect(self._import_selection_file)
        self._action_import_weidu = QAction("", self._btn_import)
        self._action_import_weidu.triggered.connect(self._import_selection_weidu)

        menu.addAction(self._action_import_file)
        menu.addAction(self._action_import_weidu)

        self._btn_import.setMenu(menu)
        self._btn_import.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        # Export selection
        self._btn_export = QPushButton()
        self._btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_export.clicked.connect(self._export_selection)

        # Export selection
        self._btn_deselect_all = QPushButton()
        self._btn_deselect_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_deselect_all.clicked.connect(self._deselect_all)

    def _create_filters_section(self) -> QWidget:
        """Create filters section avec recherche, langues ET violations."""
        filters_widget = QWidget()
        filters_layout = QVBoxLayout(filters_widget)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(SPACING_SMALL)

        # Search bar
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(tr("page.selection.search_placeholder"))
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMaxLength(MAX_SEARCH_LENGTH)
        self._search_input.textChanged.connect(self._on_search_changed)
        filters_layout.addWidget(self._search_input)

        sub_filters_layout = QHBoxLayout()
        sub_filters_layout.setContentsMargins(0, 0, 0, 0)
        sub_filters_layout.setSpacing(SPACING_SMALL)

        # Languages
        self._lang_label = QLabel(tr("page.selection.desired_languages"))
        sub_filters_layout.addWidget(self._lang_label)

        self._lang_select = MultiSelectComboBox()
        self._lang_select.setMinimumWidth(100)
        self._lang_select.selection_changed.connect(self._apply_all_filters)
        sub_filters_layout.addWidget(self._lang_select)

        # Spacer
        sub_filters_layout.addStretch()

        self._chk_show_violations = QCheckBox()
        self._chk_show_violations.stateChanged.connect(self._on_violations_filter_changed)
        sub_filters_layout.addWidget(self._chk_show_violations)

        filters_layout.addLayout(sub_filters_layout)

        return filters_widget

    # ========================================
    # Details Panel Management
    # ========================================

    def _on_component_changed(self, index: QModelIndex) -> None:
        """Handle component click to update details panel."""
        # Get source index
        source_index = self._component_selector._proxy_model.mapToSource(index)
        item = self._component_selector._model.itemFromIndex(source_index.siblingAtColumn(0))

        if not item:
            return

        # Get mod from item
        mod = item.data(ROLE_MOD)
        if mod:
            self._details_panel.update_mod(mod)

    # ========================================
    # Event Handlers
    # ========================================

    def _on_category_clicked(self, category: CategoryEnum) -> None:
        """Handle category button click."""
        # Update button states
        for cat, button in self._category_buttons.items():
            button.set_selected(cat == category)

        self._current_category = category
        self._apply_all_filters()

        logger.debug(f"Category selected: {category.value}")

    def _on_search_changed(self, text: str) -> None:
        """Handle search text change with debouncing."""
        self._search_timer.stop()

        # Only apply filter if text is empty or meets minimum length
        if len(text) == 0 or len(text) >= MIN_SEARCH_LENGTH:
            self._search_timer.start(SEARCH_DEBOUNCE_DELAY)

    def _apply_search_filter(self) -> None:
        """Apply search filter after debounce delay."""
        text = self._search_input.text()

        # Update highlight delegate
        self._highlight_delegate.set_search_text(text)

        # Apply all filters
        self._apply_all_filters()

        # Refresh view to show highlights
        self._component_selector.viewport().update()

        logger.debug(f"Search filter applied: '{text}'")

    def _on_selection_changed(self) -> None:
        """Handle component selection change."""
        self.notify_navigation_changed()

    def _deselect_all(self) -> None:
        # Ask for confirmation
        reply = QMessageBox.question(
            self,
            tr("page.selection.deselect_all_confirm_title"),
            tr("page.selection.deselect_all_confirm_message"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self._component_selector.clear_selection()

    def _import_selection_file(self) -> None:
        """Import selection from JSON file."""
        file_path, replace = self._show_import_dialog(
            title=tr("page.selection.import_select_file"),
            name_filter="JSON Files (*.json);;All Files (*.*)",
        )

        if not file_path:
            return

        import json

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                components_to_select = json.load(f)

            self._apply_imported_selection(components_to_select, replace, file_path)

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON file: %s", e)
            QMessageBox.critical(
                self,
                tr("page.selection.import_error_title"),
                tr("page.selection.import_error_invalid_json", error=str(e)),
            )
        except Exception as e:
            logger.error("Error importing selection: %s", e)
            QMessageBox.critical(
                self,
                tr("page.selection.import_error_title"),
                tr("page.selection.import_error_message", error=str(e)),
            )

    def _import_selection_weidu(self) -> None:
        """Import selection from WeiDU.log file."""
        file_path, replace = self._show_import_dialog(
            title=tr("page.selection.import_select_file"),
            name_filter="WeiDU Log (WeiDU.log);;Log Files (*.log)",
        )

        if not file_path:
            return

        try:
            components_to_select = self._weidu_parser.parse_file(file_path).get_component_ids()
            self._apply_imported_selection(components_to_select, replace, file_path)

        except Exception as e:
            logger.error("Error importing WeiDU.log: %s", e)
            QMessageBox.critical(
                self,
                tr("page.selection.import_error_title"),
                tr("page.selection.import_error_weidu", error=str(e)),
            )

    def _show_import_dialog(self, title: str, name_filter: str) -> tuple[str | None, bool]:
        """Show file import dialog with replace checkbox.

        Args:
            title: Dialog window title
            name_filter: File type filter string

        Returns:
            Tuple of (file_path, replace_flag). file_path is None if cancelled.
        """
        dialog = QFileDialog(self)
        dialog.setWindowTitle(title)
        dialog.setNameFilter(name_filter)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)

        checkbox = QCheckBox(tr("page.selection.import_replace_current_selection"))
        checkbox.setChecked(False)

        container = QWidget(dialog)
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.addStretch()
        layout.addWidget(checkbox)

        grid_layout = cast(QGridLayout, dialog.layout())
        row_count = grid_layout.rowCount()
        grid_layout.addWidget(container, row_count, 0, 1, -1)

        if dialog.exec():
            return dialog.selectedFiles()[0], checkbox.isChecked()
        else:
            return None, False

    def _apply_imported_selection(
        self, components_to_select: list[str], replace: bool, file_path: str
    ) -> None:
        """Import avec validation différée."""
        logger.info(f"Importing selection from {file_path}")

        self._orchestrator.enable_validation(False)

        try:
            if replace:
                self._component_selector.clear_selection()

            self._component_selector.restore_selection(components_to_select)

            # Stats
            reference_to_select = [
                reference.lower()
                for reference in components_to_select
                if (
                    (comp_key := reference.partition(":")[2])
                    and "choice_" not in comp_key
                    and comp_key.count(".") == 0
                )
            ]
            reference_selected = self._component_selector.get_selected_components()
            selected_list = list(set(reference_selected) & set(reference_to_select))
            total_selected = len(selected_list)
            total_to_select = len(reference_to_select)

        finally:
            self._orchestrator.enable_validation(True)
            violations = self._orchestrator.validate_current_selection()

        message = tr(
            "page.selection.import_success_message",
            to_select=total_to_select,
            selected=total_selected,
        )

        if violations:
            errors = sum(1 for v in violations if v.is_error)
            warnings = sum(1 for v in violations if v.is_warning)
            message += f"\n\n⚠️ {errors} erreur(s), {warnings} avertissement(s) détecté(s)"

        QMessageBox.information(self, tr("page.selection.import_success_title"), message)

        logger.info(
            f"Import complete: {total_selected} components, {len(violations)} violations"
        )

    def _export_selection(self) -> None:
        """Export current order to JSON file."""
        self.save_state()

        selected_components = self._component_selector.get_selected_components()
        total = len(selected_components)

        # Check if there's any selected components
        if total == 0:
            QMessageBox.warning(
                self,
                tr("page.selection.export_empty_title"),
                tr("page.selection.export_empty_message"),
            )
            return

        # Ask for save location
        selected_game = self.state_manager.get_selected_game()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            tr("page.selection.export_select_file"),
            f"{selected_game}_selection.json",
            "JSON Files (*.json);;All Files (*.*)",
        )

        if not file_path:
            return

        if not file_path.endswith(".json"):
            file_path += ".json"

        try:
            import json

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(selected_components, f, indent=2, ensure_ascii=False)

            QMessageBox.information(
                self,
                tr("page.selection.export_success_title"),
                tr("page.selection.export_success_message", count=total, path=file_path),
            )

            logger.info(f"Exported selection to {file_path}: {total} components")

        except Exception as e:
            logger.error(f"Error exporting order: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                tr("page.selection.export_error_title"),
                tr("page.selection.export_error_message", error=str(e)),
            )

    # ========================================
    # Filtering
    # ========================================

    def _apply_all_filters(self) -> None:
        """Apply all active filters to component selector."""
        # Prepare filter parameters
        text = self._search_input.text()
        game = self.state_manager.get_selected_game()
        category = None

        if self._current_category != CategoryEnum.ALL:
            category = self._current_category.value

        # Apply filters in one operation
        self._component_selector.apply_filters(
            text=text,
            category=category,
            game=game,
            languages=set(self._lang_select.selected_keys()),
        )

        # Expand/collapse based on search
        if text:
            self._component_selector.expandAll()
        else:
            self._component_selector.collapseAll()

        self._update_statistics()

    def _on_violations_filter_changed(self, state) -> None:
        show_only = state == Qt.CheckState.Checked.value

        logger.info(f"Violations filter changed: {show_only}")

        self._component_selector._proxy_model.set_show_violations_only(show_only)

        if show_only:
            self._component_selector.expandAll()
        else:
            self._component_selector.collapseAll()

        self._update_statistics()

    def _update_statistics(self) -> None:
        """Update category counters based on current filters."""
        filtered_counts = self._component_selector.get_filtered_mod_count_by_category()

        for category in CategoryEnum:
            count = filtered_counts.get(category.value, 0)
            self._category_buttons[category].update_count(count)

    # ========================================
    # BasePage Implementation
    # ========================================

    def get_page_id(self) -> str:
        """Get page identifier."""
        return "mod_selection"

    def get_page_title(self) -> str:
        """Get page title."""
        return tr("page.selection.title")

    def get_additional_buttons(self) -> list[QPushButton]:
        """Get additional buttons."""
        return [self._btn_deselect_all, self._btn_import, self._btn_export]

    def get_previous_button_config(self) -> ButtonConfig:
        """Configure previous button."""
        return ButtonConfig(visible=True, enabled=True, text=tr("button.previous"))

    def can_go_to_next_page(self) -> bool:
        """Check if can go to next page."""
        if not self._component_selector.has_selection():
            return False

        if self._orchestrator:
            if self._orchestrator.has_errors() and not self._chk_ignore_errors.isChecked():
                return False

            if self._orchestrator.has_warnings() and not self._chk_ignore_warnings.isChecked():
                return False

        return self._component_selector.has_selection()

    def on_page_shown(self) -> None:
        """Called when page becomes visible."""
        super().on_page_shown()

        game = self.state_manager.get_game_manager().get(self.state_manager.get_selected_game())
        self._component_selector.set_game(game)
        self._search_input.setFocus()

        if self._lang_select.count_items() == 0:
            for lang_code in self.state_manager.get_languages_order():
                icon_path = FLAGS_DIR / f"{lang_code}.png"
                self._lang_select.add_item(lang_code, str(icon_path))

        if self._orchestrator:
            QTimer.singleShot(200, self._trigger_validation)

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        self._left_title.setText(tr("page.selection.select_category"))
        self._btn_export.setText(tr("page.selection.btn_export"))
        self._btn_import.setText(tr("page.selection.btn_import"))
        self._btn_deselect_all.setText(tr("page.selection.btn_deselect_all"))
        self._mod_details_title.setText(tr("page.selection.mod_details_title"))
        self._violation_title.setText(tr("page.selection.violation_title"))
        self._action_import_file.setText(tr("page.selection.action_import_file"))
        self._action_import_weidu.setText(tr("page.selection.action_import_weidu"))
        self._search_input.setPlaceholderText(tr("page.selection.search_placeholder"))
        self._chk_ignore_warnings.setText(tr("page.selection.ignore_warnings"))
        self._chk_ignore_errors.setText(tr("page.selection.ignore_errors"))
        self._chk_show_violations.setText(tr("page.selection.filter.violation_only"))
        self._chk_show_violations.setToolTip(tr("page.selection.filter.violation_only_tooltip"))

        # Update category buttons
        for button in self._category_buttons.values():
            button.retranslate_ui()

        self._violation_panel.retranslate_ui()

        # Update component selector
        self._component_selector.retranslate_ui()

        # Update details panel
        self._details_panel.retranslate_ui()

        # Reapply filters to update display
        self._apply_all_filters()

    def load_state(self) -> None:
        """Load state from state manager."""
        super().load_state()

        self._chk_ignore_errors.setChecked(
            self.state_manager.get_page_option(self.get_page_id(), "ignore_errors", False)
        )
        self._chk_ignore_warnings.setChecked(
            self.state_manager.get_page_option(self.get_page_id(), "ignore_warnings", False)
        )

        # Load selected components
        selected_components = self.state_manager.get_selected_components()
        if selected_components:
            game = self.state_manager.get_game_manager().get(
                self.state_manager.get_selected_game()
            )
            self._component_selector.set_game(game)
            self._component_selector.restore_selection(selected_components)

    def save_state(self) -> None:
        """Save page data to state manager."""
        super().save_state()

        # Save selected components
        components = self._component_selector.get_selected_items()
        self.state_manager.set_selected_components(components)

        self.state_manager.set_page_option(
            self.get_page_id(), "ignore_errors", self._chk_ignore_errors.isChecked()
        )
        self.state_manager.set_page_option(
            self.get_page_id(), "ignore_warnings", self._chk_ignore_warnings.isChecked()
        )
